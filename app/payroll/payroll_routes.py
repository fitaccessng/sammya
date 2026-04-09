"""
Payroll Dashboard Routes - Complete UI for payroll management
Includes: Batch creation, approval workflow, records viewing, exports
"""

from flask import Blueprint, render_template, request, jsonify, redirect, url_for, flash, send_file
from flask_login import login_required, current_user
from datetime import datetime, date, timedelta
from decimal import Decimal
from functools import wraps
from sqlalchemy import desc, func

from app.models import User, db
from app.payroll_models import (
    PayrollBatch, PayrollRecord, PayrollApproval, SalaryMapping,
    PayrollAuditLog, PayrollExport, PayrollAdjustment, AccountingEntry,
    PayrollStatus
)
from app.payroll_batch_manager import PayrollBatchManager
from app.payroll_engine import PayrollCalculationEngine, PayrollLedgerEngine
from app.payroll_export_engine import PayrollExportEngine

payroll_bp = Blueprint('payroll', __name__, url_prefix='/payroll', template_folder='../templates/payroll')

# ==============================================================================
# ACCESS CONTROL DECORATORS
# ==============================================================================

def require_payroll_role(*roles):
    """Require user to have payroll-related role"""
    def decorator(f):
        @wraps(f)
        def decorated_function(*args, **kwargs):
            if not current_user.is_authenticated:
                flash('Please log in first', 'error')
                return redirect(url_for('auth.login'))
            
            user_role = current_user.role  # Single role field
            allowed = ['admin', 'hr_manager', 'finance_manager']
            
            if user_role not in allowed:
                flash('You do not have permission to access this page', 'error')
                return redirect(url_for('main.dashboard'))
            
            return f(*args, **kwargs)
        return decorated_function
    return decorator


# ==============================================================================
# DASHBOARD & OVERVIEW
# ==============================================================================

@payroll_bp.route('/dashboard')
@login_required
@require_payroll_role()
def dashboard():
    """Main payroll dashboard"""
    user_roles = [r.name for r in current_user.roles]
    
    # Get recent batches
    batches = PayrollBatch.query.order_by(desc(PayrollBatch.created_at)).limit(10).all()
    
    # Get statistics
    total_batches = PayrollBatch.query.count()
    pending_approval = PayrollBatch.query.filter(
        PayrollBatch.status.in_([PayrollStatus.DRAFT, PayrollStatus.HR_APPROVED, PayrollStatus.ADMIN_APPROVED])
    ).count()
    
    # Calculate total payroll processed
    total_processed = db.session.query(func.sum(PayrollBatch.total_net_salary)).filter(
        PayrollBatch.status == PayrollStatus.PAID
    ).scalar() or Decimal('0')
    
    # Staff count
    total_staff_with_mapping = SalaryMapping.query.filter_by(is_active=True).count()
    
    # Recent audit logs
    audit_logs = PayrollAuditLog.query.order_by(desc(PayrollAuditLog.created_at)).limit(20).all()
    
    context = {
        'batches': batches,
        'total_batches': total_batches,
        'pending_approval': pending_approval,
        'total_processed': total_processed,
        'total_staff': total_staff_with_mapping,
        'audit_logs': audit_logs,
        'user_roles': user_roles
    }
    
    return render_template('payroll/dashboard.html', **context)


# ==============================================================================
# SALARY MAPPING MANAGEMENT
# ==============================================================================

@payroll_bp.route('/salary-mapping')
@login_required
@require_payroll_role()
def salary_mapping_list():
    """List all salary mappings"""
    page = request.args.get('page', 1, type=int)
    per_page = request.args.get('per_page', 20, type=int)
    search = request.args.get('search', '')
    
    query = SalaryMapping.query.filter_by(is_active=True)
    
    if search:
        query = query.join(User).filter(
            (User.first_name.ilike(f'%{search}%')) |
            (User.last_name.ilike(f'%{search}%')) |
            (User.email.ilike(f'%{search}%'))
        )
    
    pagination = query.order_by(desc(SalaryMapping.created_at)).paginate(
        page=page, per_page=per_page, error_out=False
    )
    
    return render_template(
        'payroll/salary_mapping_list.html',
        pagination=pagination,
        search=search
    )


