"""
Generic API endpoints for approval/rejection across all entity types.
Used by dashboard and frontend modals.
"""

from flask import Blueprint, request, jsonify
from flask_login import current_user, login_required
from app.models import (
    db, PurchaseOrder, MaterialRequest, BOQItem, IPC, QCInspection,
    ChangeOrder, EquipmentRequest, PaymentRequest, ApprovalState
)
from app.approvals import (
    do_approve, do_reject, do_return_to_draft, 
    InvalidTransition, UnauthorizedApproval, get_approval_history
)

bp = Blueprint('api_v1', __name__, url_prefix='/api')


# Entity type to model mapping
ENTITY_MODELS = {
    'purchase_order': PurchaseOrder,
    'po': PurchaseOrder,
    'material_request': MaterialRequest,
    'pr': MaterialRequest,
    'boq_item': BOQItem,
    'boq': BOQItem,
    'ipc': IPC,
    'qc_inspection': QCInspection,
    'qc': QCInspection,
    'change_order': ChangeOrder,
    'equipment_request': EquipmentRequest,
    'payment_request': PaymentRequest,
}


@bp.route('/approve', methods=['POST'])
@login_required
def api_approve():
    """Generic approve endpoint."""
    data = request.get_json() or request.form
    entity_type = data.get('entity_type', '').strip()
    entity_id = data.get('entity_id')
    comment = data.get('comment', '').strip()
    escalate = data.get('escalate', False)
    
    if not entity_type or not entity_id:
        return jsonify({'error': 'entity_type and entity_id required'}), 400
    
    model = ENTITY_MODELS.get(entity_type)
    if not model:
        return jsonify({'error': f'Unknown entity type: {entity_type}'}), 400
    
    entity = model.query.get(entity_id)
    if not entity:
        return jsonify({'error': f'{entity_type} {entity_id} not found'}), 404
    
    try:
        result = do_approve(entity, entity_type, current_user, comment, escalate)
        return jsonify(result), 200
    except UnauthorizedApproval as e:
        return jsonify({'error': str(e)}), 403
    except InvalidTransition as e:
        return jsonify({'error': str(e)}), 400
    except Exception as e:
        db.session.rollback()
        return jsonify({'error': f'Approval failed: {str(e)}'}), 500


@bp.route('/reject', methods=['POST'])
@login_required
def api_reject():
    """Generic reject endpoint."""
    data = request.get_json() or request.form
    entity_type = data.get('entity_type', '').strip()
    entity_id = data.get('entity_id')
    comment = data.get('comment', 'Rejected').strip()
    
    if not entity_type or not entity_id:
        return jsonify({'error': 'entity_type and entity_id required'}), 400
    
    model = ENTITY_MODELS.get(entity_type)
    if not model:
        return jsonify({'error': f'Unknown entity type: {entity_type}'}), 400
    
    entity = model.query.get(entity_id)
    if not entity:
        return jsonify({'error': f'{entity_type} {entity_id} not found'}), 404
    
    try:
        result = do_reject(entity, entity_type, current_user, comment)
        return jsonify(result), 200
    except UnauthorizedApproval as e:
        return jsonify({'error': str(e)}), 403
    except InvalidTransition as e:
        return jsonify({'error': str(e)}), 400
    except Exception as e:
        db.session.rollback()
        return jsonify({'error': f'Rejection failed: {str(e)}'}), 500


@bp.route('/approval-history/<entity_type>/<int:entity_id>')
@login_required
def api_approval_history(entity_type, entity_id):
    """Get approval audit trail for an entity."""
    logs = get_approval_history(entity_type, entity_id)
    
    return jsonify({
        'entity_type': entity_type,
        'entity_id': entity_id,
        'history': [
            {
                'id': log.id,
                'action': log.action,
                'actor': log.actor.name,
                'comment': log.comment,
                'timestamp': log.timestamp.isoformat()
            }
            for log in logs
        ]
    })


@bp.route('/pending-approvals')
@login_required
def api_pending_approvals():
    """Get all pending approvals for current user based on role."""
    pending = {
        'material_requests': [],
        'purchase_orders': [],
        'qc_inspections': [],
        'payments': []
    }
    
    role = current_user.role
    
    if role in ['project_manager', 'admin']:
        mrs = MaterialRequest.query.filter_by(approval_state=ApprovalState.PENDING).all()
        pending['material_requests'] = [
            {'id': mr.id, 'project': mr.project.name if mr.project else 'N/A'}
            for mr in mrs
        ]
    
    if role in ['procurement_manager', 'executive', 'admin']:
        pos = PurchaseOrder.query.filter(
            PurchaseOrder.approval_state.in_([ApprovalState.PENDING, ApprovalState.REVIEW])
        ).all()
        pending['purchase_orders'] = [
            {'id': po.id, 'amount': float(po.total_amount or 0)}
            for po in pos
        ]
    
    if role in ['qc_manager', 'admin']:
        qcs = QCInspection.query.filter_by(approval_state=ApprovalState.PENDING).all()
        pending['qc_inspections'] = [
            {'id': qc.id}
            for qc in qcs
        ]
    
    if role in ['finance_manager', 'executive', 'admin']:
        payments = PaymentRequest.query.filter(
            PaymentRequest.approval_state.in_([ApprovalState.DRAFT, ApprovalState.PENDING])
        ).all()
        pending['payments'] = [
            {'id': p.id, 'invoice': p.invoice_number, 'amount': float(p.invoice_amount or 0)}
            for p in payments
        ]
    
    return jsonify(pending)
