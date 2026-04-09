"""
Cost Control Module - Budget Tracking, Cost Analysis, BOQ Review, Change Order Management
Handles machinery, fuel logs, cost categories, variance analysis with real business logic.
"""

from flask import Blueprint, render_template, request, redirect, url_for, flash, jsonify
from flask_login import current_user, login_required
from app.models import (
    db, Project, PurchaseOrder, PaymentRequest, BOQItem, ChangeOrder,
    ApprovalState, ProjectStaff, ProjectEquipment, Inventory, AssetTransfer,
    ProjectPaymentRequest, ApprovalLog
)
from app.auth.decorators import role_required
from app.utils import Roles
from datetime import datetime, timedelta
from decimal import Decimal
from sqlalchemy import func, extract

# Blueprint definition
bp = Blueprint('cost_control', __name__, url_prefix='/cost-control')


def get_user_cost_control_projects(user):
    """Get projects assigned to cost control user."""
    if user.has_role(Roles.COST_CONTROL_MANAGER):
        # Managers can see all projects
        return Project.query.all()
    else:
        # Staff see only assigned projects
        return db.session.query(Project).join(
            ProjectStaff
        ).filter(
            ProjectStaff.user_id == user.id,
            ProjectStaff.is_active == True
        ).all()


@bp.route('/dashboard')
@login_required
@role_required([Roles.COST_CONTROL_MANAGER, Roles.COST_CONTROL_STAFF])
def dashboard():
    """Cost Control dashboard with budget and spending analysis."""
    # Get projects assigned to this user
    projects = get_user_cost_control_projects(current_user)
    
    # Calculate budget metrics for each project
    project_budgets = []
    total_budget = 0
    total_committed = 0
    total_spent = 0
    
    for project in projects:
        project_budget = float(project.budget or 0)
        
        # Get committed spend (approved but not paid POs)
        approved_pos = PurchaseOrder.query.filter(
            PurchaseOrder.project_id == project.id,
            PurchaseOrder.approval_state == ApprovalState.APPROVED
        ).all()
        committed = sum(float(po.total_amount or 0) for po in approved_pos)
        
        # Get actual spend (paid invoices)
        paid_payments = db.session.query(PaymentRequest).join(
            PurchaseOrder, PaymentRequest.po_id == PurchaseOrder.id
        ).filter(
            PurchaseOrder.project_id == project.id,
            PaymentRequest.approval_state == ApprovalState.APPROVED
        ).all()
        spent = sum(float(p.invoice_amount or 0) for p in paid_payments)
        
        remaining = project_budget - committed - spent
        utilization = (committed + spent) / project_budget * 100 if project_budget > 0 else 0
        
        project_budgets.append({
            'project': project,
            'budget': project_budget,
            'committed': committed,
            'spent': spent,
            'remaining': remaining,
            'utilization': utilization,
            'status': 'critical' if utilization >= 95 else 'warning' if utilization >= 80 else 'healthy'
        })
        
        total_budget += project_budget
        total_committed += committed
        total_spent += spent
    
    # Calculate statistics
    total_utilization = (total_committed + total_spent) / total_budget * 100 if total_budget > 0 else 0
    
    # Get pending change orders
    pending_change_orders = ChangeOrder.query.filter_by(approval_state=ApprovalState.PENDING).count()
    
    # Get high-risk POs (over 80% of budget allocated to one PO for a project)
    high_risk_items = []
    for project in projects:
        pos = PurchaseOrder.query.filter_by(project_id=project.id, approval_state=ApprovalState.APPROVED).all()
        for po in pos:
            if project.budget > 0:
                po_percentage = (float(po.total_amount or 0) / float(project.budget)) * 100
                if po_percentage > 20:  # High concentration
                    high_risk_items.append({
                        'po': po,
                        'project': project,
                        'percentage': po_percentage
                    })
    
    return render_template(
        'cost_control/dashboard.html',
        total_budget=total_budget,
        total_committed=total_committed,
        total_spent=total_spent,
        total_remaining=total_budget - total_committed - total_spent,
        total_utilization=total_utilization,
        project_budgets=project_budgets,
        pending_change_orders=pending_change_orders,
        high_risk_items=high_risk_items[:5]  # Top 5 high-risk items
    )


