"""
Approval state machine and helper functions for workflow management.
Handles transitions, validation, and permission checks.
"""

from datetime import datetime
from app.models import (
    db, ApprovalState, ApprovalLog, User, PurchaseOrder, MaterialRequest,
    BOQItem, IPC, QCInspection, ChangeOrder, EquipmentRequest, PaymentRequest
)


# Define allowed state transitions
ALLOWED_TRANSITIONS = {
    ApprovalState.DRAFT: [ApprovalState.PENDING, ApprovalState.CANCELLED],
    ApprovalState.PENDING: [ApprovalState.REVIEW, ApprovalState.REJECTED, ApprovalState.CANCELLED],
    ApprovalState.REVIEW: [ApprovalState.APPROVED, ApprovalState.REJECTED, ApprovalState.ESCALATED],
    ApprovalState.ESCALATED: [ApprovalState.REVIEW, ApprovalState.APPROVED, ApprovalState.REJECTED],
    ApprovalState.APPROVED: [ApprovalState.CANCELLED],
    ApprovalState.REJECTED: [ApprovalState.PENDING, ApprovalState.CANCELLED],
}


# Threshold amounts (in currency units) that trigger escalation
THRESHOLDS = {
    'executive_po_approval': 500000,
    'executive_payment_approval': 1000000,
    'executive_change_order': 250000,
    'budget_warning': 0.95,  # 95% of budget
}


# Role hierarchy for approval routing
ROLE_APPROVAL_MATRIX = {
    'purchase_order': {
        'draft_creator': ['procurement_staff'],
        'draft_reviewer': ['procurement_manager'],
        'executive_approver': ['executive'],
        'can_escalate': ['procurement_manager'],
    },
    'material_request': {
        'submitter': ['project_staff', 'project_manager'],
        'project_approver': ['project_manager'],
        'cost_control_approver': ['cost_control_manager'],
        'procurement_handler': ['procurement_manager'],
    },
    'boq': {
        'creator': ['qs_staff'],
        'cost_control_approver': ['cost_control_manager'],
        'qs_approver': ['qs_manager'],
        'publisher': ['admin', 'project_manager'],
    },
    'qc_inspection': {
        'inspector': ['qc_staff'],
        'approver': ['qc_manager'],
    },
    'ipc': {
        'preparer': ['qs_staff'],
        'qs_certifier': ['qs_manager'],
        'cost_control_validator': ['cost_control_manager'],
        'finance_validator': ['finance_manager'],
        'executive_approver': ['executive'],
    },
}


class InvalidTransition(Exception):
    """Raised when an invalid state transition is attempted."""
    pass


class UnauthorizedApproval(Exception):
    """Raised when user lacks permission to approve."""
    pass


def can_transition(current_state, target_state):
    """
    Check if a transition is allowed.
    
    Args:
        current_state: Current ApprovalState
        target_state: Target ApprovalState
        
    Returns:
        bool: True if transition is allowed
    """
    return target_state in ALLOWED_TRANSITIONS.get(current_state, [])


def get_next_approvers(entity, entity_type):
    """
    Determine who should approve next based on entity type and current state.
    
    Args:
        entity: The entity object being approved
        entity_type: String like 'purchase_order', 'material_request', etc.
        
    Returns:
        list: List of required approver role strings
    """
    matrix = ROLE_APPROVAL_MATRIX.get(entity_type, {})
    
    if entity_type == 'purchase_order':
        if entity.approval_state == ApprovalState.DRAFT:
            return matrix.get('draft_reviewer', [])
        elif entity.approval_state == ApprovalState.REVIEW:
            if entity.requires_executive_approval:
                return matrix.get('executive_approver', [])
            return []  # Ready to issue
    
    elif entity_type == 'material_request':
        if entity.approval_state == ApprovalState.DRAFT:
            return matrix.get('project_approver', [])
        elif entity.approval_state == ApprovalState.REVIEW:
            return matrix.get('cost_control_approver', [])
    
    elif entity_type == 'boq':
        if entity.approval_state == ApprovalState.DRAFT:
            return matrix.get('cost_control_approver', [])
        elif entity.approval_state == ApprovalState.REVIEW:
            return matrix.get('qs_approver', [])
    
    elif entity_type == 'ipc':
        if entity.approval_state == ApprovalState.DRAFT:
            return matrix.get('qs_certifier', [])
        elif entity.approval_state == ApprovalState.REVIEW:
            return matrix.get('cost_control_validator', [])
    
    return []


