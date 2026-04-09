"""
Enterprise Payroll Calculation Engine
Handles all payroll calculations with validation, versioning, and audit trail
"""

from decimal import Decimal, ROUND_HALF_UP
from datetime import datetime, date
from typing import Dict, List, Tuple, Optional
from app.payroll_models import (
    PayrollRecord, PayrollBatch, SalaryMapping, PayrollAdjustment,
    PayrollAuditLog, AccountingEntry, PayrollStatus
)
from app.models import User, db
import logging

logger = logging.getLogger(__name__)


class PayrollCalculationEngine:
    """Core payroll calculation engine"""
    
    # Account codes for GL entries
    GL_EXPENSE_SALARY = '6010'  # Salary Expense
    GL_EXPENSE_ALLOWANCE = '6020'  # Allowance Expense
    GL_LIABILITY_TAX = '2100'  # Tax Payable
    GL_LIABILITY_PENSION = '2110'  # Pension Payable
    GL_LIABILITY_INSURANCE = '2120'  # Insurance Payable
    GL_LIABILITY_LOAN = '2130'  # Loan Deduction Payable
    GL_BANK = '1010'  # Bank Account
    
    @staticmethod
    def calculate_staff_payroll(
        user: User,
        payroll_period: str,
        batch: PayrollBatch,
        overrides: Dict = None,
        actor_id: int = None
    ) -> Tuple[PayrollRecord, List[str]]:
        """
        Calculate payroll for a single staff member
        
        Args:
            user: User object (staff)
            payroll_period: String in format YYYY-MM
            batch: PayrollBatch object
            overrides: Dict of field overrides {field: value}
            actor_id: User ID performing the action
            
        Returns:
            Tuple of (PayrollRecord, validation_errors)
        """
        errors = []
        
        try:
            # Get active salary mapping
            salary_mapping = SalaryMapping.query.filter_by(
                user_id=user.id,
                is_active=True
            ).first()
            
            if not salary_mapping:
                errors.append(f'No active salary mapping for {user.name}')
                return None, errors
            
            # Create payroll record
            record = PayrollRecord(
                batch_id=batch.id,
                user_id=user.id,
                payroll_period=payroll_period
            )
            
            # ===== BASIC SALARY =====
            record.basic_salary = Decimal(salary_mapping.basic_salary or 0)
            
            # ===== ALLOWANCES =====
            record.house_allowance = Decimal(salary_mapping.house_allowance or 0)
            record.transport_allowance = Decimal(salary_mapping.transport_allowance or 0)
            record.meal_allowance = Decimal(salary_mapping.meal_allowance or 0)
            record.risk_allowance = Decimal(salary_mapping.risk_allowance or 0)
            record.performance_allowance = Decimal(salary_mapping.performance_allowance or 0)
            record.other_allowances = Decimal(salary_mapping.other_allowances or 0)
            
            record.total_allowances = (
                record.house_allowance +
                record.transport_allowance +
                record.meal_allowance +
                record.risk_allowance +
                record.performance_allowance +
                record.other_allowances
            )
            
            # ===== GROSS CALCULATION =====
            record.gross_salary = record.basic_salary + record.total_allowances
            
            # ===== DEDUCTIONS =====
            record.tax_deduction = Decimal(salary_mapping.tax_amount or 0)
            record.pension_deduction = Decimal(salary_mapping.pension_amount or 0)
            record.insurance_deduction = Decimal(salary_mapping.insurance_amount or 0)
            record.loan_deduction = Decimal(salary_mapping.loan_amount or 0)
            record.other_deductions = Decimal(salary_mapping.other_deductions or 0)
            
            record.total_deductions = (
                record.tax_deduction +
                record.pension_deduction +
                record.insurance_deduction +
                record.loan_deduction +
                record.other_deductions
            )
            
            # ===== ADJUSTMENTS =====
            adjustments = PayrollAdjustment.query.filter_by(
                user_id=user.id,
                payroll_period=payroll_period,
                is_applied=False
            ).all()
            
            adjustment_dict = {}
            total_adjustments = Decimal(0)
            for adj in adjustments:
                adjustment_dict[adj.adjustment_type.value] = float(adj.amount)
                total_adjustments += Decimal(adj.amount or 0)
            
            record.adjustments = adjustment_dict
            record.total_adjustments = total_adjustments
            
            # ===== NET CALCULATION =====
            record.calculate_net()
            
            # ===== APPLY OVERRIDES =====
            if overrides:
                PayrollCalculationEngine._apply_overrides(record, overrides)
            
            # ===== VALIDATION =====
            record.validate()
            errors = record.validation_errors
            
            # Save record
            db.session.add(record)
            db.session.flush()
            
            # ===== AUDIT LOG =====
            if actor_id:
                log = PayrollAuditLog(
                    batch_id=batch.id,
                    user_id=user.id,
                    action='calculate_payroll',
                    entity_type='record',
                    entity_id=record.id,
                    new_values={
                        'basic': float(record.basic_salary),
                        'allowances': float(record.total_allowances),
                        'gross': float(record.gross_salary),
                        'deductions': float(record.total_deductions),
                        'adjustments': float(record.total_adjustments),
                        'net': float(record.net_salary)
                    },
                    actor_id=actor_id,
                    reason='Payroll calculation'
                )
                db.session.add(log)
            
            return record, errors
            
        except Exception as e:
            logger.error(f"Payroll calculation error for user {user.id}: {str(e)}")
            errors.append(f'Calculation error: {str(e)}')
            return None, errors
    
    @staticmethod
    def _apply_overrides(record: PayrollRecord, overrides: Dict):
        """Apply field overrides to payroll record"""
        allowed_fields = [
            'basic_salary', 'house_allowance', 'transport_allowance',
            'meal_allowance', 'risk_allowance', 'performance_allowance',
            'other_allowances', 'tax_deduction', 'pension_deduction',
            'insurance_deduction', 'loan_deduction', 'other_deductions'
        ]
        
        for field, value in overrides.items():
            if field in allowed_fields and value is not None:
                setattr(record, field, Decimal(value))
        
        # Recalculate totals
        record.total_allowances = (
            (record.house_allowance or 0) +
            (record.transport_allowance or 0) +
            (record.meal_allowance or 0) +
            (record.risk_allowance or 0) +
            (record.performance_allowance or 0) +
            (record.other_allowances or 0)
        )
        
        record.total_deductions = (
            (record.tax_deduction or 0) +
            (record.pension_deduction or 0) +
            (record.insurance_deduction or 0) +
            (record.loan_deduction or 0) +
            (record.other_deductions or 0)
        )
        
        record.gross_salary = record.basic_salary + record.total_allowances
        record.calculate_net()
    
    @staticmethod
    def calculate_batch_payroll(
        batch: PayrollBatch,
        staff_ids: List[int] = None,
        actor_id: int = None
    ) -> Tuple[int, int, List[str]]:
        """
        Calculate payroll for entire batch
        
        Args:
            batch: PayrollBatch object
            staff_ids: Optional list of specific staff IDs
            actor_id: User ID performing the action
            
        Returns:
            Tuple of (successful_count, failed_count, error_messages)
        """
        successful = 0
        failed = 0
        errors = []
        
        try:
            # Get staff to process
            query = User.query.filter_by(is_active=True)
            if staff_ids:
                query = query.filter(User.id.in_(staff_ids))
            
            staff_list = query.all()
            batch.total_records = len(staff_list)
            
            for staff in staff_list:
                record, calc_errors = PayrollCalculationEngine.calculate_staff_payroll(
                    staff, batch.payroll_period, batch, actor_id=actor_id
                )
                
                if record and not calc_errors:
                    successful += 1
                else:
                    failed += 1
                    errors.extend(calc_errors)
            
            # Update batch summary
            batch.successfully_processed = successful
            batch.failed_records = failed
            PayrollCalculationEngine._update_batch_summary(batch)
            
            # Audit log
            if actor_id:
                log = PayrollAuditLog(
                    batch_id=batch.id,
                    action='calculate_batch',
                    entity_type='batch',
                    entity_id=batch.id,
                    new_values={
                        'total': batch.total_records,
                        'successful': successful,
                        'failed': failed
                    },
                    actor_id=actor_id,
                    reason='Batch payroll calculation'
                )
                db.session.add(log)
            
            db.session.commit()
            return successful, failed, errors
            
        except Exception as e:
            db.session.rollback()
            logger.error(f"Batch calculation error: {str(e)}")
            return 0, batch.total_records, [f'Batch error: {str(e)}']
    
    @staticmethod
    def _update_batch_summary(batch: PayrollBatch):
        """Update batch financial summary from records"""
        records = PayrollRecord.query.filter_by(batch_id=batch.id).all()
        
        batch.total_basic_salary = sum(Decimal(r.basic_salary or 0) for r in records)
        batch.total_allowances = sum(Decimal(r.total_allowances or 0) for r in records)
        batch.total_gross = sum(Decimal(r.gross_salary or 0) for r in records)
        batch.total_deductions = sum(Decimal(r.total_deductions or 0) for r in records)
        batch.total_adjustments = sum(Decimal(r.total_adjustments or 0) for r in records)
        batch.total_net = sum(Decimal(r.net_salary or 0) for r in records)
    
    @staticmethod
    def validate_batch(batch: PayrollBatch) -> Tuple[bool, List[str]]:
        """Validate entire payroll batch before approval"""
        errors = []
        warnings = []
        
        # Check batch status
        if batch.status != PayrollStatus.DRAFT:
            errors.append(f'Batch must be in DRAFT status (current: {batch.status})')
        
        # Check for records
        record_count = PayrollRecord.query.filter_by(batch_id=batch.id).count()
        if record_count == 0:
            errors.append('No payroll records in batch')
        
        # Check for invalid records
        invalid_records = PayrollRecord.query.filter_by(
            batch_id=batch.id,
            is_valid=False
        ).count()
        if invalid_records > 0:
            errors.append(f'{invalid_records} records have validation errors')
        
        # Check control totals
        if batch.control_count and batch.total_records != batch.control_count:
            warnings.append(f'Record count mismatch: expected {batch.control_count}, got {batch.total_records}')
        
        if batch.control_amount and batch.total_net != batch.control_amount:
            warnings.append(f'Amount mismatch: expected {batch.control_amount}, got {batch.total_net}')
        
        is_valid = len(errors) == 0
        return is_valid, errors + warnings