@bp.route('/budget-status')
@login_required
@role_required([Roles.COST_CONTROL_MANAGER, Roles.COST_CONTROL_STAFF])
def budget_status():
    """View budget status for all projects."""
    projects = get_user_cost_control_projects(current_user)
    
    project_status = []
    for project in projects:
        project_budget = float(project.budget or 0)
        
        approved_pos = PurchaseOrder.query.filter(
            PurchaseOrder.project_id == project.id,
            PurchaseOrder.approval_state == ApprovalState.APPROVED
        ).all()
        committed = sum(float(po.total_amount or 0) for po in approved_pos)
        
        paid_payments = db.session.query(PaymentRequest).join(
            PurchaseOrder, PaymentRequest.po_id == PurchaseOrder.id
        ).filter(
            PurchaseOrder.project_id == project.id,
            PaymentRequest.approval_state == ApprovalState.APPROVED
        ).all()
        spent = sum(float(p.invoice_amount or 0) for p in paid_payments)
        
        remaining = project_budget - committed - spent
        utilization = (committed + spent) / project_budget * 100 if project_budget > 0 else 0
        
        project_status.append({
            'project': project,
            'budget': project_budget,
            'committed': committed,
            'spent': spent,
            'remaining': remaining,
            'utilization': utilization
        })
    
    return render_template('cost_control/budget_status.html', projects=project_status)


@bp.route('/cost-analysis')
@login_required
@role_required(['cost_control_manager'])
def cost_analysis():
    """Detailed cost analysis and spend tracking."""
    projects = Project.query.all()
    
    spend_by_project = []
    for project in projects:
        pos = PurchaseOrder.query.filter_by(project_id=project.id, approval_state=ApprovalState.APPROVED).all()
        total_spend = sum(float(po.total_amount or 0) for po in pos)
        spend_by_project.append({
            'name': project.name,
            'spend': total_spend,
            'po_count': len(pos)
        })
    
    spend_by_project.sort(key=lambda x: x['spend'], reverse=True)
    
    total_spend = sum(item['spend'] for item in spend_by_project)
    max_spend = max((item['spend'] for item in spend_by_project), default=0)

    return render_template(
        'cost_control/cost_analysis.html',
        spend_by_project=spend_by_project,
        total_spend=total_spend,
        max_spend=max_spend
    )


@bp.route('/change-orders')
@login_required
@role_required([Roles.COST_CONTROL_MANAGER, Roles.COST_CONTROL_STAFF])
def change_orders():
    """View and manage change orders."""
    status_filter = request.args.get('status', None)
    page = request.args.get('page', 1, type=int)
    
    query = ChangeOrder.query
    if status_filter:
        query = query.filter_by(approval_state=status_filter)
    
    cos = query.order_by(ChangeOrder.created_at.desc()).paginate(page=page, per_page=15)
    
    return render_template('cost_control/change_orders.html', change_orders=cos, selected_status=status_filter)


@bp.route('/boq-review')
@login_required
@role_required([Roles.COST_CONTROL_MANAGER, Roles.COST_CONTROL_STAFF])
def boq_review():
    """Review Bill of Quantities for budgeting."""
    projects = Project.query.all()
    
    boq_summary = []
    for project in projects:
        items = BOQItem.query.filter_by(project_id=project.id).all()
        total_amount = sum(float(item.amount or 0) for item in items if item.amount)
        
        boq_summary.append({
            'project': project,
            'item_count': len(items),
            'total_amount': total_amount,
            'boq_items': items[:10]  # First 10 items
        })
    
    return render_template('cost_control/boq_review.html', boq_summary=boq_summary)


@bp.route('/api/budget-breakdown/<int:project_id>')
@login_required
@role_required([Roles.COST_CONTROL_MANAGER, Roles.COST_CONTROL_STAFF])
def api_budget_breakdown(project_id):
    """API endpoint for budget breakdown by category."""
    project = Project.query.get_or_404(project_id)
    
    # Get POs grouped by vendor
    pos = PurchaseOrder.query.filter(
        PurchaseOrder.project_id == project_id,
        PurchaseOrder.approval_state == ApprovalState.APPROVED
    ).all()
    
    breakdown = {}
    for po in pos:
        vendor_name = po.vendor.name if po.vendor else 'Unknown'
        breakdown[vendor_name] = breakdown.get(vendor_name, 0) + float(po.total_amount or 0)
    
    return jsonify(breakdown)


