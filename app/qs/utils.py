"""
Shared utilities for QS module
"""
from flask import flash, redirect, url_for
from flask_login import current_user
from app.models import Project, ProjectStaff
from app.utils import Roles


def get_user_qs_projects():
    """Get projects where the current user is assigned (for QS staff)."""
    if current_user.has_role(Roles.SUPER_HQ):
        # Super HQ can see all projects
        return Project.query.filter_by(status='active').all()
    
    # Regular QS staff see only projects they're assigned to
    assignments = ProjectStaff.query.filter_by(
        user_id=current_user.id,
        is_active=True
    ).all()
    project_ids = [a.project_id for a in assignments]
    
    if not project_ids:
        return []
    
    return Project.query.filter(
        Project.id.in_(project_ids),
        Project.status == 'active'
    ).all()


def check_project_access(project_id):
    """Check if current user has access to this project for QS work."""
    project = Project.query.get_or_404(project_id)
    
    if current_user.has_role(Roles.SUPER_HQ):
        return project
    
    # Check if user is assigned to this project
    assignment = ProjectStaff.query.filter_by(
        user_id=current_user.id,
        project_id=project_id,
        is_active=True
    ).first()
    
    if not assignment:
        flash('You do not have access to this project', 'error')
        return None
    
    return project
