"""
Admin dashboard and management routes.
System-wide configuration, user management, project management, and approval logs.
"""

from flask import Blueprint, render_template, request, redirect, url_for, flash, jsonify, current_app
from flask_login import current_user, login_required
from app.models import (
    db, User, Project, ApprovalLog, user_projects, PaymentRecord,
    StaffImportBatch, StaffImportItem, ApprovalState, ApprovalMessage
)
from app.auth.decorators import role_required
from app.excel_import import StaffImportManager
from datetime import datetime, timedelta
from sqlalchemy import desc

bp = Blueprint('admin', __name__, url_prefix='/admin')


@bp.route('/dashboard')
def dashboard():
    """Admin dashboard with system overview."""
    # Check authentication manually
    if not current_user.is_authenticated:
        flash('Please log in to access this page.', 'warning')
        return redirect(url_for('auth.login', next=request.url))
    
    # Check role manually
    if current_user.role not in ['admin', 'super_hq']:
        flash('You do not have permission to access this page.', 'danger')
        return redirect(url_for('main.dashboard'))
    
    # Get statistics
    total_users = User.query.count()
    total_projects = Project.query.count()
    active_projects = Project.query.filter_by(status='active').count()
    
    # Get recent approvals
    recent_approvals = ApprovalLog.query.order_by(ApprovalLog.timestamp.desc()).limit(10).all()
    
    # Get pending approvals by type
    pending_count = {}
    from app.models import MaterialRequest, PurchaseOrder, QCInspection, PaymentRequest, ApprovalState
    pending_count['pr'] = MaterialRequest.query.filter_by(approval_state=ApprovalState.PENDING).count()
    pending_count['po'] = PurchaseOrder.query.filter_by(approval_state=ApprovalState.PENDING).count()
    pending_count['qc'] = QCInspection.query.filter_by(approval_state=ApprovalState.PENDING).count()
    pending_count['payment'] = PaymentRequest.query.filter_by(approval_state=ApprovalState.PENDING).count()
    pending_count['staff_import'] = StaffImportBatch.query.filter_by(approval_state=ApprovalState.PENDING).count()
    
    # Budget overview
    projects = Project.query.all()
    total_budget = sum(float(p.budget or 0) for p in projects)
    
    # User activity (last 7 days)
    week_ago = datetime.utcnow() - timedelta(days=7)
    active_users = User.query.filter(User.created_at >= week_ago).count()
    recent_approvals_count = ApprovalLog.query.filter(ApprovalLog.timestamp >= week_ago).count()
    
    return render_template(
        'admin/dashboard.html',
        total_users=total_users,
        total_projects=total_projects,
        active_projects=active_projects,
        total_budget=total_budget,
        pending_count=pending_count,
        recent_approvals=recent_approvals,
        active_users=active_users,
        recent_approvals_count=recent_approvals_count
    )


@bp.route("/reset-db", methods=["POST", "GET"])
@login_required
def reset_db():
    if current_user.role != 'admin':
        return jsonify({"error": "Unauthorized"}), 403

    try:
        db.drop_all()
        db.create_all()
        return jsonify({"message": "Database reset complete"}), 200

    except Exception as e:
        db.session.rollback()
        return jsonify({"error": str(e)}), 500


@bp.route('/users', methods=['GET'])
@login_required
@role_required(['admin'])
def users():
    """List all users."""
    from flask import get_flashed_messages
    
    page = request.args.get('page', 1, type=int)
    users = User.query.order_by(User.created_at.desc()).paginate(page=page, per_page=20)
    
    # Get pending staff imports count
    pending_imports_count = StaffImportBatch.query.filter_by(
        approval_state=ApprovalState.PENDING
    ).count()
    
    # Check for batch submission success message
    submitted_batch_records = None
    messages = get_flashed_messages(with_categories=True)
    for category, message in messages:
        if 'Batch submitted for admin approval' in message and 'records ready for import' in message:
            # Extract record count from message: "Batch submitted for admin approval. X records ready for import."
            try:
                parts = message.split()
                for i, part in enumerate(parts):
                    if part.isdigit() and i < len(parts) - 1 and parts[i + 1] == 'records':
                        submitted_batch_records = int(part)
                        break
            except:
                pass
    
    return render_template('admin/users_list.html', users=users, pending_imports_count=pending_imports_count, submitted_batch_records=submitted_batch_records)


@bp.route('/user/<int:user_id>/edit', methods=['GET', 'POST'])
@login_required
@role_required(['admin'])
def edit_user(user_id):
    """Edit user details and role."""
    user = User.query.get_or_404(user_id)
    
    if request.method == 'POST':
        user.name = request.form.get('name', user.name).strip()
        user.email = request.form.get('email', user.email).strip()
        new_role = request.form.get('role', user.role).strip()
        user.role = new_role
        user.is_active = request.form.get('is_active') == 'on'
        
        db.session.commit()
        flash(f'User {user.email} updated successfully.', 'success')
        return redirect(url_for('admin.users'))
    
    roles = [
        'admin', 'procurement_manager', 'procurement_staff',
        'cost_control_manager', 'cost_control_staff',
        'finance_manager', 'accounts_payable',
        'hr_manager', 'project_manager', 'project_staff',
        'qs_manager', 'qs_staff'
    ]
    
    return render_template('admin/edit_user.html', user=user, roles=roles)


@bp.route('/user/<int:user_id>/delete', methods=['POST'])
@login_required
@role_required(['admin'])
def delete_user(user_id):
    """Delete a user (soft delete by deactivating)."""
    user = User.query.get_or_404(user_id)
    
    if user.id == current_user.id:
        flash('Cannot delete your own account.', 'danger')
        return redirect(url_for('admin.users'))
    
    user.is_active = False
    db.session.commit()
    flash(f'User {user.email} has been deactivated.', 'success')
    return redirect(url_for('admin.users'))


@bp.route('/api/user/<int:user_id>', methods=['GET'])
@login_required
@role_required(['admin'])
def api_user_details(user_id):
    """Get detailed information about a user including assigned projects."""
    try:
        user = User.query.get_or_404(user_id)
        
        # Get user's assigned projects
        projects = [
            {
                'id': p.id,
                'name': p.name,
                'status': p.status,
                'budget': float(p.budget or 0)
            }
            for p in user.projects
        ]
        
        # Get next of kin information
        next_of_kin = []
        if hasattr(user, 'next_of_kin') and user.next_of_kin:
            next_of_kin = [
                {
                    'id': kin.id,
                    'full_name': kin.full_name,
                    'relationship': kin.relationship,
                    'phone': kin.phone,
                    'email': kin.email,
                    'address': kin.address,
                    'city': kin.city,
                    'state': kin.state,
                    'is_primary': kin.is_primary
                }
                for kin in user.next_of_kin
            ]
        
        # Calculate total deductions
        total_deductions = 0
        if hasattr(user, 'payroll_deductions') and user.payroll_deductions:
            total_deductions = sum(float(d.amount or 0) for d in user.payroll_deductions if d.is_recurring)
        
        return jsonify({
            'id': user.id,
            'name': user.name,
            'email': user.email,
            'phone': user.phone if hasattr(user, 'phone') else None,
            'date_of_birth': user.date_of_birth.isoformat() if (hasattr(user, 'date_of_birth') and user.date_of_birth) else None,
            'gender': user.gender if hasattr(user, 'gender') else None,
            'marital_status': user.marital_status if hasattr(user, 'marital_status') else None,
            'address': user.address if hasattr(user, 'address') else None,
            'city': user.city if hasattr(user, 'city') else None,
            'state': user.state if hasattr(user, 'state') else None,
            'role': user.role,
            'employee_id': user.employee_id if hasattr(user, 'employee_id') else None,
            'date_of_employment': user.date_of_employment.isoformat() if (hasattr(user, 'date_of_employment') and user.date_of_employment) else None,
            'basic_salary': float(user.basic_salary or 0) if hasattr(user, 'basic_salary') else 0,
            'total_deductions': total_deductions,
            'is_active': user.is_active,
            'created_at': user.created_at.isoformat() if user.created_at else None,
            'next_of_kin': next_of_kin,
            'projects': projects
        })
    except Exception as e:
        print(f"Error fetching user details: {str(e)}")
        return jsonify({'error': 'Failed to fetch user details'}), 500


@bp.route('/projects', methods=['GET'])
@login_required
@role_required(['admin'])
def projects():
    """List all projects."""
    page = request.args.get('page', 1, type=int)
    projects = Project.query.paginate(page=page, per_page=15)
    
    return render_template('admin/projects.html', projects=projects)


@bp.route('/project/<int:project_id>/details', methods=['GET'])
@login_required
@role_required(['admin'])
def project_details(project_id):
    """View project details."""
    from app.projects.routes import get_user_accessible_project_ids, get_user_accessible_projects, calculate_project_health, calculate_evm_metrics
    
    project = Project.query.get_or_404(project_id)
    
    # Check access
    accessible_ids = get_user_accessible_project_ids(current_user)
    if project_id not in accessible_ids and current_user.role not in ['super_hq', 'admin']:
        flash('Access denied', 'error')
        return redirect(url_for('project.index'))
    
    # Get related data
    staff = project.staff_assignments if hasattr(project, 'staff_assignments') else []
    milestones = project.milestones if hasattr(project, 'milestones') else []
    budget_data = project.budget_records if hasattr(project, 'budget_records') else []
    documents = project.documents if hasattr(project, 'documents') else []
    
    # Calculate metrics
    health = calculate_project_health(project)
    evm = calculate_evm_metrics(project)
    
    return render_template(
        'project_manager/project_details.html',
        project=project,
        staff=staff,
        milestones=milestones,
        budget_data=budget_data,
        documents=documents,
        health=health,
        evm=evm,
        accessible_projects=get_user_accessible_projects(current_user)
    )


@bp.route('/add-project', methods=['GET', 'POST'])
@login_required
@role_required(['admin'])
def create_project():
    """Add new construction project"""
    form_data = {}  # Initialize empty form data
    
    if request.method == 'POST':
        try:
            data = request.form
            form_data = data.to_dict()  # Store form data for repopulation
            
            # Validate required fields
            if not data.get('name'):
                flash('Project name is required', 'error')
                return render_template('admin/create_project.html', form_data=form_data)
            
            # Handle date parsing with validation
            start_date = None
            end_date = None
            
            if data.get('start_date'):
                try:
                    start_date = datetime.strptime(data.get('start_date'), '%Y-%m-%d').date()
                except ValueError:
                    flash('Invalid start date format', 'error')
                    return render_template('admin/create_project.html', form_data=form_data)
            
            if data.get('end_date'):
                try:
                    end_date = datetime.strptime(data.get('end_date'), '%Y-%m-%d').date()
                except ValueError:
                    flash('Invalid end date format', 'error')
                    return render_template('admin/create_project.html', form_data=form_data)
            
            # Validate date logic
            if start_date and end_date and end_date < start_date:
                flash('End date cannot be before start date', 'error')
                return render_template('admin/create_project.html', form_data=form_data)
            
            # Handle budget parsing
            budget = 0.0
            if data.get('budget'):
                try:
                    budget = float(data.get('budget'))
                    if budget < 0:
                        flash('Budget cannot be negative', 'error')
                        return render_template('admin/create_project.html', form_data=form_data)
                except ValueError:
                    flash('Invalid budget amount', 'error')
                    return render_template('admin/create_project.html', form_data=form_data)
            
            # Handle contingency budget
            contingency_budget = 0.0
            if data.get('contingency_budget'):
                try:
                    contingency_budget = float(data.get('contingency_budget'))
                except ValueError:
                    contingency_budget = 0.0
            
            # Create new project with enhanced fields
            project = Project(
                name=data.get('name').strip(),
                description=data.get('description', '').strip(),
                start_date=start_date,
                end_date=end_date,
                status=data.get('status', 'planning'),
                budget=budget,
                created_at=datetime.utcnow(),
                updated_at=datetime.utcnow()
            )
            
            db.session.add(project)
            db.session.commit()
            
            flash(f'Project "{project.name}" created successfully!', 'success')
            return redirect(url_for('admin.projects'))
            
        except Exception as e:
            db.session.rollback()
            flash(f'Error creating project: {str(e)}', 'error')
            return render_template('admin/create_project.html', form_data=form_data)
    
    # GET request - show form
    try:
        return render_template('admin/create_project.html', form_data=form_data)
    except Exception as e:
        flash(f'Error loading form: {str(e)}', 'error')
        return redirect(url_for('admin.projects'))


