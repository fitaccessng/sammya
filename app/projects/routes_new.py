import os
from datetime import datetime
from flask import Blueprint, render_template, current_app, flash, request, jsonify, url_for, redirect, session, send_file
from flask_login import login_required, current_user, logout_user
from werkzeug.utils import secure_filename
from app.models import Project, User, PurchaseOrder, PaymentRequest, PaymentRecord, BOQItem, db
from app.utils import role_required, Roles
from sqlalchemy import func

project_bp = Blueprint('project', __name__)


def get_user_accessible_projects(user):
    """Get projects that the user has access to based on their role"""
    current_app.logger.info(f"Getting accessible projects for user {user.id} ({user.role})")
    
    if user.has_role(Roles.SUPER_HQ):
        projects = Project.query.all()
        current_app.logger.info(f"SUPER_HQ user - returning all {len(projects)} projects")
        return projects
    elif user.has_role(Roles.PROJECT_MANAGER):
        managed_projects = Project.query.filter(Project.project_manager == user.name).all()
        assigned_projects_query = db.session.query(Project).join(
            StaffAssignment, Project.id == StaffAssignment.project_id
        ).filter(StaffAssignment.staff_id == user.id)
        assigned_projects = assigned_projects_query.all()
        all_projects = managed_projects + assigned_projects
        unique_projects = list({project.id: project for project in all_projects}.values())
        current_app.logger.info(f"PROJECT_MANAGER user - {len(managed_projects)} managed, {len(assigned_projects)} assigned, {len(unique_projects)} total")
        return unique_projects
    else:
        current_app.logger.info(f"Staff user - checking assignments for user {user.id}")
        user_projects_query = db.session.query(Project).join(
            StaffAssignment, Project.id == StaffAssignment.project_id
        ).filter(StaffAssignment.staff_id == user.id)
        user_projects = user_projects_query.all()
        current_app.logger.info(f"Found {len(user_projects)} direct user assignments")
        
        employee_projects = []
        try:
            employee = None
            if user.email:
                employee = Employee.query.filter_by(email=user.email).first()
                if employee:
                    current_app.logger.info(f"Found matching employee by email: {employee.name} (ID: {employee.id})")
            if not employee and user.name:
                employee = Employee.query.filter_by(name=user.name).first()
                if employee:
                    current_app.logger.info(f"Found matching employee by name: {employee.name} (ID: {employee.id})")
            
            if employee:
                employee_projects_query = db.session.query(Project).join(
                    EmployeeAssignment, Project.id == EmployeeAssignment.project_id
                ).filter(EmployeeAssignment.employee_id == employee.id)
                employee_projects = employee_projects_query.all()
                current_app.logger.info(f"Found {len(employee_projects)} employee assignments")
        except Exception as e:
            current_app.logger.error(f"Error getting employee projects for user {user.id}: {str(e)}")
            employee_projects = []
        
        all_projects = user_projects + employee_projects
        unique_projects = list({project.id: project for project in all_projects}.values())
        current_app.logger.info(f"Staff user - {len(user_projects)} user assignments + {len(employee_projects)} employee assignments = {len(unique_projects)} total projects")
        return unique_projects


def get_user_accessible_project_ids(user):
    """Get project IDs that the user has access to"""
    projects = get_user_accessible_projects(user)
    return [p.id for p in projects]


