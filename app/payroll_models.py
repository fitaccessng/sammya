"""
Enterprise Payroll Models
Complete payroll schema with salary mapping, deductions, allowances, 
batches, approvals, audit logs, and versioning
"""

from app.models import db
from datetime import datetime, date
from decimal import Decimal
from enum import Enum
from sqlalchemy.dialects.postgresql import JSON, ARRAY
from sqlalchemy import func, Index


# ==================== ENUMS ====================

class PayrollStatus(str, Enum):
    """Payroll batch status workflow"""
    DRAFT = "draft"
    HR_APPROVED = "hr_approved"
    ADMIN_APPROVED = "admin_approved"
    FINANCE_PROCESSING = "finance_processing"
    PAID = "paid"
    ARCHIVED = "archived"
    CANCELLED = "cancelled"


class DeductionType(str, Enum):
    """Types of deductions"""
    TAX = "tax"
    INSURANCE = "insurance"
    PENSION = "pension"
    LOAN = "loan"
    OTHER = "other"


class AllowanceType(str, Enum):
    """Types of allowances"""
    HOUSE = "house"
    TRANSPORT = "transport"
    MEAL = "meal"
    RISK = "risk"
    PERFORMANCE = "performance"
    OTHER = "other"


class AdjustmentType(str, Enum):
    """Types of adjustments"""
    BONUS = "bonus"
    PENALTY = "penalty"
    LEAVE_DEDUCTION = "leave_deduction"
    BACKPAY = "backpay"
    CORRECTION = "correction"
    OTHER = "other"


class ApprovalAction(str, Enum):
    """Approval actions in workflow"""
    SUBMITTED = "submitted"
    APPROVED = "approved"
    REJECTED = "rejected"
    RECALLED = "recalled"
    RETURNED = "returned"


# ==================== CORE PAYROLL MODELS ====================

class SalaryMapping(db.Model):
    """Staff salary configuration - base for all payroll calculations"""
    __tablename__ = 'salary_mapping'
    __table_args__ = (
        Index('idx_user_id_active', 'user_id', 'is_active'),
        Index('idx_effective_date', 'effective_date'),
    )
    
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id', ondelete='CASCADE'), nullable=False, unique=True)
    
    # Basic salary
    basic_salary = db.Column(db.Numeric(12, 2), nullable=False, default=0)
    
    # Allowances
    house_allowance = db.Column(db.Numeric(10, 2), default=0)
    transport_allowance = db.Column(db.Numeric(10, 2), default=0)
    meal_allowance = db.Column(db.Numeric(10, 2), default=0)
    risk_allowance = db.Column(db.Numeric(10, 2), default=0)
    performance_allowance = db.Column(db.Numeric(10, 2), default=0)
    other_allowances = db.Column(db.Numeric(10, 2), default=0)
    
    # Deductions
    tax_amount = db.Column(db.Numeric(10, 2), default=0)
    pension_amount = db.Column(db.Numeric(10, 2), default=0)
    insurance_amount = db.Column(db.Numeric(10, 2), default=0)
    loan_amount = db.Column(db.Numeric(10, 2), default=0)
    other_deductions = db.Column(db.Numeric(10, 2), default=0)
    
    # Effective dates
    effective_date = db.Column(db.Date, nullable=False, default=date.today)
    end_date = db.Column(db.Date)  # NULL = still active
    is_active = db.Column(db.Boolean, default=True, index=True)
    
    # Metadata
    created_by_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_by_id = db.Column(db.Integer, db.ForeignKey('user.id'))
    updated_at = db.Column(db.DateTime, onupdate=datetime.utcnow)
    version = db.Column(db.Integer, default=1)  # For versioning
    
    # Relationships
    user = db.relationship('User', foreign_keys=[user_id], backref='salary_mappings')
    created_by = db.relationship('User', foreign_keys=[created_by_id])
    updated_by = db.relationship('User', foreign_keys=[updated_by_id])
    
    def get_total_allowances(self):
        """Calculate total allowances"""
        return sum([
            self.house_allowance or 0,
            self.transport_allowance or 0,
            self.meal_allowance or 0,
            self.risk_allowance or 0,
            self.performance_allowance or 0,
            self.other_allowances or 0,
        ])
    
    def get_total_deductions(self):
        """Calculate total standard deductions"""
        return sum([
            self.tax_amount or 0,
            self.pension_amount or 0,
            self.insurance_amount or 0,
            self.loan_amount or 0,
            self.other_deductions or 0,
        ])
    
    def get_gross_salary(self):
        """Gross = Basic + Allowances"""
        return (self.basic_salary or 0) + self.get_total_allowances()
    
    def __repr__(self):
        return f'<SalaryMapping user_id={self.user_id} basic={self.basic_salary}>'


