"""
QS Reports and analysis endpoints
"""
from flask import Blueprint, render_template, redirect, url_for, flash, current_app
from flask_login import login_required
from app.models import Project, BOQItem
from app.utils import role_required, Roles
from datetime import datetime
from .utils import get_user_qs_projects, check_project_access

reports_bp = Blueprint('qs_reports', __name__)


@reports_bp.route('/reports', methods=['GET'])
@login_required
@role_required([Roles.SUPER_HQ, Roles.QS_MANAGER, Roles.QS_STAFF])
def reports():
    """QS Reports dashboard - Reports for assigned projects"""
    try:
        projects = get_user_qs_projects()

        total_contract_value = sum(float(project.budget or 0) for project in projects)
        total_boq = 0.0
        projects_over_budget = []

        for project in projects:
            project_total_boq = sum(float(item.amount or 0) for item in getattr(project, 'boq_items', []))
            total_boq += project_total_boq
            if project_total_boq > float(project.budget or 0):
                projects_over_budget.append(project)

        return render_template(
            'qs/reports.html',
            projects=projects,
            total_contract_value=total_contract_value,
            total_boq=total_boq,
            projects_over_budget=projects_over_budget
        )
    except Exception as e:
        current_app.logger.error(f"Error loading QS reports: {str(e)}")
        flash('Error loading reports', 'error')
        return redirect(url_for('qs_dashboard.dashboard'))


@reports_bp.route('/project/<int:project_id>/report/boq', methods=['GET'])
@login_required
@role_required([Roles.SUPER_HQ, Roles.QS_MANAGER, Roles.QS_STAFF])
def report_boq(project_id):
    """Generate BOQ report for a project"""
    try:
        project = check_project_access(project_id)
        if not project:
            return redirect(url_for('qs_dashboard.dashboard'))
        
        boq_items = BOQItem.query.filter_by(project_id=project_id).all()
        total_boq = sum(float(item.amount or 0) for item in boq_items)

        categories_summary = {}
        for item in boq_items:
            category = getattr(item, 'category', None) or item.unit or 'Uncategorized'
            if category not in categories_summary:
                categories_summary[category] = {'count': 0, 'total': 0}
            categories_summary[category]['count'] += 1
            categories_summary[category]['total'] += float(item.amount or 0)

        report_date = datetime.utcnow()
        
        return render_template('qs/report_boq.html',
            project=project,
            boq_items=boq_items,
            total_boq=total_boq,
            categories_summary=categories_summary,
            report_date=report_date,
            now=report_date
        )
    except Exception as e:
        current_app.logger.error(f"Error generating BOQ report for project {project_id}: {str(e)}")
        flash('Error generating report', 'error')
        return redirect(url_for('qs_dashboard.dashboard'))


@reports_bp.route('/project/<int:project_id>/report/valuations', methods=['GET'])
@login_required
@role_required([Roles.SUPER_HQ, Roles.QS_MANAGER, Roles.QS_STAFF])
def report_valuations(project_id):
    """Generate valuations report for a project"""
    try:
        project = check_project_access(project_id)
        if not project:
            return redirect(url_for('qs_dashboard.dashboard'))
        
        boq_items = BOQItem.query.filter_by(project_id=project_id).all()
        total_boq = sum(float(item.amount or 0) for item in boq_items)
        
        return render_template('qs/report_valuations.html',
            project=project,
            boq_items=boq_items,
            total_boq=total_boq,
            report_date=datetime.utcnow()
        )
    except Exception as e:
        current_app.logger.error(f"Error generating valuations report for project {project_id}: {str(e)}")
        flash('Error generating report', 'error')
        return redirect(url_for('qs_dashboard.dashboard'))


@reports_bp.route('/project/<int:project_id>/report/cost-summary', methods=['GET'])
@login_required
@role_required([Roles.SUPER_HQ, Roles.QS_MANAGER, Roles.QS_STAFF])
def report_cost_summary(project_id):
    """Generate cost summary report for a project"""
    try:
        project = check_project_access(project_id)
        if not project:
            return redirect(url_for('qs_dashboard.dashboard'))
        
        boq_items = BOQItem.query.filter_by(project_id=project_id).all()
        
        cost_breakdown = {}
        for item in boq_items:
            category = getattr(item, 'category', None) or item.unit or 'Uncategorized'
            if category not in cost_breakdown:
                cost_breakdown[category] = {'count': 0, 'total': 0}
            cost_breakdown[category]['count'] += 1
            cost_breakdown[category]['total'] += float(item.amount or 0)
        
        total_boq = sum(float(item.amount or 0) for item in boq_items)
        
        return render_template('qs/report_cost_summary.html',
            project=project,
            cost_breakdown=cost_breakdown,
            total_boq=total_boq,
            boq_items=boq_items,
            report_date=datetime.utcnow()
        )
    except Exception as e:
        current_app.logger.error(f"Error generating cost summary report for project {project_id}: {str(e)}")
        flash('Error generating report', 'error')
        return redirect(url_for('qs_dashboard.dashboard'))