class PayrollLedgerEngine:
    """Generate accounting ledger entries from payroll"""
    
    @staticmethod
    def generate_gl_entries(batch: PayrollBatch, actor_id: int = None):
        """Generate GL entries for payroll batch"""
        entries_created = 0
        
        try:
            # Summary GL entries (instead of individual records)
            total_basic = sum(Decimal(r.basic_salary or 0) for r in batch.records)
            total_allowance = sum(Decimal(r.total_allowances or 0) for r in batch.records)
            total_deductions = sum(Decimal(r.total_deductions or 0) for r in batch.records)
            total_net = sum(Decimal(r.net_salary or 0) for r in batch.records)
            
            # 1. Salary Expense (Debit)
            if total_basic > 0:
                entry = AccountingEntry(
                    batch_id=batch.id,
                    account_code=PayrollCalculationEngine.GL_EXPENSE_SALARY,
                    account_name='Salary Expense',
                    debit_amount=total_basic,
                    reference=f'PAYROLL-{batch.id}',
                    description=f'Salary for {batch.payroll_period}'
                )
                db.session.add(entry)
                entries_created += 1
            
            # 2. Allowance Expense (Debit)
            if total_allowance > 0:
                entry = AccountingEntry(
                    batch_id=batch.id,
                    account_code=PayrollCalculationEngine.GL_EXPENSE_ALLOWANCE,
                    account_name='Allowance Expense',
                    debit_amount=total_allowance,
                    reference=f'PAYROLL-{batch.id}',
                    description=f'Allowances for {batch.payroll_period}'
                )
                db.session.add(entry)
                entries_created += 1
            
            # 3. Liability entries by deduction type
            tax_total = sum(Decimal(r.tax_deduction or 0) for r in batch.records)
            if tax_total > 0:
                entry = AccountingEntry(
                    batch_id=batch.id,
                    account_code=PayrollCalculationEngine.GL_LIABILITY_TAX,
                    account_name='Tax Payable',
                    credit_amount=tax_total,
                    reference=f'PAYROLL-{batch.id}',
                    description=f'Tax deductions for {batch.payroll_period}'
                )
                db.session.add(entry)
                entries_created += 1
            
            # 4. Bank Payment (Credit)
            if total_net > 0:
                entry = AccountingEntry(
                    batch_id=batch.id,
                    account_code=PayrollCalculationEngine.GL_BANK,
                    account_name='Bank Account',
                    credit_amount=total_net,
                    reference=f'PAYROLL-{batch.id}',
                    description=f'Net payroll payment for {batch.payroll_period}'
                )
                db.session.add(entry)
                entries_created += 1
            
            db.session.commit()
            
            # Audit log
            if actor_id:
                log = PayrollAuditLog(
                    batch_id=batch.id,
                    action='generate_gl_entries',
                    entity_type='batch',
                    entity_id=batch.id,
                    new_values={'entries_created': entries_created},
                    actor_id=actor_id,
                    reason='GL entry generation'
                )
                db.session.add(log)
                db.session.commit()
            
            return entries_created
            
        except Exception as e:
            db.session.rollback()
            logger.error(f"GL entry generation error: {str(e)}")
            raise