class PayrollAdjustment(db.Model):
    """One-time salary adjustments (bonuses, penalties, corrections)"""
    __tablename__ = 'payroll_adjustment'
    __table_args__ = (
        Index('idx_user_payroll_period', 'user_id', 'payroll_period'),
    )
    
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id', ondelete='CASCADE'), nullable=False)
    
    adjustment_type = db.Column(db.Enum(AdjustmentType), nullable=False)
    amount = db.Column(db.Numeric(10, 2), nullable=False)
    description = db.Column(db.Text)
    
    payroll_period = db.Column(db.String(10))  # e.g., "2026-02" (YYYY-MM)
    is_applied = db.Column(db.Boolean, default=False)
    applied_in_batch_id = db.Column(db.Integer, db.ForeignKey('payroll_batch.id'))
    
    # Metadata
    created_by_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    
    # Relationships
    user = db.relationship('User', foreign_keys=[user_id])
    created_by = db.relationship('User', foreign_keys=[created_by_id])
    batch = db.relationship('PayrollBatch', foreign_keys=[applied_in_batch_id])


class PayrollBatch(db.Model):
    """Payroll batch - groups records for a period"""
    __tablename__ = 'payroll_batch'
    __table_args__ = (
        Index('idx_batch_status', 'status'),
        Index('idx_batch_period', 'payroll_period'),
        Index('idx_batch_created', 'created_at'),
    )
    
    id = db.Column(db.Integer, primary_key=True)
    batch_name = db.Column(db.String(100), nullable=False)
    payroll_period = db.Column(db.String(10), nullable=False)  # YYYY-MM format
    
    # Status workflow
    status = db.Column(db.Enum(PayrollStatus), default=PayrollStatus.DRAFT, index=True)
    
    # Financial summary
    total_records = db.Column(db.Integer, default=0)
    successfully_processed = db.Column(db.Integer, default=0)
    failed_records = db.Column(db.Integer, default=0)
    total_basic_salary = db.Column(db.Numeric(14, 2), default=0)
    total_allowances = db.Column(db.Numeric(14, 2), default=0)
    total_gross = db.Column(db.Numeric(14, 2), default=0)
    total_deductions = db.Column(db.Numeric(14, 2), default=0)
    total_adjustments = db.Column(db.Numeric(14, 2), default=0)
    total_net = db.Column(db.Numeric(14, 2), default=0)
    
    # Control totals for reconciliation
    control_count = db.Column(db.Integer)  # Expected record count
    control_amount = db.Column(db.Numeric(14, 2))  # Expected total
    
    # Dates
    start_date = db.Column(db.Date, nullable=False)
    end_date = db.Column(db.Date, nullable=False)
    payment_date = db.Column(db.Date)
    actual_payment_date = db.Column(db.Date)
    
    # Notes
    notes = db.Column(db.Text)
    rejection_reason = db.Column(db.Text)
    
    # Metadata
    created_by_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    
    # Relationships
    created_by = db.relationship('User', foreign_keys=[created_by_id])
    records = db.relationship('PayrollRecord', backref='batch', cascade='all, delete-orphan', lazy='dynamic')
    approvals = db.relationship('PayrollApproval', backref='batch', cascade='all, delete-orphan', lazy='dynamic')
    exports = db.relationship('PayrollExport', backref='batch', cascade='all, delete-orphan')
    audit_logs = db.relationship('PayrollAuditLog', foreign_keys='PayrollAuditLog.batch_id', cascade='all, delete-orphan')
    
    def __repr__(self):
        return f'<PayrollBatch {self.batch_name} period={self.payroll_period} status={self.status}>'