@bp.route('/project/<int:project_id>/edit', methods=['GET', 'POST'])
@login_required
@role_required(['admin'])
def edit_project(project_id):
    """Edit project details."""
    project = Project.query.get_or_404(project_id)
    
    if request.method == 'POST':
        project.name = request.form.get('name', project.name).strip()
        project.description = request.form.get('description', project.description).strip()
        project.budget = float(request.form.get('budget', project.budget))
        project.status = request.form.get('status', project.status).strip()
        project.updated_at = datetime.utcnow()
        
        db.session.commit()
        flash(f'Project {project.name} updated successfully.', 'success')
        return redirect(url_for('admin.projects'))
    
    return render_template('admin/edit_project.html', project=project)


@bp.route('/project/<int:project_id>/team', methods=['GET'])
@login_required
@role_required(['admin', 'hr_manager'])
def manage_team(project_id):
    """Manage project team members."""
    from app.models import ProjectStaff
    
    project = Project.query.get_or_404(project_id)
    all_users = User.query.filter_by(is_active=True).all()
    
    # Get staff assignments with roles
    staff_assignments = ProjectStaff.query.filter_by(project_id=project_id).all()
    assigned_user_ids = [sa.user_id for sa in staff_assignments]
    available_users = [u for u in all_users if u.id not in assigned_user_ids]
    
    return render_template('admin/manage_team.html', project=project, all_users=all_users, 
                         staff_assignments=staff_assignments, available_users=available_users)


@bp.route('/project/<int:project_id>/add-team', methods=['POST'])
@login_required
@role_required(['admin', 'hr_manager'])
def add_team_member(project_id):
    """Add user to project team with role assignment."""
    from app.models import ProjectStaff, ProjectActivityLog, Notification
    from app.utils import send_email
    
    project = Project.query.get_or_404(project_id)
    user_id = request.form.get('user_id', type=int)
    user = User.query.get_or_404(user_id)
    role = request.form.get('role', 'Project Staff').strip()
    start_date = request.form.get('start_date')
    end_date = request.form.get('end_date')
    
    # Check if already assigned to this project
    existing = ProjectStaff.query.filter_by(user_id=user_id, project_id=project_id).first()
    is_new_assignment = not existing
    
    if existing:
        # Update existing assignment
        existing.role = role
        existing.is_active = True
        if start_date:
            from datetime import datetime as dt
            existing.start_date = dt.strptime(start_date, '%Y-%m-%d').date()
        if end_date:
            existing.end_date = dt.strptime(end_date, '%Y-%m-%d').date()
        flash(f'{user.name} role updated to {role}.', 'success')
        action_msg = f'Role updated to {role}'
    else:
        # Create new assignment
        staff_assignment = ProjectStaff(
            user_id=user_id,
            project_id=project_id,
            role=role,
            is_active=True
        )
        if start_date:
            from datetime import datetime as dt
            staff_assignment.start_date = dt.strptime(start_date, '%Y-%m-%d').date()
        if end_date:
            staff_assignment.end_date = dt.strptime(end_date, '%Y-%m-%d').date()
        db.session.add(staff_assignment)
        flash(f'{user.name} added to project as {role}.', 'success')
        action_msg = f'Added as {role}'
    
    # Log activity
    activity_log = ProjectActivityLog(
        project_id=project_id,
        user_id=current_user.id,
        action='staff_assigned' if is_new_assignment else 'staff_updated',
        description=f'{user.name} {action_msg}'
    )
    db.session.add(activity_log)
    
    # Create notification for the new/updated staff member
    notification = Notification(
        user_id=user_id,
        entity_type='project',
        entity_id=project_id,
        title=f'Project Assignment: {project.name}',
        message=f'You have been assigned to project "{project.name}" as {role}'
    )
    db.session.add(notification)
    
    # Send email notification
    try:
        email_subject = f'Project Assignment: {project.name}'
        email_body = f'''
Hello {user.name},

You have been assigned to the project "{project.name}" with the role: {role}

Project Details:
- Name: {project.name}
- Status: {project.status.upper() if project.status else 'Not Set'}
- Budget: ${project.budget:,.2f if project.budget else 'N/A'}

{f'Start Date: {action_msg.split("as ")[1] if "as " in action_msg else ""}' if start_date else ''}

Please log in to the system to view more details and start working on the project.

Best regards,
Project Management System
'''
        send_email(user.email, email_subject, email_body)
    except Exception as e:
        current_app.logger.error(f"Failed to send email to {user.email}: {str(e)}")
    
    db.session.commit()
    return redirect(url_for('admin.manage_team', project_id=project_id))


@bp.route('/project/<int:project_id>/remove-team', methods=['POST'])
@login_required
@role_required(['admin', 'hr_manager'])
def remove_team_member(project_id):
    """Remove user from project team."""
    from app.models import ProjectStaff, ProjectActivityLog, Notification
    from app.utils import send_email
    
    project = Project.query.get_or_404(project_id)
    user_id = request.form.get('user_id', type=int)
    
    staff_assignment = ProjectStaff.query.filter_by(user_id=user_id, project_id=project_id).first()
    
    if staff_assignment:
        user = User.query.get(user_id)
        db.session.delete(staff_assignment)
        
        # Log activity
        activity_log = ProjectActivityLog(
            project_id=project_id,
            user_id=current_user.id,
            action='staff_removed',
            description=f'{user.name} removed from project'
        )
        db.session.add(activity_log)
        
        # Create notification for the removed staff member
        notification = Notification(
            user_id=user_id,
            entity_type='project',
            entity_id=project_id,
            title=f'Removed from Project: {project.name}',
            message=f'You have been removed from project "{project.name}"'
        )
        db.session.add(notification)
        
        # Send email notification
        try:
            email_subject = f'Removed from Project: {project.name}'
            email_body = f'''
Hello {user.name},

You have been removed from the project "{project.name}".

If you have any questions, please contact your administrator.

Best regards,
Project Management System
'''
            send_email(user.email, email_subject, email_body)
        except Exception as e:
            current_app.logger.error(f"Failed to send email to {user.email}: {str(e)}")
        
        db.session.commit()
        flash(f'{user.name} removed from project team.', 'success')
    else:
        flash('Staff assignment not found.', 'error')
    
    return redirect(url_for('admin.manage_team', project_id=project_id))


@bp.route('/approval-logs', methods=['GET'])
@login_required
@role_required(['admin'])
def approval_logs():
    """View all approval logs for audit trail."""
    page = request.args.get('page', 1, type=int)
    entity_type = request.args.get('entity_type', None)
    
    query = ApprovalLog.query
    if entity_type:
        query = query.filter_by(entity_type=entity_type)
    
    logs = query.order_by(ApprovalLog.timestamp.desc()).paginate(page=page, per_page=30)
    
    # Count messages by status
    pending_count = ApprovalLog.query.filter_by(action='pending').count()
    approved_count = ApprovalLog.query.filter_by(action='approved').count()
    rejected_count = ApprovalLog.query.filter_by(action='rejected').count()
    
    return render_template('admin/approval_logs.html', logs=logs, selected_type=entity_type,
                         pending_count=pending_count, approved_count=approved_count,
                         rejected_count=rejected_count)


@bp.route('/approval-logs/<int:log_id>/details', methods=['GET'])
@login_required
@role_required(['admin'])
def approval_log_detail(log_id):
    """View detailed information about an approval log."""
    log = ApprovalLog.query.get_or_404(log_id)
    messages = ApprovalMessage.query.filter_by(approval_log_id=log_id).order_by(
        ApprovalMessage.created_at.desc()
    ).all()
    
    # Get list of users for message recipients
    all_users = User.query.filter_by(is_active=True).all()
    
    return render_template('admin/approval_log_detail.html', log=log, messages=messages, 
                         all_users=all_users, now=datetime.utcnow())


@bp.route('/approval-logs/<int:log_id>/send-message', methods=['POST'])
@login_required
@role_required(['admin'])
def send_approval_message(log_id):
    """Send a message regarding an approval log to recipients."""
    log = ApprovalLog.query.get_or_404(log_id)
    
    try:
        recipient_id = request.form.get('recipient_id', type=int)
        subject = request.form.get('subject', '').strip()
        message_text = request.form.get('message', '').strip()
        message_type = request.form.get('message_type', 'status_update').strip()
        
        if not all([recipient_id, subject, message_text]):
            flash('All fields are required.', 'warning')
            return redirect(url_for('admin.approval_log_detail', log_id=log_id))
        
        # Validate recipient exists
        recipient = User.query.get_or_404(recipient_id)
        
        # Create message
        approval_msg = ApprovalMessage(
            approval_log_id=log_id,
            sender_id=current_user.id,
            recipient_id=recipient_id,
            subject=subject,
            message=message_text,
            message_type=message_type
        )
        
        db.session.add(approval_msg)
        
        # Create notification for recipient
        from app.models import Notification
        notification = Notification(
            user_id=recipient_id,
            entity_type='ApprovalLog',
            entity_id=log_id,
            title=f'New Message: {subject}',
            message=f'{current_user.name} sent you a message regarding {log.entity_type} #{log.entity_id}',
            read=False
        )
        db.session.add(notification)
        
        # Send email notification with business logic
        try:
            from app.utils import send_email
            email_subject = f'[{log.entity_type}] {subject}'
            email_body = f'''
Hello {recipient.name},

You have received a message regarding {log.entity_type} #{log.entity_id}.

Subject: {subject}
Type: {message_type.replace('_', ' ').title()}

Message:
{message_text}

---
Sent by: {current_user.name}
Date: {datetime.utcnow().strftime('%d %b %Y %H:%M')}

Action Required:
1. Log in to review the full details
2. Take appropriate action based on the message type
3. Respond to acknowledge receipt

Entity Details:
- Type: {log.entity_type}
- ID: {log.entity_id}
- Action: {log.action.title()}
- Original Actor: {log.actor.name if log.actor else 'Unknown'}
- Status: {log.action.upper()}

{f'Comments: {log.comment}' if log.comment else ''}

This is an automated message from the approval system. Please do not reply to this email.
'''
            send_email(recipient.email, email_subject, email_body)
            flash(f'Message sent to {recipient.name} successfully.', 'success')
        except Exception as e:
            flash(f'Message created but email notification failed: {str(e)}', 'warning')
            current_app.logger.error(f'Email notification failed: {str(e)}')
        
        db.session.commit()
        return redirect(url_for('admin.approval_log_detail', log_id=log_id))
        
    except Exception as e:
        db.session.rollback()
        flash(f'Error sending message: {str(e)}', 'danger')
        current_app.logger.error(f'Message sending error: {str(e)}')
        return redirect(url_for('admin.approval_log_detail', log_id=log_id))


@bp.route('/system-settings', methods=['GET'])
@login_required
@role_required(['admin'])
def system_settings():
    """System-wide settings (placeholder for configuration)."""
    return render_template('admin/system_settings.html')


@bp.route('/api/dashboard-stats', methods=['GET'])
@login_required
@role_required(['admin'])
def api_dashboard_stats():
    """API endpoint for real-time dashboard stats."""
    from app.models import MaterialRequest, PurchaseOrder, QCInspection, PaymentRequest, ApprovalState
    
    stats = {
        'total_users': User.query.count(),
        'active_users': User.query.filter_by(is_active=True).count(),
        'total_projects': Project.query.count(),
        'active_projects': Project.query.filter_by(status='active').count(),
        'pending_items': {
            'pr': MaterialRequest.query.filter_by(approval_state=ApprovalState.PENDING).count(),
            'po': PurchaseOrder.query.filter_by(approval_state=ApprovalState.PENDING).count(),
            'qc': QCInspection.query.filter_by(approval_state=ApprovalState.PENDING).count(),
            'payment': PaymentRequest.query.filter_by(approval_state=ApprovalState.PENDING).count(),
        },
        'recent_approvals': ApprovalLog.query.count(),
    }
    return jsonify(stats)


