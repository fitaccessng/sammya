"""
Enterprise Payroll API Endpoints
REST API for payroll operations with role-based access control
"""

from flask import Blueprint, request, jsonify, current_app
from flask_login import login_required, current_user
from app.models import User, db
from app.payroll_models import (
    PayrollBatch, PayrollRecord, SalaryMapping, PayrollAdjustment,
    PayrollApproval, PayrollAuditLog, PayrollExport
)
from app.payroll_batch_manager import PayrollBatchManager
from app.payroll_engine import PayrollCalculationEngine
from app.payroll_export_engine import PayrollExportEngine
from datetime import datetime, date
from decimal import Decimal
import logging

logger = logging.getLogger(__name__)

bp = Blueprint('payroll_api', __name__, url_prefix='/api/payroll')


def require_payroll_role(*roles):
    """Decorator for payroll role access control"""
    def decorator(f):
        def decorated_function(*args, **kwargs):
            if current_user.role not in roles:
                return jsonify({'error': 'Insufficient permissions'}), 403
            return f(*args, **kwargs)
        decorated_function.__name__ = f.__name__
        return decorated_function
    return decorator


# ==================== SALARY MAPPING ENDPOINTS ====================

@bp.route('/salary-mapping/<int:user_id>', methods=['GET'])
@login_required
def get_salary_mapping(user_id):
    """Get active salary mapping for user"""
    try:
        # HR and Admin can view, employees can view own
        if current_user.id != user_id and current_user.role not in ['hr_manager', 'admin']:
            return jsonify({'error': 'Unauthorized'}), 403
        
        mapping = SalaryMapping.query.filter_by(
            user_id=user_id,
            is_active=True
        ).first_or_404()
        
        return jsonify({
            'id': mapping.id,
            'user_id': mapping.user_id,
            'basic_salary': float(mapping.basic_salary),
            'house_allowance': float(mapping.house_allowance),
            'transport_allowance': float(mapping.transport_allowance),
            'meal_allowance': float(mapping.meal_allowance),
            'tax_amount': float(mapping.tax_amount),
            'pension_amount': float(mapping.pension_amount),
            'insurance_amount': float(mapping.insurance_amount),
            'total_allowances': float(mapping.get_total_allowances()),
            'total_deductions': float(mapping.get_total_deductions()),
            'gross_salary': float(mapping.get_gross_salary()),
            'effective_date': mapping.effective_date.isoformat(),
            'end_date': mapping.end_date.isoformat() if mapping.end_date else None
        })
    except Exception as e:
        logger.error(f"Salary mapping fetch error: {str(e)}")
        return jsonify({'error': str(e)}), 500


@bp.route('/salary-mapping/<int:user_id>', methods=['PUT'])
@login_required
@require_payroll_role('hr_manager', 'admin')
def update_salary_mapping(user_id):
    """Update salary mapping"""
    try:
        data = request.get_json()
        
        # Get current mapping
        old_mapping = SalaryMapping.query.filter_by(
            user_id=user_id,
            is_active=True
        ).first()
        
        if old_mapping:
            old_mapping.is_active = False
            old_mapping.end_date = date.today()
        
        # Create new mapping
        mapping = SalaryMapping(
            user_id=user_id,
            basic_salary=data.get('basic_salary', 0),
            house_allowance=data.get('house_allowance', 0),
            transport_allowance=data.get('transport_allowance', 0),
            meal_allowance=data.get('meal_allowance', 0),
            risk_allowance=data.get('risk_allowance', 0),
            tax_amount=data.get('tax_amount', 0),
            pension_amount=data.get('pension_amount', 0),
            insurance_amount=data.get('insurance_amount', 0),
            loan_amount=data.get('loan_amount', 0),
            effective_date=data.get('effective_date', date.today()),
            created_by_id=current_user.id,
            version=old_mapping.version + 1 if old_mapping else 1
        )
        
        db.session.add(mapping)
        db.session.commit()
        
        return jsonify({
            'message': 'Salary mapping updated',
            'id': mapping.id
        }), 201
        
    except Exception as e:
        db.session.rollback()
        logger.error(f"Salary update error: {str(e)}")
        return jsonify({'error': str(e)}), 500