@payroll_bp.route('/salary-mapping/<int:user_id>', methods=['GET', 'POST'])
@login_required
@require_payroll_role()
def edit_salary_mapping(user_id):
    """Edit salary mapping for staff"""
    user = User.query.get_or_404(user_id)
    current_mapping = SalaryMapping.query.filter_by(user_id=user_id, is_active=True).first()
    
    if request.method == 'POST':
        try:
            # Deactivate previous mapping
            if current_mapping:
                current_mapping.is_active = False
            
            # Create new mapping
            new_mapping = SalaryMapping(
                user_id=user_id,
                basic_salary=Decimal(request.form.get('basic_salary', 0)),
                house_allowance=Decimal(request.form.get('house_allowance', 0)),
                transport_allowance=Decimal(request.form.get('transport_allowance', 0)),
                meal_allowance=Decimal(request.form.get('meal_allowance', 0)),
                risk_allowance=Decimal(request.form.get('risk_allowance', 0)),
                performance_allowance=Decimal(request.form.get('performance_allowance', 0)),
                tax_amount=Decimal(request.form.get('tax_amount', 0)),
                pension_amount=Decimal(request.form.get('pension_amount', 0)),
                insurance_amount=Decimal(request.form.get('insurance_amount', 0)),
                loan_deduction=Decimal(request.form.get('loan_deduction', 0)),
                other_deduction=Decimal(request.form.get('other_deduction', 0)),
                effective_date=datetime.strptime(request.form.get('effective_date'), '%Y-%m-%d').date(),
                created_by_id=current_user.id,
                notes=request.form.get('notes', '')
            )
            
            db.session.add(new_mapping)
            db.session.commit()
            
            flash(f'Salary mapping updated for {user.full_name}', 'success')
            return redirect(url_for('payroll.salary_mapping_list'))
            
        except Exception as e:
            db.session.rollback()
            flash(f'Error updating salary mapping: {str(e)}', 'error')
    
    return render_template(
        'payroll/edit_salary_mapping.html',
        user=user,
        mapping=current_mapping
    )


# ==============================================================================
# BATCH MANAGEMENT
# ==============================================================================

@payroll_bp.route('/batches')
@login_required
@require_payroll_role()
def batch_list():
    """List all payroll batches"""
    page = request.args.get('page', 1, type=int)
    per_page = request.args.get('per_page', 10, type=int)
    status_filter = request.args.get('status', '')
    
    query = PayrollBatch.query
    
    if status_filter:
        query = query.filter_by(status=status_filter)
    
    pagination = query.order_by(desc(PayrollBatch.created_at)).paginate(
        page=page, per_page=per_page, error_out=False
    )
    
    return render_template(
        'payroll/batch_list.html',
        pagination=pagination,
        status_filter=status_filter,
        statuses=PayrollStatus
    )


