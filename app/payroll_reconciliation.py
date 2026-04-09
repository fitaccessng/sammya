"""
Payroll Reconciliation Module - Bank/GL Matching and Verification
"""

from datetime import datetime, date
from decimal import Decimal
from typing import List, Dict, Tuple, Optional
from enum import Enum

from app.payroll_models import (
    PayrollBatch, PayrollRecord, PayrollExport, AccountingEntry,
    PayrollStatus
)
from app.models import db
from sqlalchemy import func

# ==============================================================================
# RECONCILIATION STATUS ENUMS
# ==============================================================================

class ReconciliationStatus(str, Enum):
    """Status of reconciliation"""
    PENDING = "PENDING"
    MATCHED = "MATCHED"
    PARTIAL = "PARTIAL"
    UNMATCHED = "UNMATCHED"
    CLEARED = "CLEARED"


# ==============================================================================
# BANK RECONCILIATION
# ==============================================================================

class BankReconciliation:
    """Reconcile payroll exports with bank statements"""
    
    @staticmethod
    def reconcile_batch_with_bank(batch: PayrollBatch, 
                                   bank_records: List[Dict]) -> Dict:
        """
        Reconcile batch against bank records
        
        bank_records format: [
            {
                'date': '2026-02-05',
                'amount': 450000,
                'beneficiary': 'John Doe',
                'reference': 'JD/2026-02',
                'status': 'cleared'  # cleared, pending, failed
            }
        ]
        """
        
        batch_records = batch.payroll_records
        reconciliation_report = {
            'batch_id': batch.id,
            'batch_name': batch.batch_name,
            'payroll_period': batch.payroll_period,
            'reconciliation_date': datetime.now().isoformat(),
            'total_batch_amount': batch.total_net_salary,
            'total_bank_amount': Decimal('0'),
            'matched_records': [],
            'unmatched_batch_records': [],
            'unmatched_bank_records': [],
            'reconciliation_status': ReconciliationStatus.PENDING.value,
            'variance': Decimal('0'),
            'matched_count': 0,
            'unmatched_count': 0,
        }
        
        bank_records_used = set()
        total_bank = Decimal('0')
        
        # Match batch records to bank records
        for batch_record in batch_records:
            matched = False
            batch_amount = batch_record.net_salary
            
            for idx, bank_record in enumerate(bank_records):
                if idx in bank_records_used:
                    continue
                
                # Check if amounts match (within tolerance of 0.01)
                bank_amount = Decimal(str(bank_record.get('amount', 0)))
                
                if abs(batch_amount - bank_amount) < Decimal('1'):  # 1 Naira tolerance
                    # Found match
                    reconciliation_report['matched_records'].append({
                        'batch_record_id': batch_record.id,
                        'staff_name': batch_record.user.full_name,
                        'batch_amount': float(batch_amount),
                        'bank_amount': float(bank_amount),
                        'bank_date': bank_record.get('date'),
                        'bank_reference': bank_record.get('reference'),
                        'bank_status': bank_record.get('status', 'unknown'),
                        'variance': float(abs(batch_amount - bank_amount)),
                    })
                    
                    bank_records_used.add(idx)
                    reconciliation_report['matched_count'] += 1
                    total_bank += bank_amount
                    matched = True
                    break
            
            if not matched:
                reconciliation_report['unmatched_batch_records'].append({
                    'record_id': batch_record.id,
                    'staff_name': batch_record.user.full_name,
                    'amount': float(batch_amount),
                    'email': batch_record.user.email,
                })
                reconciliation_report['unmatched_count'] += 1
        
        # Identify unmatched bank records
        for idx, bank_record in enumerate(bank_records):
            if idx not in bank_records_used:
                reconciliation_report['unmatched_bank_records'].append({
                    'date': bank_record.get('date'),
                    'amount': float(bank_record.get('amount', 0)),
                    'beneficiary': bank_record.get('beneficiary'),
                    'reference': bank_record.get('reference'),
                    'status': bank_record.get('status'),
                })
                total_bank += Decimal(str(bank_record.get('amount', 0)))
        
        # Calculate reconciliation status
        reconciliation_report['total_bank_amount'] = float(total_bank)
        reconciliation_report['variance'] = float(
            reconciliation_report['total_batch_amount'] - total_bank
        )
        
        if reconciliation_report['unmatched_batch_records'] == [] and \
           reconciliation_report['unmatched_bank_records'] == []:
            if reconciliation_report['variance'] == 0:
                reconciliation_report['reconciliation_status'] = ReconciliationStatus.MATCHED.value
            else:
                reconciliation_report['reconciliation_status'] = ReconciliationStatus.PARTIAL.value
        elif reconciliation_report['matched_count'] > 0:
            reconciliation_report['reconciliation_status'] = ReconciliationStatus.PARTIAL.value
        else:
            reconciliation_report['reconciliation_status'] = ReconciliationStatus.UNMATCHED.value
        
        return reconciliation_report
    
    @staticmethod
    def mark_batch_reconciled(batch: PayrollBatch, 
                              reconciliation_data: Dict,
                              reconciled_by_id: int) -> Tuple[bool, str]:
        """Mark batch as reconciled after bank verification"""
        try:
            # Store reconciliation details
            batch.reconciliation_status = reconciliation_data.get('reconciliation_status')
            batch.reconciliation_date = datetime.now()
            batch.reconciliation_data = reconciliation_data
            batch.reconciliation_by_id = reconciled_by_id
            
            db.session.commit()
            
            return True, "Batch marked as reconciled"
            
        except Exception as e:
            db.session.rollback()
            return False, str(e)