# ==================== PAYROLL BATCH ENDPOINTS ====================

@bp.route('/batches', methods=['POST'])
@login_required
@require_payroll_role('hr_manager', 'admin')
def create_batch():
    """Create new payroll batch"""
    try:
        data = request.get_json()
        
        batch, errors = PayrollBatchManager.create_batch(
            batch_name=data.get('batch_name'),
            payroll_period=data.get('payroll_period'),
            start_date=datetime.fromisoformat(data.get('start_date')).date(),
            end_date=datetime.fromisoformat(data.get('end_date')).date(),
            payment_date=datetime.fromisoformat(data.get('payment_date')).date(),
            control_count=data.get('control_count'),
            control_amount=Decimal(data.get('control_amount', 0)),
            created_by_id=current_user.id
        )
        
        if not batch:
            return jsonify({'errors': errors}), 400
        
        return jsonify({
            'message': 'Batch created',
            'batch_id': batch.id,
            'period': batch.payroll_period
        }), 201
        
    except Exception as e:
        logger.error(f"Batch creation error: {str(e)}")
        return jsonify({'error': str(e)}), 500


@bp.route('/batches/<int:batch_id>', methods=['GET'])
@login_required
def get_batch(batch_id):
    """Get batch details"""
    try:
        batch = PayrollBatch.query.get_or_404(batch_id)
        
        return jsonify({
            'id': batch.id,
            'batch_name': batch.batch_name,
            'payroll_period': batch.payroll_period,
            'status': batch.status.value,
            'total_records': batch.total_records,
            'successfully_processed': batch.successfully_processed,
            'failed_records': batch.failed_records,
            'total_net': float(batch.total_net),
            'start_date': batch.start_date.isoformat(),
            'end_date': batch.end_date.isoformat(),
            'payment_date': batch.payment_date.isoformat() if batch.payment_date else None,
            'created_at': batch.created_at.isoformat()
        })
        
    except Exception as e:
        logger.error(f"Batch fetch error: {str(e)}")
        return jsonify({'error': str(e)}), 500


@bp.route('/batches/<int:batch_id>/calculate', methods=['POST'])
@login_required
@require_payroll_role('hr_manager', 'admin')
def calculate_batch(batch_id):
    """Calculate payroll for batch"""
    try:
        data = request.get_json() or {}
        staff_ids = data.get('staff_ids')
        
        success, result = PayrollBatchManager.calculate_batch(
            batch_id,
            staff_ids=staff_ids,
            actor_id=current_user.id
        )
        
        if success:
            return jsonify(result), 200
        else:
            return jsonify({'errors': result['errors']}), 400
            
    except Exception as e:
        logger.error(f"Batch calculation error: {str(e)}")
        return jsonify({'error': str(e)}), 500


@bp.route('/batches/<int:batch_id>/submit', methods=['POST'])
@login_required
@require_payroll_role('hr_manager')
def submit_batch(batch_id):
    """Submit batch for approval"""
    try:
        success, message = PayrollBatchManager.submit_for_approval(
            batch_id,
            current_user.id
        )
        
        if success:
            return jsonify({'message': message}), 200
        else:
            return jsonify({'error': message}), 400
            
    except Exception as e:
        logger.error(f"Submission error: {str(e)}")
        return jsonify({'error': str(e)}), 500


@bp.route('/batches/<int:batch_id>/approve', methods=['POST'])
@login_required
def approve_batch(batch_id):
    """Approve batch at current step"""
    try:
        data = request.get_json()
        approval_step = data.get('approval_step', 1)
        comments = data.get('comments')
        
        success, message = PayrollBatchManager.approve_batch(
            batch_id,
            approval_step,
            current_user.id,
            comments
        )
        
        if success:
            return jsonify({'message': message}), 200
        else:
            return jsonify({'error': message}), 400
            
    except Exception as e:
        logger.error(f"Approval error: {str(e)}")
        return jsonify({'error': str(e)}), 500