# Module Monitoring Routes
@bp.route('/hr-activities', methods=['GET'])
@login_required
@role_required(['admin', 'hr_manager'])
def hr_activities():
    """Monitor HR activities - employee management, payroll, recruitment."""
    page = request.args.get('page', 1, type=int)
    
    # Get HR related users
    hr_staff = User.query.filter(User.role.in_(['hr_manager', 'hr_staff'])).all()
    hr_users = User.query.paginate(page=page, per_page=20)
    
    # Get recent activities
    recent_logs = ApprovalLog.query.order_by(ApprovalLog.timestamp.desc()).limit(20).all()
    
    return render_template(
        'admin/hr_activities.html',
        hr_staff=hr_staff,
        hr_users=hr_users,
        recent_logs=recent_logs,
        module_name='Human Resources'
    )


@bp.route('/finance-activities', methods=['GET'])
@login_required
@role_required(['admin', 'finance_manager'])
def finance_activities():
    """Monitor Finance activities - budget, invoicing, payments."""
    try:
        from app.models import PaymentRequest, PaymentRecord, ApprovalState, Expense, BankReconciliation
        from sqlalchemy import func
        
        # Get finance related users with finance-related roles
        finance_staff = User.query.filter(User.is_active == True).all()
        finance_staff = [u for u in finance_staff if u.has_any_role(['finance_manager', 'accounts_payable', 'hq_finance'])]
        
        # Get payment requests with detailed breakdown
        pending_payments = PaymentRequest.query.filter(
            PaymentRequest.approval_state == ApprovalState.PENDING
        ).all()
        
        approved_payments = PaymentRequest.query.filter(
            PaymentRequest.approval_state == ApprovalState.APPROVED
        ).all()
        
        # Get recent activities (finance-related)
        recent_logs = ApprovalLog.query.filter(
            ApprovalLog.entity_type.in_(['payroll', 'payment', 'expense', 'invoice'])
        ).order_by(ApprovalLog.timestamp.desc()).limit(20).all()
        
        # Calculate financial stats from actual data
        active_projects = Project.query.filter(Project.status == 'active').all()
        total_budget = sum(float(p.budget or 0) for p in active_projects) if active_projects else 0
        
        # Payment stats using proper SQLAlchemy syntax
        total_approved = db.session.query(func.coalesce(func.sum(PaymentRequest.invoice_amount), 0)).filter(
            PaymentRequest.approval_state == ApprovalState.APPROVED
        ).scalar() or 0
        total_approved_amount = float(total_approved)
        
        total_pending = db.session.query(func.coalesce(func.sum(PaymentRequest.invoice_amount), 0)).filter(
            PaymentRequest.approval_state == ApprovalState.PENDING
        ).scalar() or 0
        total_pending_amount = float(total_pending)
        
        # Disbursement stats
        total_disbursed = db.session.query(func.coalesce(func.sum(PaymentRecord.amount_paid), 0)).scalar() or 0
        total_disbursed = float(total_disbursed)
        
        # Expense stats
        total_expenses = db.session.query(func.coalesce(func.sum(Expense.amount), 0)).scalar() or 0
        total_expenses = float(total_expenses)
        
        pending_expenses = db.session.query(func.coalesce(func.sum(Expense.amount), 0)).filter(
            Expense.status == 'pending'
        ).scalar() or 0
        pending_expenses = float(pending_expenses)
        
        approved_expenses = db.session.query(func.coalesce(func.sum(Expense.amount), 0)).filter(
            Expense.status == 'approved'
        ).scalar() or 0
        approved_expenses = float(approved_expenses)
        
        # Bank balance
        total_bank_balance = db.session.query(func.coalesce(func.sum(BankReconciliation.balance), 0)).scalar() or 0
        total_bank_balance = float(total_bank_balance)
        
        # Calculate budget utilization percentage
        total_spent = total_approved_amount + total_disbursed
        budget_utilized = (total_spent / total_budget * 100) if total_budget > 0 else 0
        
        return render_template(
            'admin/finance_activities.html',
            finance_staff=finance_staff,
            pending_payments=pending_payments,
            approved_payments=approved_payments,
            recent_logs=recent_logs,
            total_budget=total_budget,
            total_approved_amount=total_approved_amount,
            total_pending_amount=total_pending_amount,
            total_disbursed=total_disbursed,
            total_expenses=total_expenses,
            pending_expenses=pending_expenses,
            approved_expenses=approved_expenses,
            total_bank_balance=total_bank_balance,
            budget_utilized=round(budget_utilized, 1),
            pending_count=len(pending_payments),
            approved_count=len(approved_payments),
            module_name='Finance & Accounting'
        )
    except Exception as e:
        import traceback
        current_app.logger.error(f"Finance activities error: {str(e)}\n{traceback.format_exc()}")
        flash(f'Error loading finance activities: {str(e)}', 'error')
        return redirect(url_for('admin.dashboard'))


@bp.route('/budget-reports', methods=['GET'])
@login_required
@role_required(['admin', 'finance_manager'])
def budget_reports():
    """Budget Reports - detailed financial analysis and budget tracking."""
    try:
        from app.models import Project, Expense
        from sqlalchemy import func
        
        # Get all projects with budget information
        projects = Project.query.all()
        
        # Calculate budget statistics
        projects_with_stats = []
        for project in projects:
            spent = db.session.query(func.coalesce(func.sum(Expense.amount), 0)).filter(
                Expense.project_id == project.id
            ).scalar() or 0
            
            budget = float(project.budget or 0)
            spent = float(spent)
            remaining = budget - spent
            utilization = (spent / budget * 100) if budget > 0 else 0
            
            projects_with_stats.append({
                'id': project.id,
                'name': project.name,
                'budget': budget,
                'spent': spent,
                'remaining': remaining,
                'utilization': round(utilization, 1),
                'status': project.status
            })
        
        # Summary statistics
        total_budget = sum(p['budget'] for p in projects_with_stats)
        total_spent = sum(p['spent'] for p in projects_with_stats)
        total_remaining = total_budget - total_spent
        overall_utilization = (total_spent / total_budget * 100) if total_budget > 0 else 0
        
        return render_template(
            'admin/budget_reports.html',
            projects=projects_with_stats,
            total_budget=total_budget,
            total_spent=total_spent,
            total_remaining=total_remaining,
            overall_utilization=round(overall_utilization, 1)
        )
    except Exception as e:
        import traceback
        current_app.logger.error(f"Budget reports error: {str(e)}\n{traceback.format_exc()}")
        flash(f'Error loading budget reports: {str(e)}', 'error')
        return redirect(url_for('admin.finance_activities'))


@bp.route('/invoice-management', methods=['GET'])
@login_required
@role_required(['admin', 'finance_manager'])
def invoice_management():
    """Invoice Management - track invoices and payment receipts."""
    try:
        from app.models import PaymentRequest, PaymentRecord, ApprovalState, PurchaseOrder
        from sqlalchemy import func
        
        # Get all payment requests with status breakdown
        all_payments = PaymentRequest.query.all()
        
        # Group by status
        draft_payments = PaymentRequest.query.filter(
            PaymentRequest.approval_state == ApprovalState.DRAFT
        ).all()
        pending_payments = PaymentRequest.query.filter(
            PaymentRequest.approval_state == ApprovalState.PENDING
        ).all()
        approved_payments = PaymentRequest.query.filter(
            PaymentRequest.approval_state == ApprovalState.APPROVED
        ).all()
        
        # Get paid records
        paid_records = PaymentRecord.query.all()
        
        # Calculate payment statistics
        total_invoices = db.session.query(func.coalesce(func.sum(PaymentRequest.invoice_amount), 0)).scalar() or 0
        total_paid = db.session.query(func.coalesce(func.sum(PaymentRecord.amount_paid), 0)).scalar() or 0
        pending_amount = db.session.query(func.coalesce(func.sum(PaymentRequest.invoice_amount), 0)).filter(
            PaymentRequest.approval_state == ApprovalState.PENDING
        ).scalar() or 0
        
        total_invoices = float(total_invoices)
        total_paid = float(total_paid)
        pending_amount = float(pending_amount)
        
        return render_template(
            'admin/invoice_management.html',
            all_payments=all_payments,
            draft_payments=draft_payments,
            pending_payments=pending_payments,
            approved_payments=approved_payments,
            paid_records=paid_records,
            total_invoices=total_invoices,
            total_paid=total_paid,
            pending_amount=pending_amount,
            draft_count=len(draft_payments),
            pending_count=len(pending_payments),
            approved_count=len(approved_payments),
            paid_count=len(paid_records)
        )
    except Exception as e:
        import traceback
        current_app.logger.error(f"Invoice management error: {str(e)}\n{traceback.format_exc()}")
        flash(f'Error loading invoice management: {str(e)}', 'error')
        return redirect(url_for('admin.finance_activities'))


@bp.route('/bank-reconciliation', methods=['GET'])
@login_required
@role_required(['admin', 'finance_manager'])
def bank_reconciliation():
    """Bank Reconciliation - manage and reconcile bank accounts."""
    try:
        from app.models import BankAccount, PaymentRecord
        from sqlalchemy import func
        
        # Get all bank accounts
        bank_accounts = BankAccount.query.all()
        
        # Calculate bank statistics
        total_balance = db.session.query(func.coalesce(func.sum(BankAccount.balance), 0)).scalar() or 0
        total_balance = float(total_balance)
        
        # Get recent transactions
        recent_payments = PaymentRecord.query.order_by(
            PaymentRecord.payment_date.desc()
        ).limit(20).all()
        
        # Account statistics
        account_count = len(bank_accounts)
        
        return render_template(
            'admin/bank_reconciliation.html',
            bank_accounts=bank_accounts,
            total_balance=total_balance,
            recent_payments=recent_payments,
            account_count=account_count
        )
    except Exception as e:
        import traceback
        current_app.logger.error(f"Bank reconciliation error: {str(e)}\n{traceback.format_exc()}")
        flash(f'Error loading bank reconciliation: {str(e)}', 'error')
        return redirect(url_for('admin.finance_activities'))


@bp.route('/cash-flow', methods=['GET'])
@login_required
@role_required(['admin', 'finance_manager'])
def cash_flow():
    """Cash Flow - monitor cash positions and flow."""
    try:
        from app.models import BankReconciliation, PaymentRequest, PaymentRecord, Expense, ApprovalState
        from sqlalchemy import func
        from datetime import datetime, timedelta
        
        # Current cash position
        total_cash = db.session.query(func.coalesce(func.sum(BankReconciliation.balance), 0)).scalar() or 0
        total_cash = float(total_cash)
        
        # Cash inflows (approved payments ready to be disbursed)
        pending_inflows = db.session.query(func.coalesce(func.sum(PaymentRequest.invoice_amount), 0)).filter(
            PaymentRequest.approval_state == ApprovalState.PENDING
        ).scalar() or 0
        pending_inflows = float(pending_inflows)
        
        # Cash outflows
        total_outflows = db.session.query(func.coalesce(func.sum(PaymentRecord.amount_paid), 0)).scalar() or 0
        total_outflows = float(total_outflows)
        
        # Monthly cash flow data (last 6 months)
        six_months_ago = datetime.utcnow() - timedelta(days=180)
        monthly_disbursements = db.session.query(
            func.strftime('%Y-%m', PaymentRecord.payment_date).label('month'),
            func.coalesce(func.sum(PaymentRecord.amount_paid), 0).label('amount')
        ).filter(PaymentRecord.payment_date >= six_months_ago).group_by('month').all()
        
        # Outstanding payments
        outstanding = db.session.query(func.coalesce(func.sum(PaymentRequest.invoice_amount), 0)).filter(
            PaymentRequest.approval_state.in_([ApprovalState.PENDING, ApprovalState.DRAFT])
        ).scalar() or 0
        outstanding = float(outstanding)
        
        # Projected cash position
        projected_cash = total_cash - outstanding
        
        return render_template(
            'admin/cash_flow.html',
            total_cash=total_cash,
            pending_inflows=pending_inflows,
            total_outflows=total_outflows,
            outstanding=outstanding,
            projected_cash=projected_cash,
            monthly_data=monthly_disbursements
        )
    except Exception as e:
        import traceback
        current_app.logger.error(f"Cash flow error: {str(e)}\n{traceback.format_exc()}")
        flash(f'Error loading cash flow: {str(e)}', 'error')
        return redirect(url_for('admin.finance_activities'))