def should_escalate(entity, entity_type):
    """
    Determine if approval should be escalated based on value thresholds.
    
    Args:
        entity: The entity object
        entity_type: String like 'purchase_order', 'ipc', etc.
        
    Returns:
        bool: True if escalation required
    """
    if entity_type == 'purchase_order':
        return entity.total_amount > THRESHOLDS['executive_po_approval']
    
    elif entity_type == 'ipc':
        return entity.total_amount > THRESHOLDS['executive_change_order']
    
    elif entity_type == 'change_order':
        return entity.cost_impact > THRESHOLDS['executive_change_order']
    
    elif entity_type == 'payment_request':
        return entity.invoice_amount > THRESHOLDS['executive_payment_approval']
    
    return False


def can_user_approve(user, entity, entity_type):
    """
    Check if a user has permission to approve a specific entity.
    
    Args:
        user: User object
        entity: The entity to be approved
        entity_type: String type of entity
        
    Returns:
        bool: True if user can approve
        
    Raises:
        UnauthorizedApproval: If user is not permitted
    """
    if user.role == 'admin':
        return True  # Admin can approve anything
    
    next_approvers = get_next_approvers(entity, entity_type)
    
    if user.role not in next_approvers:
        raise UnauthorizedApproval(
            f"User role '{user.role}' cannot approve {entity_type} in state '{entity.approval_state}'"
        )
    
    # Check project-level permissions if applicable
    if hasattr(entity, 'project_id'):
        if entity.project_id and user not in entity.project.team_members and user.role != 'admin':
            raise UnauthorizedApproval(
                f"User is not assigned to project {entity.project_id}"
            )
    
    return True


def do_approve(entity, entity_type, actor, comment='', escalate=False):
    """
    Execute approval action on entity.
    
    Args:
        entity: The entity to approve
        entity_type: String type (e.g., 'purchase_order')
        actor: User performing approval
        comment: Optional approval comment
        escalate: If True, escalate to higher authority instead of approving
        
    Returns:
        dict: {'success': bool, 'message': str, 'entity': entity}
        
    Raises:
        InvalidTransition: If transition not allowed
        UnauthorizedApproval: If user lacks permission
    """
    # Verify user can approve
    can_user_approve(actor, entity, entity_type)
    
    # Determine target state
    if escalate:
        target_state = ApprovalState.ESCALATED
    else:
        target_state = ApprovalState.APPROVED
    
    # Validate transition
    if not can_transition(entity.approval_state, target_state):
        raise InvalidTransition(
            f"Cannot transition from {entity.approval_state} to {target_state}"
        )
    
    # Update entity state
    entity.approval_state = target_state
    
    # Check if escalation is required due to threshold
    if not escalate and should_escalate(entity, entity_type):
        entity.approval_state = ApprovalState.ESCALATED
        escalated_msg = " (Escalated due to value threshold)"
    else:
        escalated_msg = ""
    
    # Create approval log entry
    approval_log = ApprovalLog(
        entity_type=entity_type,
        entity_id=entity.id,
        action='escalated' if entity.approval_state == ApprovalState.ESCALATED else 'approved',
        actor_id=actor.id,
        comment=comment,
        timestamp=datetime.utcnow()
    )
    
    db.session.add(approval_log)
    db.session.commit()
    
    return {
        'success': True,
        'message': f"{entity_type.replace('_', ' ').title()} approved{escalated_msg}",
        'entity': entity
    }