@bp.route('/batches/<int:batch_id>/reject', methods=['POST'])
@login_required
def reject_batch(batch_id):
    """Reject batch"""
    try:
        data = request.get_json()
        rejection_reason = data.get('rejection_reason')
        
        success, message = PayrollBatchManager.reject_batch(
            batch_id,
            current_user.id,
            rejection_reason
        )
        
        if success:
            return jsonify({'message': message}), 200
        else:
            return jsonify({'error': message}), 400
            
    except Exception as e:
        logger.error(f"Rejection error: {str(e)}")
        return jsonify({'error': str(e)}), 500


# ==================== PAYROLL RECORDS ENDPOINTS ====================

@bp.route('/batches/<int:batch_id>/records', methods=['GET'])
@login_required
def get_batch_records(batch_id):
    """Get all records in batch"""
    try:
        page = request.args.get('page', 1, type=int)
        per_page = request.args.get('per_page', 50, type=int)
        
        pagination = PayrollRecord.query.filter_by(batch_id=batch_id).paginate(
            page=page, per_page=per_page
        )
        
        records = []
        for record in pagination.items:
            records.append({
                'id': record.id,
                'user_name': record.user.name if record.user else '',
                'basic_salary': float(record.basic_salary),
                'gross_salary': float(record.gross_salary),
                'total_deductions': float(record.total_deductions),
                'net_salary': float(record.net_salary),
                'is_valid': record.is_valid,
                'validation_errors': record.validation_errors
            })
        
        return jsonify({
            'records': records,
            'total': pagination.total,
            'pages': pagination.pages,
            'current_page': page
        })
        
    except Exception as e:
        logger.error(f"Records fetch error: {str(e)}")
        return jsonify({'error': str(e)}), 500


# ==================== EXPORT ENDPOINTS ====================

@bp.route('/batches/<int:batch_id>/export/bank-payment', methods=['POST'])
@login_required
@require_payroll_role('finance_manager', 'admin')
def export_bank_payment(batch_id):
    """Generate bank payment export"""
    try:
        export_format = request.json.get('format', 'csv') if request.json else 'csv'
        
        success, result = PayrollExportEngine.generate_bank_payment_export(
            batch_id, export_format
        )
        
        if success:
            return jsonify({
                'message': 'Bank export generated',
                'file_name': result['file_name'],
                'record_count': result['record_count'],
                'total_amount': float(result['total_amount'])
            }), 201
        else:
            return jsonify({'error': result['error']}), 400
            
    except Exception as e:
        logger.error(f"Bank export error: {str(e)}")
        return jsonify({'error': str(e)}), 500


@bp.route('/batches/<int:batch_id>/export/tax', methods=['POST'])
@login_required
@require_payroll_role('finance_manager', 'admin')
def export_tax(batch_id):
    """Generate tax remittance export"""
    try:
        success, result = PayrollExportEngine.generate_tax_export(batch_id)
        
        if success:
            return jsonify({
                'message': 'Tax export generated',
                'file_name': result['file_path'].split('/')[-1],
                'total_tax': float(result['total_tax'])
            }), 201
        else:
            return jsonify({'error': result['error']}), 400
            
    except Exception as e:
        logger.error(f"Tax export error: {str(e)}")
        return jsonify({'error': str(e)}), 500


# ==================== AUDIT LOG ENDPOINTS ====================

@bp.route('/batches/<int:batch_id>/audit-logs', methods=['GET'])
@login_required
@require_payroll_role('admin')
def get_audit_logs(batch_id):
    """Get audit trail for batch"""
    try:
        logs = PayrollAuditLog.query.filter_by(batch_id=batch_id).order_by(
            PayrollAuditLog.created_at.desc()
        ).all()
        
        audit_trail = []
        for log in logs:
            audit_trail.append({
                'id': log.id,
                'action': log.action,
                'actor': log.actor.name if log.actor else 'System',
                'timestamp': log.created_at.isoformat(),
                'description': f"{log.action} by {log.actor.name if log.actor else 'System'} at {log.created_at}",
                'reason': log.reason
            })
        
        return jsonify({'audit_logs': audit_trail})
        
    except Exception as e:
        logger.error(f"Audit fetch error: {str(e)}")
        return jsonify({'error': str(e)}), 500


# ==================== HEALTH CHECK ====================

@bp.route('/health', methods=['GET'])
def health_check():
    """Payroll module health check"""
    return jsonify({'status': 'ok', 'module': 'payroll'}), 200
