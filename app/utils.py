"""
Utility functions, decorators, and constants for the FitAccess application.
"""

from functools import wraps
from enum import Enum
from flask import redirect, url_for, flash, abort, current_app
from flask_login import current_user
from flask_mail import Mail, Message
from werkzeug.routing import BuildError


class Roles(str, Enum):
    """Application role definitions."""
    SUPER_HQ = 'super_hq'
    ADMIN = 'admin'
    
    # Quantity Surveyor (QS)
    QS_MANAGER = 'qs_manager'
    QS_STAFF = 'qs_staff'
    QUANTITY_SURVEYOR = 'quantity_surveyor'  # Alias for QS_MANAGER
    
    # Procurement
    HQ_PROCUREMENT = 'hq_procurement'
    PROCUREMENT_MANAGER = 'procurement_manager'
    PROCUREMENT_STAFF = 'procurement_staff'
    
    # Cost Control
    COST_CONTROL_MANAGER = 'cost_control_manager'
    COST_CONTROL_STAFF = 'cost_control_staff'
    
    # Finance
    HQ_FINANCE = 'hq_finance'
    FINANCE_MANAGER = 'finance_manager'
    ACCOUNTS_PAYABLE = 'accounts_payable'
    
    # HR
    HR_MANAGER = 'hr_manager'
    HR_STAFF = 'hr_staff'
    
    # Projects
    HQ_PROJECTS = 'hq_projects'
    PROJECT_MANAGER = 'project_manager'
    PROJECT_STAFF = 'project_staff'
    
    # Equipment
    EQUIPMENT_MANAGER = 'equipment_manager'
    
    # Legal
    LEGAL_MANAGER = 'legal_manager'


# Role descriptions and permissions
ROLE_DESCRIPTIONS = {
    'super_hq': {
        'name': 'Super HQ Administrator',
        'description': 'Full system access with all administrative privileges',
        'permissions': ['*']
    },
    'admin': {
        'name': 'Administrator',
        'description': 'Full system access with administrative privileges',
        'permissions': ['*']
    },
    'qs_manager': {
        'name': 'QS Manager - Quantity Surveyor',
        'description': 'Manages Bills of Quantities, valuations, and cost control',
        'permissions': ['boq_view', 'boq_edit', 'boq_approve', 'valuations_view', 'cost_control_view']
    },
    'qs_staff': {
        'name': 'QS Staff - Quantity Surveyor',
        'description': 'Supports QS Manager with BOQ and valuation tasks',
        'permissions': ['boq_view', 'boq_edit', 'valuations_view']
    },
    'procurement_manager': {
        'name': 'Procurement Manager',
        'description': 'Manages procurement processes, RFQs, and purchase orders',
        'permissions': ['procurement_view', 'procurement_edit', 'procurement_approve', 'po_view', 'po_edit']
    },
    'procurement_staff': {
        'name': 'Procurement Staff',
        'description': 'Supports procurement manager with sourcing and documentation',
        'permissions': ['procurement_view', 'po_view']
    },
    'cost_control_manager': {
        'name': 'Cost Control Manager',
        'description': 'Manages project costs, budgets, and financial forecasting',
        'permissions': ['cost_control_view', 'cost_control_edit', 'cost_control_approve', 'budget_view']
    },
    'cost_control_staff': {
        'name': 'Cost Control Staff',
        'description': 'Supports cost control manager with data entry and reporting',
        'permissions': ['cost_control_view', 'budget_view']
    },
    'finance_manager': {
        'name': 'Finance Manager',
        'description': 'Manages financial operations, payments, and accounting',
        'permissions': ['finance_view', 'finance_edit', 'finance_approve', 'payments_view', 'payments_edit']
    },
    'accounts_payable': {
        'name': 'Accounts Payable Officer',
        'description': 'Processes invoices, payments, and vendor management',
        'permissions': ['payments_view', 'payments_edit', 'invoices_view']
    },
    'project_manager': {
        'name': 'Project Manager',
        'description': 'Overall project management, scheduling, and team coordination',
        'permissions': ['project_view', 'project_edit', 'team_view', 'team_edit']
    },
    'project_staff': {
        'name': 'Project Staff',
        'description': 'Supports project manager with administrative and coordination tasks',
        'permissions': ['project_view', 'team_view']
    },
    'hr_manager': {
        'name': 'HR Manager',
        'description': 'Manages human resources, payroll, and employee records',
        'permissions': ['hr_view', 'hr_edit', 'hr_approve']
    },
    'hr_staff': {
        'name': 'HR Staff',
        'description': 'Supports HR Manager with documentation and administration',
        'permissions': ['hr_view']
    },
    'equipment_manager': {
        'name': 'Equipment Manager',
        'description': 'Manages equipment inventory, maintenance, and allocations',
        'permissions': ['equipment_view', 'equipment_edit', 'equipment_approve']
    },
    'legal_manager': {
        'name': 'Legal Manager',
        'description': 'Manages contracts, compliance, and legal documentation',
        'permissions': ['legal_view', 'legal_edit', 'legal_approve']
    },
    'hq_procurement': {
        'name': 'HQ Procurement',
        'description': 'HQ-level procurement oversight',
        'permissions': ['procurement_view', 'procurement_edit', 'procurement_approve']
    },
    'hq_finance': {
        'name': 'HQ Finance',
        'description': 'HQ-level financial oversight',
        'permissions': ['finance_view', 'finance_edit', 'finance_approve']
    },
    'hq_projects': {
        'name': 'HQ Projects',
        'description': 'HQ-level project oversight',
        'permissions': ['project_view', 'project_edit']
    },
}


