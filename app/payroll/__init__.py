"""
Enterprise Payroll Module - __init__.py
Initializes the payroll module with models, engines, and routes
"""

from flask import Blueprint

# Import models
from app.payroll_models import (
    PayrollStatus, DeductionType, AllowanceType, AdjustmentType,
    SalaryMapping, PayrollBatch, PayrollRecord, PayrollApproval,
    PayrollAuditLog, PayrollExport, AccountingEntry
)

# Import business logic
from app.payroll_engine import PayrollCalculationEngine, PayrollLedgerEngine
from app.payroll_batch_manager import PayrollBatchManager
from app.payroll_export_engine import PayrollExportEngine

# Create blueprint
payroll_bp = Blueprint('payroll', __name__, url_prefix='/payroll')

__all__ = [
    'PayrollStatus',
    'DeductionType',
    'AllowanceType',
    'AdjustmentType',
    'SalaryMapping',
    'PayrollBatch',
    'PayrollRecord',
    'PayrollApproval',
    'PayrollAuditLog',
    'PayrollExport',
    'AccountingEntry',
    'PayrollCalculationEngine',
    'PayrollLedgerEngine',
    'PayrollBatchManager',
    'PayrollExportEngine',
]