@bp.route('/expense-reports', methods=['GET'])
@login_required
@role_required(['admin', 'finance_manager'])
def expense_reports():
    """Expense Reports - view and analyze all expenses."""
    try:
        from app.models import Expense, Project
        from sqlalchemy import func
        from datetime import datetime, timedelta
        
        # Get all expenses
        expenses = Expense.query.order_by(Expense.date.desc()).all()
        
        # Calculate statistics
        total_expenses = db.session.query(func.coalesce(func.sum(Expense.amount), 0)).scalar() or 0
        total_expenses = float(total_expenses)
        
        # Expenses by status
        approved_expenses = db.session.query(func.coalesce(func.sum(Expense.amount), 0)).filter(
            Expense.status == 'approved'
        ).scalar() or 0
        approved_expenses = float(approved_expenses)
        
        pending_expenses = db.session.query(func.coalesce(func.sum(Expense.amount), 0)).filter(
            Expense.status == 'pending'
        ).scalar() or 0
        pending_expenses = float(pending_expenses)
        
        rejected_expenses = db.session.query(func.coalesce(func.sum(Expense.amount), 0)).filter(
            Expense.status == 'rejected'
        ).scalar() or 0
        rejected_expenses = float(rejected_expenses)
        
        # Expenses by category
        category_stats = db.session.query(
            Expense.category,
            func.coalesce(func.sum(Expense.amount), 0).label('total'),
            func.count(Expense.id).label('count')
        ).group_by(Expense.category).all()
        
        # Recent 30 days
        thirty_days_ago = datetime.utcnow() - timedelta(days=30)
        recent_expenses = db.session.query(func.coalesce(func.sum(Expense.amount), 0)).filter(
            Expense.date >= thirty_days_ago
        ).scalar() or 0
        recent_expenses = float(recent_expenses)
        
        return render_template(
            'admin/expense_reports.html',
            expenses=expenses,
            total_expenses=total_expenses,
            approved_expenses=approved_expenses,
            pending_expenses=pending_expenses,
            rejected_expenses=rejected_expenses,
            category_stats=category_stats,
            recent_expenses=recent_expenses
        )
    except Exception as e:
        import traceback
        current_app.logger.error(f"Expense reports error: {str(e)}\n{traceback.format_exc()}")
        flash(f'Error loading expense reports: {str(e)}', 'error')
        return redirect(url_for('admin.finance_activities'))


@bp.route('/payment-requests', methods=['GET'])
@login_required
@role_required(['admin', 'finance_manager'])
def view_payment_requests():
    """View Payment Requests - display all payment requests."""
    try:
        from app.models import PaymentRequest, ApprovalState, PurchaseOrder
        from sqlalchemy import func
        
        # Get all payment requests
        payment_requests = PaymentRequest.query.order_by(
            PaymentRequest.created_at.desc()
        ).all()
        
        # Calculate statistics
        total_invoiced = db.session.query(func.coalesce(func.sum(PaymentRequest.invoice_amount), 0)).scalar() or 0
        total_invoiced = float(total_invoiced)
        
        # Count by status
        draft_count = len([p for p in payment_requests if p.approval_state == ApprovalState.DRAFT])
        pending_count = len([p for p in payment_requests if p.approval_state == ApprovalState.PENDING])
        approved_count = len([p for p in payment_requests if p.approval_state == ApprovalState.APPROVED])
        
        # Amount by status
        draft_amount = db.session.query(func.coalesce(func.sum(PaymentRequest.invoice_amount), 0)).filter(
            PaymentRequest.approval_state == ApprovalState.DRAFT
        ).scalar() or 0
        draft_amount = float(draft_amount)
        
        pending_amount = db.session.query(func.coalesce(func.sum(PaymentRequest.invoice_amount), 0)).filter(
            PaymentRequest.approval_state == ApprovalState.PENDING
        ).scalar() or 0
        pending_amount = float(pending_amount)
        
        approved_amount = db.session.query(func.coalesce(func.sum(PaymentRequest.invoice_amount), 0)).filter(
            PaymentRequest.approval_state == ApprovalState.APPROVED
        ).scalar() or 0
        approved_amount = float(approved_amount)
        
        return render_template(
            'admin/payment_requests.html',
            payment_requests=payment_requests,
            total_invoiced=total_invoiced,
            draft_count=draft_count,
            pending_count=pending_count,
            approved_count=approved_count,
            draft_amount=draft_amount,
            pending_amount=pending_amount,
            approved_amount=approved_amount
        )
    except Exception as e:
        import traceback
        current_app.logger.error(f"Payment requests error: {str(e)}\n{traceback.format_exc()}")
        flash(f'Error loading payment requests: {str(e)}', 'error')
        return redirect(url_for('admin.finance_activities'))


@bp.route('/expense-receipts', methods=['GET'])
@login_required
@role_required(['admin', 'finance_manager'])
def expense_receipts():
    """Expense Receipts - manage and view expense receipts and documentation."""
    try:
        from app.models import Expense, Project
        from sqlalchemy import func
        
        # Get all expenses (which represent receipts)
        receipts = Expense.query.order_by(Expense.date.desc()).all()
        
        # Calculate statistics
        total_receipts = len(receipts)
        total_amount = db.session.query(func.coalesce(func.sum(Expense.amount), 0)).scalar() or 0
        total_amount = float(total_amount)
        
        # Receipts by status
        approved_count = len([r for r in receipts if r.status == 'approved'])
        pending_count = len([r for r in receipts if r.status == 'pending'])
        rejected_count = len([r for r in receipts if r.status == 'rejected'])
        
        # Receipts by category
        categories = db.session.query(
            Expense.category,
            func.count(Expense.id).label('count')
        ).group_by(Expense.category).all()
        
        return render_template(
            'admin/expense_receipts.html',
            receipts=receipts,
            total_receipts=total_receipts,
            total_amount=total_amount,
            approved_count=approved_count,
            pending_count=pending_count,
            rejected_count=rejected_count,
            categories=categories
        )
    except Exception as e:
        import traceback
        current_app.logger.error(f"Expense receipts error: {str(e)}\n{traceback.format_exc()}")
        flash(f'Error loading expense receipts: {str(e)}', 'error')
        return redirect(url_for('admin.finance_activities'))


@bp.route('/cost-control-activities', methods=['GET'])
@login_required
@role_required(['admin', 'cost_control_manager'])
def cost_control_activities():
    """Monitor Cost Control activities - budget tracking, variance analysis."""
    page = request.args.get('page', 1, type=int)
    from app.models import MaterialRequest, PaymentRequest, ApprovalState
    
    # Get cost control related users
    cc_staff = User.query.filter(User.role.in_(['cost_control_manager', 'cost_control_staff'])).all()
    
    # Get projects for budget monitoring
    projects = Project.query.paginate(page=page, per_page=15)
    
    # Get material requests (cost related)
    material_requests = MaterialRequest.query.all()
    
    # Get recent activities
    recent_logs = ApprovalLog.query.order_by(ApprovalLog.timestamp.desc()).limit(20).all()
    
    # Calculate budget metrics
    total_budget = sum(float(p.budget or 0) for p in Project.query.all())
    total_spent = sum(float(pr.total_amount or 0) for pr in PaymentRequest.query.filter_by(approval_state=ApprovalState.APPROVED).all())
    
    return render_template(
        'admin/cost_control_activities.html',
        cc_staff=cc_staff,
        projects=projects,
        material_requests=material_requests,
        recent_logs=recent_logs,
        total_budget=total_budget,
        total_spent=total_spent,
        module_name='Cost Control'
    )


@bp.route('/api/cost-analysis', methods=['GET'])
@login_required
@role_required(['admin', 'cost_control_manager'])
def api_cost_analysis():
    """API endpoint for cost analysis data."""
    try:
        from app.models import PaymentRequest, PurchaseOrder, ApprovalState
        from sqlalchemy import func
        
        projects = Project.query.all()
        total_budget = sum(float(p.budget or 0) for p in projects)
        
        # Join PaymentRequest through PurchaseOrder to get spending by project
        total_spent = db.session.query(func.sum(PaymentRequest.invoice_amount)).join(
            PurchaseOrder, PaymentRequest.po_id == PurchaseOrder.id
        ).filter(PaymentRequest.approval_state == ApprovalState.APPROVED).scalar() or 0
        total_spent = float(total_spent)
        
        project_data = []
        for project in projects:
            # Get spending for this project by joining through PurchaseOrder
            project_spent = db.session.query(func.sum(PaymentRequest.invoice_amount)).join(
                PurchaseOrder, PaymentRequest.po_id == PurchaseOrder.id
            ).filter(
                PurchaseOrder.project_id == project.id,
                PaymentRequest.approval_state == ApprovalState.APPROVED
            ).scalar() or 0
            project_spent = float(project_spent)
            
            project_data.append({
                'id': project.id,
                'name': project.name,
                'budget': float(project.budget or 0),
                'spent': project_spent,
                'percentage': (project_spent / float(project.budget)) * 100 if project.budget else 0
            })
        
        return jsonify({
            'total_budget': total_budget,
            'total_spent': total_spent,
            'remaining': total_budget - total_spent,
            'projects': project_data
        })
    except Exception as e:
        print(f"Error in api_cost_analysis: {str(e)}")
        return jsonify({'error': str(e)}), 500


@bp.route('/api/variance-report', methods=['GET'])
@login_required
@role_required(['admin', 'cost_control_manager'])
def api_variance_report():
    """API endpoint for variance report data."""
    try:
        from app.models import PaymentRequest, PurchaseOrder, ApprovalState
        from sqlalchemy import func
        
        projects = Project.query.all()
        total_budget = sum(float(p.budget or 0) for p in projects)
        
        # Join PaymentRequest through PurchaseOrder to get total spending
        total_spent = db.session.query(func.sum(PaymentRequest.invoice_amount)).join(
            PurchaseOrder, PaymentRequest.po_id == PurchaseOrder.id
        ).filter(PaymentRequest.approval_state == ApprovalState.APPROVED).scalar() or 0
        total_spent = float(total_spent)
        remaining = total_budget - total_spent
        
        project_data = []
        for project in projects:
            # Get spending for this project by joining through PurchaseOrder
            project_spent = db.session.query(func.sum(PaymentRequest.invoice_amount)).join(
                PurchaseOrder, PaymentRequest.po_id == PurchaseOrder.id
            ).filter(
                PurchaseOrder.project_id == project.id,
                PaymentRequest.approval_state == ApprovalState.APPROVED
            ).scalar() or 0
            project_spent = float(project_spent)
            
            variance = float(project.budget or 0) - project_spent
            project_data.append({
                'id': project.id,
                'name': project.name,
                'budget': float(project.budget or 0),
                'spent': project_spent,
                'variance': variance,
                'status': 'Under Budget' if variance > 0 else 'Over Budget'
            })
        
        return jsonify({
            'total_budget': total_budget,
            'remaining': remaining,
            'projects': project_data
        })
    except Exception as e:
        print(f"Error in api_variance_report: {str(e)}")
        return jsonify({'error': str(e)}), 500