# ==============================================================================
# GL RECONCILIATION
# ==============================================================================

class GLReconciliation:
    """Reconcile payroll GL entries with accounting records"""
    
    @staticmethod
    def reconcile_batch_with_gl(batch: PayrollBatch) -> Dict:
        """Reconcile batch GL entries"""
        
        gl_entries = AccountingEntry.query.filter_by(batch_id=batch.id).all()
        
        reconciliation_report = {
            'batch_id': batch.id,
            'batch_name': batch.batch_name,
            'payroll_period': batch.payroll_period,
            'reconciliation_date': datetime.now().isoformat(),
            'gl_entries_count': len(gl_entries),
            'total_debits': Decimal('0'),
            'total_credits': Decimal('0'),
            'balance': Decimal('0'),
            'is_balanced': True,
            'entries_by_account': {},
            'errors': [],
        }
        
        total_dr = Decimal('0')
        total_cr = Decimal('0')
        
        # Group by account
        accounts = {}
        
        for entry in gl_entries:
            account_code = entry.account_code
            
            if account_code not in accounts:
                accounts[account_code] = {
                    'account_code': account_code,
                    'account_name': entry.description,
                    'total_debits': Decimal('0'),
                    'total_credits': Decimal('0'),
                    'entries': []
                }
            
            if entry.is_debit:
                accounts[account_code]['total_debits'] += entry.amount
                total_dr += entry.amount
            else:
                accounts[account_code]['total_credits'] += entry.amount
                total_cr += entry.amount
            
            accounts[account_code]['entries'].append({
                'date': entry.transaction_date.isoformat() if entry.transaction_date else None,
                'description': entry.description,
                'amount': float(entry.amount),
                'is_debit': entry.is_debit,
                'reference': entry.reference,
            })
        
        reconciliation_report['total_debits'] = float(total_dr)
        reconciliation_report['total_credits'] = float(total_cr)
        reconciliation_report['balance'] = float(total_dr - total_cr)
        reconciliation_report['is_balanced'] = (total_dr == total_cr)
        
        if not reconciliation_report['is_balanced']:
            reconciliation_report['errors'].append(
                f"GL entries not balanced: DR ₦{total_dr:,.2f} vs CR ₦{total_cr:,.2f}"
            )
        
        reconciliation_report['entries_by_account'] = {
            code: {
                'account_code': data['account_code'],
                'account_name': data['account_name'],
                'total_debits': float(data['total_debits']),
                'total_credits': float(data['total_credits']),
                'entry_count': len(data['entries']),
            }
            for code, data in accounts.items()
        }
        
        return reconciliation_report
    
    @staticmethod
    def validate_gl_entries(batch: PayrollBatch) -> Tuple[bool, List[str]]:
        """Validate all GL entries for batch"""
        errors = []
        
        gl_entries = AccountingEntry.query.filter_by(batch_id=batch.id).all()
        
        if not gl_entries:
            errors.append(f"No GL entries found for batch {batch.id}")
            return False, errors
        
        # Check balance
        total_dr = sum(e.amount for e in gl_entries if e.is_debit)
        total_cr = sum(e.amount for e in gl_entries if not e.is_debit)
        
        if total_dr != total_cr:
            errors.append(f"GL entries not balanced: DR {total_dr} != CR {total_cr}")
        
        # Check for required accounts
        account_codes = {e.account_code for e in gl_entries}
        
        required_accounts = [
            '4100',  # Salary expense (example)
            '2100',  # Bank account (example)
        ]
        
        for required in required_accounts:
            if required not in account_codes:
                errors.append(f"Missing required GL account: {required}")
        
        # Check for negative amounts
        for entry in gl_entries:
            if entry.amount < 0:
                errors.append(f"Negative amount in GL entry {entry.id}: {entry.amount}")
        
        return len(errors) == 0, errors


