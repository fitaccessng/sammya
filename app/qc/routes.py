"""
Quality Control routes:
- Delivery inspection and GRN creation
- QC approval/rejection with gating on payment
"""

from flask import Blueprint, render_template, request, redirect, url_for, flash, jsonify
from flask_login import current_user
from datetime import datetime
from app.models import (
    db, PurchaseOrder, Delivery, QCInspection, ApprovalState
)
from app.auth.decorators import login_required, role_required
from app.approvals import do_approve, do_reject, get_approval_history

bp = Blueprint('qc', __name__, url_prefix='/qc')


@bp.route('/deliveries')
@login_required
@role_required(['qc_staff', 'qc_manager'])
def list_deliveries():
    """List all deliveries pending or requiring inspection."""
    deliveries = Delivery.query.order_by(Delivery.received_at.desc()).all()
    return render_template('qc/deliveries.html', deliveries=deliveries)


@bp.route('/delivery/create/<int:po_id>', methods=['GET', 'POST'])
@login_required
@role_required(['store_manager', 'qc_staff'])
def create_delivery(po_id):
    """Create GRN (Goods Received Note) for delivered PO."""
    po = PurchaseOrder.query.get_or_404(po_id)
    
    if request.method == 'POST':
        from app.models import DeliveryItem
        
        items_data = request.form.getlist('item_description')
        quantities = request.form.getlist('quantity_received')
        
        # Create delivery
        delivery = Delivery(
            po_id=po_id,
            received_by=current_user.id,
            approval_state=ApprovalState.PENDING
        )
        
        total_qty = 0
        
        # Add delivery items
        for i, desc in enumerate(items_data):
            if desc and quantities[i]:
                item = DeliveryItem(
                    description=desc,
                    quantity_received=quantities[i]
                )
                delivery.items.append(item)
                total_qty += float(quantities[i])
        
        delivery.total_quantity_received = total_qty
        delivery.grn_number = f"GRN-{po_id}-{datetime.utcnow().strftime('%Y%m%d%H%M%S')}"
        
        db.session.add(delivery)
        db.session.commit()
        
        flash(f'GRN {delivery.grn_number} created. Awaiting QC inspection.', 'success')
        return redirect(url_for('qc.view_delivery', delivery_id=delivery.id))
    
    return render_template('qc/create_delivery.html', po=po)


@bp.route('/delivery/<int:delivery_id>')
@login_required
def view_delivery(delivery_id):
    """View delivery details."""
    delivery = Delivery.query.get_or_404(delivery_id)
    history = get_approval_history('delivery', delivery.id)
    
    return render_template('qc/view_delivery.html', delivery=delivery, history=history)


@bp.route('/inspection/create/<int:delivery_id>', methods=['GET', 'POST'])
@login_required
@role_required(['qc_staff'])
def create_inspection(delivery_id):
    """Create QC inspection for delivery."""
    delivery = Delivery.query.get_or_404(delivery_id)
    
    if request.method == 'POST':
        approved_qty = request.form.get('approved_quantity')
        rejected_qty = request.form.get('rejected_quantity')
        rejection_reason = request.form.get('rejection_reason', '')
        
        inspection = QCInspection(
            delivery_id=delivery_id,
            inspected_by=current_user.id,
            approved_quantity=approved_qty,
            rejected_quantity=rejected_qty,
            rejection_reason=rejection_reason if rejected_qty and float(rejected_qty) > 0 else '',
            approval_state=ApprovalState.PENDING
        )
        
        db.session.add(inspection)
        db.session.commit()
        
        flash(f'QC Inspection {inspection.id} created. Awaiting manager approval.', 'success')
        return redirect(url_for('qc.view_inspection', inspection_id=inspection.id))
    
    return render_template('qc/create_inspection.html', delivery=delivery)


