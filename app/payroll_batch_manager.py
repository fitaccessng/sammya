"""
Payroll Batch Management System
Handles batch lifecycle: creation, calculation, approval, processing, payment
"""

from datetime import datetime, date, timedelta
from decimal import Decimal
from typing import Dict, List, Tuple, Optional
from app.payroll_models import (
    PayrollBatch, PayrollRecord, PayrollApproval, PayrollAuditLog,
    PayrollStatus, ApprovalAction
)
from app.payroll_engine import PayrollCalculationEngine, PayrollLedgerEngine
from app.models import User, db
import logging

logger = logging.getLogger(__name__)


class PayrollBatchManager:
    """Manage payroll batch lifecycle"""
    
    @staticmethod
    def create_batch(
        batch_name: str,
        payroll_period: str,
        start_date: date,
        end_date: date,
        payment_date: date,
        control_count: int = None,
        control_amount: Decimal = None,
        created_by_id: int = None
    ) -> Tuple[PayrollBatch, List[str]]:
        """Create new payroll batch"""
        errors = []
        
        try:
            # Validate inputs
            if not batch_name:
                errors.append('Batch name required')
            if not payroll_period or len(payroll_period) != 7 or payroll_period[4] != '-':
                errors.append('Invalid payroll period format (use YYYY-MM)')
            if end_date <= start_date:
                errors.append('End date must be after start date')
            if payment_date < end_date:
                errors.append('Payment date must be after end date')
            
            if errors:
                return None, errors
            
            # Check for existing batch
            existing = PayrollBatch.query.filter_by(
                payroll_period=payroll_period
            ).first()
            
            if existing:
                errors.append(f'Batch already exists for {payroll_period}')
                return None, errors
            
            # Create batch
            batch = PayrollBatch(
                batch_name=batch_name,
                payroll_period=payroll_period,
                start_date=start_date,
                end_date=end_date,
                payment_date=payment_date,
                control_count=control_count,
                control_amount=control_amount,
                created_by_id=created_by_id,
                status=PayrollStatus.DRAFT
            )
            
            db.session.add(batch)
            db.session.flush()
            
            # Audit log
            if created_by_id:
                log = PayrollAuditLog(
                    batch_id=batch.id,
                    action='create_batch',
                    entity_type='batch',
                    entity_id=batch.id,
                    new_values={
                        'name': batch_name,
                        'period': payroll_period
                    },
                    actor_id=created_by_id,
                    reason='Batch creation'
                )
                db.session.add(log)
            
            db.session.commit()
            return batch, []
            
        except Exception as e:
            db.session.rollback()
            logger.error(f"Batch creation error: {str(e)}")
            errors.append(f'Error: {str(e)}')
            return None, errors
    
    @staticmethod
    def calculate_batch(
        batch_id: int,
        staff_ids: List[int] = None,
        actor_id: int = None
    ) -> Tuple[bool, Dict]:
        """Calculate payroll for batch"""
        result = {
            'success': False,
            'successful': 0,
            'failed': 0,
            'errors': [],
            'message': ''
        }
        
        try:
            batch = PayrollBatch.query.get_or_404(batch_id)
            
            if batch.status != PayrollStatus.DRAFT:
                result['errors'].append(f'Batch must be in DRAFT status')
                return False, result
            
            # Calculate payroll
            successful, failed, errors = PayrollCalculationEngine.calculate_batch_payroll(
                batch, staff_ids, actor_id
            )
            
            result['successful'] = successful
            result['failed'] = failed
            result['errors'] = errors
            result['success'] = failed == 0
            result['message'] = f'Calculated payroll for {successful} staff, {failed} failures'
            
            return result['success'], result
            
        except Exception as e:
            logger.error(f"Batch calculation error: {str(e)}")
            result['errors'].append(f'Error: {str(e)}')
            return False, result
    
    @staticmethod
    def submit_for_approval(batch_id: int, actor_id: int) -> Tuple[bool, str]:
        """Submit batch for HR approval"""
        try:
            batch = PayrollBatch.query.get_or_404(batch_id)
            
            # Validate
            is_valid, errors = PayrollCalculationEngine.validate_batch(batch)
            if not is_valid:
                return False, '; '.join(errors)
            
            # Check user role
            actor = User.query.get(actor_id)
            if actor.role not in ['hr_manager', 'admin']:
                return False, 'Only HR managers can submit batches'
            
            # Change status
            batch.status = PayrollStatus.HR_APPROVED
            
            # Create approval record
            approval = PayrollApproval(
                batch_id=batch.id,
                approval_step=1,
                approval_role='hr_manager',
                action=ApprovalAction.SUBMITTED,
                status='submitted',
                action_by_id=actor_id,
                action_at=datetime.utcnow()
            )
            db.session.add(approval)
            
            # Audit
            log = PayrollAuditLog(
                batch_id=batch.id,
                action='submit_for_approval',
                entity_type='batch',
                entity_id=batch.id,
                new_values={'status': batch.status},
                actor_id=actor_id,
                reason='Submitted for approval'
            )
            db.session.add(log)
            
            db.session.commit()
            return True, f'Batch submitted for approval'
            
        except Exception as e:
            db.session.rollback()
            logger.error(f"Submission error: {str(e)}")
            return False, str(e)
    
    @staticmethod
    def approve_batch(batch_id: int, approval_step: int, actor_id: int, comments: str = None) -> Tuple[bool, str]:
        """
        Approve batch at specific step
        
        Step 1: HR Manager approval
        Step 2: Admin approval  
        Step 3: Finance Manager approval (final)
        """
        try:
            batch = PayrollBatch.query.get_or_404(batch_id)
            actor = User.query.get(actor_id)
            
            # Validate actor role
            step_roles = {
                1: ['hr_manager'],
                2: ['admin'],
                3: ['finance_manager']
            }
            
            if actor.role not in step_roles.get(approval_step, []):
                return False, f'You do not have permission for step {approval_step} approval'
            
            # Check batch status
            expected_status = {
                1: PayrollStatus.DRAFT,
                2: PayrollStatus.HR_APPROVED,
                3: PayrollStatus.ADMIN_APPROVED
            }
            
            if batch.status != expected_status.get(approval_step):
                return False, f'Batch not ready for step {approval_step}'
            
            # Update batch status
            status_map = {
                1: PayrollStatus.HR_APPROVED,
                2: PayrollStatus.ADMIN_APPROVED,
                3: PayrollStatus.FINANCE_PROCESSING
            }
            batch.status = status_map[approval_step]
            
            # Create approval record
            approval = PayrollApproval(
                batch_id=batch.id,
                approval_step=approval_step,
                approval_role=step_roles[approval_step][0],
                action=ApprovalAction.APPROVED,
                status='approved',
                comments=comments,
                action_by_id=actor_id,
                action_at=datetime.utcnow()
            )
            db.session.add(approval)
            
            # Generate GL entries at step 3
            if approval_step == 3:
                PayrollLedgerEngine.generate_gl_entries(batch, actor_id)
            
            # Audit
            log = PayrollAuditLog(
                batch_id=batch.id,
                action=f'approve_step_{approval_step}',
                entity_type='batch',
                entity_id=batch.id,
                new_values={'status': batch.status},
                actor_id=actor_id,
                reason=f'Step {approval_step} approved'
            )
            db.session.add(log)
            
            db.session.commit()
            return True, f'Batch approved at step {approval_step}'
            
        except Exception as e:
            db.session.rollback()
            logger.error(f"Approval error: {str(e)}")
            return False, str(e)
    
    @staticmethod
    def reject_batch(batch_id: int, actor_id: int, rejection_reason: str) -> Tuple[bool, str]:
        """Reject batch with reason"""
        try:
            batch = PayrollBatch.query.get_or_404(batch_id)
            
            batch.status = PayrollStatus.DRAFT  # Back to draft for revision
            batch.rejection_reason = rejection_reason
            
            # Create rejection record
            approval = PayrollApproval(
                batch_id=batch.id,
                approval_step=1,
                action=ApprovalAction.REJECTED,
                status='rejected',
                comments=rejection_reason,
                action_by_id=actor_id,
                action_at=datetime.utcnow()
            )
            db.session.add(approval)
            
            # Audit
            log = PayrollAuditLog(
                batch_id=batch.id,
                action='reject_batch',
                entity_type='batch',
                entity_id=batch.id,
                new_values={'status': batch.status},
                actor_id=actor_id,
                reason=f'Rejected: {rejection_reason}'
            )
            db.session.add(log)
            
            db.session.commit()
            return True, 'Batch rejected and returned to draft'
            
        except Exception as e:
            db.session.rollback()
            logger.error(f"Rejection error: {str(e)}")
            return False, str(e)
    
    @staticmethod
    def mark_as_paid(batch_id: int, actual_payment_date: date, actor_id: int) -> Tuple[bool, str]:
        """Mark batch as paid"""
        try:
            batch = PayrollBatch.query.get_or_404(batch_id)
            
            if batch.status != PayrollStatus.FINANCE_PROCESSING:
                return False, 'Batch must be in FINANCE_PROCESSING status'
            
            batch.status = PayrollStatus.PAID
            batch.actual_payment_date = actual_payment_date
            
            # Update all records as paid
            PayrollRecord.query.filter_by(batch_id=batch.id).update(
                {'payment_status': 'paid'},
                synchronize_session=False
            )
            
            # Audit
            log = PayrollAuditLog(
                batch_id=batch.id,
                action='mark_paid',
                entity_type='batch',
                entity_id=batch.id,
                new_values={
                    'status': batch.status,
                    'payment_date': str(actual_payment_date)
                },
                actor_id=actor_id,
                reason='Marked as paid'
            )
            db.session.add(log)
            
            db.session.commit()
            return True, 'Batch marked as paid'
            
        except Exception as e:
            db.session.rollback()
            logger.error(f"Payment marking error: {str(e)}")
            return False, str(e)
    
    @staticmethod
    def archive_batch(batch_id: int, actor_id: int) -> Tuple[bool, str]:
        """Archive completed batch"""
        try:
            batch = PayrollBatch.query.get_or_404(batch_id)
            
            if batch.status != PayrollStatus.PAID:
                return False, 'Only paid batches can be archived'
            
            batch.status = PayrollStatus.ARCHIVED
            
            # Audit
            log = PayrollAuditLog(
                batch_id=batch.id,
                action='archive_batch',
                entity_type='batch',
                entity_id=batch.id,
                new_values={'status': batch.status},
                actor_id=actor_id,
                reason='Batch archived'
            )
            db.session.add(log)
            
            db.session.commit()
            return True, 'Batch archived'
            
        except Exception as e:
            db.session.rollback()
            logger.error(f"Archive error: {str(e)}")
            return False, str(e)