def do_reject(entity, entity_type, actor, comment=''):
    """
    Execute rejection action on entity.
    
    Args:
        entity: The entity to reject
        entity_type: String type
        actor: User performing rejection
        comment: Rejection reason (recommended)
        
    Returns:
        dict: {'success': bool, 'message': str, 'entity': entity}
        
    Raises:
        InvalidTransition: If transition not allowed
    """
    target_state = ApprovalState.REJECTED
    
    if not can_transition(entity.approval_state, target_state):
        raise InvalidTransition(
            f"Cannot transition from {entity.approval_state} to {target_state}"
        )
    
    entity.approval_state = target_state
    
    approval_log = ApprovalLog(
        entity_type=entity_type,
        entity_id=entity.id,
        action='rejected',
        actor_id=actor.id,
        comment=comment,
        timestamp=datetime.utcnow()
    )
    
    db.session.add(approval_log)
    db.session.commit()
    
    return {
        'success': True,
        'message': f"{entity_type.replace('_', ' ').title()} rejected",
        'entity': entity
    }


def do_return_to_draft(entity, entity_type, actor, comment=''):
    """
    Return entity to draft for revision.
    
    Args:
        entity: The entity to return
        entity_type: String type
        actor: User performing action
        comment: Reason for return
        
    Returns:
        dict: {'success': bool, 'message': str}
    """
    if entity.approval_state != ApprovalState.REVIEW:
        raise InvalidTransition(
            f"Can only return to draft from REVIEW state, current: {entity.approval_state}"
        )
    
    entity.approval_state = ApprovalState.DRAFT
    
    approval_log = ApprovalLog(
        entity_type=entity_type,
        entity_id=entity.id,
        action='returned',
        actor_id=actor.id,
        comment=comment or "Returned for revisions",
        timestamp=datetime.utcnow()
    )
    
    db.session.add(approval_log)
    db.session.commit()
    
    return {
        'success': True,
        'message': f"{entity_type.replace('_', ' ').title()} returned to draft"
    }


def get_approval_history(entity_type, entity_id):
    """
    Retrieve full approval audit trail for an entity.
    
    Args:
        entity_type: String type
        entity_id: Integer ID
        
    Returns:
        list: ApprovalLog records ordered by timestamp
    """
    logs = ApprovalLog.query.filter_by(
        entity_type=entity_type,
        entity_id=entity_id
    ).order_by(ApprovalLog.timestamp.asc()).all()
    
    return logs


def is_budget_exceeded(project, percentage=THRESHOLDS['budget_warning']):
    """
    Check if project committed spend exceeds budget threshold.
    
    Args:
        project: Project object
        percentage: Warning threshold (default 95%)
        
    Returns:
        dict: {'exceeded': bool, 'committed': float, 'budget': float, 'percentage': float}
    """
    from decimal import Decimal
    
    # Get all approved POs
    approved_pos = PurchaseOrder.query.filter_by(
        project_id=project.id,
        approval_state=ApprovalState.APPROVED
    ).all()
    
    committed = sum(po.total_amount or 0 for po in approved_pos)
    budget = float(project.budget or 0)
    
    if budget == 0:
        return {'exceeded': False, 'committed': float(committed), 'budget': budget, 'percentage': 0}
    
    spend_percentage = float(committed) / budget
    
    return {
        'exceeded': spend_percentage >= percentage,
        'committed': float(committed),
        'budget': budget,
        'percentage': spend_percentage
    }


def check_payment_gates(po):
    """
    Check if PO can be paid based on QC approval.
    
    Args:
        po: PurchaseOrder object
        
    Returns:
        dict: {'can_pay': bool, 'reason': str}
    """
    # Get deliveries for this PO
    deliveries = po.deliveries
    
    if not deliveries:
        return {'can_pay': False, 'reason': 'No deliveries recorded'}
    
    # Check if all deliveries have QC approval
    for delivery in deliveries:
        has_qc_approval = any(
            qc.approval_state == ApprovalState.APPROVED
            for qc in delivery.qc_inspections
        )
        if not has_qc_approval:
            return {'can_pay': False, 'reason': f'Delivery {delivery.grn_number} pending QC approval'}
    
    return {'can_pay': True, 'reason': 'All deliveries QC approved'}


def calculate_ipc_payment(ipc):
    """
    Calculate payment amount with retention logic.
    
    Args:
        ipc: IPC object
        
    Returns:
        dict: {'total': float, 'retention': float, 'payment': float}
    """
    ipc.calculate_retention()
    
    return {
        'total': float(ipc.total_amount or 0),
        'retention': float(ipc.retention_amount or 0),
        'payment': float(ipc.payment_amount or 0)
    }