@bp.route('/inspection/<int:inspection_id>')
@login_required
def view_inspection(inspection_id):
    """View QC inspection details."""
    inspection = QCInspection.query.get_or_404(inspection_id)
    history = get_approval_history('qc_inspection', inspection.id)
    
    return render_template('qc/view_inspection.html', inspection=inspection, history=history)


@bp.route('/inspection/<int:inspection_id>/approve', methods=['POST'])
@login_required
@role_required(['qc_manager'])
def approve_inspection(inspection_id):
    """QC Manager approves inspection - gates payment authorization."""
    inspection = QCInspection.query.get_or_404(inspection_id)
    
    if inspection.approval_state != ApprovalState.PENDING:
        flash('Inspection not pending approval.', 'warning')
        return redirect(url_for('qc.view_inspection', inspection_id=inspection_id))
    
    comment = request.form.get('comment', '')
    
    try:
        do_approve(inspection, 'qc_inspection', current_user, comment)
        flash('QC Inspection approved. Delivery now eligible for payment.', 'success')
    except Exception as e:
        flash(f'Error: {str(e)}', 'danger')
    
    return redirect(url_for('qc.view_inspection', inspection_id=inspection_id))


@bp.route('/inspection/<int:inspection_id>/reject', methods=['POST'])
@login_required
@role_required(['qc_manager'])
def reject_inspection(inspection_id):
    """QC Manager rejects inspection - requires re-inspection or return."""
    inspection = QCInspection.query.get_or_404(inspection_id)
    
    if inspection.approval_state != ApprovalState.PENDING:
        flash('Inspection not pending approval.', 'warning')
        return redirect(url_for('qc.view_inspection', inspection_id=inspection_id))
    
    comment = request.form.get('comment', 'Quality issues noted')
    
    try:
        do_reject(inspection, 'qc_inspection', current_user, comment)
        flash('QC Inspection rejected. Notify vendor for corrective action.', 'info')
    except Exception as e:
        flash(f'Error: {str(e)}', 'danger')
    
    return redirect(url_for('qc.view_inspection', inspection_id=inspection_id))


@bp.route('/inspection/<int:inspection_id>/re-inspect', methods=['POST'])
@login_required
@role_required(['qc_staff'])
def re_inspect(inspection_id):
    """Re-inspect after rejection (creates new inspection record)."""
    inspection = QCInspection.query.get_or_404(inspection_id)
    
    if inspection.approval_state != ApprovalState.REJECTED:
        flash('Can only re-inspect rejected deliveries.', 'warning')
        return redirect(url_for('qc.view_inspection', inspection_id=inspection_id))
    
    # Create new inspection for the delivery
    new_inspection = QCInspection(
        delivery_id=inspection.delivery_id,
        inspected_by=current_user.id,
        approved_quantity=request.form.get('approved_quantity'),
        rejected_quantity=request.form.get('rejected_quantity'),
        rejection_reason=request.form.get('rejection_reason', ''),
        approval_state=ApprovalState.PENDING
    )
    
    db.session.add(new_inspection)
    db.session.commit()
    
    flash(f'Re-inspection {new_inspection.id} created.', 'success')
    return redirect(url_for('qc.view_inspection', inspection_id=new_inspection.id))


@bp.route('/api/inspection-status/<int:po_id>')
@login_required
def api_inspection_status(po_id):
    """Get QC approval status for a PO (used by finance to gate payments)."""
    po = PurchaseOrder.query.get_or_404(po_id)
    
    # Check if all deliveries have approved inspections
    can_pay = True
    reason = ''
    
    if not po.deliveries:
        can_pay = False
        reason = 'No deliveries recorded'
    else:
        for delivery in po.deliveries:
            has_approved = any(
                qc.approval_state == ApprovalState.APPROVED
                for qc in delivery.qc_inspections
            )
            if not has_approved:
                can_pay = False
                reason = f'Delivery {delivery.grn_number} pending QC approval'
                break
    
    return jsonify({
        'po_id': po_id,
        'can_pay': can_pay,
        'reason': reason,
        'status': 'approved' if can_pay else 'pending'
    })