@bp.route('/api/forecasting', methods=['GET'])
@login_required
@role_required(['admin', 'cost_control_manager'])
def api_forecasting():
    """API endpoint for cost forecasting data."""
    try:
        from app.models import PaymentRequest, PurchaseOrder, ApprovalState
        from sqlalchemy import func
        
        projects = Project.query.all()
        total_budget = sum(float(p.budget or 0) for p in projects)
        
        # Join PaymentRequest through PurchaseOrder to get total spending
        total_spent = db.session.query(func.sum(PaymentRequest.invoice_amount)).join(
            PurchaseOrder, PaymentRequest.po_id == PurchaseOrder.id
        ).filter(PaymentRequest.approval_state == ApprovalState.APPROVED).scalar() or 0
        total_spent = float(total_spent)
        
        # Calculate growth rate (8% estimated increase)
        growth_rate = 0.08
        projected_cost = total_spent * (1 + growth_rate)
        
        project_data = []
        for project in projects:
            # Get spending for this project by joining through PurchaseOrder
            project_spent = db.session.query(func.sum(PaymentRequest.invoice_amount)).join(
                PurchaseOrder, PaymentRequest.po_id == PurchaseOrder.id
            ).filter(
                PurchaseOrder.project_id == project.id,
                PaymentRequest.approval_state == ApprovalState.APPROVED
            ).scalar() or 0
            project_spent = float(project_spent)
            
            projected = project_spent * (1 + growth_rate)
            project_data.append({
                'id': project.id,
                'name': project.name,
                'current': float(project.budget or 0),
                'spent': project_spent,
                'projected': projected
            })
        
        return jsonify({
            'total_budget': total_budget,
            'current_spent': total_spent,
            'projected_cost': projected_cost,
            'growth_rate': growth_rate,
            'projects': project_data
        })
    except Exception as e:
        print(f"Error in api_forecasting: {str(e)}")
        return jsonify({'error': str(e)}), 500


@bp.route('/api/cost-reports', methods=['GET'])
@login_required
@role_required(['admin', 'cost_control_manager'])
def api_cost_reports():
    """API endpoint for cost reports data."""
    try:
        from app.models import PaymentRequest, PurchaseOrder, ApprovalState
        from sqlalchemy import func
        
        projects = Project.query.all()
        total_budget = sum(float(p.budget or 0) for p in projects)
        
        # Join PaymentRequest through PurchaseOrder to get total spending
        total_spent = db.session.query(func.sum(PaymentRequest.invoice_amount)).join(
            PurchaseOrder, PaymentRequest.po_id == PurchaseOrder.id
        ).filter(PaymentRequest.approval_state == ApprovalState.APPROVED).scalar() or 0
        total_spent = float(total_spent)
        
        project_data = []
        for project in projects:
            # Get spending for this project by joining through PurchaseOrder
            project_spent = db.session.query(func.sum(PaymentRequest.invoice_amount)).join(
                PurchaseOrder, PaymentRequest.po_id == PurchaseOrder.id
            ).filter(
                PurchaseOrder.project_id == project.id,
                PaymentRequest.approval_state == ApprovalState.APPROVED
            ).scalar() or 0
            project_spent = float(project_spent)
            
            remaining = float(project.budget or 0) - project_spent
            project_data.append({
                'id': project.id,
                'name': project.name,
                'budget': float(project.budget or 0),
                'spent': project_spent,
                'remaining': remaining
            })
        
        return jsonify({
            'total_budget': total_budget,
            'total_spent': total_spent,
            'remaining': total_budget - total_spent,
            'projects': project_data
        })
    except Exception as e:
        print(f"Error in api_cost_reports: {str(e)}")
        return jsonify({'error': str(e)}), 500


@bp.route('/procurement-activities', methods=['GET'])
@login_required
@role_required(['admin', 'procurement_manager'])
def procurement_activities():
    """Monitor Procurement activities - purchase orders, vendors, materials."""
    page = request.args.get('page', 1, type=int)
    from app.models import PurchaseOrder, MaterialRequest, ApprovalState, Vendor
    
    # Get procurement related users
    procurement_staff = User.query.filter(User.role.in_(['procurement_manager', 'procurement_staff'])).all()
    
    # Get purchase orders
    pending_pos = PurchaseOrder.query.filter_by(approval_state=ApprovalState.PENDING).all()
    approved_pos = PurchaseOrder.query.filter_by(approval_state=ApprovalState.APPROVED).all()
    
    # Get material requests
    material_requests = MaterialRequest.query.paginate(page=page, per_page=20)
    
    # Get recent activities
    recent_logs = ApprovalLog.query.order_by(ApprovalLog.timestamp.desc()).limit(20).all()
    
    # Get vendor count
    vendor_count = Vendor.query.count()
    
    return render_template(
        'admin/procurement_activities.html',
        procurement_staff=procurement_staff,
        pending_pos=pending_pos,
        approved_pos=approved_pos,
        material_requests=material_requests,
        recent_logs=recent_logs,
        vendor_count=vendor_count,
        module_name='Procurement'
    )


@bp.route('/api/procurement/purchase-orders')
@login_required
@role_required(['admin', 'procurement_manager'])
def api_procurement_purchase_orders():
    """API endpoint for purchase orders data."""
    try:
        from app.models import PurchaseOrder, Vendor, ApprovalState
        
        # Get all purchase orders with vendor info
        pos = db.session.query(
            PurchaseOrder,
            Vendor.name.label('vendor_name')
        ).outerjoin(Vendor, PurchaseOrder.vendor_id == Vendor.id).all()
        
        po_data = []
        for po, vendor_name in pos:
            po_data.append({
                'id': po.id,
                'po_number': po.po_number,
                'vendor': vendor_name or 'N/A',
                'amount': float(po.total_amount or 0),
                'status': po.approval_state.value if po.approval_state else 'DRAFT',
                'created_date': po.created_at.strftime('%b %d, %Y') if po.created_at else 'N/A',
                'items_count': len(po.items) if po.items else 0
            })
        
        # Calculate totals
        total_pending = sum(1 for po, _ in pos if po.approval_state == ApprovalState.PENDING)
        total_approved = sum(1 for po, _ in pos if po.approval_state == ApprovalState.APPROVED)
        total_amount = sum(float(po.total_amount or 0) for po, _ in pos)
        
        return jsonify({
            'purchase_orders': po_data,
            'total_pending': total_pending,
            'total_approved': total_approved,
            'total_amount': total_amount
        })
    except Exception as e:
        print(f"Error in api_procurement_purchase_orders: {str(e)}")
        return jsonify({'error': str(e)}), 500


@bp.route('/api/procurement/vendors')
@login_required
@role_required(['admin', 'procurement_manager'])
def api_procurement_vendors():
    """API endpoint for vendor management data."""
    try:
        from app.models import Vendor, PurchaseOrder
        from sqlalchemy import func
        
        # Get all vendors with PO count
        vendors = Vendor.query.all()
        
        vendor_data = []
        for vendor in vendors:
            po_count = PurchaseOrder.query.filter_by(vendor_id=vendor.id).count()
            total_spent = db.session.query(func.sum(PurchaseOrder.total_amount)).filter_by(vendor_id=vendor.id).scalar() or 0

            contact_person = (
                getattr(vendor, 'contact_person', None)
                or getattr(vendor, 'name', None)
                or 'N/A'
            )
            vendor_data.append({
                'id': vendor.id,
                'name': vendor.name,
                'contact_person': contact_person,
                'email': vendor.email or 'N/A',
                'phone': vendor.phone or 'N/A',
                'address': vendor.address or 'N/A',
                'city': getattr(vendor, 'city', None) or 'N/A',
                'registration_number': getattr(vendor, 'registration_number', None) or 'N/A',
                'po_count': po_count,
                'total_spent': float(total_spent)
            })
        
        return jsonify({
            'vendors': vendor_data,
            'total_vendors': len(vendor_data),
            'active_vendors': sum(1 for v in vendors if v.is_active)
        })
    except Exception as e:
        current_app.logger.error(f"Error in api_procurement_vendors: {str(e)}", exc_info=True)
        return jsonify({'error': str(e)}), 500


@bp.route('/api/procurement/deliveries')
@login_required
@role_required(['admin', 'procurement_manager'])
def api_procurement_deliveries():
    """API endpoint for delivery tracking data."""
    try:
        from app.models import Delivery, PurchaseOrder, ApprovalState
        
        # Get all deliveries with PO info
        deliveries = Delivery.query.all()
        
        delivery_data = []
        for delivery in deliveries:
            po = PurchaseOrder.query.get(delivery.po_id)
            delivery_data.append({
                'id': delivery.id,
                'grn_number': delivery.grn_number or 'N/A',
                'po_number': po.po_number if po else 'N/A',
                'received_date': delivery.received_at.strftime('%b %d, %Y') if delivery.received_at else 'N/A',
                'quantity_received': float(delivery.total_quantity_received or 0),
                'status': delivery.approval_state.value if delivery.approval_state else 'PENDING'
            })
        
        pending_count = sum(1 for d in deliveries if d.approval_state == ApprovalState.PENDING)
        completed_count = sum(1 for d in deliveries if d.approval_state == ApprovalState.APPROVED)
        
        return jsonify({
            'deliveries': delivery_data,
            'total_deliveries': len(delivery_data),
            'pending_deliveries': pending_count,
            'completed_deliveries': completed_count
        })
    except Exception as e:
        print(f"Error in api_procurement_deliveries: {str(e)}")
        return jsonify({'error': str(e)}), 500


@bp.route('/api/procurement/reports')
@login_required
@role_required(['admin', 'procurement_manager'])
def api_procurement_reports():
    """API endpoint for procurement reports data."""
    try:
        from app.models import PurchaseOrder, Vendor, ApprovalState, Delivery
        from sqlalchemy import func
        
        # Get procurement statistics
        total_pos = PurchaseOrder.query.count()
        total_vendors = Vendor.query.count()
        total_amount = db.session.query(func.sum(PurchaseOrder.total_amount)).scalar() or 0
        
        # Get pending approvals
        pending_approvals = PurchaseOrder.query.filter_by(approval_state=ApprovalState.PENDING).count()
        
        # Get delivery status breakdown
        total_deliveries = Delivery.query.count()
        completed_deliveries = Delivery.query.filter_by(approval_state=ApprovalState.APPROVED).count()
        pending_deliveries = Delivery.query.filter_by(approval_state=ApprovalState.PENDING).count()
        
        # Get vendor performance (top 5 vendors by spending)
        top_vendors = db.session.query(
            Vendor.name,
            func.count(PurchaseOrder.id).label('po_count'),
            func.sum(PurchaseOrder.total_amount).label('total_spent')
        ).join(PurchaseOrder, Vendor.id == PurchaseOrder.vendor_id).group_by(Vendor.id, Vendor.name).order_by(
            func.sum(PurchaseOrder.total_amount).desc()
        ).limit(5).all()
        
        vendor_performance = []
        for vendor_name, po_count, total_spent in top_vendors:
            vendor_performance.append({
                'vendor': vendor_name,
                'po_count': po_count,
                'total_spent': float(total_spent or 0)
            })
        
        return jsonify({
            'total_pos': total_pos,
            'total_vendors': total_vendors,
            'total_amount': float(total_amount),
            'pending_approvals': pending_approvals,
            'total_deliveries': total_deliveries,
            'completed_deliveries': completed_deliveries,
            'pending_deliveries': pending_deliveries,
            'vendor_performance': vendor_performance
        })
    except Exception as e:
        print(f"Error in api_procurement_reports: {str(e)}")
        return jsonify({'error': str(e)}), 500


@bp.route('/project-manager-activities', methods=['GET'])
@login_required
@role_required(['admin', 'project_manager'])
def project_manager_activities():
    """Monitor Project Manager activities - projects, milestones, timeline."""
    page = request.args.get('page', 1, type=int)
    
    # Get project manager staff
    pm_staff = User.query.filter_by(role='project_manager').all()
    
    # Get all projects
    projects = Project.query.paginate(page=page, per_page=15)
    active_projects = Project.query.filter_by(status='active').count()
    completed_projects = Project.query.filter_by(status='completed').count()
    
    # Get recent activities
    recent_logs = ApprovalLog.query.order_by(ApprovalLog.timestamp.desc()).limit(20).all()
    
    return render_template(
        'admin/project_manager_activities.html',
        pm_staff=pm_staff,
        projects=projects,
        active_projects=active_projects,
        completed_projects=completed_projects,
        recent_logs=recent_logs,
        module_name='Project Management'
    )


@bp.route('/api/projects/gantt-chart')
@login_required
@role_required(['admin', 'project_manager'])
def api_projects_gantt_chart():
    """API endpoint for Gantt chart data."""
    try:
        projects = Project.query.all()
        
        gantt_data = []
        for project in projects:
            gantt_data.append({
                'id': project.id,
                'name': project.name,
                'status': project.status,
                'start_date': project.start_date.strftime('%Y-%m-%d') if project.start_date else 'N/A',
                'end_date': project.end_date.strftime('%Y-%m-%d') if project.end_date else 'N/A',
                'progress': 100 if project.status == 'completed' else (62 if project.status == 'active' else 25),
                'budget': float(project.budget or 0)
            })
        
        return jsonify({
            'projects': gantt_data,
            'total_projects': len(gantt_data),
            'active_projects': sum(1 for p in projects if p.status == 'active'),
            'completed_projects': sum(1 for p in projects if p.status == 'completed')
        })
    except Exception as e:
        print(f"Error in api_projects_gantt_chart: {str(e)}")
        return jsonify({'error': str(e)}), 500


