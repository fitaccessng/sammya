"""
QS Project Variations and Change Orders management endpoints
"""
from flask import Blueprint, render_template, request, redirect, url_for, flash, jsonify, current_app
from flask_login import login_required, current_user
from app.models import Project, ChangeOrder, BOQItem, db, ApprovalLog
from app.utils import role_required, Roles
from .utils import check_project_access, get_user_qs_projects
from datetime import datetime

variations_bp = Blueprint('qs_variations', __name__)


@variations_bp.route('/project/<int:project_id>/variations', methods=['GET'])
@login_required
@role_required([Roles.SUPER_HQ, Roles.QS_MANAGER, Roles.QS_STAFF])
def project_variations(project_id):
    """View and manage project variations (change orders)"""
    try:
        project = check_project_access(project_id)
        if not project:
            return redirect(url_for('qs_dashboard.dashboard'))
        
        # Get all change orders for this project
        variations = ChangeOrder.query.filter_by(project_id=project_id).all()
        
        # Calculate statistics
        total_approved = sum(float(v.amount or 0) for v in variations if v.status == 'approved')
        total_pending = sum(float(v.amount or 0) for v in variations if v.status == 'pending')
        total_rejected = sum(float(v.amount or 0) for v in variations if v.status == 'rejected')
        total_variations = total_approved + total_pending + total_rejected
        
        # Group by status
        variations_by_status = {}
        for v in variations:
            status = v.status or 'draft'
            if status not in variations_by_status:
                variations_by_status[status] = []
            variations_by_status[status].append(v)
        
        # Get all assigned projects for sidebar
        projects = get_user_qs_projects()
        
        return render_template('qs/project_variations.html',
            project=project,
            projects=projects,
            variations=variations,
            variations_by_status=variations_by_status,
            total_variations=total_variations,
            total_approved=total_approved,
            total_pending=total_pending,
            total_rejected=total_rejected
        )
    except Exception as e:
        current_app.logger.error(f"Error loading variations for project {project_id}: {str(e)}")
        flash('Error loading variations', 'error')
        return redirect(url_for('qs_dashboard.dashboard'))


@variations_bp.route('/project/<int:project_id>/variation/add', methods=['POST'])
@login_required
@role_required([Roles.SUPER_HQ, Roles.QS_MANAGER, Roles.QS_STAFF])
def add_variation(project_id):
    """Add a new variation/change order"""
    try:
        project = check_project_access(project_id)
        if not project:
            return jsonify({'success': False, 'message': 'Access denied'}), 403
        
        data = request.get_json()
        
        variation = ChangeOrder(
            project_id=project_id,
            description=data.get('description'),
            amount=float(data.get('amount', 0)),
            reason=data.get('reason'),
            status='draft',
            created_by=current_user.id,
            created_at=datetime.utcnow()
        )
        
        db.session.add(variation)
        db.session.commit()
        
        return jsonify({
            'success': True, 
            'message': 'Variation added successfully',
            'variation_id': variation.id
        })
    except Exception as e:
        current_app.logger.error(f"Error adding variation: {str(e)}")
        return jsonify({'success': False, 'message': str(e)}), 400


@variations_bp.route('/variation/<int:variation_id>/edit', methods=['POST'])
@login_required
@role_required([Roles.SUPER_HQ, Roles.QS_MANAGER, Roles.QS_STAFF])
def edit_variation(variation_id):
    """Edit a variation/change order"""
    try:
        variation = ChangeOrder.query.get(variation_id)
        if not variation:
            return jsonify({'success': False, 'message': 'Variation not found'}), 404
        
        # Check project access
        project = check_project_access(variation.project_id)
        if not project:
            return jsonify({'success': False, 'message': 'Access denied'}), 403
        
        # Only allow editing if not approved/rejected
        if variation.status in ['approved', 'rejected']:
            return jsonify({'success': False, 'message': 'Cannot edit approved/rejected variations'}), 400
        
        data = request.get_json()
        
        variation.description = data.get('description', variation.description)
        variation.amount = float(data.get('amount', variation.amount))
        variation.reason = data.get('reason', variation.reason)
        variation.updated_at = datetime.utcnow()
        
        db.session.commit()
        
        return jsonify({'success': True, 'message': 'Variation updated successfully'})
    except Exception as e:
        current_app.logger.error(f"Error editing variation: {str(e)}")
        return jsonify({'success': False, 'message': str(e)}), 400


