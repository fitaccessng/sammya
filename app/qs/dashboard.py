"""
QS Dashboard endpoints
"""
from flask import Blueprint, render_template, redirect, url_for, flash, current_app
from flask_login import login_required, current_user
from app.models import BOQItem
from app.utils import role_required, Roles
from .utils import get_user_qs_projects

dashboard_bp = Blueprint('qs_dashboard', __name__)


@dashboard_bp.route('/dashboard', methods=['GET'])
@login_required
@role_required([Roles.SUPER_HQ, Roles.QS_MANAGER, Roles.QS_STAFF])
def dashboard():
    """QS Manager Dashboard - Overview of assigned project costs and valuations"""
    try:
        # Get projects assigned to this QS user
        projects = get_user_qs_projects()
        
        if not projects:
            return render_template('qs/dashboard.html',
                projects=[],
                total_boq=0,
                total_variations=0,
                total_projects=0,
                total_contract_value=0,
                total_spent=0,
                budget_remaining=0,
                projects_over_budget=[],
                projects_on_track=[],
                recent_boq_updates=[]
            )
        
        # Calculate portfolio metrics
        total_contract_value = sum(float(p.budget or 0) for p in projects)
        
        # Total BOQ values across assigned projects
        total_boq_value = 0
        for project in projects:
            boq_items = BOQItem.query.filter_by(project_id=project.id).all()
            total_boq_value += sum(float(item.amount or 0) for item in boq_items)
        
        # Total spent (placeholder)
        total_spent = 0
        
        # Projects requiring attention (over budget)
        projects_over_budget = []
        projects_on_track = []
        
        for project in projects:
            project_spent = 0
            budget_remaining = float(project.budget or 0) - project_spent
            utilization = (project_spent / float(project.budget) * 100) if project.budget else 0
            
            project_data = {
                'id': project.id,
                'name': project.name,
                'budget': float(project.budget or 0),
                'spent': project_spent,
                'remaining': budget_remaining,
                'utilization': round(utilization, 1)
            }
            
            if utilization > 90:
                projects_over_budget.append(project_data)
            else:
                projects_on_track.append(project_data)
        
        # Recent BOQ updates
        recent_boq_updates = BOQItem.query.filter(
            BOQItem.project_id.in_([p.id for p in projects])
        ).order_by(BOQItem.updated_at.desc()).limit(10).all() if projects else []
        
        return render_template('qs/dashboard.html',
            projects=projects,
            total_boq=total_boq_value,
            total_variations=0,
            total_projects=len(projects),
            total_contract_value=total_contract_value,
            total_spent=total_spent,
            budget_remaining=total_contract_value - total_spent,
            projects_over_budget=projects_over_budget,
            projects_on_track=projects_on_track,
            recent_boq_updates=recent_boq_updates
        )
        
    except Exception as e:
        current_app.logger.error(f"Error loading QS dashboard: {str(e)}", exc_info=True)
        flash('Error loading dashboard', 'error')
        return redirect(url_for('main.index'))