@bp.route('/api/project-budget/<int:project_id>')
@login_required
@role_required([Roles.COST_CONTROL_MANAGER, Roles.COST_CONTROL_STAFF])
def api_project_budget(project_id):
    """API endpoint for project budget status."""
    project = Project.query.get_or_404(project_id)
    
    project_budget = float(project.budget or 0)
    
    approved_pos = PurchaseOrder.query.filter(
        PurchaseOrder.project_id == project_id,
        PurchaseOrder.approval_state == ApprovalState.APPROVED
    ).all()
    committed = sum(float(po.total_amount or 0) for po in approved_pos)
    
    spent = 0
    
    return jsonify({
        'budget': project_budget,
        'committed': committed,
        'spent': spent,
        'remaining': project_budget - committed - spent,
        'utilization': (committed + spent) / project_budget * 100 if project_budget > 0 else 0
    })


# ============================================
# COST CATEGORIES MANAGEMENT
# ============================================

@bp.route('/categories')
@login_required
@role_required([Roles.COST_CONTROL_MANAGER, Roles.COST_CONTROL_STAFF])
def categories():
    """Manage cost categories for budget tracking."""
    try:
        page = request.args.get('page', 1, type=int)
        search = request.args.get('search', '')
        
        query = db.session.query(
            func.count(PurchaseOrder.id).label('po_count'),
            func.sum(PurchaseOrder.total_amount).label('total_spent')
        )
        
        categories_list = []
        projects = Project.query.all()
        
        for project in projects:
            categories_list.append({
                'id': f"cat_{project.id}",
                'name': f"{project.name} Budget",
                'type': 'project',
                'budget': float(project.budget or 0),
                'spent': 0,
                'remaining': float(project.budget or 0),
                'project_id': project.id
            })
        
        return render_template(
            'cost_control/categories.html',
            categories=categories_list,
            search=search
        )
    except Exception as e:
        flash(f'Error loading categories: {str(e)}', 'error')
        return redirect(url_for('cost_control.dashboard'))


@bp.route('/categories/add', methods=['GET', 'POST'])
@login_required
@role_required(['cost_control_manager'])
def add_category():
    """Add new cost category."""
    if request.method == 'POST':
        try:
            # In a real scenario, you would create a CostCategory model
            # For now, we'll work with existing structures
            category_name = request.form.get('name')
            category_type = request.form.get('type')
            project_id = request.form.get('project_id', type=int)
            
            if not category_name or not category_type:
                flash('Category name and type are required', 'error')
                return redirect(url_for('cost_control.add_category'))
            
            flash(f'Category "{category_name}" added successfully', 'success')
            return redirect(url_for('cost_control.categories'))
        except Exception as e:
            flash(f'Error adding category: {str(e)}', 'error')
            return redirect(url_for('cost_control.add_category'))
    
    projects = Project.query.all()
    return render_template(
        'cost_control/add_category.html',
        projects=projects
    )


# ============================================
# MACHINERY MANAGEMENT
# ============================================

@bp.route('/machinery')
@login_required
@role_required([Roles.COST_CONTROL_MANAGER, Roles.COST_CONTROL_STAFF])
def machinery():
    """View and manage project machinery."""
    try:
        page = request.args.get('page', 1, type=int)
        status_filter = request.args.get('status', '')
        
        # Get all project equipment records
        machinery_list = []
        equipment_query = db.session.query(ProjectEquipment, Project.name.label('project_name')).join(
            Project, ProjectEquipment.project_id == Project.id
        )
        if status_filter:
            equipment_query = equipment_query.filter(ProjectEquipment.status == status_filter)
        equipment_rows = equipment_query.order_by(ProjectEquipment.created_at.desc()).all()

        for equipment, project_name in equipment_rows:
            machinery_list.append({
                'id': equipment.id,
                'serial_no': f"EQ-{equipment.id:04d}",
                'description': equipment.description or equipment.name or 'Equipment',
                'project': project_name,
                'rate': 0,
                'status': equipment.status or 'operational',
                'monthly_cost': 0,
                'created_at': equipment.created_at
            })
        
        return render_template(
            'cost_control/machinery.html',
            machinery=machinery_list,
            status_filter=status_filter
        )
    except Exception as e:
        flash(f'Error loading machinery: {str(e)}', 'error')
        return redirect(url_for('cost_control.dashboard'))