@variations_bp.route('/variation/<int:variation_id>/submit', methods=['POST'])
@login_required
@role_required([Roles.SUPER_HQ, Roles.QS_MANAGER, Roles.QS_STAFF])
def submit_variation(variation_id):
    """Submit variation for approval"""
    try:
        variation = ChangeOrder.query.get(variation_id)
        if not variation:
            return jsonify({'success': False, 'message': 'Variation not found'}), 404
        
        # Check project access
        project = check_project_access(variation.project_id)
        if not project:
            return jsonify({'success': False, 'message': 'Access denied'}), 403
        
        if variation.status != 'draft':
            return jsonify({'success': False, 'message': 'Can only submit draft variations'}), 400
        
        variation.status = 'pending'
        variation.updated_at = datetime.utcnow()
        
        # Create approval log
        approval_log = ApprovalLog(
            reference_type='change_order',
            reference_id=variation_id,
            action='submitted',
            user_id=current_user.id,
            status='pending',
            created_at=datetime.utcnow()
        )
        db.session.add(approval_log)
        db.session.commit()
        
        return jsonify({'success': True, 'message': 'Variation submitted for approval'})
    except Exception as e:
        current_app.logger.error(f"Error submitting variation: {str(e)}")
        return jsonify({'success': False, 'message': str(e)}), 400


@variations_bp.route('/variation/<int:variation_id>/approve', methods=['POST'])
@login_required
@role_required([Roles.SUPER_HQ, Roles.FINANCE_MANAGER, Roles.PROJECT_MANAGER])
def approve_variation(variation_id):
    """Approve a variation"""
    try:
        variation = ChangeOrder.query.get(variation_id)
        if not variation:
            return jsonify({'success': False, 'message': 'Variation not found'}), 404
        
        # Check project access
        project = check_project_access(variation.project_id)
        if not project:
            return jsonify({'success': False, 'message': 'Access denied'}), 403
        
        if variation.status != 'pending':
            return jsonify({'success': False, 'message': 'Can only approve pending variations'}), 400
        
        variation.status = 'approved'
        variation.approved_by = current_user.id
        variation.approved_at = datetime.utcnow()
        
        # Create approval log
        approval_log = ApprovalLog(
            reference_type='change_order',
            reference_id=variation_id,
            action='approved',
            user_id=current_user.id,
            status='approved',
            created_at=datetime.utcnow()
        )
        db.session.add(approval_log)
        db.session.commit()
        
        return jsonify({'success': True, 'message': 'Variation approved successfully'})
    except Exception as e:
        current_app.logger.error(f"Error approving variation: {str(e)}")
        return jsonify({'success': False, 'message': str(e)}), 400


@variations_bp.route('/variation/<int:variation_id>/reject', methods=['POST'])
@login_required
@role_required([Roles.SUPER_HQ, Roles.FINANCE_MANAGER, Roles.PROJECT_MANAGER])
def reject_variation(variation_id):
    """Reject a variation"""
    try:
        variation = ChangeOrder.query.get(variation_id)
        if not variation:
            return jsonify({'success': False, 'message': 'Variation not found'}), 404
        
        # Check project access
        project = check_project_access(variation.project_id)
        if not project:
            return jsonify({'success': False, 'message': 'Access denied'}), 403
        
        if variation.status != 'pending':
            return jsonify({'success': False, 'message': 'Can only reject pending variations'}), 400
        
        data = request.get_json()
        
        variation.status = 'rejected'
        variation.rejection_reason = data.get('reason', '')
        variation.rejected_by = current_user.id
        variation.rejected_at = datetime.utcnow()
        
        # Create approval log
        approval_log = ApprovalLog(
            reference_type='change_order',
            reference_id=variation_id,
            action='rejected',
            user_id=current_user.id,
            status='rejected',
            notes=data.get('reason', ''),
            created_at=datetime.utcnow()
        )
        db.session.add(approval_log)
        db.session.commit()
        
        return jsonify({'success': True, 'message': 'Variation rejected'})
    except Exception as e:
        current_app.logger.error(f"Error rejecting variation: {str(e)}")
        return jsonify({'success': False, 'message': str(e)}), 400


@variations_bp.route('/variation/<int:variation_id>/delete', methods=['POST'])
@login_required
@role_required([Roles.SUPER_HQ, Roles.QS_MANAGER])
def delete_variation(variation_id):
    """Delete a variation (only if draft)"""
    try:
        variation = ChangeOrder.query.get(variation_id)
        if not variation:
            return jsonify({'success': False, 'message': 'Variation not found'}), 404
        
        # Check project access
        project = check_project_access(variation.project_id)
        if not project:
            return jsonify({'success': False, 'message': 'Access denied'}), 403
        
        if variation.status != 'draft':
            return jsonify({'success': False, 'message': 'Can only delete draft variations'}), 400
        
        project_id = variation.project_id
        db.session.delete(variation)
        db.session.commit()
        
        return jsonify({'success': True, 'message': 'Variation deleted successfully'})
    except Exception as e:
        current_app.logger.error(f"Error deleting variation: {str(e)}")
        return jsonify({'success': False, 'message': str(e)}), 400
