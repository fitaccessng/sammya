"""
Accounting System Integration - Connect payroll GL entries to accounting module
"""

from datetime import datetime, date
from decimal import Decimal
from typing import List, Dict, Tuple, Optional
from enum import Enum

from app.models import db

# ==============================================================================
# ACCOUNTING INTEGRATION ENUMS
# ==============================================================================

class GLAccountType(str, Enum):
    """GL Account Types"""
    ASSET = "ASSET"
    LIABILITY = "LIABILITY"
    EQUITY = "EQUITY"
    REVENUE = "REVENUE"
    EXPENSE = "EXPENSE"


class PostingStatus(str, Enum):
    """GL Entry Posting Status"""
    DRAFT = "DRAFT"
    POSTED = "POSTED"
    REVERSED = "REVERSED"


# ==============================================================================
# CHART OF ACCOUNTS MAPPING
# ==============================================================================

class ChartOfAccounts:
    """Map payroll components to GL accounts"""
    
    # Standard payroll GL account codes
    STANDARD_ACCOUNTS = {
        # Expense Accounts
        '4100': {'code': '4100', 'name': 'Salary Expense', 'type': GLAccountType.EXPENSE},
        '4101': {'code': '4101', 'name': 'House Allowance Expense', 'type': GLAccountType.EXPENSE},
        '4102': {'code': '4102', 'name': 'Transport Allowance Expense', 'type': GLAccountType.EXPENSE},
        '4103': {'code': '4103', 'name': 'Meal Allowance Expense', 'type': GLAccountType.EXPENSE},
        '4104': {'code': '4104', 'name': 'Risk Allowance Expense', 'type': GLAccountType.EXPENSE},
        '4105': {'code': '4105', 'name': 'Performance Allowance Expense', 'type': GLAccountType.EXPENSE},
        
        # Liability Accounts
        '2100': {'code': '2100', 'name': 'Salary Payable', 'type': GLAccountType.LIABILITY},
        '2101': {'code': '2101', 'name': 'Withholding Tax Payable', 'type': GLAccountType.LIABILITY},
        '2102': {'code': '2102', 'name': 'Pension Contribution Payable', 'type': GLAccountType.LIABILITY},
        '2103': {'code': '2103', 'name': 'Insurance Payable', 'type': GLAccountType.LIABILITY},
        '2104': {'code': '2104', 'name': 'Loan Deduction Payable', 'type': GLAccountType.LIABILITY},
        
        # Bank Account
        '1100': {'code': '1100', 'name': 'Bank Account', 'type': GLAccountType.ASSET},
    }
    
    @staticmethod
    def get_account(account_code: str) -> Optional[Dict]:
        """Get account details"""
        return ChartOfAccounts.STANDARD_ACCOUNTS.get(account_code)
    
    @staticmethod
    def get_salary_expense_account() -> Dict:
        """Get salary expense account"""
        return ChartOfAccounts.STANDARD_ACCOUNTS['4100']
    
    @staticmethod
    def get_bank_account() -> Dict:
        """Get bank account"""
        return ChartOfAccounts.STANDARD_ACCOUNTS['1100']
    
    @staticmethod
    def get_allowance_account(allowance_type: str) -> Dict:
        """Get account for specific allowance"""
        mapping = {
            'house_allowance': '4101',
            'transport_allowance': '4102',
            'meal_allowance': '4103',
            'risk_allowance': '4104',
            'performance_allowance': '4105',
        }
        code = mapping.get(allowance_type)
        if code:
            return ChartOfAccounts.STANDARD_ACCOUNTS[code]
        return None
    
    @staticmethod
    def get_deduction_account(deduction_type: str) -> Dict:
        """Get account for specific deduction"""
        mapping = {
            'tax_amount': '2101',
            'pension_amount': '2102',
            'insurance_amount': '2103',
            'loan_deduction': '2104',
        }
        code = mapping.get(deduction_type)
        if code:
            return ChartOfAccounts.STANDARD_ACCOUNTS[code]
        return None


# ==============================================================================
# GL POSTING ENGINE
# ==============================================================================

