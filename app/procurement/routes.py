"""
Procurement Module - Complete Implementation
Routes for:
- Procurement Dashboard & Analytics
- Material Request (PR) creation, submission, approval
- Purchase Order (PO) drafting, approval, issuance
- Inventory & Asset Management
- Vendor Management & Performance
- Budget & Maintenance Tracking
- Reports & Notifications
"""

from flask import (
    Blueprint, render_template, request, redirect, url_for, flash, jsonify,
    current_app, session, send_file
)
from flask_login import current_user, login_required
from datetime import datetime, timedelta
from decimal import Decimal, InvalidOperation
from sqlalchemy import func
from sqlalchemy.exc import SQLAlchemyError
import pandas as pd
from io import BytesIO
import os

# Import extensions and models
from app.models import (
    db, User, Project, PurchaseOrder, PurchaseOrderItem, Vendor, 
    Inventory, ProjectStaff, ApprovalState, AssetTransfer, ApprovalLog
)
from app.utils import role_required, Roles

# Blueprint definition
bp = Blueprint('procurement', __name__, url_prefix='/procurement')


def get_user_procurement_projects(user):
    """Get projects assigned to procurement user."""
    if user.has_role(Roles.HQ_PROCUREMENT) or user.has_role(Roles.PROCUREMENT_MANAGER):
        # Managers can see all active projects
        return Project.query.filter_by(status='active').all()
    else:
        # Staff see only assigned projects
        return db.session.query(Project).join(
            ProjectStaff
        ).filter(
            ProjectStaff.user_id == user.id,
            ProjectStaff.is_active == True,
            Project.status == 'active'
        ).all()


# ============================================================================
# DASHBOARD & HOME
# ============================================================================

@bp.route('/')
@login_required
@role_required([Roles.HQ_PROCUREMENT, Roles.PROCUREMENT_MANAGER, Roles.PROCUREMENT_STAFF])
def index():
    """Main procurement dashboard."""
    try:
        # Asset statistics
        total_assets = Inventory.query.count()
        active_assets = Inventory.query.count()
        
        # Purchase order statistics
        total_orders = PurchaseOrder.query.count()
        pending_orders = PurchaseOrder.query.filter(
            PurchaseOrder.approval_state == ApprovalState.PENDING
        ).count()
        
        # Vendor statistics
        total_vendors = Vendor.query.count()
        active_vendors = Vendor.query.filter(
            Vendor.is_active == True
        ).count()
        
        # Budget statistics - calculate from purchase orders
        total_po_amount = db.session.query(
            func.sum(PurchaseOrder.total_amount)
        ).scalar() or Decimal('0')
        
        utilized = db.session.query(
            func.sum(PurchaseOrder.total_amount)
        ).filter(PurchaseOrder.approval_state == ApprovalState.APPROVED).scalar() or Decimal('0')
        
        remaining_budget = float(total_po_amount) - float(utilized)
        
        summary = {
            'total_assets': total_assets,
            'active_assets': active_assets,
            'total_orders': total_orders,
            'pending_orders': pending_orders,
            'total_vendors': total_vendors,
            'active_vendors': active_vendors,
            'total_budget': float(total_po_amount),
            'utilized_budget': float(utilized),
            'remaining_budget': remaining_budget,
            'budget_utilization': (float(utilized) / float(total_po_amount) * 100) if float(total_po_amount) > 0 else 0
        }
        
        return render_template('procurement/index.html', summary=summary)
    except Exception as e:
        current_app.logger.error(f"Dashboard error: {str(e)}")
        flash("Error loading dashboard", "error")
        return render_template('error.html'), 500