# ==============================================================================
# MULTI-BATCH RECONCILIATION
# ==============================================================================

class BatchReconciliation:
    """Reconcile multiple batches"""
    
    @staticmethod
    def get_reconciliation_summary(start_date: date, 
                                   end_date: date) -> Dict:
        """Get reconciliation summary for period"""
        
        batches = PayrollBatch.query.filter(
            PayrollBatch.created_at >= start_date,
            PayrollBatch.created_at <= end_date,
            PayrollBatch.status == PayrollStatus.PAID
        ).all()
        
        summary = {
            'period_start': start_date.isoformat(),
            'period_end': end_date.isoformat(),
            'batches_count': len(batches),
            'total_amount': Decimal('0'),
            'reconciled_batches': 0,
            'unreconciled_batches': 0,
            'partial_batches': 0,
            'batches': []
        }
        
        for batch in batches:
            batch_summary = {
                'batch_id': batch.id,
                'batch_name': batch.batch_name,
                'payroll_period': batch.payroll_period,
                'total_net_salary': float(batch.total_net_salary),
                'records_count': len(batch.payroll_records),
                'reconciliation_status': batch.reconciliation_status or 'UNRECONCILED',
                'reconciliation_date': batch.reconciliation_date.isoformat() if batch.reconciliation_date else None,
            }
            
            summary['batches'].append(batch_summary)
            summary['total_amount'] += batch.total_net_salary
            
            if batch.reconciliation_status == ReconciliationStatus.MATCHED.value:
                summary['reconciled_batches'] += 1
            elif batch.reconciliation_status == ReconciliationStatus.PARTIAL.value:
                summary['partial_batches'] += 1
            else:
                summary['unreconciled_batches'] += 1
        
        summary['total_amount'] = float(summary['total_amount'])
        
        return summary


# ==============================================================================
# RECONCILIATION REPORT GENERATION
# ==============================================================================

class ReconciliationReportGenerator:
    """Generate reconciliation reports"""
    
    @staticmethod
    def generate_bank_reconciliation_report(batch: PayrollBatch, 
                                            bank_records: List[Dict],
                                            output_path: str) -> Tuple[bool, str]:
        """Generate bank reconciliation report"""
        try:
            import csv
            
            report = BankReconciliation.reconcile_batch_with_bank(batch, bank_records)
            
            with open(output_path, 'w', newline='') as f:
                writer = csv.writer(f)
                
                # Header
                writer.writerow(['Bank Reconciliation Report'])
                writer.writerow([f"Batch: {report['batch_name']}"])
                writer.writerow([f"Period: {report['payroll_period']}"])
                writer.writerow([f"Date: {report['reconciliation_date']}"])
                writer.writerow([])
                
                # Summary
                writer.writerow(['Summary', 'Amount'])
                writer.writerow(['Total Batch Amount', f"₦{report['total_batch_amount']:,.2f}"])
                writer.writerow(['Total Bank Amount', f"₦{report['total_bank_amount']:,.2f}"])
                writer.writerow(['Variance', f"₦{report['variance']:,.2f}"])
                writer.writerow(['Matched Records', report['matched_count']])
                writer.writerow(['Unmatched Records', report['unmatched_count']])
                writer.writerow(['Reconciliation Status', report['reconciliation_status']])
                writer.writerow([])
                
                # Matched records
                writer.writerow(['Matched Records', '', '', '', '', ''])
                writer.writerow(['Staff Name', 'Batch Amount', 'Bank Amount', 'Variance', 'Bank Reference', 'Bank Status'])
                
                for matched in report['matched_records']:
                    writer.writerow([
                        matched['staff_name'],
                        f"₦{matched['batch_amount']:,.2f}",
                        f"₦{matched['bank_amount']:,.2f}",
                        f"₦{matched['variance']:,.2f}",
                        matched['bank_reference'],
                        matched['bank_status'],
                    ])
                
                writer.writerow([])
                
                # Unmatched batch records
                if report['unmatched_batch_records']:
                    writer.writerow(['Unmatched Batch Records'])
                    writer.writerow(['Staff Name', 'Amount', 'Email'])
                    for unmatched in report['unmatched_batch_records']:
                        writer.writerow([
                            unmatched['staff_name'],
                            f"₦{unmatched['amount']:,.2f}",
                            unmatched['email'],
                        ])
                
            return True, output_path
            
        except Exception as e:
            return False, str(e)