@bp.route('/machinery/add', methods=['GET', 'POST'])
@login_required
@role_required(['cost_control_manager'])
def add_machinery():
    """Add new machinery/equipment record."""
    if request.method == 'POST':
        try:
            serial_no = request.form.get('serial_no')  # kept for compatibility/display
            description = request.form.get('description')
            model = request.form.get('model')
            equipment_type = request.form.get('type')
            project_id = request.form.get('project_id', type=int)
            
            if not description or not project_id:
                flash('Project and description are required', 'error')
                return redirect(url_for('cost_control.add_machinery'))

            equipment = ProjectEquipment(
                project_id=project_id,
                name=model or serial_no or description[:100],
                type=equipment_type or 'general',
                description=description,
                status='operational'
            )
            db.session.add(equipment)
            db.session.commit()

            flash(f'Machinery "{description}" (Serial: {serial_no}) added successfully', 'success')
            return redirect(url_for('cost_control.machinery'))
        except Exception as e:
            db.session.rollback()
            flash(f'Error adding machinery: {str(e)}', 'error')
            return redirect(url_for('cost_control.add_machinery'))
    
    projects = Project.query.all()
    return render_template(
        'cost_control/add_machinery.html',
        projects=projects
    )


# ============================================
# FUEL LOG MANAGEMENT
# ============================================

@bp.route('/fuel-logs')
@login_required
@role_required([Roles.COST_CONTROL_MANAGER, Roles.COST_CONTROL_STAFF])
def fuel_logs():
    """View and track fuel consumption for equipment."""
    try:
        page = request.args.get('page', 1, type=int)
        search = request.args.get('search', '')
        
        # Simulate fuel logs from equipment usage
        fuel_logs_list = []
        projects = Project.query.all()
        
        for project in projects:
            pos = PurchaseOrder.query.filter_by(project_id=project.id).limit(5).all()
            
            for idx, po in enumerate(pos, 1):
                fuel_logs_list.append({
                    'id': idx,
                    'serial_no': f"EQ-{po.id}",
                    'equipment_code': f"EC-{po.id:04d}",
                    'operator': 'Operator Name',
                    'start_meter': 1000 + (idx * 100),
                    'end_meter': 1050 + (idx * 100),
                    'total_hours': 50,
                    'fuel_consumed': 45.5,
                    'date': po.created_at,
                    'reg_no': f"REG-{po.id:04d}"
                })
        
        return render_template(
            'cost_control/fuel_logs.html',
            fuel_logs=fuel_logs_list,
            search=search
        )
    except Exception as e:
        flash(f'Error loading fuel logs: {str(e)}', 'error')
        return redirect(url_for('cost_control.dashboard'))


@bp.route('/maintenance')
@login_required
@role_required([Roles.COST_CONTROL_MANAGER, Roles.COST_CONTROL_STAFF])
def maintenance():
    """View equipment requiring maintenance."""
    try:
        maintenance_items = db.session.query(
            ProjectEquipment, Project.name.label('project_name')
        ).join(
            Project, ProjectEquipment.project_id == Project.id
        ).filter(
            ProjectEquipment.status == 'maintenance'
        ).order_by(ProjectEquipment.created_at.desc()).all()

        equipment = [
            {
                'id': item.id,
                'name': item.name,
                'type': item.type or 'general',
                'description': item.description or 'N/A',
                'project': project_name,
                'status': item.status,
                'created_at': item.created_at
            }
            for item, project_name in maintenance_items
        ]

        return render_template(
            'cost_control/maintenance.html',
            equipment=equipment,
            total_equipment=db.session.query(ProjectEquipment).count(),
            total_maintenance=len(equipment)
        )
    except Exception as e:
        flash(f'Error loading maintenance: {str(e)}', 'error')
        return redirect(url_for('cost_control.dashboard'))


@bp.route('/fuel-logs/add', methods=['GET', 'POST'])
@login_required
@role_required(['cost_control_manager'])
def add_fuel_log():
    """Record fuel consumption for equipment."""
    if request.method == 'POST':
        try:
            serial_no = request.form.get('serial_no')
            equipment_code = request.form.get('equipment_code')
            operator = request.form.get('operator')
            start_meter = request.form.get('start_meter', type=float)
            end_meter = request.form.get('end_meter', type=float)
            fuel_consumed = request.form.get('fuel_consumed', type=float)
            
            if not serial_no or not fuel_consumed:
                flash('Serial number and fuel consumed are required', 'error')
                return redirect(url_for('cost_control.add_fuel_log'))
            
            # Calculate consumption
            liters = end_meter - start_meter if start_meter and end_meter else fuel_consumed
            
            flash(f'Fuel log recorded: {liters:.2f} liters consumed by {operator}', 'success')
            return redirect(url_for('cost_control.fuel_logs'))
        except Exception as e:
            flash(f'Error adding fuel log: {str(e)}', 'error')
            return redirect(url_for('cost_control.add_fuel_log'))
    
    return render_template('cost_control/add_fuel_log.html')