@bp.route('/dashboard')
@login_required
@role_required([Roles.HQ_PROCUREMENT, Roles.PROCUREMENT_MANAGER, Roles.PROCUREMENT_STAFF])
def dashboard():
    """Procurement dashboard with detailed metrics."""
    try:
        # Order metrics
        total_po = PurchaseOrder.query.count()
        pending_po = PurchaseOrder.query.filter_by(approval_state=ApprovalState.PENDING).count()
        approved_po = PurchaseOrder.query.filter_by(approval_state=ApprovalState.APPROVED).count()
        completed_po = PurchaseOrder.query.filter_by(approval_state=ApprovalState.DRAFT).count()
        
        # Recent orders
        recent_po = PurchaseOrder.query.order_by(
            PurchaseOrder.created_at.desc()
        ).limit(5).all()
        
        # Vendor metrics
        total_vendors = Vendor.query.count()
        active_vendors = Vendor.query.filter_by(is_active=True).count()
        
        # Calculate average PO value
        avg_po_value = db.session.query(
            func.avg(PurchaseOrder.total_amount)
        ).scalar() or Decimal('0')
        
        metrics = {
            'total_po': total_po,
            'pending_po': pending_po,
            'approved_po': approved_po,
            'completed_po': completed_po,
            'avg_po_value': float(avg_po_value),
            'total_vendors': total_vendors,
            'active_vendors': active_vendors,
            'recent_po': recent_po
        }
        
        return render_template('procurement/dashboard.html', metrics=metrics)
    except Exception as e:
        current_app.logger.error(f"Dashboard error: {str(e)}")
        flash("Error loading dashboard", "error")
        return render_template('error.html'), 500


# ============================================================================
# PURCHASE ORDERS
# ============================================================================

@bp.route('/purchases')
@login_required
@role_required([Roles.HQ_PROCUREMENT, Roles.PROCUREMENT_MANAGER, Roles.PROCUREMENT_STAFF])
def purchases():
    """View all purchase orders."""
    try:
        page = request.args.get('page', 1, type=int)
        per_page = 20
        
        # Get orders with pagination
        paginated = PurchaseOrder.query.paginate(
            page=page, per_page=per_page, error_out=False
        )
        
        orders = paginated.items
        
        # Statistics
        stats = {
            'total': PurchaseOrder.query.count(),
            'pending': PurchaseOrder.query.filter_by(approval_state=ApprovalState.PENDING).count(),
            'approved': PurchaseOrder.query.filter_by(approval_state=ApprovalState.APPROVED).count(),
            'draft': PurchaseOrder.query.filter_by(approval_state=ApprovalState.DRAFT).count(),
            'rejected': PurchaseOrder.query.filter_by(approval_state=ApprovalState.REJECTED).count(),
            # Keep aliases expected by existing templates.
            'completed': PurchaseOrder.query.filter_by(approval_state=ApprovalState.APPROVED).count(),
            'cancelled': PurchaseOrder.query.filter_by(approval_state=ApprovalState.REJECTED).count()
        }
        
        return render_template(
            'procurement/purchases.html',
            orders=orders,
            pagination=paginated,
            stats=stats
        )
    except Exception as e:
        current_app.logger.error(f"Purchase list error: {str(e)}")
        flash("Error loading purchases", "error")
        return render_template('error.html'), 500


@bp.route('/po/create', methods=['GET', 'POST'])
@login_required
@role_required([Roles.HQ_PROCUREMENT, Roles.PROCUREMENT_MANAGER, Roles.PROCUREMENT_STAFF])
def create_purchase_order():
    """Create new purchase order."""
    if request.method == 'POST':
        try:
            import json

            project_id = request.form.get('project_id', type=int)
            vendor_id = request.form.get('vendor_id', type=int)
            if not project_id or not vendor_id:
                raise ValueError("Project and vendor are required.")

            # Parse line items payload from client-side builder.
            line_items_data = request.form.get('line_items', '[]')
            line_items = json.loads(line_items_data) if line_items_data else []
            if not isinstance(line_items, list):
                raise ValueError("Invalid line items payload.")

            normalized_items = []
            total_amount = Decimal('0')
            for raw_item in line_items:
                description = str(raw_item.get('description', '')).strip()
                if not description:
                    continue

                quantity = Decimal(str(raw_item.get('quantity', 0) or 0))
                unit_rate = Decimal(str(raw_item.get('unitCost', 0) or 0))
                if quantity <= 0:
                    raise ValueError(f"Quantity must be greater than zero for item '{description}'.")
                if unit_rate < 0:
                    raise ValueError(f"Unit cost cannot be negative for item '{description}'.")

                total_amount += quantity * unit_rate
                normalized_items.append({
                    'description': description,
                    'unit': str(raw_item.get('unit', '')).strip() or None,
                    'quantity': quantity,
                    'unit_rate': unit_rate
                })

            if not normalized_items:
                raise ValueError("Add at least one valid line item before creating a purchase order.")

            po = PurchaseOrder(
                project_id=project_id,
                vendor_id=vendor_id,
                po_number=f"PO-{datetime.utcnow().strftime('%Y%m%d%H%M%S')}",
                total_amount=total_amount,
                approval_state=ApprovalState.DRAFT,
                issued_by=current_user.id
            )
            db.session.add(po)
            db.session.flush()

            for item in normalized_items:
                po_item = PurchaseOrderItem(
                    po_id=po.id,
                    description=item['description'],
                    unit=item['unit'],
                    quantity=item['quantity'],
                    unit_rate=item['unit_rate']
                )
                po_item.calculate_amount()
                db.session.add(po_item)

            db.session.commit()

            flash(f'Purchase Order created successfully with {len(normalized_items)} items', 'success')
            return redirect(url_for('procurement.view_purchase_order', po_id=po.id))
        except Exception as e:
            db.session.rollback()
            current_app.logger.error(f"Create PO error: {str(e)}", exc_info=True)
            flash(f"Error creating purchase order: {str(e)}", "error")
    
    try:
        projects = get_user_procurement_projects(current_user)
        vendors = Vendor.query.filter_by(is_active=True).all()
        return render_template(
            'procurement/create_po.html',
            projects=projects,
            vendors=vendors
        )
    except Exception as e:
        current_app.logger.error(f"Create PO form error: {str(e)}")
        flash("Error loading form", "error")
        return render_template('error.html'), 500


