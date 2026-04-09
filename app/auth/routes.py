"""
Authentication routes (login, logout, registration).
"""

from flask import Blueprint, render_template, request, redirect, url_for, flash, jsonify, current_app
from flask_login import login_user, logout_user, current_user
from app.models import db, User
from werkzeug.routing import BuildError

bp = Blueprint('auth', __name__, url_prefix='/auth')


@bp.route('/login', methods=['GET', 'POST'])
def login():
    """Handle user login with role-specific redirect."""
    if current_user.is_authenticated:
        return redirect(get_dashboard_for_role(current_user.role))
    
    if request.method == 'POST':
        email = request.form.get('email', '').strip()
        password = request.form.get('password', '')
        
        if not email or not password:
            flash('Email and password are required.', 'warning')
            return redirect(url_for('auth.login'))
        
        user = User.query.filter_by(email=email).first()
        
        if user and user.check_password(password) and user.is_active:
            login_user(user, remember=request.form.get('remember', False))
            # Redirect to role-specific dashboard
            next_page = get_dashboard_for_role(user.role)
            return redirect(next_page)
        else:
            flash('Invalid email or password.', 'danger')
    
    return render_template('auth/login.html')


def get_dashboard_for_role(role):
    """Get the appropriate dashboard URL for the user's role."""
    role_dashboard_map = {
        # Admin & Super HQ
        'admin': 'admin.dashboard',
        'super_hq': 'admin.dashboard',
        
        # Procurement
        'procurement_manager': 'procurement.dashboard',
        'procurement_staff': 'procurement.dashboard',
        
        # Cost Control
        'cost_control_manager': 'cost_control.dashboard',
        'cost_control_staff': 'cost_control.dashboard',
        
        # Finance
        'finance_manager': 'finance.finance_home',
        'accounts_payable': 'finance.dashboard',
        
        # HR
        'hr_manager': 'hr.hr_home',
        'hr_staff': 'hr.hr_home',
        
        # Projects
        'project_manager': 'project.dashboard',
        'project_staff': 'project.staff_dashboard',
        
        # QS
        'qs_manager': 'qs_dashboard.dashboard',
        'qs_staff': 'qs_dashboard.dashboard',
        
        # Equipment & Legal
        'equipment_manager': 'admin.dashboard',
        'legal_manager': 'admin.dashboard',
    }
    
    # Get endpoint, default to admin dashboard if role not found
    endpoint = role_dashboard_map.get(role, 'admin.dashboard')
    try:
        return url_for(endpoint)
    except BuildError:
        current_app.logger.error(f"Invalid dashboard endpoint mapping for role '{role}': {endpoint}")
        return url_for('main.dashboard')


@bp.route('/logout')
def logout():
    """Handle user logout."""
    logout_user()
    flash('You have been logged out.', 'info')
    return redirect(url_for('auth.login'))


@bp.route('/register', methods=['GET', 'POST'])
def register():
    """Admin user registration - create users with specific roles."""
    
    if request.method == 'POST':
        name = request.form.get('name', '').strip()
        email = request.form.get('email', '').strip()
        password = request.form.get('password', '')
        role = request.form.get('role', '').strip()
        
        if not all([name, email, password, role]):
            flash('All fields are required.', 'warning')
            return redirect(url_for('auth.register'))
        
        if User.query.filter_by(email=email).first():
            flash('Email already registered.', 'warning')
            return redirect(url_for('auth.register'))
        
        user = User(
            name=name,
            email=email,
            role=role,
            is_active=True
        )
        user.set_password(password)
        
        db.session.add(user)
        db.session.commit()
        
        flash(f'User {email} created successfully as {role}.', 'success')
        return redirect(url_for('auth.register'))
    
    # Define available roles with descriptions
    roles = [
        ('admin', 'Admin - Full system access'),
        
        # Quantity Surveyor
        ('qs_manager', 'QS Manager - Quantity Surveyor'),
        ('qs_staff', 'QS Staff - Quantity Surveyor'),
        
        # Procurement
        ('procurement_manager', 'Procurement Manager'),
        ('procurement_staff', 'Procurement Staff'),
        
        # Cost Control
        ('cost_control_manager', 'Cost Control Manager'),
        ('cost_control_staff', 'Cost Control Staff'),
        
        # Finance
        ('finance_manager', 'Finance Manager'),
        ('accounts_payable', 'Accounts Payable Officer'),
        
        # Project Management
        ('project_manager', 'Project Manager'),
        ('project_staff', 'Project Staff'),
        
        # HR
        ('hr_manager', 'HR Manager'),
        
        # Equipment
        ('equipment_manager', 'Equipment Manager'),
        
        # Legal
        ('legal_manager', 'Legal Manager'),
    ]
    
    return render_template('auth/register.html', roles=roles)


@bp.route('/api/current-user')
def api_current_user():
    """Get current user info as JSON."""
    if current_user.is_authenticated:
        return jsonify({
            'id': current_user.id,
            'name': current_user.name,
            'email': current_user.email,
            'role': current_user.role,
            'is_authenticated': True
        })
    return jsonify({'is_authenticated': False}), 401
