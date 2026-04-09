"""
Main application routes - Home, Login, Signup, Password reset, etc.
"""
from flask import Blueprint, render_template, request, redirect, url_for, flash, session
from flask_login import login_user, logout_user, current_user, login_required
from app.models import User, db
from app.utils import Roles
from werkzeug.routing import BuildError
import random
import string
import logging
import uuid

# Configure logger for this module
logger = logging.getLogger(__name__)

main_bp = Blueprint("main", __name__)


@main_bp.route("/")
def index():
    """Main home page."""
    if current_user.is_authenticated:
        return redirect(url_for("main.dashboard"))
    return render_template("index.html")


@main_bp.route('/dashboard')
def dashboard():
    """Redirect to role-specific dashboard."""
    if not current_user.is_authenticated:
        flash('Please log in to access the dashboard.', 'warning')
        return redirect(url_for('auth.login'))
    
    # Redirect to role-specific dashboard
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
        'finance_manager': 'finance.dashboard',
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
    
    endpoint = role_dashboard_map.get(current_user.role, 'admin.dashboard')
    try:
        return redirect(url_for(endpoint))
    except BuildError:
        logger.error(f"Invalid dashboard endpoint mapping for role '{current_user.role}': {endpoint}")
        return redirect(url_for('admin.dashboard'))


@main_bp.route('/signup', methods=['GET', 'POST'])
def signup():
    """User signup page."""
    if current_user.is_authenticated:
        return redirect(url_for('main.dashboard'))
    
    try:
        if request.method == 'POST':
            name = request.form.get("name", "").strip()
            email = request.form.get("email", "").strip()
            role = request.form.get("role", "").strip()
            password = request.form.get("password", "").strip()
            confirm_password = request.form.get("confirm_password", "").strip()

            # Input validation
            if not all([name, email, role, password, confirm_password]):
                flash("All fields are required", "error")
                return render_template("auth/signup.html")

            if password != confirm_password:
                flash("Passwords do not match", "error")
                return render_template("auth/signup.html")

            if len(password) < 6:
                flash("Password must be at least 6 characters long", "error")
                return render_template("auth/signup.html")

            # Check existing user
            if User.query.filter_by(email=email).first():
                flash("Email already registered", "error")
                return render_template("auth/signup.html")

            # Validate role
            valid_roles = ['super_hq', 'admin', 'procurement_manager', 'procurement_staff',
                          'cost_control_manager', 'cost_control_staff', 'finance_manager',
                          'accounts_payable', 'hr_manager', 'hr_staff', 'project_manager', 'project_staff',
                          'qs_manager', 'qs_staff', 'equipment_manager', 'legal_manager']
            
            if role not in valid_roles:
                flash("Invalid role selected", "error")
                return render_template("auth/signup.html")

            # Create new user
            new_user = User(name=name, email=email, role=role)
            new_user.set_password(password)
            db.session.add(new_user)
            db.session.commit()

            flash("Account created successfully! You can now log in.", "success")
            return redirect(url_for("main.login"))

    except Exception as e:
        logger.error(f"Signup error: {str(e)}")
        db.session.rollback()
        flash("An error occurred during signup. Please try again.", "error")
        return render_template("auth/signup.html")
        
    return render_template("auth/signup.html")


@main_bp.route("/login", methods=["GET", "POST"])
def login():
    """User login page."""
    if current_user.is_authenticated:
        return redirect(url_for('main.dashboard'))
    
    if request.method == "POST":
        try:
            email = request.form.get("email", "").strip()
            password = request.form.get("password", "").strip()

            if not email or not password:
                flash("Email and password are required", "error")
                return render_template("auth/login.html")

            user = User.query.filter_by(email=email).first()
            
            if not user:
                flash("Invalid email or password", "error")
                return render_template("auth/login.html")

            if not user.is_active:
                flash("Account is inactive. Please contact support.", "error")
                return render_template("auth/login.html")

            if user.check_password(password):
                login_user(user)
                session.permanent = True
                flash(f"Welcome back, {user.name}!", "success")
                return redirect(url_for('main.dashboard'))
            else:
                flash("Invalid email or password", "error")
                return render_template("auth/login.html")
                
        except Exception as e:
            logger.error(f"Login error: {str(e)}", exc_info=True)
            flash(f"An error occurred: {str(e)}", "error")
            return render_template("auth/login.html")
    
    return render_template("auth/login.html")