@bp.route('/po/<int:po_id>')
@login_required
def view_purchase_order(po_id):
    """View purchase order details."""
    try:
        po = PurchaseOrder.query.get_or_404(po_id)
        return render_template('procurement/view_po.html', po=po)
    except Exception as e:
        current_app.logger.error(f"View PO error: {str(e)}")
        flash("Error loading purchase order", "error")
        return render_template('error.html'), 500


@bp.route('/po/<int:po_id>/approve', methods=['POST'])
@login_required
@role_required([Roles.HQ_PROCUREMENT, Roles.PROCUREMENT_MANAGER, Roles.PROCUREMENT_STAFF])
def approve_purchase_order(po_id):
    """Approve purchase order."""
    try:
        po = PurchaseOrder.query.get_or_404(po_id)
        po.approval_state = ApprovalState.PENDING
        po.issued_at = datetime.utcnow()
        db.session.add(ApprovalLog(
            entity_type='purchase_order',
            entity_id=po.id,
            action='submitted',
            actor_id=current_user.id,
            comment='Submitted by Procurement to Cost Control for review.'
        ))
        db.session.commit()
        
        flash('Purchase Order sent to Cost Control for approval', 'success')
    except Exception as e:
        db.session.rollback()
        current_app.logger.error(f"Approve PO error: {str(e)}")
        flash("Error approving purchase order", "error")
    
    return redirect(url_for('procurement.view_purchase_order', po_id=po_id))


@bp.route('/po/<int:po_id>/reject', methods=['POST'])
@login_required
@role_required([Roles.HQ_PROCUREMENT, Roles.PROCUREMENT_MANAGER, Roles.PROCUREMENT_STAFF])
def reject_purchase_order(po_id):
    """Reject purchase order."""
    try:
        po = PurchaseOrder.query.get_or_404(po_id)
        reason = request.form.get('reason', '')
        po.approval_state = ApprovalState.REJECTED
        db.session.commit()
        
        flash(f'Purchase Order rejected', 'success')
    except Exception as e:
        db.session.rollback()
        current_app.logger.error(f"Reject PO error: {str(e)}")
        flash("Error rejecting purchase order", "error")
    
    return redirect(url_for('procurement.view_purchase_order', po_id=po_id))


# ============================================================================
# INVENTORY & ASSETS
# ============================================================================

@bp.route('/assets')
@login_required
@role_required([Roles.HQ_PROCUREMENT, Roles.PROCUREMENT_MANAGER, Roles.PROCUREMENT_STAFF])
def assets():
    """View all assets."""
    try:
        page = request.args.get('page', 1, type=int)
        per_page = 20
        
        paginated = Inventory.query.paginate(
            page=page, per_page=per_page, error_out=False
        )
        
        assets = paginated.items
        
        low_stock_count = Inventory.query.filter(
            Inventory.quantity_on_hand <= Inventory.reorder_level
        ).count()
        no_stock_count = Inventory.query.filter(
            Inventory.quantity_on_hand == 0
        ).count()
        stats = {
            'total': Inventory.query.count(),
            'active': Inventory.query.count(),
            'low_stock': low_stock_count,
            'no_stock': no_stock_count,
            # Template compatibility keys.
            'maintenance': low_stock_count,
            'retired': no_stock_count
        }
        
        return render_template(
            'procurement/assets.html',
            assets=assets,
            pagination=paginated,
            stats=stats
        )
    except Exception as e:
        current_app.logger.error(f"Assets list error: {str(e)}")
        flash("Error loading assets", "error")
        return render_template('error.html'), 500