class PayrollRecord(db.Model):
    """Individual payroll record per staff per period"""
    __tablename__ = 'payroll_record'
    __table_args__ = (
        Index('idx_batch_user', 'batch_id', 'user_id'),
        Index('idx_user_period', 'user_id', 'payroll_period'),
    )
    
    id = db.Column(db.Integer, primary_key=True)
    batch_id = db.Column(db.Integer, db.ForeignKey('payroll_batch.id', ondelete='CASCADE'), nullable=False)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id', ondelete='CASCADE'), nullable=False)
    payroll_period = db.Column(db.String(10), nullable=False)
    
    # Salary components (at time of calculation - immutable)
    basic_salary = db.Column(db.Numeric(12, 2), nullable=False)
    
    # Allowances detail
    house_allowance = db.Column(db.Numeric(10, 2), default=0)
    transport_allowance = db.Column(db.Numeric(10, 2), default=0)
    meal_allowance = db.Column(db.Numeric(10, 2), default=0)
    risk_allowance = db.Column(db.Numeric(10, 2), default=0)
    performance_allowance = db.Column(db.Numeric(10, 2), default=0)
    other_allowances = db.Column(db.Numeric(10, 2), default=0)
    total_allowances = db.Column(db.Numeric(12, 2), default=0)
    
    # Gross calculation
    gross_salary = db.Column(db.Numeric(12, 2), default=0)
    
    # Deductions detail
    tax_deduction = db.Column(db.Numeric(10, 2), default=0)
    pension_deduction = db.Column(db.Numeric(10, 2), default=0)
    insurance_deduction = db.Column(db.Numeric(10, 2), default=0)
    loan_deduction = db.Column(db.Numeric(10, 2), default=0)
    other_deductions = db.Column(db.Numeric(10, 2), default=0)
    total_deductions = db.Column(db.Numeric(12, 2), default=0)
    
    # Adjustments
    adjustments = db.Column(JSON, default={})  # {type: amount, ...}
    total_adjustments = db.Column(db.Numeric(12, 2), default=0)
    
    # Net calculation
    net_salary = db.Column(db.Numeric(12, 2), default=0)
    
    # Payment details
    bank_account = db.Column(db.String(50))
    bank_name = db.Column(db.String(100))
    payment_status = db.Column(db.String(20), default='pending')  # pending, paid, failed, reversed
    payment_reference = db.Column(db.String(100))
    
    # Validation & processing
    validation_errors = db.Column(JSON, default=[])  # List of error messages
    is_valid = db.Column(db.Boolean, default=True)
    processing_status = db.Column(db.String(20), default='pending')  # pending, processing, success, failed
    processing_error = db.Column(db.Text)
    
    # Versioning
    record_version = db.Column(db.Integer, default=1)
    previous_record_id = db.Column(db.Integer, db.ForeignKey('payroll_record.id'))
    
    # Metadata
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, onupdate=datetime.utcnow)
    
    # Relationships
    user = db.relationship('User', foreign_keys=[user_id])
    previous_record = db.relationship('PayrollRecord', remote_side=[id])
    
    def calculate_net(self):
        """Calculate net salary: Gross - Deductions + Adjustments"""
        self.net_salary = (self.gross_salary or 0) - (self.total_deductions or 0) + (self.total_adjustments or 0)
        return self.net_salary
    
    def validate(self):
        """Validate payroll record"""
        errors = []
        
        if not self.user:
            errors.append('Invalid user')
        if self.basic_salary <= 0:
            errors.append('Invalid basic salary')
        if self.total_deductions < 0:
            errors.append('Deductions cannot be negative')
        if self.gross_salary < 0:
            errors.append('Gross salary calculation error')
        if self.net_salary < 0:
            errors.append('Net salary is negative - review deductions')
        
        self.validation_errors = errors
        self.is_valid = len(errors) == 0
        return self.is_valid
    
    def __repr__(self):
        return f'<PayrollRecord user_id={self.user_id} period={self.payroll_period} net={self.net_salary}>'


class PayrollApproval(db.Model):
    """Approval workflow - 3-tier: HR → Admin → Finance"""
    __tablename__ = 'payroll_approval'
    __table_args__ = (
        Index('idx_batch_step', 'batch_id', 'approval_step'),
    )
    
    id = db.Column(db.Integer, primary_key=True)
    batch_id = db.Column(db.Integer, db.ForeignKey('payroll_batch.id', ondelete='CASCADE'), nullable=False)
    
    # Approval tier
    approval_step = db.Column(db.Integer, nullable=False)  # 1=HR, 2=Admin, 3=Finance
    approval_role = db.Column(db.String(50), nullable=False)  # hr_manager, admin, finance_manager
    
    # Action & status
    action = db.Column(db.Enum(ApprovalAction), nullable=False)
    status = db.Column(db.String(20), nullable=False)  # pending, approved, rejected, recalled
    
    # Comments
    comments = db.Column(db.Text)
    
    # Metadata
    requested_at = db.Column(db.DateTime, default=datetime.utcnow)
    action_by_id = db.Column(db.Integer, db.ForeignKey('user.id'))
    action_at = db.Column(db.DateTime)
    
    # Relationships
    action_by = db.relationship('User', foreign_keys=[action_by_id])
    
    def __repr__(self):
        return f'<PayrollApproval batch={self.batch_id} step={self.approval_step} action={self.action}>'


