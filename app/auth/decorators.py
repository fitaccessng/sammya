"""
Authentication and authorization decorators.
"""

from functools import wraps
from flask import redirect, url_for, flash, jsonify, session, request
from flask_login import current_user, login_required as login_required_flask


def login_required(f):
    """Wrapper for flask_login.login_required with proper redirect."""
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if not current_user.is_authenticated:
            flash('You must be logged in to access this page.', 'warning')
            return redirect(url_for('auth.login', next=request.url))
        return f(*args, **kwargs)
    return decorated_function


def role_required(required_roles):
    """
    Decorator to restrict endpoints to specific roles.
    
    Args:
        required_roles: List of role strings or single role string
        
    Usage:
        @role_required(['admin', 'finance_manager'])
        def my_endpoint():
            pass
    """
    if isinstance(required_roles, str):
        required_roles = [required_roles]
    
    def decorator(f):
        @wraps(f)
        def decorated_function(*args, **kwargs):
            if not current_user.is_authenticated:
                flash('You must be logged in.', 'warning')
                return redirect(url_for('auth.login', next=request.url))
            
            if current_user.role not in required_roles:
                if request.is_json or request.path.startswith('/api'):
                    return jsonify({'error': 'Insufficient permissions'}), 403
                else:
                    flash(f'You do not have permission to access this resource. Required role: {", ".join(required_roles)}', 'danger')
                    return redirect(url_for('main.dashboard'))
            
            return f(*args, **kwargs)
        return decorated_function
    return decorator


def project_required(f):
    """
    Decorator to ensure user is assigned to the project being accessed.
    Requires project_id as route parameter.
    """
    @wraps(f)
    def decorated_function(*args, **kwargs):
        from app.models import Project
        
        project_id = kwargs.get('project_id')
        if not project_id:
            flash('Project ID not specified.', 'danger')
            return redirect(url_for('main.dashboard'))
        
        project = Project.query.get_or_404(project_id)
        
        # Admin can access all projects
        if current_user.role == 'admin':
            return f(*args, **kwargs)
        
        # Check if user is in project team
        if current_user not in project.team_members:
            flash('You are not assigned to this project.', 'danger')
            return redirect(url_for('main.dashboard'))
        
        return f(*args, **kwargs)
    return decorated_function