@bp.route('/asset/create', methods=['GET', 'POST'])
@login_required
@role_required([Roles.HQ_PROCUREMENT, Roles.PROCUREMENT_MANAGER, Roles.PROCUREMENT_STAFF])
def add_asset():
    """Create new asset."""
    if request.method == 'POST':
        try:
            project_id = request.form.get('project_id', type=int)
            item_description = (request.form.get('item_description') or '').strip()
            unit = (request.form.get('unit') or '').strip() or None
            quantity_on_hand = Decimal(str(request.form.get('quantity_on_hand', '0') or '0'))
            reorder_level = Decimal(str(request.form.get('reorder_level', '0') or '0'))

            if not item_description:
                raise ValueError("Item description is required.")
            if quantity_on_hand < 0:
                raise ValueError("Quantity on hand cannot be negative.")
            if reorder_level < 0:
                raise ValueError("Reorder level cannot be negative.")

            asset = Inventory(
                project_id=project_id if project_id else None,
                item_description=item_description,
                unit=unit,
                quantity_on_hand=quantity_on_hand,
                reorder_level=reorder_level
            )
            db.session.add(asset)
            db.session.commit()
            
            flash(f'Asset {asset.item_description} created', 'success')
            return redirect(url_for('procurement.assets'))
        except (ValueError, InvalidOperation) as e:
            db.session.rollback()
            flash(f"Error creating asset: {str(e)}", "error")
        except Exception as e:
            db.session.rollback()
            current_app.logger.error(f"Create asset error: {str(e)}", exc_info=True)
            flash(f"Error creating asset: {str(e)}", "error")

    projects = get_user_procurement_projects(current_user)
    return render_template('procurement/add_asset.html', projects=projects)


@bp.route('/asset-transfer', methods=['GET', 'POST'])
@login_required
@role_required([Roles.HQ_PROCUREMENT, Roles.PROCUREMENT_MANAGER, Roles.PROCUREMENT_STAFF, Roles.COST_CONTROL_MANAGER, Roles.COST_CONTROL_STAFF])
def asset_transfer():
    """Transfer assets between projects."""
    if request.method == 'POST':
        try:
            inventory_id = request.form.get('inventory_id', type=int)
            to_project_id = request.form.get('to_project_id', type=int)
            quantity = Decimal(str(request.form.get('quantity', '0') or '0'))
            reason = (request.form.get('reason') or '').strip()

            source_item = Inventory.query.get_or_404(inventory_id)
            if quantity <= 0:
                raise ValueError("Quantity must be greater than zero.")
            if quantity > Decimal(str(source_item.quantity_on_hand or 0)):
                raise ValueError("Transfer quantity exceeds quantity on hand.")
            if not to_project_id:
                raise ValueError("Destination project is required.")

            source_item.quantity_on_hand = Decimal(str(source_item.quantity_on_hand or 0)) - quantity

            destination_item = Inventory.query.filter_by(
                project_id=to_project_id,
                item_description=source_item.item_description,
                unit=source_item.unit
            ).first()
            if destination_item:
                destination_item.quantity_on_hand = Decimal(str(destination_item.quantity_on_hand or 0)) + quantity
            else:
                destination_item = Inventory(
                    project_id=to_project_id,
                    item_description=source_item.item_description,
                    unit=source_item.unit,
                    quantity_on_hand=quantity,
                    reorder_level=source_item.reorder_level
                )
                db.session.add(destination_item)

            transfer = AssetTransfer(
                inventory_id=source_item.id,
                from_project_id=source_item.project_id,
                to_project_id=to_project_id,
                quantity=quantity,
                reason=reason,
                status='completed',
                transferred_by=current_user.id,
                transfer_date=datetime.utcnow()
            )
            db.session.add(transfer)
            db.session.commit()
            flash('Asset transfer completed successfully.', 'success')
        except Exception as e:
            db.session.rollback()
            flash(f'Error transferring asset: {str(e)}', 'error')
        return redirect(url_for('procurement.asset_transfer'))

    inventories = Inventory.query.order_by(Inventory.item_description.asc()).all()
    projects = Project.query.order_by(Project.name.asc()).all()
    transfers = AssetTransfer.query.order_by(AssetTransfer.transfer_date.desc()).limit(100).all()
    return render_template(
        'procurement/asset_transfer.html',
        inventories=inventories,
        projects=projects,
        transfers=transfers
    )