class PayrollAuditLog(db.Model):
    """Immutable audit trail for all payroll operations"""
    __tablename__ = 'payroll_audit_log'
    __table_args__ = (
        Index('idx_batch_audit', 'batch_id'),
        Index('idx_audit_timestamp', 'created_at'),
    )
    
    id = db.Column(db.Integer, primary_key=True)
    batch_id = db.Column(db.Integer, db.ForeignKey('payroll_batch.id'))
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'))
    
    # Action details
    action = db.Column(db.String(100), nullable=False)  # create_batch, calculate, approve, export, etc.
    entity_type = db.Column(db.String(50))  # batch, record, approval
    entity_id = db.Column(db.Integer)
    
    # Changes
    old_values = db.Column(JSON)  # Previous state
    new_values = db.Column(JSON)  # New state
    changes = db.Column(JSON)  # Fields that changed
    
    # Metadata
    actor_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    reason = db.Column(db.Text)  # Why the action was taken
    created_at = db.Column(db.DateTime, default=datetime.utcnow, index=True)
    ip_address = db.Column(db.String(45))
    
    # Relationships
    batch = db.relationship('PayrollBatch', foreign_keys=[batch_id])
    user = db.relationship('User', foreign_keys=[user_id])
    actor = db.relationship('User', foreign_keys=[actor_id])


class PayrollExport(db.Model):
    """Generated payroll exports (bank payments, tax, pension, etc.)"""
    __tablename__ = 'payroll_export'
    __table_args__ = (
        Index('idx_batch_export', 'batch_id'),
        Index('idx_export_type', 'export_type'),
    )
    
    id = db.Column(db.Integer, primary_key=True)
    batch_id = db.Column(db.Integer, db.ForeignKey('payroll_batch.id', ondelete='CASCADE'), nullable=False)
    
    export_type = db.Column(db.String(50), nullable=False)  # bank_payment, tax_remittance, pension, insurance, loan
    export_format = db.Column(db.String(20), nullable=False)  # csv, excel, txt, xml, json
    
    # File details
    file_name = db.Column(db.String(255), nullable=False)
    file_path = db.Column(db.String(500))
    file_size = db.Column(db.Integer)
    file_hash = db.Column(db.String(64))  # SHA-256
    
    # Control totals
    record_count = db.Column(db.Integer, default=0)
    total_amount = db.Column(db.Numeric(14, 2), default=0)
    
    # Status
    status = db.Column(db.String(20), default='generated')  # generated, transmitted, acknowledged, settled, failed
    transmission_status = db.Column(db.String(20))  # pending, sent, received, confirmed
    transmission_ref = db.Column(db.String(100))
    
    # Metadata
    created_by_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    transmitted_at = db.Column(db.DateTime)
    
    # Relationships
    created_by = db.relationship('User', foreign_keys=[created_by_id])
    
    def __repr__(self):
        return f'<PayrollExport batch={self.batch_id} type={self.export_type}>'


class AccountingEntry(db.Model):
    """Accounting ledger entries from payroll"""
    __tablename__ = 'accounting_entry'
    __table_args__ = (
        Index('idx_batch_entry', 'batch_id'),
        Index('idx_entry_date', 'entry_date'),
        Index('idx_account_code', 'account_code'),
    )
    
    id = db.Column(db.Integer, primary_key=True)
    batch_id = db.Column(db.Integer, db.ForeignKey('payroll_batch.id'))
    
    # GL Account
    account_code = db.Column(db.String(20), nullable=False)  # Chart of Accounts code
    account_name = db.Column(db.String(255), nullable=False)
    
    # Debit/Credit
    debit_amount = db.Column(db.Numeric(14, 2), default=0)
    credit_amount = db.Column(db.Numeric(14, 2), default=0)
    
    # Details
    reference = db.Column(db.String(100))  # Cross-reference to payroll entity
    description = db.Column(db.Text)
    
    # Metadata
    entry_date = db.Column(db.Date, nullable=False, default=date.today)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    
    # Relationships
    batch = db.relationship('PayrollBatch', foreign_keys=[batch_id])