class GLPostingEngine:
    """Post payroll GL entries to accounting system"""
    
    @staticmethod
    def generate_payroll_gl_entries(batch, created_by_id: int) -> Tuple[bool, List[Dict]]:
        """
        Generate GL entries for payroll batch
        Creates: Dr Salary Expense, Cr Salary Payable (for net)
                 Dr Allowance Expense, Cr Salary Payable (for each allowance)
                 Dr Salary Payable, Cr Tax/Pension/Insurance/Loan payables
                 Dr Tax/Pension/Insurance/Loan payables, Cr Bank
        """
        
        try:
            from app.payroll_models import AccountingEntry
            
            entries = []
            records = batch.payroll_records
            
            if not records:
                return False, ['No payroll records in batch']
            
            transaction_date = datetime.now().date()
            reference = f"PAYROLL_{batch.id}_{batch.payroll_period}"
            
            # Aggregate totals for efficiency
            total_basic = sum(r.basic_salary for r in records)
            total_house = sum(r.house_allowance for r in records)
            total_transport = sum(r.transport_allowance for r in records)
            total_meal = sum(r.meal_allowance for r in records)
            total_risk = sum(r.risk_allowance for r in records)
            total_performance = sum(r.performance_allowance for r in records)
            total_gross = sum(r.gross_salary for r in records)
            
            total_tax = sum(r.tax_amount for r in records)
            total_pension = sum(r.pension_amount for r in records)
            total_insurance = sum(r.insurance_amount for r in records)
            total_loan = sum(r.loan_deduction for r in records)
            total_deductions = sum(r.total_deductions for r in records)
            total_net = sum(r.net_salary for r in records)
            
            # 1. Dr Salary Expense 4100, Cr Salary Payable 2100
            if total_basic > 0:
                # Debit
                entry = AccountingEntry(
                    batch_id=batch.id,
                    account_code='4100',
                    description='Salary Expense - ' + batch.batch_name,
                    amount=total_basic,
                    is_debit=True,
                    transaction_date=transaction_date,
                    reference=reference,
                    posting_status=PostingStatus.DRAFT.value,
                    created_by_id=created_by_id,
                )
                db.session.add(entry)
                entries.append(entry)
                
                # Credit
                entry = AccountingEntry(
                    batch_id=batch.id,
                    account_code='2100',
                    description='Salary Payable - ' + batch.batch_name,
                    amount=total_basic,
                    is_debit=False,
                    transaction_date=transaction_date,
                    reference=reference,
                    posting_status=PostingStatus.DRAFT.value,
                    created_by_id=created_by_id,
                )
                db.session.add(entry)
                entries.append(entry)
            
            # 2. Allowance entries
            allowance_map = [
                (total_house, '4101', 'House Allowance'),
                (total_transport, '4102', 'Transport Allowance'),
                (total_meal, '4103', 'Meal Allowance'),
                (total_risk, '4104', 'Risk Allowance'),
                (total_performance, '4105', 'Performance Allowance'),
            ]
            
            for total, account_code, description in allowance_map:
                if total > 0:
                    # Debit
                    entry = AccountingEntry(
                        batch_id=batch.id,
                        account_code=account_code,
                        description=f'{description} Expense - {batch.batch_name}',
                        amount=total,
                        is_debit=True,
                        transaction_date=transaction_date,
                        reference=reference,
                        posting_status=PostingStatus.DRAFT.value,
                        created_by_id=created_by_id,
                    )
                    db.session.add(entry)
                    entries.append(entry)
                    
                    # Credit
                    entry = AccountingEntry(
                        batch_id=batch.id,
                        account_code='2100',
                        description=f'{description} Payable - {batch.batch_name}',
                        amount=total,
                        is_debit=False,
                        transaction_date=transaction_date,
                        reference=reference,
                        posting_status=PostingStatus.DRAFT.value,
                        created_by_id=created_by_id,
                    )
                    db.session.add(entry)
                    entries.append(entry)
            
            # 3. Deduction entries
            deduction_map = [
                (total_tax, '2101', 'Withholding Tax'),
                (total_pension, '2102', 'Pension Contribution'),
                (total_insurance, '2103', 'Insurance'),
                (total_loan, '2104', 'Loan Deduction'),
            ]
            
            for total, account_code, description in deduction_map:
                if total > 0:
                    # Dr Deduction Payable, Cr Salary Payable
                    # Debit
                    entry = AccountingEntry(
                        batch_id=batch.id,
                        account_code=account_code,
                        description=f'{description} Payable - {batch.batch_name}',
                        amount=total,
                        is_debit=True,
                        transaction_date=transaction_date,
                        reference=reference,
                        posting_status=PostingStatus.DRAFT.value,
                        created_by_id=created_by_id,
                    )
                    db.session.add(entry)
                    entries.append(entry)
                    
                    # Credit
                    entry = AccountingEntry(
                        batch_id=batch.id,
                        account_code='2100',
                        description=f'Salary Payable - {description} - {batch.batch_name}',
                        amount=total,
                        is_debit=False,
                        transaction_date=transaction_date,
                        reference=reference,
                        posting_status=PostingStatus.DRAFT.value,
                        created_by_id=created_by_id,
                    )
                    db.session.add(entry)
                    entries.append(entry)
            
            # 4. Bank payment entry: Dr Salary Payable 2100, Cr Bank 1100
            if total_net > 0:
                # Debit
                entry = AccountingEntry(
                    batch_id=batch.id,
                    account_code='2100',
                    description=f'Salary Payable (Payment) - {batch.batch_name}',
                    amount=total_net,
                    is_debit=True,
                    transaction_date=transaction_date,
                    reference=reference,
                    posting_status=PostingStatus.DRAFT.value,
                    created_by_id=created_by_id,
                )
                db.session.add(entry)
                entries.append(entry)
                
                # Credit
                entry = AccountingEntry(
                    batch_id=batch.id,
                    account_code='1100',
                    description=f'Bank (Salary Payment) - {batch.batch_name}',
                    amount=total_net,
                    is_debit=False,
                    transaction_date=transaction_date,
                    reference=reference,
                    posting_status=PostingStatus.DRAFT.value,
                    created_by_id=created_by_id,
                )
                db.session.add(entry)
                entries.append(entry)
            
            db.session.commit()
            
            return True, [
                {
                    'account_code': e.account_code,
                    'description': e.description,
                    'amount': float(e.amount),
                    'is_debit': e.is_debit,
                } for e in entries
            ]
            
        except Exception as e:
            db.session.rollback()
            return False, [str(e)]
    
    @staticmethod
    def post_entries_to_gl(batch_id: int, posted_by_id: int) -> Tuple[bool, str]:
        """Post GL entries from draft to posted status"""
        try:
            from app.payroll_models import AccountingEntry
            
            entries = AccountingEntry.query.filter_by(
                batch_id=batch_id,
                posting_status=PostingStatus.DRAFT.value
            ).all()
            
            if not entries:
                return False, "No draft entries to post"
            
            for entry in entries:
                entry.posting_status = PostingStatus.POSTED.value
                entry.posted_at = datetime.now()
                entry.posted_by_id = posted_by_id
            
            db.session.commit()
            
            return True, f"Posted {len(entries)} entries to GL"
            
        except Exception as e:
            db.session.rollback()
            return False, str(e)