@bp.route('/asset/<int:asset_id>/edit', methods=['GET', 'POST'])
@login_required
@role_required([Roles.HQ_PROCUREMENT, Roles.PROCUREMENT_MANAGER, Roles.PROCUREMENT_STAFF])
def edit_asset(asset_id):
    """Edit asset."""
    try:
        asset = Inventory.query.get_or_404(asset_id)
        
        if request.method == 'POST':
            asset.item_description = request.form.get('description', asset.item_description)
            asset.unit = request.form.get('unit', asset.unit)
            asset.quantity_on_hand = float(request.form.get('quantity_on_hand', asset.quantity_on_hand))
            asset.reorder_level = float(request.form.get('reorder_level', asset.reorder_level))
            db.session.commit()
            
            flash('Asset updated', 'success')
            return redirect(url_for('procurement.assets'))
        
        return render_template('procurement/edit_asset.html', asset=asset)
    except Exception as e:
        db.session.rollback()
        current_app.logger.error(f"Edit asset error: {str(e)}")
        flash("Error editing asset", "error")
        return render_template('error.html'), 500


@bp.route('/asset/<int:asset_id>/delete', methods=['POST'])
@login_required
@role_required([Roles.HQ_PROCUREMENT, Roles.PROCUREMENT_MANAGER, Roles.PROCUREMENT_STAFF])
def delete_asset(asset_id):
    """Delete asset."""
    try:
        asset = Inventory.query.get_or_404(asset_id)
        db.session.delete(asset)
        db.session.commit()
        
        flash('Asset deleted', 'success')
    except Exception as e:
        db.session.rollback()
        current_app.logger.error(f"Delete asset error: {str(e)}")
        flash("Error deleting asset", "error")
    
    return redirect(url_for('procurement.assets'))


# ============================================================================
# VENDORS & SUPPLIERS
# ============================================================================

@bp.route('/suppliers')
@login_required
@role_required([Roles.HQ_PROCUREMENT, Roles.PROCUREMENT_MANAGER, Roles.PROCUREMENT_STAFF])
def suppliers():
    """View all vendors/suppliers."""
    try:
        page = request.args.get('page', 1, type=int)
        per_page = 20
        
        paginated = Vendor.query.paginate(
            page=page, per_page=per_page, error_out=False
        )
        
        vendors = paginated.items
        
        stats = {
            'total': Vendor.query.count(),
            'active': Vendor.query.filter_by(is_active=True).count(),
            'inactive': Vendor.query.filter_by(is_active=False).count()
        }
        
        return render_template(
            'procurement/suppliers.html',
            vendors=vendors,
            pagination=paginated,
            stats=stats
        )
    except Exception as e:
        current_app.logger.error(f"Suppliers list error: {str(e)}")
        flash("Error loading suppliers", "error")
        return render_template('error.html'), 500


@bp.route('/supplier/create', methods=['GET', 'POST'])
@login_required
@role_required([Roles.HQ_PROCUREMENT, Roles.PROCUREMENT_MANAGER, Roles.PROCUREMENT_STAFF])
def create_vendor():
    """Create new vendor."""
    if request.method == 'POST':
        try:
            vendor = Vendor(
                name=request.form.get('name'),
                email=request.form.get('email'),
                phone=request.form.get('phone'),
                address=request.form.get('address'),
                city=request.form.get('city'),
                registration_number=request.form.get('registration_number'),
                is_active=True
            )
            db.session.add(vendor)
            db.session.commit()
            
            flash(f'Vendor {vendor.name} created', 'success')
            return redirect(url_for('procurement.suppliers'))
        except Exception as e:
            db.session.rollback()
            current_app.logger.error(f"Create vendor error: {str(e)}")
            flash(f"Error creating vendor: {str(e)}", "error")
    
    return render_template('procurement/create_vendor.html')