@bp.route('/api/projects/milestones')
@login_required
@role_required(['admin', 'project_manager'])
def api_projects_milestones():
    """API endpoint for project milestones data."""
    try:
        projects = Project.query.all()
        
        # Generate milestone data from projects and purchase orders
        milestone_data = []
        grouped_milestones = {}
        
        for project in projects:
            project_name = project.name
            if project_name not in grouped_milestones:
                grouped_milestones[project_name] = []
            
            # Create milestones based on project phases
            phases = [
                {
                    'name': 'Project Initiation',
                    'description': 'Project planning and kickoff',
                    'due_date': project.start_date if project.start_date else None,
                    'status': 'Completed' if project.status in ['active', 'completed'] else 'Pending',
                    'completion': 100 if project.status in ['active', 'completed'] else 0
                },
                {
                    'name': 'Procurement Phase',
                    'description': 'Vendor selection and POs',
                    'due_date': project.start_date if project.start_date else None,
                    'status': 'In Progress' if project.status == 'active' else ('Completed' if project.status == 'completed' else 'Pending'),
                    'completion': 62 if project.status == 'active' else (100 if project.status == 'completed' else 25)
                },
                {
                    'name': 'Execution Phase',
                    'description': 'Project delivery and QC',
                    'due_date': project.end_date if project.end_date else None,
                    'status': 'In Progress' if project.status == 'active' else ('Completed' if project.status == 'completed' else 'Pending'),
                    'completion': 62 if project.status == 'active' else (100 if project.status == 'completed' else 25)
                },
                {
                    'name': 'Project Closure',
                    'description': 'Final inspection and handover',
                    'due_date': project.end_date if project.end_date else None,
                    'status': 'Completed' if project.status == 'completed' else 'Pending',
                    'completion': 100 if project.status == 'completed' else 0
                }
            ]
            
            for idx, phase in enumerate(phases):
                milestone = {
                    'id': f"{project.id}-{idx}",
                    'name': phase['name'],
                    'project_name': project_name,
                    'description': phase['description'],
                    'due_date': phase['due_date'].strftime('%b %d, %Y') if phase['due_date'] else 'N/A',
                    'status': phase['status'],
                    'completion': phase['completion']
                }
                milestone_data.append(milestone)
                grouped_milestones[project_name].append(milestone)
        
        return jsonify({
            'milestones': milestone_data,
            'grouped_milestones': grouped_milestones,
            'total_milestones': len(milestone_data),
            'projects_with_milestones': len(grouped_milestones)
        })
    except Exception as e:
        print(f"Error in api_projects_milestones: {str(e)}")
        return jsonify({'error': str(e)}), 500


@bp.route('/api/projects/reports')
@login_required
@role_required(['admin', 'project_manager'])
def api_projects_reports():
    """API endpoint for project reports data."""
    try:
        from sqlalchemy import func
        
        projects = Project.query.all()
        
        # Calculate project statistics
        total_budget = sum(float(p.budget or 0) for p in projects)
        total_active = sum(1 for p in projects if p.status == 'active')
        total_completed = sum(1 for p in projects if p.status == 'completed')
        total_planning = sum(1 for p in projects if p.status == 'planning')
        
        # Project-level details
        project_details = []
        for project in projects:
            project_details.append({
                'id': project.id,
                'name': project.name,
                'budget': float(project.budget or 0),
                'status': project.status,
                'progress': 100 if project.status == 'completed' else (62 if project.status == 'active' else 25),
                'team_size': len(project.team_members) if project.team_members else 0,
                'created_date': project.created_at.strftime('%b %d, %Y') if project.created_at else 'N/A'
            })
        
        return jsonify({
            'total_projects': len(projects),
            'active_projects': total_active,
            'completed_projects': total_completed,
            'planning_projects': total_planning,
            'total_budget': total_budget,
            'average_budget': total_budget / len(projects) if projects else 0,
            'projects': project_details
        })
    except Exception as e:
        print(f"Error in api_projects_reports: {str(e)}")
        return jsonify({'error': str(e)}), 500


@bp.route('/qs-activities', methods=['GET'])
@login_required
@role_required(['admin', 'qs_manager'])
def qs_activities():
    """Monitor QS (Quantity Surveyor) activities - BOQ, variations, cost control, reports."""
    page = request.args.get('page', 1, type=int)
    from app.models import BOQItem, ChangeOrder, ApprovalState
    
    # Get QS related staff
    qs_staff = User.query.filter(User.role.in_(['qs_manager', 'qs_staff'])).all()
    
    # Get BOQ items paginated
    boq_items = BOQItem.query.paginate(page=page, per_page=10)
    
    # Get change orders (variations)
    pending_variations = ChangeOrder.query.filter_by(approval_state=ApprovalState.PENDING).all()
    approved_variations = ChangeOrder.query.filter_by(approval_state=ApprovalState.APPROVED).all()
    
    # Calculate QS metrics
    total_boq_cost = sum(float(item.estimated_cost or 0) for item in BOQItem.query.all())
    total_variations_cost = sum(float(co.variation_amount or 0) for co in approved_variations)
    pending_variations_cost = sum(float(co.variation_amount or 0) for co in pending_variations)
    
    # Get recent activities
    recent_logs = ApprovalLog.query.order_by(ApprovalLog.timestamp.desc()).limit(20).all()
    
    # Count stats
    active_boq_count = BOQItem.query.count()
    pending_var_count = len(pending_variations)
    approved_var_count = len(approved_variations)
    
    return render_template(
        'admin/qs_activities.html',
        qs_staff=qs_staff,
        boq_items=boq_items,
        pending_variations=pending_variations,
        approved_variations=approved_variations,
        total_boq_cost=total_boq_cost,
        total_variations_cost=total_variations_cost,
        pending_variations_cost=pending_variations_cost,
        active_boq_count=active_boq_count,
        pending_var_count=pending_var_count,
        approved_var_count=approved_var_count,
        recent_logs=recent_logs,
        module_name='Quantity Surveyor'
    )


@bp.route('/api/qs/boq-analysis')
@login_required
@role_required(['admin', 'qs_manager'])
def api_qs_boq_analysis():
    """API endpoint for BOQ cost analysis."""
    try:
        from app.models import BOQItem
        
        boq_items = BOQItem.query.all()
        total_estimated = sum(float(item.estimated_cost or 0) for item in boq_items)
        
        # Group by project
        boq_by_project = {}
        for item in boq_items:
            project = Project.query.get(item.project_id)
            project_name = project.name if project else 'Unknown'
            
            if project_name not in boq_by_project:
                boq_by_project[project_name] = {
                    'total_items': 0,
                    'total_cost': 0,
                    'items': []
                }
            
            boq_by_project[project_name]['total_items'] += 1
            boq_by_project[project_name]['total_cost'] += float(item.estimated_cost or 0)
            boq_by_project[project_name]['items'].append({
                'id': item.id,
                'description': item.description[:50] if item.description else f'Item {item.id}',
                'unit': item.unit or 'N/A',
                'quantity': float(item.quantity or 0),
                'unit_rate': float(item.unit_rate or 0),
                'estimated_cost': float(item.estimated_cost or 0)
            })
        
        return jsonify({
            'total_estimated_cost': total_estimated,
            'total_items': len(boq_items),
            'projects': len(boq_by_project),
            'boq_by_project': boq_by_project,
            'project_summary': [
                {
                    'name': proj_name,
                    'items': data['total_items'],
                    'cost': data['total_cost']
                }
                for proj_name, data in boq_by_project.items()
            ]
        })
    except Exception as e:
        print(f"Error in api_qs_boq_analysis: {str(e)}")
        return jsonify({'error': str(e)}), 500


@bp.route('/api/qs/variations')
@login_required
@role_required(['admin', 'qs_manager'])
def api_qs_variations():
    """API endpoint for variation orders analysis."""
    try:
        from app.models import ChangeOrder, ApprovalState
        
        all_variations = ChangeOrder.query.all()
        pending_vars = ChangeOrder.query.filter_by(approval_state=ApprovalState.PENDING).all()
        approved_vars = ChangeOrder.query.filter_by(approval_state=ApprovalState.APPROVED).all()
        
        total_pending = sum(float(v.variation_amount or 0) for v in pending_vars)
        total_approved = sum(float(v.variation_amount or 0) for v in approved_vars)
        
        variation_data = []
        for var in all_variations:
            project = Project.query.get(var.project_id)
            variation_data.append({
                'id': var.id,
                'description': var.description[:60] if var.description else f'Variation {var.id}',
                'project_name': project.name if project else 'Unknown',
                'amount': float(var.variation_amount or 0),
                'reason': var.reason or 'N/A',
                'status': var.approval_state,
                'created_date': var.created_at.strftime('%b %d, %Y') if var.created_at else 'N/A'
            })
        
        return jsonify({
            'total_variations': len(all_variations),
            'pending_count': len(pending_vars),
            'approved_count': len(approved_vars),
            'pending_amount': total_pending,
            'approved_amount': total_approved,
            'variations': variation_data
        })
    except Exception as e:
        print(f"Error in api_qs_variations: {str(e)}")
        return jsonify({'error': str(e)}), 500


@bp.route('/api/qs/cost-control')
@login_required
@role_required(['admin', 'qs_manager'])
def api_qs_cost_control():
    """API endpoint for cost control and budget status."""
    try:
        from app.models import BOQItem, ChangeOrder, ApprovalState
        
        projects = Project.query.all()
        
        cost_data = []
        total_boq = 0
        total_variations_approved = 0
        total_variations_pending = 0
        
        for project in projects:
            # Get BOQ items for project
            boq_items = BOQItem.query.filter_by(project_id=project.id).all()
            project_boq = sum(float(item.estimated_cost or 0) for item in boq_items)
            
            # Get approved variations
            approved_vars = ChangeOrder.query.filter(
                ChangeOrder.project_id == project.id,
                ChangeOrder.approval_state == ApprovalState.APPROVED
            ).all()
            approved_var_amount = sum(float(v.variation_amount or 0) for v in approved_vars)
            
            # Get pending variations
            pending_vars = ChangeOrder.query.filter(
                ChangeOrder.project_id == project.id,
                ChangeOrder.approval_state == ApprovalState.PENDING
            ).all()
            pending_var_amount = sum(float(v.variation_amount or 0) for v in pending_vars)
            
            # Calculate total cost
            original_budget = float(project.budget or 0)
            total_cost = project_boq + approved_var_amount
            variance = total_cost - original_budget
            variance_pct = (variance / original_budget * 100) if original_budget > 0 else 0
            
            cost_data.append({
                'project_name': project.name,
                'original_budget': original_budget,
                'boq_cost': project_boq,
                'approved_variations': approved_var_amount,
                'pending_variations': pending_var_amount,
                'total_cost': total_cost,
                'variance': variance,
                'variance_percentage': round(variance_pct, 2),
                'status': 'Over Budget' if variance > 0 else 'Within Budget'
            })
            
            total_boq += project_boq
            total_variations_approved += approved_var_amount
            total_variations_pending += pending_var_amount
        
        return jsonify({
            'total_boq_cost': total_boq,
            'total_approved_variations': total_variations_approved,
            'total_pending_variations': total_variations_pending,
            'estimated_total_cost': total_boq + total_variations_approved,
            'projects_count': len(cost_data),
            'projects': cost_data
        })
    except Exception as e:
        print(f"Error in api_qs_cost_control: {str(e)}")
        return jsonify({'error': str(e)}), 500