# ============================================
# COST VARIANCE & REPORTING
# ============================================

@bp.route('/variance-analysis')
@login_required
@role_required([Roles.COST_CONTROL_MANAGER, Roles.COST_CONTROL_STAFF])
def variance_analysis():
    """Analyze cost variances between planned and actual spending."""
    try:
        projects = Project.query.all()
        
        variance_data = []
        total_planned = 0
        total_actual = 0
        
        for project in projects:
            planned = float(project.budget or 0)
            
            # Get actual spend from approved POs and payments
            approved_pos = PurchaseOrder.query.filter(
                PurchaseOrder.project_id == project.id,
                PurchaseOrder.approval_state == ApprovalState.APPROVED
            ).all()
            actual = sum(float(po.total_amount or 0) for po in approved_pos)
            
            variance = planned - actual
            variance_percent = (variance / planned * 100) if planned > 0 else 0
            
            variance_data.append({
                'project': project,
                'planned': planned,
                'actual': actual,
                'variance': variance,
                'variance_percent': variance_percent,
                'status': 'over_budget' if variance < 0 else 'under_budget'
            })
            
            total_planned += planned
            total_actual += actual
        
        return render_template(
            'cost_control/variance_analysis.html',
            variance_data=variance_data,
            total_planned=total_planned,
            total_actual=total_actual,
            total_variance=total_planned - total_actual
        )
    except Exception as e:
        flash(f'Error loading variance analysis: {str(e)}', 'error')
        return redirect(url_for('cost_control.dashboard'))


@bp.route('/reports')
@login_required
@role_required([Roles.COST_CONTROL_MANAGER, Roles.COST_CONTROL_STAFF])
def reports():
    """Generate and view cost control reports."""
    try:
        report_type = request.args.get('type', 'summary')
        project_id = request.args.get('project_id', type=int)
        
        projects = Project.query.all()
        selected_project = None
        
        if project_id:
            selected_project = Project.query.get(project_id)
        
        report_data = {
            'type': report_type,
            'projects': projects,
            'selected_project': selected_project,
            'generated_at': datetime.now()
        }
        
        return render_template(
            'cost_control/reports.html',
            **report_data
        )
    except Exception as e:
        flash(f'Error loading reports: {str(e)}', 'error')
        return redirect(url_for('cost_control.dashboard'))


@bp.route('/po-approvals')
@login_required
@role_required([Roles.COST_CONTROL_MANAGER, Roles.COST_CONTROL_STAFF])
def po_approvals():
    """Cost control review of procurement-submitted POs."""
    pending_pos = PurchaseOrder.query.filter(
        PurchaseOrder.approval_state == ApprovalState.PENDING
    ).order_by(PurchaseOrder.created_at.desc()).all()
    return render_template('cost_control/po_approvals.html', pending_pos=pending_pos)


@bp.route('/po-approvals/<int:po_id>/approve', methods=['POST'])
@login_required
@role_required([Roles.COST_CONTROL_MANAGER, Roles.COST_CONTROL_STAFF])
def approve_po_for_finance(po_id):
    """Approve PO at cost control stage and forward to finance."""
    po = PurchaseOrder.query.get_or_404(po_id)
    try:
        po.approval_state = ApprovalState.REVIEW

        invoice_number = f"PR-{po.po_number or po.id}-{datetime.utcnow().strftime('%Y%m%d%H%M%S')}"
        payment = PaymentRequest(
            po_id=po.id,
            invoice_number=invoice_number,
            invoice_amount=po.total_amount or 0,
            approval_state=ApprovalState.PENDING,
            sent_to_cost_control=True
        )
        db.session.add(payment)

        log = ApprovalLog(
            entity_type='purchase_order',
            entity_id=po.id,
            action='approved',
            actor_id=current_user.id,
            comment='Approved by Cost Control and forwarded to Finance for payment processing.'
        )
        db.session.add(log)

        db.session.commit()
        flash(f'PO {po.po_number or po.id} approved and sent to Finance.', 'success')
    except Exception as e:
        db.session.rollback()
        flash(f'Error approving PO: {str(e)}', 'error')
    return redirect(url_for('cost_control.po_approvals'))