@bp.route('/vendor/<int:vendor_id>')
@login_required
def view_vendor(vendor_id):
    """View vendor details."""
    try:
        vendor = Vendor.query.get_or_404(vendor_id)
        
        # Get vendor statistics
        total_orders = PurchaseOrder.query.filter_by(vendor_id=vendor_id).count()
        completed_orders = PurchaseOrder.query.filter_by(
            vendor_id=vendor_id, status='completed'
        ).count()
        
        stats = {
            'total_orders': total_orders,
            'completed_orders': completed_orders,
            'success_rate': (completed_orders / total_orders * 100) if total_orders > 0 else 0
        }
        
        return render_template('procurement/view_vendor.html', vendor=vendor, stats=stats)
    except Exception as e:
        current_app.logger.error(f"View vendor error: {str(e)}")
        flash("Error loading vendor", "error")
        return render_template('error.html'), 500


# ============================================================================
# BUDGET & ANALYTICS
# ============================================================================

@bp.route('/budget')
@login_required
@role_required([Roles.HQ_PROCUREMENT, Roles.PROCUREMENT_MANAGER, Roles.PROCUREMENT_STAFF])
def budget():
    """View budget summary."""
    try:
        # Budget data from purchase orders grouped by project
        pos = PurchaseOrder.query.all()
        
        budget_by_project = {}
        for po in pos:
            if po.project_id is None:
                continue
            project_id = po.project_id
            if project_id not in budget_by_project:
                budget_by_project[project_id] = {'allocated': 0, 'spent': 0}
            
            amount = float(po.total_amount or 0)
            budget_by_project[project_id]['allocated'] += amount
            
            # Only count as spent if approved
            try:
                if po.approval_state and str(po.approval_state) == str(ApprovalState.APPROVED):
                    budget_by_project[project_id]['spent'] += amount
            except:
                pass
        
        # Ensure budget_by_project is a dict
        if not isinstance(budget_by_project, dict):
            budget_by_project = {}
        
        budget_summary = {
            'total_allocated': 0,
            'total_spent': 0,
            'items_list': []
        }
        
        # Safely iterate over budget_by_project
        for project_id, data in list(budget_by_project.items()):
            project = Project.query.get(project_id)
            allocated = float(data.get('allocated', 0) or 0)
            spent = float(data.get('spent', 0) or 0)
            
            budget_summary['total_allocated'] += allocated
            budget_summary['total_spent'] += spent
            
            utilization = (spent / allocated * 100) if allocated > 0 else 0
            
            budget_summary['items_list'].append({
                'project_id': project_id,
                'project_name': project.name if project else f'Project #{project_id}',
                'allocated': allocated,
                'spent': spent,
                'remaining': allocated - spent,
                'utilization': utilization
            })
        
        return render_template('procurement/budget.html', budget=budget_summary)
    except Exception as e:
        current_app.logger.error(f"Budget error: {str(e)}")
        import traceback
        current_app.logger.error(traceback.format_exc())
        flash("Error loading budget", "error")
        return render_template('error.html'), 500


@bp.route('/analytics')
@login_required
@role_required([Roles.HQ_PROCUREMENT, Roles.PROCUREMENT_MANAGER, Roles.PROCUREMENT_STAFF])
def analytics():
    """Analytics dashboard."""
    try:
        # Spend by vendor
        vendor_spend = db.session.query(
            Vendor.name,
            func.sum(PurchaseOrder.total_amount).label('total')
        ).join(PurchaseOrder).group_by(Vendor.name).order_by(
            func.sum(PurchaseOrder.total_amount).desc()
        ).limit(10).all()
        
        # Spend by month (last 6 months)
        six_months_ago = datetime.utcnow() - timedelta(days=180)
        spend_by_month = db.session.query(
            func.strftime('%Y-%m', PurchaseOrder.created_at).label('month'),
            func.sum(PurchaseOrder.total_amount)
        ).filter(PurchaseOrder.created_at >= six_months_ago).group_by(
            func.strftime('%Y-%m', PurchaseOrder.created_at)
        ).order_by('month').all()
        
        analytics_data = {
            'vendor_spend': [{'vendor': v[0], 'amount': float(v[1] or 0)} for v in vendor_spend],
            'spend_by_month': [{'month': m[0], 'amount': float(m[1] or 0)} for m in spend_by_month]
        }
        
        return render_template('procurement/analytics.html', data=analytics_data)
    except Exception as e:
        current_app.logger.error(f"Analytics error: {str(e)}")
        flash("Error loading analytics", "error")
        return render_template('error.html'), 500


