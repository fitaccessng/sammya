"""
ENTERPRISE PAYROLL MODULE - COMPLETE ARCHITECTURE & IMPLEMENTATION GUIDE

This is a production-grade, enterprise-level payroll system with:
- Comprehensive salary mapping with allowances and deductions
- Automated payroll calculation engine with versioning
- Multi-tier approval workflow (HR → Admin → Finance)
- Immutable audit logs for compliance
- Multiple export engines (bank, tax, pension, insurance, loans)
- Accounting ledger integration
- Role-based access control
- Error handling and reconciliation
- API endpoints for integrations

================================================================================
DATABASE SCHEMA & MODELS
================================================================================

1. SalaryMapping (salary_mapping)
   - Core staff salary configuration
   - Per-staff: basic_salary, allowances (house, transport, meal, risk, performance)
   - Per-staff: deductions (tax, pension, insurance, loan, other)
   - Versioning: tracks salary changes over time
   - Indexes: user_id, effective_date, is_active

2. PayrollBatch (payroll_batch)
   - Groups payroll records for a period (YYYY-MM)
   - Status workflow: DRAFT → HR_APPROVED → ADMIN_APPROVED → FINANCE_PROCESSING → PAID → ARCHIVED
   - Financial summary: totals for audit and reconciliation
   - Control totals: for validation against expected amounts
   - Indexes: status, payroll_period, created_at

3. PayrollRecord (payroll_record)
   - Individual staff payroll calculations
   - Immutable: captures salary state at time of calculation
   - Stores all components: basic, allowances, gross, deductions, adjustments, net
   - Validation: tracks errors for review
   - Versioning: supports corrections without losing original
   - Indexes: batch_id, user_id, payroll_period

4. PayrollAdjustment (payroll_adjustment)
   - One-time adjustments: bonuses, penalties, backpay, corrections
   - Applied per period, per staff
   - Tracks whether applied to specific batch
   - Types: bonus, penalty, leave_deduction, backpay, correction, other

5. PayrollApproval (payroll_approval)
   - 3-tier approval workflow
   - Step 1: HR Manager (hr_manager)
   - Step 2: Admin (admin)
   - Step 3: Finance Manager (finance_manager)
   - Actions: submitted, approved, rejected, recalled, returned
   - Audit: timestamps and comments recorded

6. PayrollAuditLog (payroll_audit_log)
   - Immutable audit trail (cannot be edited or deleted)
   - Every action tracked: create, calculate, approve, export, etc.
   - Old and new values captured for change tracking
   - Actor ID for user accountability
   - Reason field for business justification
   - Indexes: batch_id, created_at

7. PayrollExport (payroll_export)
   - Records all generated exports
   - Types: bank_payment, tax_remittance, pension, insurance, loan
   - Formats: CSV, Excel, TXT, XML, JSON
   - File integrity: SHA-256 hash
   - Status tracking: generated, transmitted, acknowledged, settled
   - Transmission reference for bank reconciliation

8. AccountingEntry (accounting_entry)
   - GL integration: Dr/Cr entries from payroll
   - Salary expense (Dr) vs. deductions liabilities (Cr)
   - Bank payment (Cr) vs. employee net salary
   - Chart of Accounts integration
   - Cross-reference to batch for traceability

================================================================================
PAYROLL CALCULATION ENGINE (PayrollCalculationEngine)
================================================================================

Calculation Flow:
  1. Get active SalaryMapping for staff
  2. Initialize PayrollRecord with base data
  3. Calculate allowances: sum all allowance types
  4. Calculate gross: basic + allowances
  5. Calculate deductions: sum all deduction types
  6. Get adjustments: bonuses, penalties, corrections
  7. Calculate net: gross - deductions + adjustments
  8. Validate: check for errors
  9. Save: store immutable record
 10. Audit: log the action

Key Methods:
  - calculate_staff_payroll(user, period, batch, overrides, actor_id)
    → Calculates single staff payroll with optional field overrides
    → Returns: (PayrollRecord, validation_errors)

  - calculate_batch_payroll(batch, staff_ids, actor_id)
    → Batch process all staff in period
    → Returns: (successful_count, failed_count, error_list)

  - validate_batch(batch)
    → Pre-approval validation
    → Checks: status, records, invalid entries, control totals
    → Returns: (is_valid, error_list)

Features:
  ✓ Decimal precision arithmetic (no floating point errors)
  ✓ Override capability for manual adjustments
  ✓ Validation before save
  ✓ Audit trail for every calculation
  ✓ Idempotent: multiple calculations same result

================================================================================
PAYROLL BATCH MANAGER (PayrollBatchManager)
================================================================================

Batch Lifecycle:
  1. create_batch() → DRAFT status
  2. calculate_batch() → Populate records
  3. submit_for_approval() → HR_APPROVED
  4. approve_batch(step=1) → Admin reviews
  5. approve_batch(step=2) → Finance reviews
  6. approve_batch(step=3) → FINANCE_PROCESSING (GL entries generated)
  7. mark_as_paid() → PAID (actual payment)
  8. archive_batch() → ARCHIVED

Status Workflow:
  DRAFT
    ↓
  HR_APPROVED → [reject] → DRAFT
    ↓
  ADMIN_APPROVED → [reject] → DRAFT
    ↓
  FINANCE_PROCESSING → [GL Generated] → PAID
    ↓
  ARCHIVED

Approval Chain:
  1. HR Manager: Reviews salary correctness, accuracy
     - Check: all staff present, no missing mappings
     - Validate: mathematical correctness
     
  2. Admin: Reviews compliance, policy adherence
     - Check: control totals match expected
     - Validate: no duplicate records
     
  3. Finance Manager: Reviews financial impact
     - Check: GL entries correct
     - Authorize: payment processing

Rejection Handling:
  - Batch returns to DRAFT
  - Staff can modify and resubmit
  - All actions logged with reasons

================================================================================
EXPORT ENGINES (PayrollExportEngine)
================================================================================

Bank Payment Export:
  - Format: CSV, Excel, TXT, XML
  - Content: Account number, beneficiary name, amount, bank code, reference
  - All net salaries with staff info
  - Formats for different bank requirements

Tax Remittance Export:
  - Format: CSV
  - Content: Employee name, ID, basic, taxable income, tax amount, period
  - For revenue authority remittance
  - By pay period with running totals

Pension Export:
  - Format: CSV
  - Content: Employee name, pension ID, contribution, period
  - For pension fund submission

Insurance Export:
  - Format: CSV
  - Insurance deductions summary
  - For insurance company remittance

Loan Export:
  - Format: CSV
  - Loan deductions by staff
  - For loan servicing

GL Export (Accounting Integration):
  - Generates GL entries: Dr salary expense, Cr bank/liabilities
  - Supports reconciliation
  - Account code mapping per deduction type

File Integrity:
  - SHA-256 hash: detect any modifications
  - Timestamp: when generated
  - Record count: quantity verification
  - Total amount: amount verification
  - Status tracking: transmitted, settled, failed

================================================================================
ROLE-BASED ACCESS CONTROL
================================================================================

HR Manager (hr_manager):
  ✓ View own salary mapping
  ✓ Create payroll batches
  ✓ Calculate payroll
  ✓ Submit for approval
  ✓ View payroll records
  ✓ Add adjustments
  ✓ View audit logs
  ✗ Cannot approve (HR step 1) - handled separately
  ✗ Cannot export

Admin (admin):
  ✓ All HR Manager permissions
  ✓ Approve batches (step 1: HR)
  ✓ Manage salary mappings
  ✓ Create/modify/delete batches
  ✓ View all payroll data
  ✓ View complete audit logs

Finance Manager (finance_manager):
  ✓ Approve batches (step 2: Admin)
  ✓ Approve batches (step 3: Finance)
  ✓ Mark as paid
  ✓ Generate exports
  ✓ View payroll records
  ✓ View GL entries
  ✓ Export reports

Employee (staff):
  ✓ View own payroll records (pay stubs)
  ✓ View own salary mapping (current month)
  ✗ Cannot modify or approve
  ✗ Cannot access others' data

Separation of Duties:
  - HR creates/calculates
  - Admin reviews/approves
  - Finance authorizes payment/exports
  - No one person can complete full cycle

================================================================================
APPROVAL WORKFLOW & AUDIT TRAILS
================================================================================

Three-Tier Approval System:

TIER 1: HR MANAGER
  Action: submit_for_approval()
  Role: hr_manager
  Checks:
    - Batch in DRAFT status
    - All records calculated
    - No invalid records
    - Control totals match
  Result: Batch → HR_APPROVED
  Log: Submit action, timestamp, reason

TIER 2: ADMIN
  Action: approve_batch(step=1)
  Role: admin
  Checks:
    - Batch in HR_APPROVED status
    - Review calculations
    - Verify policy compliance
  Result: Batch → ADMIN_APPROVED
  Log: Approval action, comments, timestamp

TIER 3: FINANCE MANAGER
  Action: approve_batch(step=2)
  Role: finance_manager
  Checks:
    - Batch in ADMIN_APPROVED status
    - Final authorization for payment
  Result: Batch → FINANCE_PROCESSING
  Actions:
    - Generate GL entries
    - Generate bank export
    - Prepare payment file
  Log: Finance approval, timestamp

REJECTION HANDLING:
  reject_batch(batch_id, reason):
    - Batch → DRAFT (can be recalculated)
    - Reason logged
    - All changes reset
    - Staff notified

AUDIT LOGGING:
  Every action creates immutable PayrollAuditLog:
    - action: calculate_payroll, approve_batch, export, etc.
    - entity_type: batch, record, approval
    - old_values: previous state (if applicable)
    - new_values: new state
    - changes: fields that changed
    - actor_id: who performed action
    - reason: why (business justification)
    - timestamp: when
    - ip_address: from where

Cannot be deleted or edited - compliance requirement

================================================================================
VALIDATION & ERROR HANDLING
================================================================================

Pre-Calculation Validation:
  ✓ SalaryMapping exists and active
  ✓ Basic salary > 0
  ✓ Deductions not negative
  ✓ All required fields present

Post-Calculation Validation:
  ✓ Gross >= Basic (with allowances)
  ✓ Net salary not negative
  ✓ Deductions <= Gross
  ✓ Adjustments reasonable

Batch Validation:
  ✓ Batch in correct status
  ✓ All records processed
  ✓ No invalid records remain
  ✓ Control totals match expected
  ✓ Record count matches control

Error Recovery:
  - Failed records flagged with error messages
  - Can recalculate specific staff
  - Can override individual fields
  - Version tracking preserves history
  - No data loss, only additions

Reconciliation:
  - Control totals: expected vs. actual
  - Record count: expected vs. actual
  - Amount verification: GL entries balance
  - Export totals: match batch totals

================================================================================
API ENDPOINTS
================================================================================

SALARY MAPPING:
  GET /api/payroll/salary-mapping/<user_id>
    → Get active salary mapping
    → Access: Staff own, HR/Admin all
    
  PUT /api/payroll/salary-mapping/<user_id>
    → Update salary (creates new version, deactivates old)
    → Access: HR Manager, Admin
    → Body: {basic_salary, allowances, deductions, effective_date}

PAYROLL BATCH:
  POST /api/payroll/batches
    → Create batch
    → Access: HR Manager, Admin
    → Body: {batch_name, payroll_period, start_date, end_date, payment_date}
    
  GET /api/payroll/batches/<batch_id>
    → Get batch details
    → Access: All payroll users
    
  POST /api/payroll/batches/<batch_id>/calculate
    → Calculate payroll
    → Access: HR Manager, Admin
    → Body: {staff_ids (optional)}
    
  POST /api/payroll/batches/<batch_id>/submit
    → Submit for approval (HR step)
    → Access: HR Manager
    
  POST /api/payroll/batches/<batch_id>/approve
    → Approve at step
    → Access: Admin (step 1), Finance (step 2)
    → Body: {approval_step, comments}
    
  POST /api/payroll/batches/<batch_id>/reject
    → Reject batch
    → Access: Admin, Finance
    → Body: {rejection_reason}

PAYROLL RECORDS:
  GET /api/payroll/batches/<batch_id>/records?page=1&per_page=50
    → Get batch records paginated
    → Access: All payroll users

EXPORTS:
  POST /api/payroll/batches/<batch_id>/export/bank-payment
    → Generate bank payment export
    → Access: Finance Manager, Admin
    → Body: {format: csv|excel|txt}
    
  POST /api/payroll/batches/<batch_id>/export/tax
    → Generate tax remittance
    → Access: Finance Manager, Admin
    
  POST /api/payroll/batches/<batch_id>/export/pension
    → Generate pension remittance
    → Access: Finance Manager, Admin

AUDIT:
  GET /api/payroll/batches/<batch_id>/audit-logs
    → Get audit trail
    → Access: Admin only

HEALTH:
  GET /api/payroll/health
    → Module health check
    → Returns: {status: ok}

================================================================================
IMPLEMENTATION CHECKLIST
================================================================================

Models (Completed):
  ✓ SalaryMapping - Staff salary configuration
  ✓ PayrollBatch - Batch container
  ✓ PayrollRecord - Individual calculations
  ✓ PayrollAdjustment - One-time adjustments
  ✓ PayrollApproval - Approval workflow
  ✓ PayrollAuditLog - Immutable audit trail
  ✓ PayrollExport - Generated exports
  ✓ AccountingEntry - GL integration

Calculation Engine (Completed):
  ✓ calculate_staff_payroll() - Single staff calculation
  ✓ calculate_batch_payroll() - Batch processing
  ✓ _apply_overrides() - Field overrides
  ✓ validate_batch() - Pre-approval validation
  ✓ _update_batch_summary() - Summary calculations

Batch Manager (Completed):
  ✓ create_batch() - New batch creation
  ✓ calculate_batch() - Trigger calculations
  ✓ submit_for_approval() - HR submission
  ✓ approve_batch() - Multi-tier approval
  ✓ reject_batch() - Rejection with reason
  ✓ mark_as_paid() - Payment completion
  ✓ archive_batch() - Archive completion

Export Engines (Completed):
  ✓ generate_bank_payment_export() - Bank payments
  ✓ _write_bank_csv() - CSV format
  ✓ _write_bank_excel() - Excel format
  ✓ _write_bank_txt() - Fixed-width format
  ✓ generate_tax_export() - Tax remittance
  ✓ generate_pension_export() - Pension export
  ✓ _calculate_file_hash() - File integrity
  ✓ get_export_file() - Download export

GL Engine (Completed):
  ✓ generate_gl_entries() - Create GL entries
  ✓ Account code mapping

API Endpoints (Completed):
  ✓ Salary mapping endpoints
  ✓ Batch management endpoints
  ✓ Approval endpoints
  ✓ Export endpoints
  ✓ Audit log endpoints
  ✓ Role-based access control

Additional Components (To implement):
  □ UI/Dashboard for payroll management
  □ Payroll reports (slips, summaries, tax reports)
  □ Bank reconciliation module
  □ Tax compliance reports
  □ Employee self-service portal
  □ Email notifications
  □ Scheduled batch processing
  □ Data import utilities

================================================================================
SECURITY & COMPLIANCE
================================================================================

Access Control:
  ✓ Role-based: Different permissions per role
  ✓ Separation of duties: No single person controls full process
  ✓ Field-level: Staff can only see own data
  ✓ API authentication: Requires login
  ✓ Rate limiting: Prevent abuse

Data Protection:
  ✓ Immutable audit logs: Cannot be deleted
  ✓ Versioning: Track all changes
  ✓ Backup: Regular database backups
  ✓ Encryption: Sensitive fields (bank accounts, salary)
  ✓ Data retention: Archive old batches

Compliance:
  ✓ Tax law adherence: Correct tax calculations
  ✓ Pension regulations: Proper pension contributions
  ✓ Labor laws: Minimum salary, leave deductions
  ✓ GL posting: Proper accounting treatment
  ✓ Audit trail: Complete accountability

Error Handling:
  ✓ Validation errors: Detailed messages
  ✓ Graceful degradation: Partial failures don't stop batch
  ✓ Recovery: Reprocess failed records
  ✓ Logging: All errors logged for diagnosis
  ✓ Alerts: Critical errors notify administrators

================================================================================
PRODUCTION DEPLOYMENT
================================================================================

Database Setup:
  1. Run migrations: flask db upgrade
  2. Create indexes for performance
  3. Set up connection pooling
  4. Enable query logging
  5. Configure backups

Application Configuration:
  1. Set Flask_ENV=production
  2. Enable HTTPS/SSL
  3. Configure CORS if needed
  4. Set up logging to file/syslog
  5. Configure email for notifications

Performance Optimization:
  1. Database indexes on frequently queried fields
  2. Query optimization: eager loading where needed
  3. Caching: Redis for frequently accessed data
  4. Batch processing: Async jobs for large batches
  5. File handling: Stream large exports

Monitoring:
  1. Application health: /api/payroll/health
  2. Database: Monitor connections, slow queries
  3. File system: Check export directory size
  4. Audit logs: Alert on unusual patterns
  5. Approvals: Track average processing time

Maintenance:
  1. Regular backups: Daily full, hourly incremental
  2. Archive old batches: Move to archive storage
  3. Update dependencies: Security patches
  4. Performance tuning: Monitor and optimize
  5. Compliance audits: Regular reviews

================================================================================
TESTING RECOMMENDATIONS
================================================================================

Unit Tests:
  □ SalaryMapping calculations
  □ PayrollRecord validation
  □ Calculation engine
  □ Batch manager operations
  □ Export generation

Integration Tests:
  □ Full batch processing workflow
  □ Multi-tier approval flow
  □ GL entry generation
  □ Audit log creation
  □ API endpoints

End-to-End Tests:
  □ Create batch → Calculate → Approve → Export → Pay
  □ Rejection and recalculation
  □ Override and recalculation
  □ Archive and retrieval

Security Tests:
  □ Role-based access control
  □ Data validation and sanitization
  □ SQL injection prevention
  □ XSS prevention
  □ CSRF protection

Performance Tests:
  □ Batch processing speed (1000+ staff)
  □ Export generation speed
  □ API response times
  □ Database query optimization
  □ Concurrent user load

================================================================================
USAGE EXAMPLES
================================================================================

1. Create and process payroll:

   from app.payroll_batch_manager import PayrollBatchManager
   from app.payroll_engine import PayrollCalculationEngine
   from datetime import date
   
   # Create batch
   batch, errors = PayrollBatchManager.create_batch(
       batch_name='February 2026 Payroll',
       payroll_period='2026-02',
       start_date=date(2026, 2, 1),
       end_date=date(2026, 2, 28),
       payment_date=date(2026, 3, 5),
       created_by_id=admin_user_id
   )
   
   # Calculate payroll
   success, result = PayrollBatchManager.calculate_batch(batch.id, actor_id=admin_user_id)
   
   # Submit for approval
   success, msg = PayrollBatchManager.submit_for_approval(batch.id, admin_user_id)
   
   # HR Manager approves
   success, msg = PayrollBatchManager.approve_batch(batch.id, 1, hr_manager_id)
   
   # Admin approves
   success, msg = PayrollBatchManager.approve_batch(batch.id, 2, admin_id)
   
   # Finance approves
   success, msg = PayrollBatchManager.approve_batch(batch.id, 3, finance_manager_id)
   
   # Export for payment
   success, result = PayrollExportEngine.generate_bank_payment_export(batch.id, 'excel')
   
   # Mark as paid
   success, msg = PayrollBatchManager.mark_as_paid(batch.id, date.today(), finance_manager_id)

2. Update salary mapping:

   from app.payroll_models import SalaryMapping
   
   # Create new mapping (old one auto-deactivated)
   mapping = SalaryMapping(
       user_id=staff_id,
       basic_salary=5000000,
       house_allowance=500000,
       tax_amount=750000,
       pension_amount=250000,
       created_by_id=admin_user_id
   )
   db.session.add(mapping)
   db.session.commit()

3. Add bonus adjustment:

   from app.payroll_models import PayrollAdjustment, AdjustmentType
   
   adjustment = PayrollAdjustment(
       user_id=staff_id,
       adjustment_type=AdjustmentType.BONUS,
       amount=500000,
       description='Performance bonus',
       payroll_period='2026-02',
       created_by_id=admin_user_id
   )
   db.session.add(adjustment)
   db.session.commit()

4. View audit trail:

   from app.payroll_models import PayrollAuditLog
   
   logs = PayrollAuditLog.query.filter_by(batch_id=batch_id).order_by(
       PayrollAuditLog.created_at.desc()
   ).all()
   
   for log in logs:
       print(f"{log.created_at}: {log.action} by {log.actor.name}")
       print(f"  Reason: {log.reason}")

================================================================================
SUPPORT & MAINTENANCE
================================================================================

Common Issues:
  1. Calculation errors
     → Check SalaryMapping is active and accurate
     → Verify no validation errors in PayrollRecord
     → Review PayrollAuditLog for action history

  2. Approval stuck in draft
     → Check batch status and required approvals
     → Verify user role has permission
     → Review rejection reason if applicable

  3. Export generation failed
     → Check file system permissions
     → Verify export path exists
     → Check for disk space

  4. GL entries not created
     → Ensure batch reached Finance step
     → Check AccountingEntry was created
     → Verify account codes are valid

Support Contacts:
  - Technical: IT Department
  - Business: Payroll Manager
  - Finance: CFO Office
  - Compliance: Legal/Audit

================================================================================
END OF DOCUMENTATION
================================================================================
"""

print(__doc__)