# Dashboard
@project_bp.route('/')
@login_required
@role_required([Roles.SUPER_HQ, Roles.PROJECT_MANAGER, Roles.PROJECT_STAFF, Roles.HQ_FINANCE, Roles.HQ_PROCUREMENT])
def project_home():
    try:
        current_app.logger.info(f"User {current_user.id} ({current_user.role}) accessing project dashboard")
        
        projects = get_user_accessible_projects(current_user)
        current_app.logger.info(f"User {current_user.id} ({current_user.role}) accessing {len(projects)} projects")
        
        total_projects = len(projects)
        active_projects = len([p for p in projects if p.status in ['Active', 'In Progress']])
        completed_projects = len([p for p in projects if p.status == 'Completed'])
        planning_projects = len([p for p in projects if p.status == 'Planning'])
        
        total_budget = sum([p.budget or 0 for p in projects])
        total_spent = 0
        
        recent_activities = []
        for project in projects[:5]:
            recent_activities.append({
                'project_name': project.name,
                'activity': 'Project created' if project.status == 'Planning' else f'Status updated to {project.status}',
                'timestamp': project.updated_at or project.created_at,
                'user': project.project_manager or 'System'
            })
        
        enhanced_projects = []
        for project in projects:
            try:
                user_assignment = StaffAssignment.query.filter_by(
                    project_id=project.id, 
                    staff_id=current_user.id
                ).first()
                user_role_in_project = user_assignment.role if user_assignment else "Manager"
                
                milestones = project.milestones if hasattr(project, 'milestones') else []
                completed_milestones = [m for m in milestones if hasattr(m, 'status') and m.status == 'Completed']
                progress = (len(completed_milestones) / len(milestones) * 100) if milestones else 0
                
                all_staff_assignments = StaffAssignment.query.filter_by(project_id=project.id).all()
                
                enhanced_project = {
                    'id': project.id,
                    'name': project.name,
                    'description': project.description or 'No description provided',
                    'status': project.status or 'Planning',
                    'progress': progress,
                    'manager': project.project_manager or 'Not assigned',
                    'created_at': project.created_at,
                    'updated_at': project.updated_at,
                    'start_date': project.start_date,
                    'end_date': project.end_date,
                    'budget': project.budget or 0,
                    'spent': project.budget * 0.6 if project.budget else 0,
                    'milestone_count': len(milestones),
                    'completed_milestones': len(completed_milestones),
                    'staff_count': len(all_staff_assignments),
                    'days_remaining': (project.end_date - datetime.now().date()).days if project.end_date else None,
                    'is_overdue': project.end_date and project.end_date < datetime.now().date() and project.status != 'Completed',
                    'priority': 'High' if project.budget and project.budget > 10000000 else 'Medium' if project.budget and project.budget > 5000000 else 'Normal',
                    'user_role': user_role_in_project,
                    'is_manager': project.project_manager == current_user.name,
                    'staff': {
                        'Project Manager': project.project_manager or 'Not assigned',
                        'Team Size': len(all_staff_assignments),
                        'User Role': user_role_in_project
                    }
                }
                enhanced_projects.append(enhanced_project)
            except Exception as e:
                current_app.logger.error(f"Error processing project {project.id}: {str(e)}")
                enhanced_projects.append({
                    'id': project.id,
                    'name': project.name,
                    'description': project.description or 'No description provided',
                    'status': project.status or 'Planning',
                    'progress': project.progress or 0,
                    'manager': project.project_manager or 'Not assigned',
                    'created_at': project.created_at.strftime('%Y-%m-%d') if project.created_at else 'Unknown',
                    'start_date': project.start_date.strftime('%Y-%m-%d') if project.start_date else 'Not set',
                    'end_date': project.end_date.strftime('%Y-%m-%d') if project.end_date else 'Not set',
                    'budget': project.budget or 0,
                    'spent': 0,
                    'milestone_count': 0,
                    'completed_milestones': 0,
                    'staff_count': 0,
                    'days_remaining': None,
                    'is_overdue': False,
                    'priority': 'Normal',
                    'user_role': 'Member',
                    'is_manager': False,
                    'staff': {
                        'Project Manager': project.project_manager or 'Not assigned',
                        'Team Size': 0,
                        'User Role': 'Member'
                    }
                })
        
        project_stats = {
            'total_projects': total_projects,
            'active_projects': active_projects,
            'completed_projects': completed_projects,
            'planning_projects': planning_projects,
            'total_budget': total_budget,
            'total_spent': total_spent,
            'completion_rate': (completed_projects / total_projects * 100) if total_projects > 0 else 0,
            'user_name': current_user.name,
            'user_role': current_user.role
        }
        
        return render_template('projects/index.html', 
                             projects=enhanced_projects,
                             project_stats=project_stats,
                             recent_activities=recent_activities,
                             current_user=current_user,
                             current_date_obj=datetime.now().date())
    except Exception as e:
        current_app.logger.error(f"Project dashboard error: {str(e)}", exc_info=True)
        return render_template('projects/index.html', 
                             projects=[],
                             project_stats={
                                 'total_projects': 0,
                                 'active_projects': 0,
                                 'completed_projects': 0,
                                 'planning_projects': 0,
                                 'total_budget': 0,
                                 'total_spent': 0,
                                 'completion_rate': 0,
                                 'user_name': current_user.name if current_user.is_authenticated else 'Unknown',
                                 'user_role': current_user.role if current_user.is_authenticated else 'Unknown'
                             },
                             recent_activities=[],
                             current_user=current_user,
                             current_date_obj=datetime.now().date())