# ============================================================================
# MAINTENANCE & NOTIFICATIONS
# ============================================================================

@bp.route('/maintenance')
@login_required
@role_required([Roles.HQ_PROCUREMENT, Roles.PROCUREMENT_MANAGER, Roles.PROCUREMENT_STAFF])
def maintenance():
    """View maintenance schedule."""
    try:
        # Get low stock items that need attention
        low_stock = Inventory.query.filter(
            Inventory.quantity_on_hand <= Inventory.reorder_level
        ).all()
        
        no_stock = Inventory.query.filter(
            Inventory.quantity_on_hand == 0
        ).all()
        
        maintenance_data = {
            'low_stock': low_stock,
            'no_stock': no_stock,
            'total_low': len(low_stock),
            'total_no': len(no_stock)
        }
        
        return render_template('procurement/maintenance.html', data=maintenance_data)
    except Exception as e:
        current_app.logger.error(f"Maintenance error: {str(e)}")
        flash("Error loading maintenance", "error")
        return render_template('error.html'), 500


@bp.route('/notifications')
@login_required
@role_required([Roles.HQ_PROCUREMENT, Roles.PROCUREMENT_MANAGER, Roles.PROCUREMENT_STAFF])
def notifications():
    """View notifications and alerts."""
    try:
        alert_data = []

        low_stock_items = Inventory.query.filter(
            Inventory.quantity_on_hand <= Inventory.reorder_level
        ).limit(25).all()
        for item in low_stock_items:
            alert_data.append({
                'id': f"inv-{item.id}",
                'title': f"Low stock: {item.item_description}",
                'type': 'inventory',
                'severity': 'warning' if (item.quantity_on_hand or 0) > 0 else 'critical',
                'status': 'unread',
                'created_at': item.last_updated.strftime('%Y-%m-%d %H:%M:%S') if item.last_updated else ''
            })

        pending_orders = PurchaseOrder.query.filter(
            PurchaseOrder.approval_state == ApprovalState.PENDING
        ).order_by(PurchaseOrder.created_at.desc()).limit(25).all()
        for po in pending_orders:
            alert_data.append({
                'id': f"po-{po.id}",
                'title': f"Pending approval: {po.po_number or ('PO-' + str(po.id))}",
                'type': 'purchase_order',
                'severity': 'info',
                'status': 'unread',
                'created_at': po.created_at.strftime('%Y-%m-%d %H:%M:%S') if po.created_at else ''
            })

        alert_data = sorted(alert_data, key=lambda x: x['created_at'], reverse=True)[:50]

        return render_template('procurement/notifications.html', alerts=alert_data)
    except Exception as e:
        current_app.logger.error(f"Notifications error: {str(e)}")
        flash("Error loading notifications", "error")
        return render_template('error.html'), 500


# ============================================================================
# REPORTS
# ============================================================================

@bp.route('/reports')
@login_required
@role_required([Roles.HQ_PROCUREMENT, Roles.PROCUREMENT_MANAGER, Roles.PROCUREMENT_STAFF])
def reports():
    """View reports."""
    try:
        # Generate reports from existing data
        pos = PurchaseOrder.query.all()
        
        report_data = [
            {
                'id': po.id,
                'po_number': po.po_number,
                'type': 'Purchase Order',
                'amount': float(po.total_amount or 0),
                'status': po.approval_state.value,
                'created_at': po.created_at.strftime('%Y-%m-%d') if po.created_at else 'N/A'
            }
            for po in pos
        ]
        
        return render_template('procurement/reports.html', reports=report_data)
    except Exception as e:
        current_app.logger.error(f"Reports error: {str(e)}")
        flash("Error loading reports", "error")
        return render_template('error.html'), 500


# ============================================================================
# SEARCH
# ============================================================================

