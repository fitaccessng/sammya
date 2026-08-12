"""
Authentication routes (login, logout, registration).
"""

from flask import Blueprint, render_template, request, redirect, url_for, flash, jsonify
from flask_login import login_user, logout_user, current_user
from app.models import db, User
from app.utils import ROLE_GROUPS, dashboard_url_for_role, normalize_role, valid_signup_roles

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
    return dashboard_url_for_role(role)


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
        role = normalize_role(request.form.get('role', ''))
        
        if not all([name, email, password, role]):
            flash('All fields are required.', 'warning')
            return redirect(url_for('auth.register'))
        
        if User.query.filter_by(email=email).first():
            flash('Email already registered.', 'warning')
            return redirect(url_for('auth.register'))

        if role not in valid_signup_roles():
            flash('Invalid role selected.', 'warning')
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
    
    return render_template('auth/register.html', role_groups=ROLE_GROUPS)


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