@reports_bp.route('/project/<int:project_id>/cost-summary', methods=['GET'])
@login_required
@role_required([Roles.SUPER_HQ, Roles.QS_MANAGER, Roles.QS_STAFF])
def project_cost_summary(project_id):
    """View comprehensive cost summary and profitability analysis"""
    try:
        project = check_project_access(project_id)
        if not project:
            return redirect(url_for('qs_dashboard.dashboard'))
        
        # Contract values
        original_contract = float(project.budget or 0)
        total_variations = 0
        revised_contract = original_contract + total_variations
        
        # Expenditure
        cost_to_date = 0
        projected_final_cost = cost_to_date
        
        # Profitability
        estimated_profit = revised_contract - projected_final_cost
        profit_margin = (estimated_profit / revised_contract * 100) if revised_contract > 0 else 0
        
        # Cost by category
        boq_items = BOQItem.query.filter_by(project_id=project_id).all()
        total_boq_value = sum(float(item.amount or 0) for item in boq_items)
        
        cost_by_category = {}
        for item in boq_items:
            category = getattr(item, 'category', None) or item.unit or 'Uncategorized'
            if category not in cost_by_category:
                cost_by_category[category] = {'allocated': 0, 'spent': 0}
            cost_by_category[category]['allocated'] += float(item.amount or 0)
        
        return render_template('qs/project_cost_summary.html',
            project=project,
            contract_value=original_contract,
            total_variations=total_variations,
            revised_contract=revised_contract,
            cost_to_date=cost_to_date,
            projected_final_cost=projected_final_cost,
            estimated_profit=estimated_profit,
            profit_margin=round(profit_margin, 1),
            total_boq=total_boq_value,
            cost_by_category=cost_by_category
        )
    except Exception as e:
        current_app.logger.error(f"Error loading cost summary for project {project_id}: {str(e)}")
        flash('Error loading cost summary', 'error')
        return redirect(url_for('qs_dashboard.dashboard'))


@reports_bp.route('/project/<int:project_id>/cost-control', methods=['GET'])
@login_required
@role_required([Roles.SUPER_HQ, Roles.QS_MANAGER, Roles.QS_STAFF])
def cost_control_report(project_id):
    """View cost control report with risk analysis"""
    try:
        project = check_project_access(project_id)
        if not project:
            return redirect(url_for('qs_dashboard.dashboard'))

        boq_items = BOQItem.query.filter_by(project_id=project_id).all()
        total_boq = sum(float(item.amount or 0) for item in boq_items)
        categories = []
        category_totals = {}
        for item in boq_items:
            category = getattr(item, 'category', None) or item.unit or 'Uncategorized'
            category_totals[category] = category_totals.get(category, 0) + float(item.amount or 0)
        for category, total in category_totals.items():
            categories.append({'name': category, 'total': total})
        categories.sort(key=lambda entry: entry['total'], reverse=True)
        
        # Contract values
        original_contract = float(project.budget or 0)
        total_variations = 0
        total_claims = 0
        revised_contract = original_contract + total_variations + total_claims
        
        # Expenditure
        total_expenditure = 0
        total_commitments = 0
        projected_final_cost = total_expenditure + total_commitments
        
        # Risk assessment
        budget_variance = revised_contract - projected_final_cost
        variance_percentage = (budget_variance / revised_contract * 100) if revised_contract > 0 else 0
        
        if variance_percentage < -10:
            risk_level = 'HIGH'
            risk_color = 'red'
        elif variance_percentage < 0:
            risk_level = 'MEDIUM'
            risk_color = 'yellow'
        else:
            risk_level = 'LOW'
            risk_color = 'green'
        
        return render_template('qs/cost_control.html',
            project=project,
            total_boq=total_boq,
            categories=categories,
            original_contract=original_contract,
            revised_contract=revised_contract,
            total_expenditure=total_expenditure,
            projected_final_cost=projected_final_cost,
            budget_variance=budget_variance,
            variance_percentage=round(variance_percentage, 1),
            risk_level=risk_level,
            risk_color=risk_color
        )
    except Exception as e:
        current_app.logger.error(f"Error loading cost control report for project {project_id}: {str(e)}")
        flash('Error loading cost control report', 'error')
        return redirect(url_for('qs_dashboard.dashboard'))