@bp.route('/search', methods=['GET', 'POST'])
@login_required
@role_required([Roles.HQ_PROCUREMENT, Roles.PROCUREMENT_MANAGER, Roles.PROCUREMENT_STAFF])
def search():
    """Search across procurement data."""
    try:
        query = request.args.get('q', '').strip() if request.method == 'GET' else request.form.get('q', '').strip()
        results = {'assets': [], 'orders': [], 'vendors': []}
        
        if query:
            # Search assets
            results['assets'] = Inventory.query.filter(
                (Inventory.item_description.ilike(f"%{query}%")) |
                (Inventory.unit.ilike(f"%{query}%"))
            ).limit(10).all()
            
            # Search orders
            results['orders'] = PurchaseOrder.query.filter(
                PurchaseOrder.po_number.ilike(f"%{query}%")
            ).limit(10).all()
            
            # Search vendors
            results['vendors'] = Vendor.query.filter(
                (Vendor.name.ilike(f"%{query}%")) |
                (Vendor.city.ilike(f"%{query}%"))
            ).limit(10).all()
        
        return render_template('procurement/search.html', query=query, results=results)
    except Exception as e:
        current_app.logger.error(f"Search error: {str(e)}")
        flash("Error performing search", "error")
        return render_template('error.html'), 500


# ============================================================================
# SETTINGS & PROFILE
# ============================================================================

@bp.route('/settings', methods=['GET', 'POST'])
@login_required
@role_required([Roles.HQ_PROCUREMENT, Roles.PROCUREMENT_MANAGER, Roles.PROCUREMENT_STAFF])
def settings():
    """Procurement settings."""
    try:
        settings_data = {
            'email_alerts': True,
            'browser_notifications': True,
            'require_approval': True,
            'theme': 'light'
        }
        
        if request.method == 'POST':
            settings_data['email_alerts'] = request.form.get('email_alerts') == 'on'
            settings_data['browser_notifications'] = request.form.get('browser_notifications') == 'on'
            settings_data['require_approval'] = request.form.get('require_approval') == 'on'
            settings_data['theme'] = request.form.get('theme', 'light')
            flash('Settings updated', 'success')
        
        return render_template('procurement/settings.html', data=settings_data)
    except Exception as e:
        current_app.logger.error(f"Settings error: {str(e)}")
        flash("Error loading settings", "error")
        return render_template('error.html'), 500


@bp.route('/profile')
@login_required
@role_required([Roles.HQ_PROCUREMENT, Roles.PROCUREMENT_MANAGER, Roles.PROCUREMENT_STAFF])
def profile():
    """User profile."""
    try:
        user = current_user
        
        purchases_created = PurchaseOrder.query.filter_by(
            issued_by=user.id
        ).count()
        
        orders_approved = PurchaseOrder.query.filter(
            PurchaseOrder.approval_state == ApprovalState.APPROVED
        ).count()
        
        assets_managed = Inventory.query.count()
        
        profile_data = {
            'name': user.name,
            'email': user.email,
            'role': getattr(user, 'role', 'Unknown'),
            'purchases_created': purchases_created,
            'orders_approved': orders_approved,
            'assets_managed': assets_managed,
            'joined_date': user.created_at.strftime('%Y-%m-%d') if hasattr(user, 'created_at') and user.created_at else 'Unknown'
        }
        
        return render_template('procurement/profile.html', data=profile_data)
    except Exception as e:
        current_app.logger.error(f"Profile error: {str(e)}")
        flash("Error loading profile", "error")
        return render_template('error.html'), 500


# ===== INVOICE MANAGEMENT =====

@bp.route('/invoices')
@login_required
@role_required([Roles.PROCUREMENT_MANAGER, Roles.HQ_PROCUREMENT])
def view_invoices():
    """View invoices sent by Finance to Procurement."""
    try:
        from app.models import PaymentRequest
        
        page = request.args.get('page', 1, type=int)
        
        # Get invoices sent to procurement
        invoices = PaymentRequest.query.filter_by(sent_to_procurement=True).order_by(
            PaymentRequest.created_at.desc()
        ).paginate(page=page, per_page=20)
        
        total_amount = db.session.query(
            func.sum(PaymentRequest.invoice_amount)
        ).filter_by(sent_to_procurement=True).scalar() or 0
        
        return render_template(
            'procurement/invoices.html',
            invoices=invoices,
            total_amount=float(total_amount)
        )
    except Exception as e:
        current_app.logger.error(f"Invoice viewing error: {str(e)}")
        flash(f"Error loading invoices: {str(e)}", "error")
        return redirect(url_for('procurement.dashboard'))
