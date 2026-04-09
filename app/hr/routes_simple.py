"""
HR Module - Simplified Employee Management System
Uses existing database models (User, ProjectStaff, Project)
"""

from flask import Blueprint, render_template, request, redirect, url_for, flash, jsonify, current_app
from flask_login import current_user, login_required
from datetime import datetime, timedelta, date
from functools import wraps
from sqlalchemy import func, desc

# Import models
from app.models import db, User, Project, ProjectStaff
from app.utils import role_required, Roles

# Create blueprint
bp = Blueprint('hr', __name__, url_prefix='/hr')

# ==================== DECORATORS ====================

def hr_required(f):
    """Check if user has HR role"""
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if not current_user or current_user.role not in [Roles.HR_MANAGER, Roles.HR_STAFF, Roles.ADMIN]:
            flash("Access denied. Insufficient permissions.", "error")
            return redirect(url_for('main.dashboard'))
        return f(*args, **kwargs)
    return decorated_function

# ==================== DASHBOARD ROUTES ====================

@bp.route('/')
@bp.route('/home')
@login_required
@hr_required
def hr_home():
    """Main HR Dashboard"""
    try:
        # User Statistics
        total_staff = User.query.count()
        active_staff = User.query.filter_by(is_active=True).count()
        inactive_staff = total_staff - active_staff
        
        # Staff Roles
        staff_by_role = db.session.query(
            User.role, 
            func.count(User.id)
        ).filter(User.role.isnot(None)).group_by(User.role).all()
        
        # Project Statistics
        total_projects = Project.query.count()
        active_projects = Project.query.filter_by(status='active').count()
        
        # Project Assignments
        total_assignments = ProjectStaff.query.filter_by(is_active=True).count()
        
        # Recent staff
        recent_users = User.query.order_by(desc(User.created_at)).limit(5).all()
        
        dashboard_data = {
            'total_staff': total_staff,
            'active_staff': active_staff,
            'inactive_staff': inactive_staff,
            'staff_by_role': [{'role': role, 'count': count} for role, count in staff_by_role],
            'total_projects': total_projects,
            'active_projects': active_projects,
            'total_assignments': total_assignments,
            'recent_staff': recent_users
        }
        
        return render_template('hr/index.html', dashboard=dashboard_data)
        
    except Exception as e:
        current_app.logger.error(f"HR Dashboard Error: {str(e)}")
        flash("Error loading HR dashboard", "error")
        return redirect(url_for('main.dashboard'))

# ==================== STAFF MANAGEMENT ROUTES ====================

@bp.route('/staff')
@login_required
@hr_required
def staff_list():
    """List all staff members"""
    try:
        page = request.args.get('page', 1, type=int)
        search = request.args.get('search', '')
        status_filter = request.args.get('status', 'active')
        
        query = User.query
        
        if search:
            query = query.filter(User.name.ilike(f'%{search}%') | User.email.ilike(f'%{search}%'))
        
        if status_filter == 'active':
            query = query.filter_by(is_active=True)
        elif status_filter == 'inactive':
            query = query.filter_by(is_active=False)
        
        staff = query.order_by(User.name).paginate(page=page, per_page=20)
        
        stats = {
            'total_staff': User.query.count(),
            'active_staff': User.query.filter_by(is_active=True).count(),
            'inactive_staff': User.query.filter_by(is_active=False).count(),
        }
        
        return render_template('hr/staff_list.html', staff=staff, stats=stats, search=search, status_filter=status_filter)
        
    except Exception as e:
        current_app.logger.error(f"Staff List Error: {str(e)}")
        flash("Error loading staff list", "error")
        return redirect(url_for('hr.hr_home'))

@bp.route('/staff/<int:staff_id>')
@login_required
@hr_required
def staff_details(staff_id):
    """View staff member details"""
    try:
        staff = User.query.get_or_404(staff_id)
        
        # Get project assignments
        assignments = ProjectStaff.query.filter_by(user_id=staff_id).all()
        
        return render_template('hr/staff_details.html', staff=staff, assignments=assignments)
        
    except Exception as e:
        current_app.logger.error(f"Staff Details Error: {str(e)}")
        flash("Error loading staff details", "error")
        return redirect(url_for('hr.staff_list'))

@bp.route('/staff/<int:staff_id>/edit', methods=['GET', 'POST'])
@login_required
@hr_required
def edit_staff(staff_id):
    """Edit staff member"""
    try:
        staff = User.query.get_or_404(staff_id)
        
        if request.method == 'POST':
            staff.name = request.form.get('name', staff.name)
            staff.email = request.form.get('email', staff.email)
            staff.role = request.form.get('role', staff.role)
            staff.is_active = request.form.get('is_active') == 'on'
            
            db.session.commit()
            flash('Staff member updated successfully', 'success')
            return redirect(url_for('hr.staff_details', staff_id=staff_id))
        
        roles = [Roles.HR_MANAGER, Roles.HR_STAFF, Roles.QS_MANAGER, Roles.QC_MANAGER, 
                 Roles.COST_MANAGER, Roles.SAFETY_MANAGER, Roles.ADMIN]
        
        return render_template('hr/edit_staff.html', staff=staff, roles=roles)
        
    except Exception as e:
        current_app.logger.error(f"Edit Staff Error: {str(e)}")
        flash("Error editing staff member", "error")
        return redirect(url_for('hr.staff_list'))

# ==================== PROJECT ASSIGNMENTS ====================

@bp.route('/assignments')
@login_required
@hr_required
def assignments():
    """View project staff assignments"""
    try:
        page = request.args.get('page', 1, type=int)
        
        assignments = ProjectStaff.query.filter_by(is_active=True)\
            .join(User).join(Project)\
            .order_by(desc(ProjectStaff.created_at))\
            .paginate(page=page, per_page=20)
        
        return render_template('hr/assignments.html', assignments=assignments)
        
    except Exception as e:
        current_app.logger.error(f"Assignments Error: {str(e)}")
        flash("Error loading assignments", "error")
        return redirect(url_for('hr.hr_home'))

# ==================== BASIC STATS ====================

@bp.route('/api/stats')
@login_required
@hr_required
def get_stats():
    """Get HR statistics as JSON"""
    try:
        stats = {
            'total_staff': User.query.count(),
            'active_staff': User.query.filter_by(is_active=True).count(),
            'inactive_staff': User.query.filter_by(is_active=False).count(),
            'total_projects': Project.query.count(),
            'active_projects': Project.query.filter_by(status='active').count(),
            'total_assignments': ProjectStaff.query.filter_by(is_active=True).count()
        }
        return jsonify(stats)
    except Exception as e:
        current_app.logger.error(f"Stats API Error: {str(e)}")
        return jsonify({'error': str(e)}), 500