@reports_bp.route('/project/<int:project_id>/cost-forecast', methods=['GET'])
@login_required
@role_required([Roles.SUPER_HQ, Roles.QS_MANAGER, Roles.QS_STAFF])
def cost_forecast(project_id):
    """View budget forecast and performance indicators"""
    try:
        project = check_project_access(project_id)
        if not project:
            return redirect(url_for('qs_dashboard.dashboard'))
        
        # Get BOQ data
        boq_items = BOQItem.query.filter_by(project_id=project_id).all()
        total_boq = sum(float(item.amount or 0) for item in boq_items)
        total_spent = 0
        
        # Performance indices
        cost_performance_index = 1.0
        schedule_performance_index = 1.0
        work_done_percentage = (total_spent / total_boq * 100) if total_boq > 0 else 0
        
        # Cost to complete
        cost_to_complete = total_boq - total_spent if total_spent < total_boq else 0
        estimated_final_cost = total_spent + cost_to_complete
        
        # Forecast by category
        forecast_by_category = {}
        for item in boq_items:
            category = getattr(item, 'category', None) or item.unit or 'Uncategorized'
            if category not in forecast_by_category:
                forecast_by_category[category] = {'allocated': 0, 'spent': 0}
            forecast_by_category[category]['allocated'] += float(item.amount or 0)
        
        return render_template('qs/cost_forecast.html',
            project=project,
            total_boq=total_boq,
            total_spent=total_spent,
            cost_to_complete=cost_to_complete,
            estimated_final_cost=estimated_final_cost,
            cost_performance_index=round(cost_performance_index, 2),
            schedule_performance_index=round(schedule_performance_index, 2),
            work_done_percentage=round(work_done_percentage, 1),
            forecast_by_category=forecast_by_category
        )
    except Exception as e:
        current_app.logger.error(f"Error loading cost forecast for project {project_id}: {str(e)}")
        flash('Error loading cost forecast', 'error')
        return redirect(url_for('qs_dashboard.dashboard'))


@reports_bp.route('/project/<int:project_id>/valuations', methods=['GET'])
@login_required
@role_required([Roles.SUPER_HQ, Roles.QS_MANAGER, Roles.QS_STAFF])
def project_valuations(project_id):
    """View and manage project valuations and payment certificates"""
    try:
        project = check_project_access(project_id)
        if not project:
            return redirect(url_for('qs_dashboard.dashboard'))
        
        # Placeholder - feature in development
        valuations = []
        boq_items = BOQItem.query.filter_by(project_id=project_id).all()
        total_boq_value = sum(float(item.amount or 0) for item in boq_items)
        
        return render_template('qs/project_valuations.html',
            project=project,
            valuations=valuations,
            total_boq_value=total_boq_value,
            work_done_percentage=0
        )
    except Exception as e:
        current_app.logger.error(f"Error loading valuations for project {project_id}: {str(e)}")
        flash('Error loading valuations', 'error')
        return redirect(url_for('qs_dashboard.dashboard'))


@reports_bp.route('/project/<int:project_id>/claims', methods=['GET'])
@login_required
@role_required([Roles.SUPER_HQ, Roles.QS_MANAGER, Roles.QS_STAFF])
def project_claims(project_id):
    """View and manage project claims"""
    try:
        project = check_project_access(project_id)
        if not project:
            return redirect(url_for('qs_dashboard.dashboard'))
        
        claims = []
        original_contract = float(project.budget or 0)
        
        return render_template('qs/project_claims.html',
            project=project,
            claims=claims,
            original_contract=original_contract
        )
    except Exception as e:
        current_app.logger.error(f"Error loading claims for project {project_id}: {str(e)}")
        flash('Error loading claims', 'error')
        return redirect(url_for('qs_dashboard.dashboard'))


@reports_bp.route('/project/<int:project_id>/variations', methods=['GET'])
@login_required
@role_required([Roles.SUPER_HQ, Roles.QS_MANAGER, Roles.QS_STAFF])
def project_variations(project_id):
    """View project variations"""
    try:
        project = check_project_access(project_id)
        if not project:
            return redirect(url_for('qs_dashboard.dashboard'))
        
        variations = []
        
        return render_template('qs/project_variations.html',
            project=project,
            variations=variations
        )
    except Exception as e:
        current_app.logger.error(f"Error loading variations for project {project_id}: {str(e)}")
        flash('Error loading variations', 'error')
        return redirect(url_for('qs_dashboard.dashboard'))


@reports_bp.route('/project/<int:project_id>/payment-applications', methods=['GET'])
@login_required
@role_required([Roles.SUPER_HQ, Roles.QS_MANAGER, Roles.QS_STAFF])
def payment_applications(project_id):
    """View payment applications for a project"""
    try:
        project = check_project_access(project_id)
        if not project:
            return redirect(url_for('qs_dashboard.dashboard'))
        
        applications = []
        
        return render_template('qs/project_payment_apps.html',
            project=project,
            applications=applications
        )
    except Exception as e:
        current_app.logger.error(f"Error loading payment applications for project {project_id}: {str(e)}")
        flash('Error loading payment applications', 'error')
        return redirect(url_for('qs_dashboard.dashboard'))


@reports_bp.route('/project/<int:project_id>/subcontractor-payments', methods=['GET'])
@login_required
@role_required([Roles.SUPER_HQ, Roles.QS_MANAGER, Roles.QS_STAFF])
def subcontractor_payments(project_id):
    """View subcontractor payments for a project"""
    try:
        project = check_project_access(project_id)
        if not project:
            return redirect(url_for('qs_dashboard.dashboard'))
        
        subcontractors = []
        
        return render_template('qs/project_subcontractor_payments.html',
            project=project,
            subcontractors=subcontractors
        )
    except Exception as e:
        current_app.logger.error(f"Error loading subcontractor payments for project {project_id}: {str(e)}")
        flash('Error loading subcontractor payments', 'error')
        return redirect(url_for('qs_dashboard.dashboard'))