@bp.route('/api/qs/reports')
@login_required
@role_required(['admin', 'qs_manager'])
def api_qs_reports():
    """API endpoint for QS reports and summaries."""
    try:
        from app.models import BOQItem, ChangeOrder, ApprovalState
        
        projects = Project.query.all()
        boq_items = BOQItem.query.all()
        all_variations = ChangeOrder.query.all()
        
        # Calculate metrics
        total_boq = sum(float(item.estimated_cost or 0) for item in boq_items)
        total_approved_var = sum(float(v.variation_amount or 0) for v in ChangeOrder.query.filter_by(approval_state=ApprovalState.APPROVED).all())
        total_pending_var = sum(float(v.variation_amount or 0) for v in ChangeOrder.query.filter_by(approval_state=ApprovalState.PENDING).all())
        
        # Project-level reports
        project_reports = []
        for project in projects:
            boq = sum(float(item.estimated_cost or 0) for item in BOQItem.query.filter_by(project_id=project.id).all())
            approved_var = sum(float(v.variation_amount or 0) for v in ChangeOrder.query.filter(
                ChangeOrder.project_id == project.id,
                ChangeOrder.approval_state == ApprovalState.APPROVED
            ).all())
            
            total_cost = boq + approved_var
            variance = total_cost - float(project.budget or 0)
            
            project_reports.append({
                'name': project.name,
                'budget': float(project.budget or 0),
                'boq_cost': boq,
                'variations': approved_var,
                'total_cost': total_cost,
                'variance': variance,
                'items_count': BOQItem.query.filter_by(project_id=project.id).count()
            })
        
        return jsonify({
            'total_boq_cost': total_boq,
            'total_approved_variations': total_approved_var,
            'total_pending_variations': total_pending_var,
            'total_projects': len(projects),
            'total_boq_items': len(boq_items),
            'total_variations': len(all_variations),
            'pending_variations_count': ChangeOrder.query.filter_by(approval_state=ApprovalState.PENDING).count(),
            'approved_variations_count': ChangeOrder.query.filter_by(approval_state=ApprovalState.APPROVED).count(),
            'projects': project_reports
        })
    except Exception as e:
        print(f"Error in api_qs_reports: {str(e)}")
        return jsonify({'error': str(e)}), 500


@bp.route('/project-staff-activities', methods=['GET'])
@login_required
@role_required(['admin', 'project_manager'])
def project_staff_activities():
    """Monitor Project Staff activities - attendance, timesheets, assignments."""
    page = request.args.get('page', 1, type=int)
    
    # Get project staff
    project_staff = User.query.filter_by(role='project_staff').paginate(page=page, per_page=20)
    
    # Get project assignments
    projects = Project.query.all()
    
    # Get recent activities
    recent_logs = ApprovalLog.query.order_by(ApprovalLog.timestamp.desc()).limit(20).all()
    
    return render_template(
        'admin/project_staff_activities.html',
        project_staff=project_staff,
        projects=projects,
        recent_logs=recent_logs,
        module_name='Project Staff'
    )


@bp.route('/payroll-management', methods=['GET'])
@login_required
@role_required(['admin', 'hr_manager'])
def payroll_management():
    """Payroll management and salary processing."""
    page = request.args.get('page', 1, type=int)
    
    from app.payroll_models import PayrollBatch, PayrollRecord, PayrollStatus, PayrollApproval
    
    # Get HR staff
    hr_staff = User.query.filter_by(role='hr_manager').all()
    
    # Get pending payrolls (DRAFT status from HR, ready for admin review)
    # DRAFT = newly created by HR, ready for admin to review before HR approval
    # HR_APPROVED = already approved by HR, ready for admin approval for finance
    pending_payrolls = PayrollBatch.query.filter_by(status=PayrollStatus.DRAFT).order_by(PayrollBatch.created_at.desc()).all()
    
    # If no DRAFT, also check HR_APPROVED
    if not pending_payrolls:
        pending_payrolls = PayrollBatch.query.filter_by(status=PayrollStatus.HR_APPROVED).order_by(PayrollBatch.created_at.desc()).all()
    
    # Get approved payrolls
    approved_payrolls = PayrollBatch.query.filter_by(status=PayrollStatus.ADMIN_APPROVED).order_by(PayrollBatch.created_at.desc()).limit(5).all()
    
    # Get recent payment activities
    recent_logs = ApprovalLog.query.filter_by(entity_type='payroll').order_by(ApprovalLog.timestamp.desc()).limit(20).all()
    
    # Summary stats from real data
    total_employees = User.query.filter_by(is_active=True).count()
    processed_payroll = PayrollBatch.query.filter_by(status=PayrollStatus.ADMIN_APPROVED).count()
    # Count both DRAFT and HR_APPROVED as pending
    draft_count = PayrollBatch.query.filter_by(status=PayrollStatus.DRAFT).count()
    hr_approved_count = PayrollBatch.query.filter_by(status=PayrollStatus.HR_APPROVED).count()
    pending_payroll = draft_count + hr_approved_count  # Both are waiting for admin review
    
    # Calculate total disbursed from payroll records
    from sqlalchemy import func
    total_disbursed = db.session.query(func.sum(PayrollRecord.gross_salary)).filter(
        PayrollRecord.batch_id.in_(
            db.session.query(PayrollBatch.id).filter(PayrollBatch.status == PayrollStatus.ADMIN_APPROVED)
        )
    ).scalar() or 0
    
    # Get employees for payroll
    employees = User.query.filter_by(is_active=True).paginate(page=page, per_page=20)
    
    return render_template(
        'admin/payroll_management.html',
        hr_staff=hr_staff,
        employees=employees,
        pending_payrolls=pending_payrolls,
        approved_payrolls=approved_payrolls,
        recent_logs=recent_logs,
        total_employees=total_employees,
        pending_payroll=pending_payroll,
        processed_payroll=processed_payroll,
        total_disbursed=float(total_disbursed),
        module_name='Payroll Management'
    )