ROLE_DASHBOARD_ENDPOINTS = {
    'admin': 'admin.dashboard',
    'super_hq': 'admin.dashboard',
    'hq_procurement': 'procurement.dashboard',
    'procurement_manager': 'procurement.dashboard',
    'procurement_staff': 'procurement.dashboard',
    'cost_control_manager': 'cost_control.dashboard',
    'cost_control_staff': 'cost_control.dashboard',
    'hq_finance': 'finance.finance_home',
    'finance_manager': 'finance.finance_home',
    'accounts_payable': 'finance.finance_home',
    'hr_manager': 'hr.hr_home',
    'hr_staff': 'hr.hr_home',
    'hq_projects': 'project.dashboard',
    'project_manager': 'project.dashboard',
    'project_staff': 'project.staff_dashboard',
    'qs_manager': 'qs_dashboard.dashboard',
    'qs_staff': 'qs_dashboard.dashboard',
    'equipment_manager': 'admin.dashboard',
    'legal_manager': 'admin.dashboard',
}


ROLE_GROUPS = [
    ('Administration', [
        ('super_hq', 'Super HQ - Enterprise-wide admin dashboard'),
        ('admin', 'Admin - Full system administration dashboard'),
    ]),
    ('HQ', [
        ('hq_procurement', 'HQ Procurement - Procurement oversight dashboard'),
        ('hq_finance', 'HQ Finance - Finance oversight dashboard'),
        ('hq_projects', 'HQ Projects - Project oversight dashboard'),
    ]),
    ('Procurement', [
        ('procurement_manager', 'Procurement Manager - Procurement management dashboard'),
        ('procurement_staff', 'Procurement Staff - Procurement staff dashboard'),
    ]),
    ('Cost Control', [
        ('cost_control_manager', 'Cost Control Manager - Budget and variance dashboard'),
        ('cost_control_staff', 'Cost Control Staff - Cost tracking dashboard'),
    ]),
    ('Finance', [
        ('finance_manager', 'Finance Manager - Finance management dashboard'),
        ('accounts_payable', 'Accounts Payable - Payment processing dashboard'),
    ]),
    ('HR', [
        ('hr_manager', 'HR Manager - HR management dashboard'),
        ('hr_staff', 'HR Staff - HR staff dashboard'),
    ]),
    ('Projects', [
        ('project_manager', 'Project Manager - Project management dashboard'),
        ('project_staff', 'Project Staff - Staff project dashboard'),
    ]),
    ('QS', [
        ('qs_manager', 'QS Manager - QS management dashboard'),
        ('qs_staff', 'QS Staff - QS staff dashboard'),
    ]),
    ('Specialist', [
        ('equipment_manager', 'Equipment Manager - Equipment operations dashboard'),
        ('legal_manager', 'Legal Manager - Legal and compliance dashboard'),
    ]),
]


def valid_signup_roles():
    """Return role values that have an explicit dashboard mapping."""
    return list(ROLE_DASHBOARD_ENDPOINTS.keys())


def dashboard_url_for_role(role, fallback_endpoint='main.dashboard'):
    """Return the dashboard URL that matches a user's assigned role."""
    endpoint = ROLE_DASHBOARD_ENDPOINTS.get(role, 'main.dashboard')
    try:
        return url_for(endpoint)
    except BuildError:
        current_app.logger.error(
            f"Invalid dashboard endpoint mapping for role '{role}': {endpoint}"
        )
        return url_for(fallback_endpoint)


def role_required(required_roles):
    """
    Decorator to check if current user has required role.
    
    Args:
        required_roles: List of required roles
    """
    def decorator(f):
        @wraps(f)
        def decorated_function(*args, **kwargs):
            if not current_user.is_authenticated:
                flash('Please log in to access this page.', 'danger')
                return redirect(url_for('auth.login'))
            
            # Check if user has required role
            user_role = current_user.role
            if user_role not in required_roles and user_role != Roles.SUPER_HQ:
                flash('You do not have permission to access this page.', 'danger')
                abort(403)
            
            return f(*args, **kwargs)
        return decorated_function
    return decorator


def send_email(recipient, subject, body, html=None):
    """
    Send an email to a recipient.
    
    Args:
        recipient: Email address of recipient
        subject: Email subject
        body: Plain text email body
        html: Optional HTML email body
    
    Returns:
        True if email sent successfully, False otherwise
    """
    try:
        from flask_mail import Mail, Message
        
        msg = Message(
            subject=subject,
            recipients=[recipient],
            body=body,
            html=html
        )
        
        mail = Mail(current_app)
        mail.send(msg)
        return True
    except Exception as e:
        print(f"Error sending email: {e}")
        return False

