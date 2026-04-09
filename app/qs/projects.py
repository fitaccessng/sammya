"""
QS Project management endpoints
"""
from flask import Blueprint, render_template, redirect, url_for, flash, current_app, jsonify, request
from flask_login import login_required, current_user
from app.models import Project, BOQItem, ProjectStaff, db, ChangeOrder
from app.utils import role_required, Roles
from .utils import check_project_access, get_user_qs_projects
from datetime import datetime
from sqlalchemy import and_

projects_bp = Blueprint('qs_projects', __name__)


@projects_bp.route('/project/<int:project_id>', methods=['GET'])
@login_required
@role_required([Roles.SUPER_HQ, Roles.QS_MANAGER, Roles.QS_STAFF])
def view_project(project_id):
    """View project details for QS work."""
    try:
        project = check_project_access(project_id)
        if not project:
            return redirect(url_for('qs_dashboard.dashboard'))
        
        # Get BOQ items for this project
        boq_items = BOQItem.query.filter_by(project_id=project_id).all()
        total_boq_value = sum(float(item.amount or 0) for item in boq_items)
        
        # Get variation orders (change orders)
        variations = ChangeOrder.query.filter_by(project_id=project_id).all()
        total_variations = sum(float(v.amount or 0) for v in variations)
        
        # Get staff assignments
        staff = ProjectStaff.query.filter_by(project_id=project_id, is_active=True).all()
        
        # Get all assigned projects for sidebar
        projects = get_user_qs_projects()
        
        # Calculate project statistics
        contract_value = float(project.budget or 0)
        revised_contract = contract_value + total_variations
        
        # Group BOQ by category
        boq_by_category = {}
        for item in boq_items:
            category = item.category or 'General'
            if category not in boq_by_category:
                boq_by_category[category] = {'items': [], 'total': 0, 'count': 0}
            boq_by_category[category]['items'].append(item)
            boq_by_category[category]['total'] += float(item.amount or 0)
            boq_by_category[category]['count'] += 1
        
        return render_template('qs/project_view.html',
            project=project,
            projects=projects,
            boq_items=boq_items,
            total_boq=total_boq_value,
            boq_by_category=boq_by_category,
            variations=variations,
            total_variations=total_variations,
            contract_value=contract_value,
            revised_contract=revised_contract,
            staff=staff,
            progress_percentage=0  # Calculate from expenditure if available
        )
    except Exception as e:
        current_app.logger.error(f"Error viewing project {project_id}: {str(e)}")
        flash('Error loading project', 'error')
        return redirect(url_for('qs_dashboard.dashboard'))


@projects_bp.route('/project/<int:project_id>/cost-summary', methods=['GET'])
@login_required
@role_required([Roles.SUPER_HQ, Roles.QS_MANAGER, Roles.QS_STAFF])
def project_cost_summary(project_id):
    """View comprehensive cost summary for a project"""
    try:
        project = check_project_access(project_id)
        if not project:
            return redirect(url_for('qs_dashboard.dashboard'))
        
        # Get all project costs
        boq_items = BOQItem.query.filter_by(project_id=project_id).all()
        total_boq = sum(float(item.amount or 0) for item in boq_items)
        
        # Get variations
        variations = ChangeOrder.query.filter_by(project_id=project_id).all()
        total_variations = sum(float(v.amount or 0) for v in variations if v.status == 'approved')
        
        # Contract values
        original_contract = float(project.budget or 0)
        revised_contract = original_contract + total_variations
        
        # Cost analysis by category
        cost_by_category = {}
        for item in boq_items:
            category = item.category or 'General'
            if category not in cost_by_category:
                cost_by_category[category] = {'allocated': 0, 'percentage': 0}
            cost_by_category[category]['allocated'] += float(item.amount or 0)
        
        # Calculate percentages
        for category in cost_by_category:
            if total_boq > 0:
                cost_by_category[category]['percentage'] = round(
                    (cost_by_category[category]['allocated'] / total_boq * 100), 1
                )
        
        # Get all assigned projects for sidebar
        projects = get_user_qs_projects()
        
        return render_template('qs/project_cost_summary.html',
            project=project,
            projects=projects,
            original_contract=original_contract,
            total_variations=total_variations,
            revised_contract=revised_contract,
            total_boq=total_boq,
            cost_by_category=cost_by_category,
            budget_remaining=revised_contract - total_boq
        )
    except Exception as e:
        current_app.logger.error(f"Error loading cost summary for project {project_id}: {str(e)}")
        flash('Error loading cost summary', 'error')
        return redirect(url_for('qs_dashboard.dashboard'))