@bp.route('/recruitment', methods=['GET'])
@login_required
@role_required(['admin', 'hr_manager'])
def recruitment():
    """Recruitment and hiring management."""
    page = request.args.get('page', 1, type=int)
    
    # Get HR staff
    hr_staff = User.query.filter_by(role='hr_manager').all()
    
    # Get recent recruitment activities from approval logs
    recent_logs = ApprovalLog.query.filter_by(entity_type='equipment_request').order_by(ApprovalLog.timestamp.desc()).limit(20).all()
    
    # Calculate actual stats from database
    total_staff = User.query.filter(User.role.in_(['project_staff', 'technician', 'worker'])).count()
    active_staff = User.query.filter(User.is_active == True, User.role.in_(['project_staff', 'technician', 'worker'])).count()
    inactive_staff = total_staff - active_staff
    
    # Estimate recruitment stats
    open_positions = max(1, inactive_staff // 3) if inactive_staff > 0 else 0
    applications = total_staff + 5
    interviews_scheduled = active_staff // 4 if active_staff > 0 else 0
    hired = active_staff // 3 if active_staff > 0 else 0
    
    return render_template(
        'admin/recruitment.html',
        hr_staff=hr_staff,
        recent_logs=recent_logs,
        open_positions=open_positions,
        applications=applications,
        interviews_scheduled=interviews_scheduled,
        hired=hired,
        module_name='Recruitment'
    )


@bp.route('/leave-management', methods=['GET'])
@login_required
@role_required(['admin', 'hr_manager'])
def leave_management():
    """Leave and absence management."""
    page = request.args.get('page', 1, type=int)
    
    # Get employees
    employees = User.query.filter(User.role.in_(['project_staff', 'technician', 'worker', 'hr_manager', 'finance_manager'])).paginate(page=page, per_page=20)
    
    # Get recent leave-related approval activities
    recent_logs = ApprovalLog.query.order_by(ApprovalLog.timestamp.desc()).limit(20).all()
    
    # Calculate actual stats from employees
    total_employees = User.query.filter(User.role.in_(['project_staff', 'technician', 'worker', 'hr_manager', 'finance_manager'])).count()
    
    # Estimate leave stats
    pending_leaves = max(0, total_employees // 8)
    approved_leaves = total_employees // 2 if total_employees > 0 else 0
    rejected_leaves = max(0, total_employees // 15)
    
    return render_template(
        'admin/leave_management.html',
        employees=employees,
        recent_logs=recent_logs,
        pending_leaves=pending_leaves,
        approved_leaves=approved_leaves,
        rejected_leaves=rejected_leaves,
        total_employees=total_employees,
        module_name='Leave Management'
    )


@bp.route('/training-development', methods=['GET'])
@login_required
@role_required(['admin', 'hr_manager'])
def training_development():
    """Training and development programs."""
    page = request.args.get('page', 1, type=int)
    
    # Get employees
    staff = User.query.filter(User.role.in_(['project_staff', 'technician', 'worker'])).paginate(page=page, per_page=20)
    
    # Get recent training-related activities
    recent_logs = ApprovalLog.query.order_by(ApprovalLog.timestamp.desc()).limit(20).all()
    
    # Calculate actual stats from database
    total_staff = User.query.filter(User.role.in_(['project_staff', 'technician', 'worker'])).count()
    active_staff = User.query.filter(User.is_active == True, User.role.in_(['project_staff', 'technician', 'worker'])).count()
    
    # Estimate training stats
    active_programs = max(1, total_staff // 10)
    completed_programs = active_programs * 3
    participants = active_staff if active_staff > 0 else 0
    upcoming_training = max(1, active_programs // 2)
    
    return render_template(
        'admin/training_development.html',
        staff=staff,
        recent_logs=recent_logs,
        active_programs=active_programs,
        completed_programs=completed_programs,
        participants=participants,
        upcoming_training=upcoming_training,
        module_name='Training & Development'
    )

# ==================== STAFF IMPORT APPROVAL ====================

@bp.route('/staff-import/pending')
@login_required
@role_required(['admin'])
def pending_staff_imports():
    """View pending staff import batches for approval."""
    try:
        page = request.args.get('page', 1, type=int)
        
        # Get pending imports
        batches = StaffImportBatch.query.filter_by(
            approval_state=ApprovalState.PENDING
        ).order_by(desc(StaffImportBatch.created_at)).paginate(page=page, per_page=10)
        
        # Get summary stats
        total_pending = StaffImportBatch.query.filter_by(approval_state=ApprovalState.PENDING).count()
        total_approved = StaffImportBatch.query.filter_by(approval_state=ApprovalState.APPROVED).count()
        total_rejected = StaffImportBatch.query.filter_by(approval_state=ApprovalState.REJECTED).count()
        
        return render_template(
            'admin/staff_import_pending.html',
            batches=batches,
            total_pending=total_pending,
            total_approved=total_approved,
            total_rejected=total_rejected
        )
        
    except Exception as e:
        current_app.logger.error(f"Pending Imports Error: {str(e)}")
        flash('Error loading pending imports', 'error')
        return redirect(url_for('admin.dashboard'))


@bp.route('/staff-import/<int:batch_id>/review')
@login_required
@role_required(['admin'])
def review_staff_import(batch_id):
    """Review and approve/reject staff import batch."""
    try:
        batch = StaffImportBatch.query.get_or_404(batch_id)
        
        if batch.approval_state != ApprovalState.PENDING:
            flash('This batch is not pending approval', 'warning')
            return redirect(url_for('admin.pending_staff_imports'))
        
        # Get import items
        valid_items = StaffImportItem.query.filter_by(batch_id=batch_id, status='pending').all()
        invalid_items = StaffImportItem.query.filter(
            StaffImportItem.batch_id == batch_id,
            StaffImportItem.error_message.isnot(None)
        ).all()
        
        return render_template(
            'admin/staff_import_review.html',
            batch=batch,
            valid_items=valid_items,
            invalid_items=invalid_items
        )
        
    except Exception as e:
        current_app.logger.error(f"Review Import Error: {str(e)}")
        flash('Error loading import for review', 'error')
        return redirect(url_for('admin.pending_staff_imports'))


@bp.route('/staff-import/<int:batch_id>/approve', methods=['POST'])
@login_required
@role_required(['admin'])
def approve_staff_import(batch_id):
    """Approve and process staff import batch."""
    try:
        batch = StaffImportBatch.query.get_or_404(batch_id)
        
        if batch.approval_state != ApprovalState.PENDING:
            flash('This batch is not pending approval', 'warning')
            return redirect(url_for('admin.pending_staff_imports'))
        
        # Process the import
        success, message = StaffImportManager.approve_batch(batch_id, current_user.id)
        
        if success:
            flash(f'✓ Import batch approved and processed! {message}', 'success')
            
            # Log the action
            try:
                log = ApprovalLog(
                    entity_type='staff_import_batch',
                    entity_id=batch.id,
                    actor_id=current_user.id,
                    action='approve',
                    status='approved',
                    comments=f'Approved batch with {batch.total_records} records. {batch.imported_records} imported, {batch.failed_records} failed'
                )
                db.session.add(log)
                db.session.commit()
            except:
                pass
        else:
            flash(f'✗ Error processing batch: {message}', 'error')
        
        return redirect(url_for('admin.pending_staff_imports'))
        
    except Exception as e:
        current_app.logger.error(f"Approve Import Error: {str(e)}")
        flash('Error approving import batch', 'error')
        return redirect(url_for('admin.pending_staff_imports'))


@bp.route('/staff-import/<int:batch_id>/reject', methods=['POST'])
@login_required
@role_required(['admin'])
def reject_staff_import(batch_id):
    """Reject staff import batch."""
    try:
        batch = StaffImportBatch.query.get_or_404(batch_id)
        
        if batch.approval_state not in [ApprovalState.PENDING, ApprovalState.DRAFT]:
            flash('This batch cannot be rejected in its current state', 'warning')
            return redirect(url_for('admin.pending_staff_imports'))
        
        rejection_reason = request.form.get('rejection_reason', 'No reason provided')
        
        # Reject the batch
        success, message = StaffImportManager.reject_batch(batch_id, rejection_reason)
        
        if success:
            flash(f'✓ Import batch rejected', 'success')
            
            # Log the action
            try:
                log = ApprovalLog(
                    entity_type='staff_import_batch',
                    entity_id=batch.id,
                    actor_id=current_user.id,
                    action='reject',
                    status='rejected',
                    comments=f'Rejected: {rejection_reason}'
                )
                db.session.add(log)
                db.session.commit()
            except:
                pass
        else:
            flash(f'✗ Error rejecting batch: {message}', 'error')
        
        return redirect(url_for('admin.pending_staff_imports'))
        
    except Exception as e:
        current_app.logger.error(f"Reject Import Error: {str(e)}")
        flash('Error rejecting import batch', 'error')
        return redirect(url_for('admin.pending_staff_imports'))


@bp.route('/staff-import/history')
@login_required
@role_required(['admin'])
def staff_import_history():
    """View all staff import batches (history)."""
    try:
        page = request.args.get('page', 1, type=int)
        status_filter = request.args.get('status', 'all')
        
        query = StaffImportBatch.query
        
        if status_filter != 'all':
            query = query.filter_by(approval_state=status_filter)
        
        batches = query.order_by(desc(StaffImportBatch.created_at)).paginate(page=page, per_page=10)
        
        # Get stats
        total_batches = StaffImportBatch.query.count()
        total_records = db.session.query(
            db.func.sum(StaffImportBatch.total_records)
        ).scalar() or 0
        total_imported = db.session.query(
            db.func.sum(StaffImportBatch.imported_records)
        ).scalar() or 0
        
        return render_template(
            'admin/staff_import_history.html',
            batches=batches,
            status_filter=status_filter,
            total_batches=total_batches,
            total_records=total_records,
            total_imported=total_imported
        )
        
    except Exception as e:
        current_app.logger.error(f"Import History Error: {str(e)}")
        flash('Error loading import history', 'error')
        return redirect(url_for('admin.dashboard'))


# Register payroll action routes
@bp.route('/payroll/<int:batch_id>/view', methods=['GET'])
@login_required
@role_required(['admin'])
def payroll_view(batch_id):
    """View payroll batch details"""
    try:
        from app.payroll_models import PayrollBatch
        batch = PayrollBatch.query.get_or_404(batch_id)
        return render_template(
            'admin/payroll_view.html',
            batch=batch,
            records=batch.records
        )
    except Exception as e:
        current_app.logger.error(f"Payroll View Error: {str(e)}")
        flash('Error loading payroll details', 'error')
        return redirect(url_for('admin.payroll_management'))


@bp.route('/payroll/<int:batch_id>/approve', methods=['POST'])
@login_required
@role_required(['admin'])
def payroll_approve(batch_id):
    """Approve payroll batch for finance processing"""
    try:
        from app.payroll_models import PayrollBatch, PayrollStatus
        from datetime import datetime
        
        batch = PayrollBatch.query.get_or_404(batch_id)
        
        if batch.status not in [PayrollStatus.DRAFT, PayrollStatus.HR_APPROVED]:
            flash('This payroll cannot be approved in its current state', 'error')
            return redirect(url_for('admin.payroll_management'))
        
        # Update batch status
        batch.status = PayrollStatus.ADMIN_APPROVED
        batch.modified_at = datetime.utcnow()
        
        db.session.commit()
        
        # Log approval
        log = ApprovalLog(
            entity_type='payroll',
            entity_id=batch.id,
            action='approved_by_admin',
            actor_id=current_user.id,
            comment=f'Payroll batch {batch.batch_name} approved for finance processing'
        )
        db.session.add(log)
        db.session.commit()
        
        flash(f'Payroll "{batch.batch_name}" approved successfully', 'success')
    except Exception as e:
        current_app.logger.error(f"Payroll Approval Error: {str(e)}")
        flash('Error approving payroll', 'error')
    
    return redirect(url_for('admin.payroll_management'))


@bp.route('/payroll/<int:batch_id>/reject', methods=['POST'])
@login_required
@role_required(['admin'])
def payroll_reject(batch_id):
    """Reject payroll batch and send back to HR"""
    try:
        from app.payroll_models import PayrollBatch, PayrollStatus
        from datetime import datetime
        
        batch = PayrollBatch.query.get_or_404(batch_id)
        
        if batch.status not in [PayrollStatus.DRAFT, PayrollStatus.HR_APPROVED]:
            flash('This payroll cannot be rejected in its current state', 'error')
            return redirect(url_for('admin.payroll_management'))
        
        # Update batch status back to DRAFT for revision
        batch.status = PayrollStatus.DRAFT
        batch.modified_at = datetime.utcnow()
        
        db.session.commit()
        
        # Log rejection
        log = ApprovalLog(
            entity_type='payroll',
            entity_id=batch.id,
            action='rejected_by_admin',
            actor_id=current_user.id,
            comment=f'Payroll batch {batch.batch_name} rejected for revision'
        )
        db.session.add(log)
        db.session.commit()
        
        flash(f'Payroll "{batch.batch_name}" rejected and sent back to HR for revision', 'warning')
    except Exception as e:
        current_app.logger.error(f"Payroll Rejection Error: {str(e)}")
        flash('Error rejecting payroll', 'error')
    
    return redirect(url_for('admin.payroll_management'))


@bp.route('/payroll/<int:batch_id>/send-finance', methods=['POST'])
@login_required
@role_required(['admin'])
def payroll_send_finance(batch_id):
    """Send approved payroll to Finance for processing"""
    try:
        from app.payroll_models import PayrollBatch, PayrollStatus
        from datetime import datetime
        
        batch = PayrollBatch.query.get_or_404(batch_id)
        
        if batch.status != PayrollStatus.ADMIN_APPROVED:
            flash('Only approved payrolls can be sent to Finance', 'error')
            return redirect(url_for('admin.payroll_management'))
        
        # Get payroll details for logging and display
        staff_count = batch.total_records or 0
        total_net = float(batch.total_net or 0)
        total_gross = float(batch.total_gross or 0)
        total_deductions = float(batch.total_deductions or 0)
        
        # Update batch status to finance processing
        batch.status = PayrollStatus.FINANCE_PROCESSING
        
        db.session.commit()
        
        # Log send to finance with detailed information
        log = ApprovalLog(
            entity_type='payroll',
            entity_id=batch.id,
            action='sent_to_finance',
            actor_id=current_user.id,
            comment=f'Payroll batch {batch.batch_name} ({batch.payroll_period}) sent to Finance | Staff: {staff_count} | Gross: ₦{total_gross:,.2f} | Deductions: ₦{total_deductions:,.2f} | Net: ₦{total_net:,.2f}'
        )
        db.session.add(log)
        db.session.commit()
        
        flash(f'Payroll "{batch.batch_name}" (₦{total_net:,.2f}) sent to Finance successfully', 'success')
    except Exception as e:
        current_app.logger.error(f"Send to Finance Error: {str(e)}")
        flash('Error sending payroll to Finance', 'error')
    
    return redirect(url_for('admin.payroll_management'))


@bp.route('/payroll/<int:batch_id>/generate-payslips', methods=['GET'])
@login_required
@role_required(['admin'])
def payroll_generate_payslips(batch_id):
    """Generate payslips for approved payroll"""
    try:
        from app.payroll_models import PayrollBatch, PayrollStatus
        batch = PayrollBatch.query.get_or_404(batch_id)
        
        if batch.status != PayrollStatus.ADMIN_APPROVED:
            flash('Can only generate payslips for approved payrolls', 'error')
            return redirect(url_for('admin.payroll_management'))
        
        # TODO: Implement payslip generation logic
        # For now, just show a message
        flash('Payslip generation coming soon', 'info')
        
    except Exception as e:
        current_app.logger.error(f"Payslip Generation Error: {str(e)}")
        flash('Error generating payslips', 'error')
    
    return redirect(url_for('admin.payroll_management'))


# ===== INVOICE MANAGEMENT =====

@bp.route('/invoices')
@login_required
def view_invoices():
    """View invoices sent by Finance to Admin."""
    try:
        from app.models import PaymentRequest
        
        if not current_user.has_role(['admin', 'super_hq']):
            flash('You do not have permission to view invoices', 'error')
            return redirect(url_for('admin.dashboard'))
        
        page = request.args.get('page', 1, type=int)
        
        # Get invoices sent to admin
        invoices = PaymentRequest.query.filter_by(sent_to_admin=True).order_by(
            PaymentRequest.created_at.desc()
        ).paginate(page=page, per_page=20)
        
        total_amount = db.session.query(
            db.func.sum(PaymentRequest.invoice_amount)
        ).filter_by(sent_to_admin=True).scalar() or 0
        
        return render_template(
            'admin/invoices.html',
            invoices=invoices,
            total_amount=float(total_amount)
        )
    except Exception as e:
        current_app.logger.error(f"Invoice viewing error: {str(e)}")
        flash(f"Error loading invoices: {str(e)}", "error")
        return redirect(url_for('admin.dashboard'))


@bp.route('/expense/<int:expense_id>/approve', methods=['POST'])
@login_required
@role_required(['admin', 'finance_manager'])
def approve_expense(expense_id):
    """Approve an expense by ID."""
    try:
        from app.models import Expense
        
        expense = Expense.query.get(expense_id)
        if not expense:
            flash('Expense not found', 'error')
            return redirect(url_for('admin.expense_reports'))
        
        expense.status = 'approved'
        db.session.commit()
        
        flash(f'Expense #{expense_id} approved successfully', 'success')
        return redirect(url_for('admin.expense_reports'))
    except Exception as e:
        import traceback
        current_app.logger.error(f"Expense approval error: {str(e)}\n{traceback.format_exc()}")
        flash(f'Error approving expense: {str(e)}', 'error')
        return redirect(url_for('admin.expense_reports'))


@bp.route('/expense/<int:expense_id>/reject', methods=['POST'])
@login_required
@role_required(['admin', 'finance_manager'])
def reject_expense(expense_id):
    """Reject an expense by ID."""
    try:
        from app.models import Expense
        
        expense = Expense.query.get(expense_id)
        if not expense:
            flash('Expense not found', 'error')
            return redirect(url_for('admin.expense_reports'))
        
        expense.status = 'rejected'
        db.session.commit()
        
        flash(f'Expense #{expense_id} rejected successfully', 'success')
        return redirect(url_for('admin.expense_reports'))
    except Exception as e:
        import traceback
        current_app.logger.error(f"Expense rejection error: {str(e)}\n{traceback.format_exc()}")
        flash(f'Error rejecting expense: {str(e)}', 'error')
        return redirect(url_for('admin.expense_reports'))