@main_bp.route("/forgot-password", methods=["GET", "POST"])
def forgot_password():
    """Forgot password page."""
    if current_user.is_authenticated:
        return redirect(url_for('main.dashboard'))
    
    try:
        if request.method == "POST":
            email = request.form.get("email", "").strip()
            
            if not email:
                flash("Email is required", "error")
                return render_template("auth/forgot_password.html")
            
            user = User.query.filter_by(email=email).first()
            
            if user:
                # Generate reset token
                reset_token = str(uuid.uuid4())
                user.reset_token = reset_token
                db.session.commit()
                
                # TODO: Send email with reset link
                flash("If an account exists with this email, you will receive a password reset link.", "info")
            else:
                # Don't reveal if email exists (security)
                flash("If an account exists with this email, you will receive a password reset link.", "info")
        
    except Exception as e:
        logger.error(f"Forgot password error: {str(e)}")
        flash("An error occurred. Please try again.", "error")
        db.session.rollback()
    
    return render_template("auth/forgot_password.html")


@main_bp.route("/reset-password/<token>", methods=["GET", "POST"])
def reset_password(token):
    """Reset password page."""
    if current_user.is_authenticated:
        return redirect(url_for('main.dashboard'))
    
    user = User.query.filter_by(reset_token=token).first()
    
    if not user:
        flash("Invalid or expired reset link", "error")
        return redirect(url_for("main.login"))

    try:
        if request.method == "POST":
            password = request.form.get("password", "").strip()
            confirm_password = request.form.get("confirm_password", "").strip()
            
            if not password or not confirm_password:
                flash("Both password fields are required", "error")
                return render_template("auth/reset_password.html", token=token)
            
            if password != confirm_password:
                flash("Passwords do not match", "error")
                return render_template("auth/reset_password.html", token=token)
            
            if len(password) < 6:
                flash("Password must be at least 6 characters long", "error")
                return render_template("auth/reset_password.html", token=token)
            
            user.set_password(password)
            user.reset_token = None
            db.session.commit()
            
            flash("Password reset successful! You can now log in.", "success")
            return redirect(url_for("main.login"))
    
    except Exception as e:
        logger.error(f"Password reset error: {str(e)}")
        flash("An error occurred. Please try again.", "error")
        db.session.rollback()

    return render_template("auth/reset_password.html", token=token)


@main_bp.route('/logout')
def logout():
    """Logout user."""
    logout_user()
    flash('You have been logged out successfully.', 'success')
    return redirect(url_for('main.login'))


@main_bp.route('/account/settings', methods=['GET', 'POST'])
@login_required
def account_settings():
    """Shared account settings for all roles."""
    if request.method == 'POST':
        try:
            action = request.form.get('action', '').strip()
            if action == 'email':
                new_email = (request.form.get('email') or '').strip().lower()
                password = request.form.get('password') or ''
                if not new_email or not password:
                    flash('Email and current password are required.', 'error')
                    return redirect(url_for('main.account_settings'))
                if not current_user.check_password(password):
                    flash('Current password is incorrect.', 'error')
                    return redirect(url_for('main.account_settings'))
                existing = User.query.filter(User.email == new_email, User.id != current_user.id).first()
                if existing:
                    flash('Email is already in use.', 'error')
                    return redirect(url_for('main.account_settings'))
                current_user.email = new_email
                db.session.commit()
                flash('Email updated successfully.', 'success')
                return redirect(url_for('main.account_settings'))

            if action == 'password':
                current_password = request.form.get('current_password') or ''
                new_password = request.form.get('new_password') or ''
                confirm_password = request.form.get('confirm_password') or ''
                if not current_user.check_password(current_password):
                    flash('Current password is incorrect.', 'error')
                    return redirect(url_for('main.account_settings'))
                if len(new_password) < 6:
                    flash('New password must be at least 6 characters.', 'error')
                    return redirect(url_for('main.account_settings'))
                if new_password != confirm_password:
                    flash('New passwords do not match.', 'error')
                    return redirect(url_for('main.account_settings'))
                current_user.set_password(new_password)
                db.session.commit()
                flash('Password updated successfully.', 'success')
                return redirect(url_for('main.account_settings'))

            flash('Invalid settings action.', 'error')
            return redirect(url_for('main.account_settings'))
        except Exception as e:
            db.session.rollback()
            logger.error(f"Account settings error: {str(e)}", exc_info=True)
            flash('Could not update account settings.', 'error')
            return redirect(url_for('main.account_settings'))

    return render_template('account/settings.html')