# ==============================================================================
# ACCOUNTING RECONCILIATION
# ==============================================================================

class AccountingReconciliation:
    """Reconcile payroll GL entries with accounting records"""
    
    @staticmethod
    def get_payroll_impact_summary(start_date: date, end_date: date) -> Dict:
        """Get summary of payroll impact on GL accounts"""
        
        from app.payroll_models import AccountingEntry
        
        entries = AccountingEntry.query.filter(
            AccountingEntry.transaction_date >= start_date,
            AccountingEntry.transaction_date <= end_date
        ).all()
        
        summary = {
            'period_start': start_date.isoformat(),
            'period_end': end_date.isoformat(),
            'total_debits': Decimal('0'),
            'total_credits': Decimal('0'),
            'accounts': {},
            'entries_count': len(entries),
        }
        
        for entry in entries:
            account_code = entry.account_code
            
            if account_code not in summary['accounts']:
                summary['accounts'][account_code] = {
                    'account_code': account_code,
                    'account_name': entry.description,
                    'debits': Decimal('0'),
                    'credits': Decimal('0'),
                }
            
            if entry.is_debit:
                summary['accounts'][account_code]['debits'] += entry.amount
                summary['total_debits'] += entry.amount
            else:
                summary['accounts'][account_code]['credits'] += entry.amount
                summary['total_credits'] += entry.amount
        
        # Convert to float for JSON serialization
        summary['total_debits'] = float(summary['total_debits'])
        summary['total_credits'] = float(summary['total_credits'])
        
        for account in summary['accounts'].values():
            account['debits'] = float(account['debits'])
            account['credits'] = float(account['credits'])
        
        return summary


# ==============================================================================
# EXPORT TO ACCOUNTING SYSTEM
# ==============================================================================

class AccountingExport:
    """Export payroll data to accounting systems"""
    
    @staticmethod
    def export_to_csv(batch_id: int, output_path: str) -> Tuple[bool, str]:
        """Export GL entries to CSV for accounting system import"""
        try:
            from app.payroll_models import AccountingEntry
            import csv
            
            entries = AccountingEntry.query.filter_by(batch_id=batch_id).all()
            
            with open(output_path, 'w', newline='') as f:
                writer = csv.DictWriter(f, fieldnames=[
                    'account_code', 'description', 'amount', 'debit_credit',
                    'transaction_date', 'reference', 'batch_id'
                ])
                
                writer.writeheader()
                
                for entry in entries:
                    writer.writerow({
                        'account_code': entry.account_code,
                        'description': entry.description,
                        'amount': str(entry.amount),
                        'debit_credit': 'DR' if entry.is_debit else 'CR',
                        'transaction_date': entry.transaction_date.isoformat(),
                        'reference': entry.reference,
                        'batch_id': entry.batch_id,
                    })
            
            return True, output_path
            
        except Exception as e:
            return False, str(e)
    
    @staticmethod
    def export_to_json(batch_id: int, output_path: str) -> Tuple[bool, str]:
        """Export GL entries to JSON for API integration"""
        try:
            from app.payroll_models import AccountingEntry
            import json
            
            entries = AccountingEntry.query.filter_by(batch_id=batch_id).all()
            
            entries_data = []
            for entry in entries:
                entries_data.append({
                    'account_code': entry.account_code,
                    'description': entry.description,
                    'amount': str(entry.amount),
                    'is_debit': entry.is_debit,
                    'transaction_date': entry.transaction_date.isoformat(),
                    'reference': entry.reference,
                    'posting_status': entry.posting_status,
                })
            
            with open(output_path, 'w') as f:
                json.dump(entries_data, f, indent=2)
            
            return True, output_path
            
        except Exception as e:
            return False, str(e)