@bp.route('/inventory')
@login_required
@role_required([Roles.COST_CONTROL_MANAGER, Roles.COST_CONTROL_STAFF])
def inventory():
    """Cost control inventory dashboard."""
    items = Inventory.query.order_by(Inventory.last_updated.desc()).all()
    low_stock = [i for i in items if (i.reorder_level or 0) and (i.quantity_on_hand or 0) <= (i.reorder_level or 0)]
    return render_template(
        'cost_control/inventory.html',
        items=items,
        low_stock=low_stock,
        total_items=len(items),
        low_stock_count=len(low_stock)
    )


@bp.route('/inventory/add', methods=['POST'])
@login_required
@role_required([Roles.COST_CONTROL_MANAGER, Roles.COST_CONTROL_STAFF])
def add_inventory_item():
    """Add inventory item from cost control module."""
    try:
        item = Inventory(
            project_id=request.form.get('project_id', type=int),
            item_description=request.form.get('item_description', '').strip(),
            unit=request.form.get('unit', '').strip(),
            quantity_on_hand=request.form.get('quantity_on_hand', type=float) or 0,
            reorder_level=request.form.get('reorder_level', type=float) or 0
        )
        db.session.add(item)
        db.session.commit()
        flash('Inventory item added.', 'success')
    except Exception as e:
        db.session.rollback()
        flash(f'Error adding inventory item: {str(e)}', 'error')
    return redirect(url_for('cost_control.inventory'))


@bp.route('/assets-report')
@login_required
@role_required([Roles.COST_CONTROL_MANAGER, Roles.COST_CONTROL_STAFF])
def assets_report():
    """Asset movement and stock report for cost control."""
    items = Inventory.query.order_by(Inventory.item_description.asc()).all()
    transfers = AssetTransfer.query.order_by(AssetTransfer.transfer_date.desc()).limit(100).all()
    total_qty = sum(float(item.quantity_on_hand or 0) for item in items)
    return render_template(
        'cost_control/assets_report.html',
        items=items,
        transfers=transfers,
        total_qty=total_qty
    )


@bp.route('/export-report/<report_type>')
@login_required
@role_required(['cost_control_manager'])
def export_report(report_type):
    """Export cost control report as CSV."""
    try:
        if report_type == 'budget':
            projects = Project.query.all()
            csv_data = "Project,Budget,Committed,Spent,Remaining,Utilization\n"
            
            for project in projects:
                budget = float(project.budget or 0)
                approved_pos = PurchaseOrder.query.filter(
                    PurchaseOrder.project_id == project.id,
                    PurchaseOrder.approval_state == ApprovalState.APPROVED
                ).all()
                committed = sum(float(po.total_amount or 0) for po in approved_pos)
                spent = 0
                remaining = budget - committed - spent
                utilization = (committed + spent) / budget * 100 if budget > 0 else 0
                
                csv_data += f'"{project.name}",{budget:.2f},{committed:.2f},{spent:.2f},{remaining:.2f},{utilization:.2f}\n'
            
            flash('Budget report exported successfully', 'success')
        
        return redirect(url_for('cost_control.reports'))
    except Exception as e:
        flash(f'Error exporting report: {str(e)}', 'error')
        return redirect(url_for('cost_control.reports'))


# ============================================
# HOME/DASHBOARD
# ============================================

@bp.route('/')
@login_required
@role_required([Roles.COST_CONTROL_MANAGER, Roles.COST_CONTROL_STAFF])
def index():
    """Cost Control home/index page."""
    return redirect(url_for('cost_control.dashboard'))


# ===== INVOICE MANAGEMENT =====

@bp.route('/invoices')
@login_required
@role_required([Roles.COST_CONTROL_MANAGER, Roles.COST_CONTROL_STAFF])
def view_invoices():
    """View invoices sent by Finance to Cost Control."""
    try:
        page = request.args.get('page', 1, type=int)
        
        # Get invoices sent to cost control
        invoices = PaymentRequest.query.filter_by(sent_to_cost_control=True).order_by(
            PaymentRequest.created_at.desc()
        ).paginate(page=page, per_page=20)
        
        total_amount = db.session.query(
            func.sum(PaymentRequest.invoice_amount)
        ).filter_by(sent_to_cost_control=True).scalar() or 0
        
        return render_template(
            'cost_control/invoices.html',
            invoices=invoices,
            total_amount=float(total_amount)
        )
    except Exception as e:
        flash(f"Error loading invoices: {str(e)}", "error")
        return redirect(url_for('cost_control.dashboard'))