@payroll_bp.route('/batches/create', methods=['GET', 'POST'])
@login_required
@require_payroll_role()
def create_batch():
    """Create new payroll batch"""
    if request.method == 'POST':
        try:
            start_date = datetime.strptime(request.form.get('start_date'), '%Y-%m-%d').date()
            end_date = datetime.strptime(request.form.get('end_date'), '%Y-%m-%d').date()
            payment_date = datetime.strptime(request.form.get('payment_date'), '%Y-%m-%d').date()
            
            batch, errors = PayrollBatchManager.create_batch(
                batch_name=request.form.get('batch_name'),
                payroll_period=request.form.get('payroll_period'),
                start_date=start_date,
                end_date=end_date,
                payment_date=payment_date,
                created_by_id=current_user.id
            )
            
            if batch:
                flash(f'Batch "{batch.batch_name}" created successfully', 'success')
                return redirect(url_for('payroll.view_batch', batch_id=batch.id))
            else:
                flash(f'Error creating batch: {", ".join(errors)}', 'error')
                
        except Exception as e:
            flash(f'Error: {str(e)}', 'error')
    
    # Suggest next month
    today = date.today()
    next_month = date(today.year + (today.month // 12), (today.month % 12) + 1, 1)
    month_end = date(next_month.year, next_month.month, 1) - timedelta(days=1)
    
    return render_template(
        'payroll/create_batch.html',
        next_month=next_month,
        month_end=month_end,
        payment_date=next_month.replace(day=5)
    )


@payroll_bp.route('/batches/<int:batch_id>')
@login_required
@require_payroll_role()
def view_batch(batch_id):
    """View batch details"""
    batch = PayrollBatch.query.get_or_404(batch_id)
    
    # Get records for this batch
    page = request.args.get('page', 1, type=int)
    records_pagination = PayrollRecord.query.filter_by(batch_id=batch_id).paginate(
        page=page, per_page=20, error_out=False
    )
    
    # Get approval history
    approvals = PayrollApproval.query.filter_by(batch_id=batch_id).order_by(
        PayrollApproval.step.asc()
    ).all()
    
    # Get audit logs
    audit_logs = PayrollAuditLog.query.filter_by(batch_id=batch_id).order_by(
        desc(PayrollAuditLog.created_at)
    ).all()
    
    context = {
        'batch': batch,
        'records_pagination': records_pagination,
        'approvals': approvals,
        'audit_logs': audit_logs,
        'can_edit': batch.status == PayrollStatus.DRAFT,
        'can_calculate': batch.status == PayrollStatus.DRAFT,
        'can_submit': batch.status == PayrollStatus.DRAFT and batch.payroll_records,
        'can_approve': current_user.has_role('admin') or current_user.has_role('finance_manager'),
        'user_roles': [r.name for r in current_user.roles]
    }
    
    return render_template('payroll/view_batch.html', **context)


@payroll_bp.route('/batches/<int:batch_id>/calculate', methods=['POST'])
@login_required
@require_payroll_role()
def calculate_batch(batch_id):
    """Calculate payroll for batch"""
    batch = PayrollBatch.query.get_or_404(batch_id)
    
    if batch.status != PayrollStatus.DRAFT:
        return jsonify({'success': False, 'message': 'Batch must be in DRAFT status'}), 400
    
    try:
        success, result = PayrollBatchManager.calculate_batch(batch_id, current_user.id)
        
        if success:
            flash(f'Payroll calculated: {result["successful"]} records, {result["failed"]} failures', 'success')
            return redirect(url_for('payroll.view_batch', batch_id=batch_id))
        else:
            flash(f'Error during calculation: {result}', 'error')
            
    except Exception as e:
        flash(f'Error: {str(e)}', 'error')
    
    return redirect(url_for('payroll.view_batch', batch_id=batch_id))


# ==============================================================================
# APPROVAL WORKFLOW
# ==============================================================================

@payroll_bp.route('/batches/<int:batch_id>/approve', methods=['GET', 'POST'])
@login_required
@require_payroll_role()
def approve_batch(batch_id):
    """Approve payroll batch"""
    batch = PayrollBatch.query.get_or_404(batch_id)
    user_roles = [r.name for r in current_user.roles]
    
    # Determine approval step based on status and role
    if batch.status == PayrollStatus.DRAFT:
        flash('Batch must be submitted for approval first', 'error')
        return redirect(url_for('payroll.view_batch', batch_id=batch_id))
    
    if request.method == 'POST':
        try:
            comments = request.form.get('comments', '')
            
            if batch.status == PayrollStatus.HR_APPROVED and 'admin' in user_roles:
                step = 1
            elif batch.status == PayrollStatus.ADMIN_APPROVED and 'finance_manager' in user_roles:
                step = 2
            else:
                flash('You do not have permission to approve this batch at this step', 'error')
                return redirect(url_for('payroll.view_batch', batch_id=batch_id))
            
            success, msg = PayrollBatchManager.approve_batch(batch_id, step, current_user.id, comments)
            
            if success:
                flash(msg, 'success')
                return redirect(url_for('payroll.view_batch', batch_id=batch_id))
            else:
                flash(f'Error: {msg}', 'error')
                
        except Exception as e:
            flash(f'Error: {str(e)}', 'error')
    
    return render_template(
        'payroll/approve_batch.html',
        batch=batch,
        user_roles=user_roles
    )


@payroll_bp.route('/batches/<int:batch_id>/reject', methods=['POST'])
@login_required
@require_payroll_role()
def reject_batch(batch_id):
    """Reject payroll batch"""
    batch = PayrollBatch.query.get_or_404(batch_id)
    
    try:
        reason = request.form.get('reason', 'No reason provided')
        success, msg = PayrollBatchManager.reject_batch(batch_id, reason, current_user.id)
        
        if success:
            flash(msg, 'success')
        else:
            flash(f'Error: {msg}', 'error')
            
    except Exception as e:
        flash(f'Error: {str(e)}', 'error')
    
    return redirect(url_for('payroll.view_batch', batch_id=batch_id))


@payroll_bp.route('/batches/<int:batch_id>/submit', methods=['POST'])
@login_required
@require_payroll_role()
def submit_batch(batch_id):
    """Submit batch for approval"""
    batch = PayrollBatch.query.get_or_404(batch_id)
    
    if batch.status != PayrollStatus.DRAFT:
        flash('Batch must be in DRAFT status', 'error')
        return redirect(url_for('payroll.view_batch', batch_id=batch_id))
    
    try:
        success, msg = PayrollBatchManager.submit_for_approval(batch_id, current_user.id)
        
        if success:
            flash('Batch submitted for approval', 'success')
        else:
            flash(f'Error: {msg}', 'error')
            
    except Exception as e:
        flash(f'Error: {str(e)}', 'error')
    
    return redirect(url_for('payroll.view_batch', batch_id=batch_id))


# ==============================================================================
# EXPORTS
# ==============================================================================

@payroll_bp.route('/batches/<int:batch_id>/export/bank-payment/<format>')
@login_required
@require_payroll_role()
def export_bank_payment(batch_id, format):
    """Export bank payment file"""
    batch = PayrollBatch.query.get_or_404(batch_id)
    
    if format not in ['csv', 'excel', 'txt']:
        flash('Invalid export format', 'error')
        return redirect(url_for('payroll.view_batch', batch_id=batch_id))
    
    try:
        success, result = PayrollExportEngine.generate_bank_payment_export(batch_id, format)
        
        if success:
            file_path = result['file_path']
            filename = result['filename']
            return send_file(file_path, as_attachment=True, download_name=filename)
        else:
            flash(f'Export error: {result}', 'error')
            
    except Exception as e:
        flash(f'Error: {str(e)}', 'error')
    
    return redirect(url_for('payroll.view_batch', batch_id=batch_id))


@payroll_bp.route('/batches/<int:batch_id>/export/tax')
@login_required
@require_payroll_role()
def export_tax(batch_id):
    """Export tax remittance file"""
    batch = PayrollBatch.query.get_or_404(batch_id)
    
    try:
        success, result = PayrollExportEngine.generate_tax_export(batch_id)
        
        if success:
            file_path = result['file_path']
            filename = result['filename']
            return send_file(file_path, as_attachment=True, download_name=filename)
        else:
            flash(f'Export error: {result}', 'error')
            
    except Exception as e:
        flash(f'Error: {str(e)}', 'error')
    
    return redirect(url_for('payroll.view_batch', batch_id=batch_id))


@payroll_bp.route('/batches/<int:batch_id>/export/pension')
@login_required
@require_payroll_role()
def export_pension(batch_id):
    """Export pension remittance file"""
    batch = PayrollBatch.query.get_or_404(batch_id)
    
    try:
        success, result = PayrollExportEngine.generate_pension_export(batch_id)
        
        if success:
            file_path = result['file_path']
            filename = result['filename']
            return send_file(file_path, as_attachment=True, download_name=filename)
        else:
            flash(f'Export error: {result}', 'error')
            
    except Exception as e:
        flash(f'Error: {str(e)}', 'error')
    
    return redirect(url_for('payroll.view_batch', batch_id=batch_id))


# ==============================================================================
# AUDIT LOGS
# ==============================================================================

@payroll_bp.route('/batches/<int:batch_id>/audit-logs')
@login_required
@require_payroll_role()
def audit_logs(batch_id):
    """View audit logs for batch"""
    batch = PayrollBatch.query.get_or_404(batch_id)
    
    page = request.args.get('page', 1, type=int)
    logs_pagination = PayrollAuditLog.query.filter_by(batch_id=batch_id).order_by(
        desc(PayrollAuditLog.created_at)
    ).paginate(page=page, per_page=50, error_out=False)
    
    return render_template(
        'payroll/audit_logs.html',
        batch=batch,
        logs_pagination=logs_pagination
    )


# ==============================================================================
# HEALTH CHECK
# ==============================================================================

@payroll_bp.route('/health')
def health():
    """Health check endpoint"""
    return jsonify({'status': 'ok', 'module': 'payroll'}), 200
