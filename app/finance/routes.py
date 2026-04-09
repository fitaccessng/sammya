"""
Finance Module - Complete Finance Management System
- Dashboard with QuickBooks-like features
- Payment verification and approval workflows
- Bank reconciliation and account management
- Expense tracking and categorization
- Payroll processing and approval
- Document management and audit trails
- Financial reporting and analytics
"""

from datetime import datetime, timedelta
import os
from flask import Blueprint, render_template, current_app, flash, request, jsonify, url_for, redirect, send_file, session
from flask_login import current_user, logout_user, login_required
from app.utils import role_required, Roles
from app.models import (
    db, User, Project, PurchaseOrder, PaymentRequest, PaymentRecord, 
    ApprovalState, QCInspection, Vendor, BOQItem, Expense, BankAccount, BankReconciliation, ChangeOrder, ProjectStaff,
    ApprovalLog, Payroll, ChartOfAccount, LedgerEntry, RevenueSale, ProjectPaymentRequest
)
from sqlalchemy import func, desc, or_, and_, case
from werkzeug.utils import secure_filename
import os
import pandas as pd
from io import BytesIO
import json
import csv

finance_bp = Blueprint('finance', __name__, url_prefix='/finance')
bp = finance_bp  # Alias for backward compatibility


NOTE_TAGS_PREFIX = "[tags:"
NOTE_META_PREFIX = "[meta:"


def _as_float(value, default=0.0):
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _split_note_fields(raw_text):
    text = raw_text or ""
    clean = text
    tags = []
    meta = {}

    tags_start = clean.find(NOTE_TAGS_PREFIX)
    if tags_start != -1:
        tags_end = clean.find("]", tags_start)
        if tags_end != -1:
            tag_value = clean[tags_start + len(NOTE_TAGS_PREFIX):tags_end]
            tags = [t.strip() for t in tag_value.split(",") if t.strip()]
            clean = (clean[:tags_start] + clean[tags_end + 1:]).strip()

    meta_start = clean.find(NOTE_META_PREFIX)
    if meta_start != -1:
        meta_end = clean.find("]", meta_start)
        if meta_end != -1:
            raw_meta = clean[meta_start + len(NOTE_META_PREFIX):meta_end]
            for pair in raw_meta.split(";"):
                if "=" in pair:
                    key, val = pair.split("=", 1)
                    key = key.strip()
                    val = val.strip()
                    if key:
                        meta[key] = val
            clean = (clean[:meta_start] + clean[meta_end + 1:]).strip()

    return clean, tags, meta


def _compose_note(clean_text, tags=None, meta=None):
    note = (clean_text or "").strip()
    if tags:
        normalized = ",".join(sorted({t.strip() for t in tags if t and t.strip()}))
        if normalized:
            note = f"{note} {NOTE_TAGS_PREFIX}{normalized}]".strip()
    if meta:
        pairs = [f"{k}={v}" for k, v in meta.items() if v not in (None, "")]
        if pairs:
            note = f"{note} {NOTE_META_PREFIX}{';'.join(pairs)}]".strip()
    return note


def _post_double_entry(debit_account_id, credit_account_id, amount, description, reference="", entity_type=None, entity_id=None, category=None):
    amount = _as_float(amount)
    if amount <= 0 or not debit_account_id or not credit_account_id:
        return

    meta = {}
    if category:
        meta["category"] = category
    line_description = _compose_note(description, meta=meta)

    db.session.add(LedgerEntry(
        account_id=debit_account_id,
        description=line_description,
        reference=reference,
        debit=amount,
        credit=0,
        entity_type=entity_type,
        entity_id=entity_id,
        created_by=current_user.id if current_user and current_user.is_authenticated else None
    ))
    db.session.add(LedgerEntry(
        account_id=credit_account_id,
        description=line_description,
        reference=reference,
        debit=0,
        credit=amount,
        entity_type=entity_type,
        entity_id=entity_id,
        created_by=current_user.id if current_user and current_user.is_authenticated else None
    ))


def _find_or_create_equity_account():
    account = ChartOfAccount.query.filter_by(account_code="3000").first()
    if account:
        return account
    account = ChartOfAccount(
        account_code="3000",
        account_name="Owner Equity",
        account_type="equity"
    )
    db.session.add(account)
    db.session.flush()
    return account


def _find_or_create_undeposited_funds_account():
    account = ChartOfAccount.query.filter_by(account_code="1015").first()
    if account:
        return account
    account = ChartOfAccount(
        account_code="1015",
        account_name="Undeposited Funds",
        account_type="asset"
    )
    db.session.add(account)
    db.session.flush()
    return account


def _upsert_chart_bank_link(bank_account, opening_balance=0.0):
    marker = f"bank_account_id={bank_account.id}"
    linked = ChartOfAccount.query.filter(
        ChartOfAccount.description.ilike(f"%{marker}%")
    ).first()

    if linked:
        return linked

    code_candidate = f"BANK-{bank_account.id}"
    linked = ChartOfAccount(
        account_code=code_candidate,
        account_name=bank_account.account_name,
        account_type="asset",
        description=_compose_note(
            bank_account.bank_name or "",
            meta={
                "bank_account_id": bank_account.id,
                "account_number": bank_account.account_number
            }
        )
    )
    db.session.add(linked)
    db.session.flush()

    opening_balance = _as_float(opening_balance)
    if opening_balance > 0:
        equity = _find_or_create_equity_account()
        _post_double_entry(
            debit_account_id=linked.id,
            credit_account_id=equity.id,
            amount=opening_balance,
            description=f"Opening balance - {bank_account.account_name}",
            reference=f"OPEN-BANK-{bank_account.id}",
            entity_type="bank_account",
            entity_id=bank_account.id,
            category="opening_balance"
        )
    return linked


def get_user_finance_projects(user):
    """Get projects accessible to finance user."""
    if user.has_role(Roles.HQ_FINANCE) or user.has_role(Roles.FINANCE_MANAGER) or user.has_role(Roles.SUPER_HQ):
        # Managers can see all projects
        return Project.query.all()
    else:
        # Staff see only assigned projects
        return db.session.query(Project).join(
            ProjectStaff
        ).filter(
            ProjectStaff.user_id == user.id,
            ProjectStaff.is_active == True
        ).all()


def ensure_default_chart_of_accounts():
    """Create base accounts for the 5 account types when missing."""
    defaults = [
        ('1000', 'Cash at Bank', 'asset'),
        ('1100', 'Accounts Receivable', 'asset'),
        ('2000', 'Accounts Payable', 'liability'),
        ('3000', 'Owner Equity', 'equity'),
        ('4000', 'Revenue', 'revenue'),
        ('5000', 'Operating Expenses', 'expense')
    ]
    for code, name, acc_type in defaults:
        exists = ChartOfAccount.query.filter_by(account_code=code).first()
        if not exists:
            db.session.add(ChartOfAccount(account_code=code, account_name=name, account_type=acc_type))
    db.session.commit()


# ===== DASHBOARD ROUTES =====

@finance_bp.route('/')
@role_required([Roles.SUPER_HQ, Roles.HQ_FINANCE, Roles.FINANCE_MANAGER, Roles.ACCOUNTS_PAYABLE, Roles.ADMIN])
def finance_home():
    """Main finance dashboard with comprehensive financial summary."""
    try:
        current_month = datetime.now().month
        current_year = datetime.now().year
        
        # Enhanced financial summary
        bank_balance = db.session.query(func.sum(BankReconciliation.balance)).scalar() or 0
        
        monthly_expenses = db.session.query(func.sum(Expense.amount)).filter(
            func.extract('month', Expense.date) == current_month,
            func.extract('year', Expense.date) == current_year
        ).scalar() or 0
        
        # Get payment requests data
        pending_payments = PaymentRequest.query.filter_by(approval_state=ApprovalState.PENDING).all()
        approved_payments = PaymentRequest.query.filter_by(approval_state=ApprovalState.APPROVED).all()
        
        pending_amount = sum(float(p.invoice_amount or 0) for p in pending_payments)
        approved_amount = sum(float(p.invoice_amount or 0) for p in approved_payments)
        
        # Get recent transactions
        recent_transactions = PaymentRecord.query.order_by(PaymentRecord.payment_date.desc()).limit(5).all()
        
        # Document counts
        total_documents = PaymentRequest.query.count() + Expense.query.count()
        pending_review = PaymentRequest.query.filter_by(approval_state=ApprovalState.PENDING).count()
        
        summary = {
            'bank_balance': bank_balance,
            'monthly_expenses': monthly_expenses,
            'pending_expenses': db.session.query(func.sum(Expense.amount)).filter(Expense.status == 'pending').scalar() or 0,
            'approved_expenses': db.session.query(func.sum(Expense.amount)).filter(Expense.status == 'approved').scalar() or 0,
            'pending_payments': pending_amount,
            'approved_payments': approved_amount,
            'pending_payment_count': len(pending_payments),
            'approved_payment_count': len(approved_payments),
            'total_documents': total_documents,
            'pending_review': pending_review,
        }
        
        return render_template('finance/index.html', summary=summary, transactions=recent_transactions)
    except Exception as e:
        current_app.logger.error(f"Finance dashboard error: {str(e)}")
        flash(f"Error loading finance dashboard: {str(e)}", "error")
        return redirect(url_for('finance.finance_home'))


@finance_bp.route('/dashboard')
@role_required([Roles.SUPER_HQ, Roles.HQ_FINANCE, Roles.FINANCE_MANAGER, Roles.ACCOUNTS_PAYABLE, Roles.ADMIN])
def dashboard():
    """Finance dashboard with QuickBooks-like features."""
    try:
        # Cash Summary
        pending_payments = PaymentRequest.query.filter_by(approval_state=ApprovalState.PENDING).all()
        pending_amount = sum(float(p.invoice_amount or 0) for p in pending_payments)
        
        approved_payments = PaymentRequest.query.filter_by(approval_state=ApprovalState.APPROVED).all()
        approved_amount = sum(float(p.invoice_amount or 0) for p in approved_payments)
        
        processed_payments = PaymentRecord.query.all()
        total_paid = sum(float(p.amount_paid or 0) for p in processed_payments)
        
        # Invoice aging analysis
        all_invoices = PaymentRequest.query.all()
        current_30 = len([p for p in all_invoices if (datetime.utcnow() - p.created_at).days <= 30])
        overdue_30_60 = len([p for p in all_invoices if 30 < (datetime.utcnow() - p.created_at).days <= 60])
        overdue_60_90 = len([p for p in all_invoices if 60 < (datetime.utcnow() - p.created_at).days <= 90])
        overdue_90 = len([p for p in all_invoices if (datetime.utcnow() - p.created_at).days > 90])
        
        # Top vendors by spend
        vendors = Vendor.query.all()
        vendor_spend = {}
        for vendor in vendors:
            spend = sum(float(p.amount_paid or 0) for p in processed_payments if p.payment_request and p.payment_request.purchase_order and p.payment_request.purchase_order.vendor_id == vendor.id)
            if spend > 0:
                vendor_spend[vendor.name] = spend
        
        top_vendors = sorted(vendor_spend.items(), key=lambda x: x[1], reverse=True)[:5]
        recent_payments = PaymentRecord.query.order_by(PaymentRecord.payment_date.desc()).limit(10).all()
        
        return render_template(
            'finance/dashboard.html',
            pending_amount=pending_amount,
            approved_amount=approved_amount,
            total_paid=total_paid,
            pending_count=len(pending_payments),
            approved_count=len(approved_payments),
            processed_count=len(processed_payments),
            current_30=current_30,
            overdue_30_60=overdue_30_60,
            overdue_60_90=overdue_60_90,
            overdue_90=overdue_90,
            top_vendors=top_vendors,
            recent_payments=recent_payments
        )
    except Exception as e:
        current_app.logger.error(f"Finance dashboard error: {str(e)}")
        flash("Error loading dashboard", "error")
        return redirect(url_for('finance.finance_home'))


# ===== PAYMENT MANAGEMENT ROUTES =====

@finance_bp.route('/payments')
@role_required([Roles.SUPER_HQ, Roles.HQ_FINANCE, Roles.FINANCE_MANAGER, Roles.ACCOUNTS_PAYABLE, Roles.ADMIN])
def list_payments():
    """List all payment requests and records."""
    pending = PaymentRequest.query.filter_by(approval_state=ApprovalState.PENDING).all()
    approved = PaymentRequest.query.filter_by(approval_state=ApprovalState.APPROVED).all()
    
    return render_template('finance/payments.html', pending=pending, approved=approved)


@finance_bp.route('/payment/create/<int:po_id>', methods=['GET', 'POST'])
@role_required([Roles.SUPER_HQ, Roles.HQ_FINANCE, Roles.FINANCE_MANAGER, Roles.ACCOUNTS_PAYABLE, Roles.ADMIN])
def create_payment_request(po_id):
    """Create payment request from approved PO."""
    po = PurchaseOrder.query.get_or_404(po_id)
    
    if request.method == 'POST':
        try:
            invoice_number = request.form.get('invoice_number').strip()
            invoice_amount = request.form.get('invoice_amount')
            
            if not invoice_number or not invoice_amount:
                flash('Invoice number and amount required.', 'warning')
                return redirect(url_for('finance.create_payment_request', po_id=po_id))
            
            payment_request = PaymentRequest(
                po_id=po_id,
                invoice_number=invoice_number,
                amount=float(invoice_amount),
                approval_state=ApprovalState.DRAFT
            )
            
            db.session.add(payment_request)
            db.session.commit()
            
            flash(f'Payment Request created successfully.', 'success')
            return redirect(url_for('finance.view_payment_request', payment_id=payment_request.id))
        except Exception as e:
            db.session.rollback()
            flash(f'Error creating payment request: {str(e)}', 'error')
    
    return render_template('finance/create_payment_request.html', po=po)


@finance_bp.route('/payment/<int:payment_id>')
@role_required([Roles.SUPER_HQ, Roles.HQ_FINANCE, Roles.FINANCE_MANAGER, Roles.ACCOUNTS_PAYABLE, Roles.ADMIN])
def view_payment_request(payment_id):
    """View payment request details."""
    payment = PaymentRequest.query.get_or_404(payment_id)
    return render_template('finance/view_payment_request.html', payment=payment)


@finance_bp.route('/payment/<int:payment_id>/verify', methods=['POST'])
@role_required([Roles.SUPER_HQ, Roles.HQ_FINANCE, Roles.FINANCE_MANAGER, Roles.ACCOUNTS_PAYABLE, Roles.ADMIN])
def verify_payment(payment_id):
    """Finance manager verifies and approves payment."""
    try:
        payment = PaymentRequest.query.get_or_404(payment_id)
        
        if payment.approval_state != ApprovalState.DRAFT:
            flash('Payment request not in draft state.', 'warning')
            return redirect(url_for('finance.view_payment_request', payment_id=payment_id))
        
        payment.approval_state = ApprovalState.APPROVED
        payment.verified_by = current_user.id
        
        db.session.commit()
        flash('Payment verified and approved.', 'success')
    except Exception as e:
        db.session.rollback()
        flash(f'Error: {str(e)}', 'error')
    
    return redirect(url_for('finance.view_payment_request', payment_id=payment_id))


@finance_bp.route('/payment/<int:payment_id>/process', methods=['POST'])
@role_required([Roles.SUPER_HQ, Roles.HQ_FINANCE, Roles.FINANCE_MANAGER, Roles.ACCOUNTS_PAYABLE, Roles.ADMIN])
def process_payment(payment_id):
    """Process and execute payment."""
    try:
        payment = PaymentRequest.query.get_or_404(payment_id)
        
        if payment.approval_state != ApprovalState.APPROVED:
            flash('Payment not approved for processing.', 'warning')
            return redirect(url_for('finance.view_payment_request', payment_id=payment_id))
        
        payment_method = request.form.get('payment_method')
        reference_number = request.form.get('reference_number').strip()
        
        record = PaymentRecord(
            payment_request_id=payment_id,
            po_id=payment.po_id,
            amount_paid=payment.invoice_amount,
            payment_date=datetime.utcnow(),
            payment_method=payment_method,
            reference_number=reference_number,
            processed_by=current_user.id
        )
        
        db.session.add(record)
        db.session.commit()
        
        flash(f'Payment processed successfully.', 'success')
        return redirect(url_for('finance.view_payment_record', record_id=record.id))
    except Exception as e:
        db.session.rollback()
        flash(f'Error: {str(e)}', 'error')
    
    return redirect(url_for('finance.view_payment_request', payment_id=payment_id))


@finance_bp.route('/payment-record/create', methods=['POST'])
@login_required
@role_required([Roles.SUPER_HQ, Roles.HQ_FINANCE, Roles.FINANCE_MANAGER, Roles.ACCOUNTS_PAYABLE, Roles.ADMIN])
def payment_record_create():
    """Create payment record with bank account deduction and reconciliation."""
    try:
        # Get form data
        bank_account_id = request.form.get('bank_account_id', type=int)
        payment_request_id = request.form.get('payment_request_id', type=int)
        amount_paid = request.form.get('amount_paid', type=float)
        payment_method = request.form.get('payment_method', '').strip()
        reference_number = request.form.get('reference_number', '').strip()
        payment_notes = request.form.get('payment_notes', '').strip()
        
        # Validate required fields
        if not all([bank_account_id, payment_request_id, amount_paid, payment_method, reference_number]):
            flash('All required fields must be filled.', 'warning')
            return redirect(url_for('finance.payment'))
        
        # Fetch bank account and payment request
        bank_account = BankAccount.query.get_or_404(bank_account_id)
        payment_request = PaymentRequest.query.get_or_404(payment_request_id)
        
        # Validate payment request is approved
        if payment_request.approval_state != ApprovalState.APPROVED:
            flash('Payment request must be approved before payment.', 'warning')
            return redirect(url_for('finance.payment'))
        
        # Validate sufficient balance
        if amount_paid > bank_account.balance:
            flash(f'Insufficient balance. Available: ₦{bank_account.balance:,.2f}', 'error')
            return redirect(url_for('finance.payment'))
        
        # Create payment record
        record = PaymentRecord(
            payment_request_id=payment_request_id,
            po_id=payment_request.po_id,
            amount_paid=amount_paid,
            payment_date=datetime.utcnow(),
            payment_method=payment_method,
            reference_number=reference_number,
            processed_by=current_user.id
        )
        db.session.add(record)
        db.session.flush()
        
        # Deduct amount from bank account
        bank_account.balance -= amount_paid
        bank_asset_account = _upsert_chart_bank_link(bank_account, opening_balance=0)

        # Payment ledger entry: Dr Accounts Payable, Cr Bank
        payable_account = ChartOfAccount.query.filter_by(account_code='2000').first()
        if payable_account and bank_asset_account:
            _post_double_entry(
                debit_account_id=payable_account.id,
                credit_account_id=bank_asset_account.id,
                amount=amount_paid,
                description=f"Payment disbursement {reference_number}",
                reference=f"PAY-{record.reference_number or record.id}",
                entity_type='payment_record',
                entity_id=record.id,
                category='payment'
            )
        
        # Create bank reconciliation record
        reconciliation = BankReconciliation(
            bank_account_id=bank_account_id,
            statement_date=datetime.utcnow(),
            ledger_balance=bank_account.balance,
            statement_balance=bank_account.balance,
            difference=0.0,
            balance=bank_account.balance,
            status='reconciled',
            notes=f'Payment {reference_number} - {payment_notes}' if payment_notes else f'Payment {reference_number}'
        )
        
        # Handle file upload if provided
        if 'proof_document' in request.files:
            file = request.files['proof_document']
            if file and file.filename:
                try:
                    # Save file to uploads directory
                    upload_dir = os.path.join('app', 'static', 'uploads', 'payments')
                    os.makedirs(upload_dir, exist_ok=True)
                    
                    filename = secure_filename(f"{reference_number}_{file.filename}")
                    filepath = os.path.join(upload_dir, filename)
                    file.save(filepath)
                    
                    record.proof_document = f'/static/uploads/payments/{filename}'
                except Exception as e:
                    current_app.logger.error(f"File upload error: {str(e)}")
        
        # Save all changes in transaction
        db.session.add(reconciliation)
        db.session.commit()
        
        flash(f'Payment of ₦{amount_paid:,.2f} processed successfully. Account balance updated.', 'success')
        return redirect(url_for('finance.payment'))
        
    except Exception as e:
        db.session.rollback()
        current_app.logger.error(f"Payment record creation error: {str(e)}")
        flash(f'Error processing payment: {str(e)}', 'error')
        return redirect(url_for('finance.payment'))


@finance_bp.route('/payment-record/<int:record_id>')
@role_required([Roles.SUPER_HQ, Roles.HQ_FINANCE, Roles.FINANCE_MANAGER, Roles.ACCOUNTS_PAYABLE, Roles.ADMIN])
def view_payment_record(record_id):
    """View completed payment record."""
    record = PaymentRecord.query.get_or_404(record_id)
    return render_template('finance/view_payment_record.html', record=record)


# ===== EXPENSE MANAGEMENT ROUTES =====

@finance_bp.route('/expenses')
@role_required([Roles.SUPER_HQ, Roles.HQ_FINANCE, Roles.FINANCE_MANAGER, Roles.ACCOUNTS_PAYABLE, Roles.ADMIN])
def expenses():
    """View and manage expenses."""
    try:
        page = request.args.get('page', 1, type=int)
        per_page = request.args.get('per_page', 20, type=int)
        status_filter = request.args.get('status', 'all')
        category_filter = request.args.get('category', 'all')
        
        query = Expense.query
        
        if status_filter != 'all':
            query = query.filter(Expense.status == status_filter)
        if category_filter != 'all':
            query = query.filter(Expense.category == category_filter)
        
        expenses = query.order_by(desc(Expense.date)).paginate(
            page=page, per_page=per_page, error_out=False
        )
        
        categories = db.session.query(Expense.category).distinct().all()
        categories = [c[0] for c in categories if c[0]]
        expense_accounts = ChartOfAccount.query.filter_by(account_type='expense').order_by(ChartOfAccount.account_name.asc()).all()
        for account in expense_accounts:
            if account.account_name not in categories:
                categories.append(account.account_name)
        categories = sorted(categories)

        # Get all bank accounts for expense account selection
        bank_accounts = BankAccount.query.filter_by(is_active=True).all()

        # Live summary cards
        total_expenses = db.session.query(func.sum(Expense.amount)).scalar() or 0
        total_pending = db.session.query(func.sum(Expense.amount)).filter(Expense.status == 'pending').scalar() or 0
        total_approved = db.session.query(func.sum(Expense.amount)).filter(Expense.status == 'approved').scalar() or 0
        now = datetime.utcnow()
        month_total = db.session.query(func.sum(Expense.amount)).filter(
            func.extract('month', Expense.date) == now.month,
            func.extract('year', Expense.date) == now.year
        ).scalar() or 0

        return render_template('finance/expenses.html', 
                             expenses=expenses,
                             status_filter=status_filter,
                             category_filter=category_filter,
                             categories=categories,
                             bank_accounts=bank_accounts,
                             expense_accounts=expense_accounts,
                             total_expenses=float(total_expenses),
                             total_pending=float(total_pending),
                             total_approved=float(total_approved),
                             month_total=float(month_total))
    except Exception as e:
        current_app.logger.error(f"Expenses loading error: {str(e)}")
        flash(f'Error loading expenses: {str(e)}', 'error')
        return redirect(url_for('finance.finance_home'))


@finance_bp.route('/expenses/add', methods=['POST'])
@role_required([Roles.SUPER_HQ, Roles.HQ_FINANCE, Roles.FINANCE_MANAGER, Roles.ACCOUNTS_PAYABLE, Roles.ADMIN])
def add_expense():
    """Add new expense."""
    try:
        from app.models import ProjectDocument
        data = request.get_json() if request.is_json else request.form
        
        # Get form data
        description = data.get('description', '').strip()
        amount = data.get('amount')
        category = data.get('category', '').strip()
        date_str = data.get('date')
        expense_account_id = data.get('expense_account_id', type=int) if hasattr(data, 'get') else None
        paye_reference = data.get('paye_reference', '').strip()
        paye_amount = _as_float(data.get('paye_amount'))
        tags_raw = data.get('tags', '')
        tags = [t.strip() for t in tags_raw.split(',') if t.strip()] if tags_raw else []
        
        # Validate required fields
        if not all([description, amount, date_str]):
            return jsonify({'status': 'error', 'message': 'All fields are required'}), 400
        
        try:
            amount = float(amount)
            expense_date = datetime.strptime(date_str, '%Y-%m-%d')
        except (ValueError, TypeError) as ve:
            return jsonify({'status': 'error', 'message': f'Invalid amount or date: {str(ve)}'}), 400
        
        # Get first active project for assignment (optional)
        from app.models import Project
        project = Project.query.filter_by(status='active').first()
        expense_account = None
        if expense_account_id:
            expense_account = ChartOfAccount.query.get(expense_account_id)
            if expense_account and expense_account.account_type != 'expense':
                return jsonify({'status': 'error', 'message': 'Selected account must be an expense account'}), 400
        if not category and expense_account:
            category = expense_account.account_name
        if not category:
            return jsonify({'status': 'error', 'message': 'Category is required'}), 400

        metadata = {}
        if paye_reference:
            metadata["paye_reference"] = paye_reference
        if paye_amount > 0:
            metadata["paye_amount"] = f"{paye_amount:.2f}"
        
        expense = Expense(
            description=_compose_note(description, tags=tags, meta=metadata),
            amount=amount,
            category=category,
            date=expense_date,
            status='pending',
            project_id=project.id if project else None
        )
        
        db.session.add(expense)
        db.session.flush()

        # Save invoice/receipt document if provided.
        if not request.is_json and 'invoice_file' in request.files:
            file = request.files['invoice_file']
            if file and file.filename:
                file_ext = file.filename.rsplit('.', 1)[1].lower() if '.' in file.filename else ''
                allowed_extensions = {'pdf', 'jpg', 'jpeg', 'png', 'doc', 'docx'}
                if file_ext not in allowed_extensions:
                    return jsonify({'status': 'error', 'message': 'Invalid invoice file type'}), 400
                upload_dir = os.path.join(current_app.root_path, 'uploads', 'finance_documents')
                os.makedirs(upload_dir, exist_ok=True)
                filename = secure_filename(f"expense_{expense.id}_{datetime.utcnow().strftime('%Y%m%d_%H%M%S')}_{file.filename}")
                filepath = os.path.join(upload_dir, filename)
                file.save(filepath)

                doc_meta = {"expense_id": expense.id}
                if expense.project_id:
                    document = ProjectDocument(
                        project_id=expense.project_id,
                        title=f"Expense Invoice EXP-{expense.id}",
                        description=_compose_note(f"Invoice for expense {expense.id}", tags=tags, meta=doc_meta),
                        file_name=filename,
                        file_path=f"/uploads/finance_documents/{filename}",
                        document_type=file_ext,
                        uploaded_by_id=current_user.id,
                        created_at=datetime.utcnow()
                    )
                    db.session.add(document)

        # Optional immediate expense posting to ledger.
        expense_account = expense_account or ChartOfAccount.query.filter(
            ChartOfAccount.account_type == 'expense',
            func.lower(ChartOfAccount.account_name) == category.lower()
        ).first()
        cash_account = ChartOfAccount.query.filter_by(account_code='1000').first()
        if expense_account and cash_account:
            reference = f"EXP-{expense.id}"
            if paye_amount > 0:
                paye_account = ChartOfAccount.query.filter_by(account_code='2100').first()
                if not paye_account:
                    paye_account = ChartOfAccount(
                        account_code='2100',
                        account_name='PAYE Payable',
                        account_type='liability'
                    )
                    db.session.add(paye_account)
                    db.session.flush()

                db.session.add(LedgerEntry(
                    account_id=expense_account.id,
                    description=_compose_note(description, tags=tags, meta={"category": category}),
                    reference=reference,
                    debit=amount,
                    credit=0,
                    entity_type='expense',
                    entity_id=expense.id,
                    created_by=current_user.id
                ))
                net_cash = max(amount - paye_amount, 0)
                db.session.add(LedgerEntry(
                    account_id=cash_account.id,
                    description=_compose_note(description, tags=tags, meta={"category": category}),
                    reference=reference,
                    debit=0,
                    credit=net_cash,
                    entity_type='expense',
                    entity_id=expense.id,
                    created_by=current_user.id
                ))
                if paye_amount > 0:
                    db.session.add(LedgerEntry(
                        account_id=paye_account.id,
                        description=_compose_note(description, tags=tags, meta={"category": "paye"}),
                        reference=reference,
                        debit=0,
                        credit=paye_amount,
                        entity_type='expense',
                        entity_id=expense.id,
                        created_by=current_user.id
                    ))
            else:
                _post_double_entry(
                    debit_account_id=expense_account.id,
                    credit_account_id=cash_account.id,
                    amount=amount,
                    description=description,
                    reference=reference,
                    entity_type='expense',
                    entity_id=expense.id,
                    category=category
                )

        db.session.commit()
        
        return jsonify({'status': 'success', 'message': 'Expense added successfully', 'expense_id': expense.id}), 201
    except Exception as e:
        db.session.rollback()
        current_app.logger.error(f"Expense addition error: {str(e)}")
        return jsonify({'status': 'error', 'message': str(e)}), 500


@finance_bp.route('/expenses/<int:expense_id>')
@role_required([Roles.SUPER_HQ, Roles.HQ_FINANCE, Roles.FINANCE_MANAGER, Roles.ACCOUNTS_PAYABLE, Roles.ADMIN])
def expense_details(expense_id):
    """View expense details."""
    try:
        from app.models import ProjectDocument
        expense = Expense.query.get_or_404(expense_id)
        clean_description, tags, metadata = _split_note_fields(expense.description)
        invoice_docs = ProjectDocument.query.filter(
            or_(
                ProjectDocument.title.ilike(f"%EXP-{expense.id}%"),
                ProjectDocument.description.ilike(f"%expense_id={expense.id}%")
            )
        ).order_by(ProjectDocument.created_at.desc()).all()
        return render_template(
            'finance/expense_details.html',
            expense=expense,
            clean_description=clean_description,
            expense_tags=tags,
            expense_meta=metadata,
            invoice_docs=invoice_docs
        )
    except Exception as e:
        current_app.logger.error(f"Error loading expense details: {str(e)}")
        flash('Error loading expense details', 'error')
        return redirect(url_for('finance.expenses'))


# ===== BANK RECONCILIATION ROUTES =====

@finance_bp.route('/bank-reconciliation')
@role_required([Roles.SUPER_HQ, Roles.HQ_FINANCE, Roles.FINANCE_MANAGER, Roles.ACCOUNTS_PAYABLE, Roles.ADMIN])
def bank_reconciliation():
    """Bank reconciliation dashboard - shows list of accounts and balances."""
    try:
        # Get all active bank accounts
        bank_accounts = BankAccount.query.filter_by(is_active=True).all()
        
        # Calculate totals
        total_bank_balance = sum(float(acc.balance or 0) for acc in bank_accounts) if bank_accounts else 0
        
        # Prepare account summaries
        account_summaries = []
        for account in bank_accounts:
            summary = {
                'id': account.id,
                'account_name': account.account_name,
                'account_number': account.account_number,
                'bank_name': account.bank_name,
                'balance': float(account.balance or 0),
                'currency': account.currency or 'NGN'
            }
            account_summaries.append(summary)
        
        return render_template('finance/bank_reconciliation.html',
                             bank_accounts=bank_accounts,
                             total_bank_balance=total_bank_balance,
                             account_summaries=account_summaries)
    except Exception as e:
        current_app.logger.error(f"Bank reconciliation error: {str(e)}")
        flash("Error loading bank reconciliation", "error")
        return redirect(url_for('finance.finance_home'))


@finance_bp.route('/bank-reconciliation/create-account', methods=['POST'])
@role_required([Roles.SUPER_HQ, Roles.HQ_FINANCE, Roles.FINANCE_MANAGER, Roles.ACCOUNTS_PAYABLE, Roles.ADMIN])
def create_bank_account():
    """Create new bank account."""
    try:
        data = request.form
        opening_balance = float(data.get('opening_balance', 0))
        
        account = BankAccount(
            account_name=data.get('account_name'),
            account_number=data.get('account_number'),
            bank_name=data.get('bank_name'),
            balance=opening_balance,
            is_active=True
        )
        
        db.session.add(account)
        db.session.flush()
        _upsert_chart_bank_link(account, opening_balance=opening_balance)
        db.session.commit()
        
        return jsonify({'status': 'success', 'message': 'Bank account created successfully', 'account_id': account.id})
    except Exception as e:
        current_app.logger.error(f"Error creating bank account: {str(e)}")
        return jsonify({'status': 'error', 'message': 'Failed to create bank account'}), 500


@finance_bp.route('/bank-reconciliation/add-transaction', methods=['POST'])
@role_required([Roles.SUPER_HQ, Roles.HQ_FINANCE, Roles.FINANCE_MANAGER, Roles.ACCOUNTS_PAYABLE, Roles.ADMIN])
def add_transaction():
    """Add bank transaction."""
    try:
        data = request.get_json() if request.is_json else request.form
        
        account_id = data.get('account_id')
        transaction_type = data.get('transaction_type')
        amount = float(data.get('amount', 0))
        description = data.get('description', '')
        reference_number = data.get('reference_number', '')
        
        account = BankAccount.query.get(account_id)
        if not account:
            return jsonify({'status': 'error', 'message': 'Bank account not found'}), 404
        
        if transaction_type.lower() == 'debit' and account.current_balance < amount:
            return jsonify({'status': 'error', 'message': 'Insufficient funds'}), 400
        
        transaction = BankTransaction(
            account_id=account_id,
            transaction_type=transaction_type.title(),
            amount=amount,
            description=description,
            reference_number=reference_number,
            transaction_date=datetime.now().date(),
            created_by=current_user.id
        )
        
        if transaction_type.lower() == 'credit':
            account.current_balance += amount
            account.book_balance += amount
        else:
            account.current_balance -= amount
            account.book_balance -= amount
        
        db.session.add(transaction)
        db.session.commit()
        
        return jsonify({'status': 'success', 'message': 'Transaction recorded successfully'})
    except Exception as e:
        db.session.rollback()
        current_app.logger.error(f"Error adding transaction: {str(e)}")
        return jsonify({'status': 'error', 'message': str(e)}), 500


@finance_bp.route('/bank-reconciliation/account/<int:account_id>')
@role_required([Roles.SUPER_HQ, Roles.HQ_FINANCE, Roles.FINANCE_MANAGER, Roles.ACCOUNTS_PAYABLE, Roles.ADMIN])
def view_account_transactions(account_id):
    """View account details and reconciliation history."""
    try:
        account = BankAccount.query.get(account_id)
        if not account:
            flash('Account not found', 'error')
            return redirect(url_for('finance.bank_reconciliation'))
        
        # Get reconciliation history for this account
        reconciliations = BankReconciliation.query.filter_by(
            bank_account_id=account_id
        ).order_by(BankReconciliation.created_at.desc()).all()
        
        return render_template('finance/account_details.html',
                             account=account,
                             reconciliations=reconciliations)
    except Exception as e:
        current_app.logger.error(f"Error viewing account transactions: {str(e)}")
        flash(f"Error loading account details: {str(e)}", "error")
        return redirect(url_for('finance.bank_reconciliation'))


@finance_bp.route('/bank-reconciliation/account/<int:account_id>/reconcile', methods=['GET', 'POST'])
@role_required([Roles.SUPER_HQ, Roles.HQ_FINANCE, Roles.FINANCE_MANAGER, Roles.ACCOUNTS_PAYABLE, Roles.ADMIN])
def reconcile_account_form(account_id):
    """Reconcile account - GET shows form, POST submits reconciliation."""
    try:
        account = BankAccount.query.get(account_id)
        if not account:
            flash('Account not found', 'error')
            return redirect(url_for('finance.bank_reconciliation'))
        
        if request.method == 'POST':
            # Handle reconciliation submission
            statement_balance = request.form.get('statement_balance', type=float)
            notes = request.form.get('notes', '')
            
            if statement_balance is None:
                flash('Please enter the bank statement balance', 'error')
                return redirect(url_for('finance.reconcile_account_form', account_id=account_id))
            
            ledger_balance = float(account.balance or 0)
            difference = statement_balance - ledger_balance
            
            # Create reconciliation record
            reconciliation = BankReconciliation(
                bank_account_id=account_id,
                statement_date=datetime.utcnow(),
                statement_balance=statement_balance,
                ledger_balance=ledger_balance,
                difference=difference,
                balance=account.balance,
                status='reconciled' if difference == 0 else 'discrepancy',
                notes=notes
            )
            
            db.session.add(reconciliation)
            db.session.flush()  # Generate the ID without committing yet
            
            # Log the action with the generated ID
            audit_log = ApprovalLog(
                entity_type='BankReconciliation',
                entity_id=reconciliation.id,
                action='RECONCILED',
                actor_id=current_user.id,
                comment=f'Account {account.account_name} reconciled. Difference: ₦{abs(difference):,.2f}',
                timestamp=datetime.utcnow()
            )
            db.session.add(audit_log)
            db.session.commit()  # Now commit both records
            
            if difference == 0:
                flash(f'✓ Account {account.account_name} reconciled successfully! Accounts are balanced.', 'success')
            else:
                flash(f'⚠️ Reconciliation completed. Difference: ₦{abs(difference):,.2f}', 'warning')
            
            return redirect(url_for('finance.view_account_transactions', account_id=account_id))
        
        # GET request - show form
        current_ledger_balance = float(account.balance or 0)
        
        return render_template('finance/reconcile_account.html',
                             account=account,
                             ledger_balance=current_ledger_balance)
    except Exception as e:
        current_app.logger.error(f"Error in reconciliation form: {str(e)}")
        flash(f"Error: {str(e)}", "error")
        return redirect(url_for('finance.bank_reconciliation'))




# ===== AUDIT AND REPORTING ROUTES =====

@finance_bp.route('/audit')
@role_required([Roles.SUPER_HQ, Roles.HQ_FINANCE, Roles.FINANCE_MANAGER, Roles.ACCOUNTS_PAYABLE, Roles.ADMIN])
def audit():
    """View audit logs."""
    try:
        # Show approval logs as audit trail
        from app.models import ApprovalLog
        page = request.args.get('page', 1, type=int)
        per_page = request.args.get('per_page', 50, type=int)
        
        audits = ApprovalLog.query.order_by(desc(ApprovalLog.timestamp)).paginate(
            page=page, per_page=per_page, error_out=False
        )
        
        return render_template('finance/audit.html', audits=audits)
    except Exception as e:
        current_app.logger.error(f"Audit loading error: {str(e)}")
        flash(f'Error loading audit logs: {str(e)}', 'error')
        return redirect(url_for('finance.finance_home'))


@finance_bp.route('/reports')
@role_required([Roles.SUPER_HQ, Roles.HQ_FINANCE, Roles.FINANCE_MANAGER, Roles.ACCOUNTS_PAYABLE, Roles.ADMIN])
def reports():
    """Financial reports."""
    try:
        # Return a summary of financial reports/data
        # This can be expanded to show actual Report model data when available
        pending_payments = PaymentRequest.query.filter_by(approval_state=ApprovalState.PENDING).all()
        approved_payments = PaymentRequest.query.filter_by(approval_state=ApprovalState.APPROVED).all()
        processed_payments = PaymentRecord.query.all()
        
        total_pending = sum(float(p.invoice_amount or 0) for p in pending_payments)
        total_approved = sum(float(p.invoice_amount or 0) for p in approved_payments)
        total_paid = sum(float(p.amount_paid or 0) for p in processed_payments)
        
        return render_template('finance/reports.html', 
                             total_pending=total_pending,
                             total_approved=total_approved,
                             total_paid=total_paid,
                             pending_count=len(pending_payments),
                             approved_count=len(approved_payments))
    except Exception as e:
        current_app.logger.error(f"Reports loading error: {str(e)}")
        flash(f'Error loading reports: {str(e)}', 'error')
        return redirect(url_for('finance.finance_home'))


# ===== EXPORT ROUTES =====

@finance_bp.route('/export/expenses/<format>')
@role_required([Roles.SUPER_HQ, Roles.HQ_FINANCE, Roles.FINANCE_MANAGER, Roles.ACCOUNTS_PAYABLE, Roles.ADMIN])
def export_expenses(format):
    """Export expenses to Excel or CSV."""
    try:
        expenses = Expense.query.all()
        
        data = []
        for expense in expenses:
            data.append({
                'Date': expense.date.strftime('%Y-%m-%d'),
                'Category': expense.category,
                'Description': expense.description,
                'Amount': expense.amount,
                'Status': expense.status
            })
        
        df = pd.DataFrame(data)
        
        if format.lower() == 'csv':
            output = BytesIO()
            df.to_csv(output, index=False)
            output.seek(0)
            
            filename = f"expenses_{datetime.now().strftime('%Y%m%d_%H%M%S')}.csv"
            return send_file(output, as_attachment=True, download_name=filename, mimetype='text/csv')
        else:
            return jsonify({'error': 'Invalid format'}), 400
            
    except Exception as e:
        current_app.logger.error(f"Error exporting expenses: {str(e)}")
        return jsonify({'error': str(e)}), 500


@finance_bp.route('/logout')
@role_required([Roles.SUPER_HQ, Roles.HQ_FINANCE, Roles.FINANCE_MANAGER, Roles.ACCOUNTS_PAYABLE, Roles.ADMIN])
def logout():
    """Logout user."""
    try:
        session.clear()
        flash("Successfully logged out", "success")
        return redirect(url_for('auth.login'))
    except Exception as e:
        current_app.logger.error(f"Logout error: {str(e)}")
        return redirect(url_for('finance.finance_home'))


# ===== NEW FINANCE PAGES - TRANSACTION MANAGEMENT =====

@finance_bp.route('/account-transactions')
@login_required
@role_required([Roles.SUPER_HQ, Roles.HQ_FINANCE, Roles.FINANCE_MANAGER, Roles.ACCOUNTS_PAYABLE, Roles.ADMIN])
def account_transactions():
    """Display all account transactions from all modules with filters."""
    try:
        page = request.args.get('page', 1, type=int)
        account_filter = request.args.get('account_id')
        transaction_type = request.args.get('type', 'all')  # all, payment, reconciliation, ledger

        # Bank accounts for filter dropdown
        all_accounts = BankAccount.query.filter_by(is_active=True).all()

        # Source queries
        payment_query = PaymentRecord.query.order_by(PaymentRecord.payment_date.desc())
        reconciliation_query = BankReconciliation.query.order_by(BankReconciliation.created_at.desc())
        ledger_query = LedgerEntry.query.order_by(LedgerEntry.entry_date.desc())

        # Summary totals
        total_payments = db.session.query(func.sum(PaymentRecord.amount_paid)).scalar() or 0
        total_reconciliation_balance = db.session.query(func.sum(BankReconciliation.balance)).scalar() or 0

        # Merge all transaction-like rows
        all_transactions = []

        for payment in payment_query.all():
            all_transactions.append({
                'type': 'Payment',
                'date': payment.payment_date,
                'reference': payment.reference_number or f"PAY-{payment.id}",
                'description': f"{payment.payment_method} - Payment",
                'amount': float(payment.amount_paid or 0),
                'method': payment.payment_method,
                'status': 'Completed',
                'account_id': None,
                'id': payment.id,
                'detail_url': url_for('finance.view_payment_record', record_id=payment.id)
            })

        for reconciliation in reconciliation_query.all():
            all_transactions.append({
                'type': 'Reconciliation',
                'date': reconciliation.created_at,
                'reference': f"REC-{reconciliation.id}",
                'description': f"Reconciliation - {reconciliation.status}",
                'amount': float(reconciliation.balance or 0),
                'method': 'Bank',
                'status': reconciliation.status,
                'account_id': reconciliation.bank_account_id,
                'id': reconciliation.id,
                'detail_url': url_for('finance.view_account_transactions', account_id=reconciliation.bank_account_id)
            })

        for entry in ledger_query.all():
            all_transactions.append({
                'type': 'Ledger',
                'date': entry.entry_date,
                'reference': entry.reference or f"GL-{entry.id}",
                'description': entry.description,
                'amount': float(entry.debit or 0) if float(entry.debit or 0) > 0 else float(entry.credit or 0),
                'method': 'Ledger',
                'status': 'Posted',
                'account_id': entry.account_id,
                'id': entry.id,
                'detail_url': url_for('finance.ledger', account_id=entry.account_id)
            })

        all_transactions.sort(key=lambda x: x['date'], reverse=True)

        if transaction_type == 'payment':
            all_transactions = [t for t in all_transactions if t['type'] == 'Payment']
        elif transaction_type == 'reconciliation':
            all_transactions = [t for t in all_transactions if t['type'] == 'Reconciliation']
        elif transaction_type == 'ledger':
            all_transactions = [t for t in all_transactions if t['type'] == 'Ledger']

        if account_filter:
            all_transactions = [t for t in all_transactions if t['account_id'] == int(account_filter)]

        per_page = 20
        total_transactions = len(all_transactions)
        total_pages = (total_transactions + per_page - 1) // per_page
        
        start_idx = (page - 1) * per_page
        end_idx = start_idx + per_page
        paginated_transactions = all_transactions[start_idx:end_idx]
        
        return render_template(
            'finance/account_transactions.html',
            transactions=paginated_transactions,
            all_accounts=all_accounts,
            account_filter=account_filter,
            transaction_type=transaction_type,
            current_page=page,
            total_pages=total_pages,
            total_transactions=total_transactions,
            total_payments=float(total_payments),
            total_balance=float(total_reconciliation_balance)
        )
    except Exception as e:
        current_app.logger.error(f"Account transactions error: {str(e)}")
        flash(f"Error loading transactions: {str(e)}", "error")
        return redirect(url_for('finance.dashboard'))


@finance_bp.route('/transactions')
@login_required
@role_required([Roles.SUPER_HQ, Roles.HQ_FINANCE, Roles.FINANCE_MANAGER, Roles.ACCOUNTS_PAYABLE, Roles.ADMIN])
def transactions():
    """Display general transaction ledger."""
    try:
        page = request.args.get('page', 1, type=int)
        start_date = request.args.get('start_date')
        end_date = request.args.get('end_date')
        
        query = PaymentRecord.query.order_by(PaymentRecord.payment_date.desc())
        
        if start_date:
            start = datetime.strptime(start_date, '%Y-%m-%d')
            query = query.filter(PaymentRecord.payment_date >= start)
        
        if end_date:
            end = datetime.strptime(end_date, '%Y-%m-%d')
            query = query.filter(PaymentRecord.payment_date <= end)
        
        transactions = query.paginate(page=page, per_page=25)
        total_amount = db.session.query(func.sum(PaymentRecord.amount_paid)).scalar() or 0
        
        return render_template(
            'finance/transactions.html',
            transactions=transactions,
            total_amount=total_amount,
            start_date=start_date,
            end_date=end_date
        )
    except Exception as e:
        current_app.logger.error(f"Transactions error: {str(e)}")
        flash(f"Error loading transactions: {str(e)}", "error")
        return redirect(url_for('finance.dashboard'))


@finance_bp.route('/chart-of-accounts', methods=['GET', 'POST'])
@login_required
@role_required([Roles.SUPER_HQ, Roles.HQ_FINANCE, Roles.FINANCE_MANAGER, Roles.ACCOUNTS_PAYABLE, Roles.ADMIN])
def chart_of_accounts():
    """View/create chart of accounts with all 5 account types."""
    try:
        ensure_default_chart_of_accounts()
        if request.method == 'POST':
            account_code = request.form.get('account_code', '').strip()
            account_name = request.form.get('account_name', '').strip()
            account_type = request.form.get('account_type', '').strip().lower()
            opening_balance = _as_float(request.form.get('opening_balance'))
            if not account_code or not account_name or account_type not in ['asset', 'liability', 'revenue', 'expense', 'equity']:
                flash('Valid account code, name and type are required.', 'error')
                return redirect(url_for('finance.chart_of_accounts'))
            if ChartOfAccount.query.filter_by(account_code=account_code).first():
                flash('Account code already exists.', 'error')
                return redirect(url_for('finance.chart_of_accounts'))

            account = ChartOfAccount(
                account_code=account_code,
                account_name=account_name,
                account_type=account_type,
                description=request.form.get('description', '').strip()
            )
            db.session.add(account)
            db.session.flush()

            if opening_balance > 0:
                equity = _find_or_create_equity_account()
                if account_type in ['asset', 'expense']:
                    _post_double_entry(
                        debit_account_id=account.id,
                        credit_account_id=equity.id,
                        amount=opening_balance,
                        description=f"Opening balance - {account_name}",
                        reference=f"OPEN-{account.id}",
                        entity_type="chart_of_account",
                        entity_id=account.id,
                        category="opening_balance"
                    )
                else:
                    _post_double_entry(
                        debit_account_id=equity.id,
                        credit_account_id=account.id,
                        amount=opening_balance,
                        description=f"Opening balance - {account_name}",
                        reference=f"OPEN-{account.id}",
                        entity_type="chart_of_account",
                        entity_id=account.id,
                        category="opening_balance"
                    )

            # Optional one-click bank account creation for asset accounts.
            create_bank = request.form.get('create_bank_account') == 'on'
            if create_bank and account_type == 'asset':
                bank_account_number = request.form.get('bank_account_number', '').strip()
                bank_name = request.form.get('bank_name', '').strip() or 'Bank'
                if bank_account_number:
                    bank_account = BankAccount.query.filter_by(account_number=bank_account_number).first()
                    if not bank_account:
                        bank_account = BankAccount(
                            account_name=account_name,
                            account_number=bank_account_number,
                            bank_name=bank_name,
                            balance=opening_balance,
                            currency=request.form.get('currency', 'NGN') or 'NGN',
                            is_active=True
                        )
                        db.session.add(bank_account)
                        db.session.flush()
                    account.description = _compose_note(
                        account.description or "",
                        meta={
                            "bank_account_id": bank_account.id,
                            "account_number": bank_account.account_number
                        }
                    )

            db.session.commit()
            flash('Account created successfully.', 'success')
            return redirect(url_for('finance.chart_of_accounts'))

        accounts = ChartOfAccount.query.order_by(ChartOfAccount.account_code.asc()).all()
        balances = {}
        for acc in accounts:
            debit = db.session.query(func.sum(LedgerEntry.debit)).filter_by(account_id=acc.id).scalar() or 0
            credit = db.session.query(func.sum(LedgerEntry.credit)).filter_by(account_id=acc.id).scalar() or 0
            balances[acc.id] = float(debit) - float(credit)

        account_transaction_counts = {
            account.id: db.session.query(func.count(LedgerEntry.id)).filter_by(account_id=account.id).scalar() or 0
            for account in accounts
        }
        total_cash_in = db.session.query(func.sum(LedgerEntry.credit)).scalar() or 0
        total_cash_out = db.session.query(func.sum(LedgerEntry.debit)).scalar() or 0
        return render_template(
            'finance/chart_of_accounts.html',
            accounts=accounts,
            balances=balances,
            account_transaction_counts=account_transaction_counts,
            total_cash_in=float(total_cash_in),
            total_cash_out=float(total_cash_out)
        )
    except Exception as e:
        db.session.rollback()
        current_app.logger.error(f"Chart of accounts error: {str(e)}")
        flash(f"Error loading chart of accounts: {str(e)}", "error")
        return redirect(url_for('finance.dashboard'))


@finance_bp.route('/chart-of-accounts/<int:account_id>/delete', methods=['POST'])
@login_required
@role_required([Roles.SUPER_HQ, Roles.HQ_FINANCE, Roles.FINANCE_MANAGER, Roles.ADMIN])
def delete_chart_account(account_id):
    """Delete chart account if it has no posted entries."""
    try:
        account = ChartOfAccount.query.get_or_404(account_id)
        ledger_count = db.session.query(func.count(LedgerEntry.id)).filter_by(account_id=account.id).scalar() or 0
        if ledger_count > 0:
            flash('Cannot delete account with posted ledger entries.', 'error')
            return redirect(url_for('finance.chart_of_accounts'))

        db.session.delete(account)
        db.session.commit()
        flash('Account deleted successfully.', 'success')
        return redirect(url_for('finance.chart_of_accounts'))
    except Exception as e:
        db.session.rollback()
        current_app.logger.error(f"Delete chart account error: {str(e)}")
        flash(f"Error deleting account: {str(e)}", "error")
        return redirect(url_for('finance.chart_of_accounts'))


@finance_bp.route('/ledger', methods=['GET', 'POST'])
@login_required
@role_required([Roles.SUPER_HQ, Roles.HQ_FINANCE, Roles.FINANCE_MANAGER, Roles.ACCOUNTS_PAYABLE, Roles.ADMIN])
def ledger():
    """General ledger entries (expenses and all account types)."""
    try:
        ensure_default_chart_of_accounts()
        if request.method == 'POST':
            debit_account_id = request.form.get('debit_account_id', type=int)
            credit_account_id = request.form.get('credit_account_id', type=int)
            account_id = request.form.get('account_id', type=int)
            description = request.form.get('description', '').strip()
            reference = request.form.get('reference', '').strip()
            category = request.form.get('category', '').strip()
            amount = request.form.get('amount', type=float) or 0
            debit = request.form.get('debit', type=float) or 0
            credit = request.form.get('credit', type=float) or 0

            if debit_account_id and credit_account_id and amount > 0:
                _post_double_entry(
                    debit_account_id=debit_account_id,
                    credit_account_id=credit_account_id,
                    amount=amount,
                    description=description,
                    reference=reference,
                    entity_type='manual_ledger',
                    entity_id=None,
                    category=category or 'manual'
                )
            elif account_id and description and (debit > 0 or credit > 0):
                db.session.add(LedgerEntry(
                    account_id=account_id,
                    description=_compose_note(description, meta={"category": category} if category else None),
                    reference=reference,
                    debit=debit,
                    credit=credit,
                    entity_type='manual_ledger',
                    entity_id=None,
                    created_by=current_user.id
                ))
            else:
                flash('Provide debit account, credit account and amount (or use legacy single-line entry).', 'error')
                return redirect(url_for('finance.ledger'))
            db.session.commit()
            flash('Ledger entry posted successfully.', 'success')
            return redirect(url_for('finance.ledger'))

        account_filter = request.args.get('account_id', type=int)
        entry_query = LedgerEntry.query.order_by(LedgerEntry.entry_date.desc())
        if account_filter:
            entry_query = entry_query.filter(LedgerEntry.account_id == account_filter)
        entries = entry_query.limit(300).all()
        for entry in entries:
            entry.detail_url = None
            if entry.entity_type == 'expense' and entry.entity_id:
                entry.detail_url = url_for('finance.expense_details', expense_id=entry.entity_id)
            elif entry.entity_type == 'bank_reconciliation' and entry.entity_id:
                reconciliation = BankReconciliation.query.get(entry.entity_id)
                if reconciliation and reconciliation.bank_account_id:
                    entry.detail_url = url_for('finance.view_account_transactions', account_id=reconciliation.bank_account_id)
            elif entry.entity_type == 'payment_record' and entry.entity_id:
                entry.detail_url = url_for('finance.view_payment_record', record_id=entry.entity_id)
            elif entry.entity_type == 'payment_request' and entry.entity_id:
                entry.detail_url = url_for('finance.view_payment_request', payment_id=entry.entity_id)
        accounts = ChartOfAccount.query.order_by(ChartOfAccount.account_code.asc()).all()
        return render_template('finance/ledger.html', entries=entries, accounts=accounts, account_filter=account_filter)
    except Exception as e:
        db.session.rollback()
        current_app.logger.error(f"Ledger error: {str(e)}")
        flash(f"Error loading ledger: {str(e)}", "error")
        return redirect(url_for('finance.dashboard'))


@finance_bp.route('/revenue-sales', methods=['GET', 'POST'])
@login_required
@role_required([Roles.SUPER_HQ, Roles.HQ_FINANCE, Roles.FINANCE_MANAGER, Roles.ADMIN])
def revenue_sales():
    """Capture revenue/sales and feed bookkeeping."""
    try:
        from app.models import ProjectDocument
        ensure_default_chart_of_accounts()
        if request.method == 'POST':
            amount = request.form.get('amount', type=float) or 0
            customer_name = request.form.get('customer_name', '').strip()
            description = request.form.get('description', '').strip()
            project_id = request.form.get('project_id', type=int)
            invoice_number = request.form.get('invoice_number', '').strip()
            sale_date = request.form.get('sale_date', '').strip()
            bank_account_id = request.form.get('bank_account_id', type=int)
            selected_revenue_account_id = request.form.get('revenue_account_id', type=int)
            payment_timing = request.form.get('payment_timing', 'later').strip().lower()
            collection_status = request.form.get('collection_status', 'not_paid').strip().lower()
            item_service_delivered = request.form.get('item_service_delivered') == 'on'
            invoice_issued = request.form.get('invoice_issued') == 'on'
            tags = [t.strip() for t in request.form.get('tags', '').split(',') if t.strip()]
            if amount <= 0 or not customer_name:
                flash('Customer name and a positive amount are required.', 'error')
                return redirect(url_for('finance.revenue_sales'))
            if not item_service_delivered or not invoice_issued:
                flash('Revenue can only be recognized after delivery/service completion and invoice issuance.', 'error')
                return redirect(url_for('finance.revenue_sales'))
            if payment_timing not in {'immediate', 'later'}:
                payment_timing = 'later'
            if collection_status not in {'not_paid', 'paid_deposited', 'paid_not_deposited'}:
                collection_status = 'not_paid'
            if collection_status == 'paid_deposited' and not bank_account_id:
                flash('Select a bank account when payment is received and deposited.', 'error')
                return redirect(url_for('finance.revenue_sales'))

            meta = {}
            if invoice_number:
                meta["invoice_number"] = invoice_number
            if sale_date:
                meta["sale_date"] = sale_date
            meta["payment_timing"] = payment_timing
            meta["collection_status"] = collection_status
            meta["recognized"] = "true"

            revenue = RevenueSale(
                project_id=project_id,
                customer_name=customer_name,
                description=_compose_note(description, tags=tags, meta=meta),
                amount=amount,
                sale_date=datetime.strptime(sale_date, "%Y-%m-%d") if sale_date else datetime.utcnow(),
                status='received' if collection_status != 'not_paid' else 'pending',
                created_by=current_user.id
            )
            db.session.add(revenue)
            db.session.flush()

            revenue_account = ChartOfAccount.query.get(selected_revenue_account_id) if selected_revenue_account_id else ChartOfAccount.query.filter_by(account_type='revenue').order_by(ChartOfAccount.account_code.asc()).first()
            debit_account = None
            if collection_status == 'paid_deposited':
                bank_account = BankAccount.query.get(bank_account_id) if bank_account_id else None
                if bank_account:
                    bank_account.balance = _as_float(bank_account.balance) + amount
                    debit_account = _upsert_chart_bank_link(bank_account, opening_balance=0)
                if not debit_account:
                    debit_account = ChartOfAccount.query.filter_by(account_code='1000').first()
            elif collection_status == 'paid_not_deposited':
                debit_account = _find_or_create_undeposited_funds_account()
            else:
                debit_account = ChartOfAccount.query.filter_by(account_code='1100').first()

            if revenue_account and debit_account:
                _post_double_entry(
                    debit_account_id=debit_account.id,
                    credit_account_id=revenue_account.id,
                    amount=amount,
                    description=f"Revenue from {customer_name}",
                    reference=f"REV-{revenue.id}",
                    entity_type='revenue_sale',
                    entity_id=revenue.id,
                    category='revenue'
                )

            if 'invoice_file' in request.files:
                file = request.files['invoice_file']
                if file and file.filename and project_id:
                    file_ext = file.filename.rsplit('.', 1)[1].lower() if '.' in file.filename else ''
                    if file_ext in {'pdf', 'jpg', 'jpeg', 'png', 'doc', 'docx'}:
                        upload_dir = os.path.join(current_app.root_path, 'uploads', 'finance_documents')
                        os.makedirs(upload_dir, exist_ok=True)
                        filename = secure_filename(f"revenue_{revenue.id}_{datetime.utcnow().strftime('%Y%m%d_%H%M%S')}_{file.filename}")
                        filepath = os.path.join(upload_dir, filename)
                        file.save(filepath)
                        doc_meta = {"revenue_id": revenue.id}
                        if invoice_number:
                            doc_meta["invoice_number"] = invoice_number
                        db.session.add(ProjectDocument(
                            project_id=project_id,
                            title=f"Revenue Invoice REV-{revenue.id}",
                            description=_compose_note("Revenue invoice", tags=tags, meta=doc_meta),
                            file_name=filename,
                            file_path=f"/uploads/finance_documents/{filename}",
                            document_type=file_ext,
                            uploaded_by_id=current_user.id,
                            created_at=datetime.utcnow()
                        ))
            db.session.commit()
            if collection_status == 'paid_not_deposited':
                flash('Revenue recorded and payment captured as undeposited cheque.', 'success')
            elif collection_status == 'not_paid':
                flash('Revenue recorded as receivable (payment pending).', 'success')
            else:
                flash('Revenue/sale recorded successfully.', 'success')
            return redirect(url_for('finance.revenue_sales'))

        projects = Project.query.order_by(Project.name.asc()).all()
        records = RevenueSale.query.order_by(RevenueSale.sale_date.desc()).limit(200).all()
        for record in records:
            record.clean_description, record.tags, record.meta = _split_note_fields(record.description)
            record.invoice_docs = ProjectDocument.query.filter(
                ProjectDocument.title.ilike(f"%REV-{record.id}%")
            ).count() if record.project_id else 0
        total_revenue = db.session.query(func.sum(RevenueSale.amount)).scalar() or 0
        bank_accounts = BankAccount.query.filter_by(is_active=True).order_by(BankAccount.account_name.asc()).all()
        revenue_accounts = ChartOfAccount.query.filter_by(account_type='revenue').order_by(ChartOfAccount.account_name.asc()).all()
        return render_template(
            'finance/revenue_sales.html',
            projects=projects,
            records=records,
            total_revenue=float(total_revenue),
            bank_accounts=bank_accounts,
            revenue_accounts=revenue_accounts
        )
    except Exception as e:
        db.session.rollback()
        current_app.logger.error(f"Revenue/sales error: {str(e)}")
        flash(f"Error loading revenue/sales: {str(e)}", "error")
        return redirect(url_for('finance.dashboard'))


@finance_bp.route('/revenue-sales/<int:revenue_id>/deposit-cheque', methods=['POST'])
@login_required
@role_required([Roles.SUPER_HQ, Roles.HQ_FINANCE, Roles.FINANCE_MANAGER, Roles.ADMIN])
def deposit_revenue_cheque(revenue_id):
    """Move an undeposited cheque into a selected bank account with audit trail."""
    try:
        revenue = RevenueSale.query.get_or_404(revenue_id)
        bank_account_id = request.form.get('bank_account_id', type=int)
        deposit_reference = request.form.get('deposit_reference', '').strip()
        deposit_date = request.form.get('deposit_date', '').strip()

        if not bank_account_id:
            flash('Please select a bank account for cheque deposit.', 'error')
            return redirect(url_for('finance.revenue_sales'))

        clean_text, tags, meta = _split_note_fields(revenue.description)
        collection_status = (meta.get('collection_status') or 'not_paid').strip().lower()
        if collection_status != 'paid_not_deposited':
            flash('This revenue item is not marked as an undeposited cheque.', 'warning')
            return redirect(url_for('finance.revenue_sales'))

        bank_account = BankAccount.query.get_or_404(bank_account_id)
        amount = _as_float(revenue.amount)
        if amount <= 0:
            flash('Revenue amount is invalid for deposit.', 'error')
            return redirect(url_for('finance.revenue_sales'))

        undeposited_account = _find_or_create_undeposited_funds_account()
        bank_asset_account = _upsert_chart_bank_link(bank_account, opening_balance=0)

        # Increase bank balance now that cheque is deposited.
        bank_account.balance = _as_float(bank_account.balance) + amount

        # Transfer entry: Dr Bank, Cr Undeposited Funds
        _post_double_entry(
            debit_account_id=bank_asset_account.id,
            credit_account_id=undeposited_account.id,
            amount=amount,
            description=f"Cheque deposit for revenue {revenue.customer_name}",
            reference=deposit_reference or f"DEP-REV-{revenue.id}",
            entity_type='revenue_sale',
            entity_id=revenue.id,
            category='cheque_deposit'
        )

        meta['collection_status'] = 'paid_deposited'
        meta['deposited_bank_account_id'] = str(bank_account.id)
        if deposit_reference:
            meta['deposit_reference'] = deposit_reference
        if deposit_date:
            meta['deposit_date'] = deposit_date
        revenue.description = _compose_note(clean_text, tags=tags, meta=meta)
        revenue.status = 'received'

        db.session.add(ApprovalLog(
            entity_type='revenue_sale',
            entity_id=revenue.id,
            action='CHEQUE_DEPOSITED',
            actor_id=current_user.id,
            comment=(
                f"Cheque deposited to {bank_account.account_name}"
                f"{' | Ref: ' + deposit_reference if deposit_reference else ''}"
            ),
            timestamp=datetime.utcnow()
        ))

        db.session.commit()
        flash('Cheque deposited successfully and audit trail recorded.', 'success')
        return redirect(url_for('finance.revenue_sales'))
    except Exception as e:
        db.session.rollback()
        current_app.logger.error(f"Deposit cheque error: {str(e)}")
        flash(f"Error depositing cheque: {str(e)}", "error")
        return redirect(url_for('finance.revenue_sales'))


@finance_bp.route('/project-payment-requests')
@login_required
@role_required([Roles.SUPER_HQ, Roles.HQ_FINANCE, Roles.FINANCE_MANAGER, Roles.ADMIN])
def project_payment_requests():
    """Finance approval list for project-originated payment requests."""
    requests = ProjectPaymentRequest.query.order_by(ProjectPaymentRequest.request_date.desc()).all()
    return render_template('finance/project_payment_requests.html', requests=requests)


@finance_bp.route('/project-payment-requests/<int:request_id>/approve', methods=['POST'])
@login_required
@role_required([Roles.SUPER_HQ, Roles.HQ_FINANCE, Roles.FINANCE_MANAGER, Roles.ADMIN])
def approve_project_payment_request(request_id):
    req = ProjectPaymentRequest.query.get_or_404(request_id)
    req.approval_state = 'approved'
    req.approved_by = current_user.id
    req.approved_at = datetime.utcnow()
    db.session.commit()
    flash('Project payment request approved.', 'success')
    return redirect(url_for('finance.project_payment_requests'))


@finance_bp.route('/project-payment-requests/<int:request_id>/reject', methods=['POST'])
@login_required
@role_required([Roles.SUPER_HQ, Roles.HQ_FINANCE, Roles.FINANCE_MANAGER, Roles.ADMIN])
def reject_project_payment_request(request_id):
    req = ProjectPaymentRequest.query.get_or_404(request_id)
    req.approval_state = 'rejected'
    req.rejection_reason = request.form.get('reason', '').strip()
    req.approved_by = current_user.id
    req.approved_at = datetime.utcnow()
    db.session.commit()
    flash('Project payment request rejected.', 'warning')
    return redirect(url_for('finance.project_payment_requests'))


# ===== BANK MANAGEMENT =====

@finance_bp.route('/cashflow')
@login_required
@role_required([Roles.SUPER_HQ, Roles.HQ_FINANCE, Roles.FINANCE_MANAGER, Roles.ADMIN])
def cashflow():
    """Cash flow analysis and forecasting."""
    try:
        # Calculate current balance from all active bank accounts
        current_balance = 0.0
        try:
            balance = db.session.query(func.sum(BankAccount.balance)).filter(
                BankAccount.is_active == True
            ).scalar()
            if balance is not None:
                current_balance = float(balance)
        except:
            current_balance = 0.0
        
        # Calculate 30-day inflows (payments made from accounts)
        thirty_days_ago = datetime.utcnow() - timedelta(days=30)
        inflows = 0.0
        try:
            inflow_sum = db.session.query(func.sum(PaymentRecord.amount_paid)).filter(
                PaymentRecord.payment_date >= thirty_days_ago
            ).scalar()
            if inflow_sum is not None:
                inflows = float(inflow_sum)
        except:
            inflows = 0.0
        
        # Calculate 30-day outflows (approved expenses)
        outflows = 0.0
        try:
            outflow_sum = db.session.query(func.sum(Expense.amount)).filter(
                Expense.date >= thirty_days_ago,
                Expense.status == 'approved'
            ).scalar()
            if outflow_sum is not None:
                outflows = float(outflow_sum)
        except:
            outflows = 0.0
        
        # Calculate weekly trend data (last 5 weeks)
        weekly_data = []
        try:
            for week_num in range(4, -1, -1):
                week_start = datetime.utcnow() - timedelta(days=(4-week_num)*7+7)
                week_end = datetime.utcnow() - timedelta(days=(4-week_num)*7)
                
                week_inflow = 0.0
                try:
                    wi = db.session.query(func.sum(PaymentRecord.amount_paid)).filter(
                        PaymentRecord.payment_date >= week_start,
                        PaymentRecord.payment_date <= week_end
                    ).scalar()
                    if wi is not None:
                        week_inflow = float(wi)
                except:
                    week_inflow = 0.0
                
                week_outflow = 0.0
                try:
                    wo = db.session.query(func.sum(Expense.amount)).filter(
                        Expense.date >= week_start,
                        Expense.date <= week_end,
                        Expense.status == 'approved'
                    ).scalar()
                    if wo is not None:
                        week_outflow = float(wo)
                except:
                    week_outflow = 0.0
                
                weekly_data.append({
                    'week': week_start.strftime('%b %d'),
                    'inflow': week_inflow,
                    'outflow': week_outflow,
                    'net': week_inflow - week_outflow
                })
        except Exception as e:
            current_app.logger.error(f"Weekly data error: {str(e)}")
            weekly_data = []
        
        # Get latest 10 payment inflows (money received)
        latest_inflows = []
        try:
            latest_inflows = PaymentRecord.query.order_by(PaymentRecord.payment_date.desc()).limit(10).all()
        except:
            latest_inflows = []
        
        # Get latest 10 approved expenses (money spent)
        latest_outflows = []
        try:
            latest_outflows = Expense.query.filter(
                Expense.status == 'approved'
            ).order_by(Expense.date.desc()).limit(10).all()
        except:
            latest_outflows = []
        
        # Calculate forecast data
        forecasted_inflows = 0.0
        try:
            fi = db.session.query(func.sum(PaymentRequest.invoice_amount)).filter(
                PaymentRequest.approval_state.in_(['approved', 'pending'])
            ).scalar()
            if fi is not None:
                forecasted_inflows = float(fi)
        except:
            forecasted_inflows = 0.0
        
        forecasted_outflows = 0.0
        try:
            fo = db.session.query(func.sum(Expense.amount)).filter(
                Expense.status.in_(['pending', 'approved'])
            ).scalar()
            if fo is not None:
                forecasted_outflows = float(fo)
        except:
            forecasted_outflows = 0.0
        
        # Calculate projected cash position
        projected_balance = current_balance + inflows - outflows
        cash_gap = forecasted_outflows - forecasted_inflows
        
        return render_template(
            'finance/cashflow.html',
            current_balance=current_balance,
            inflows=inflows,
            outflows=outflows,
            weekly_data=weekly_data,
            latest_inflows=latest_inflows,
            latest_outflows=latest_outflows,
            forecasted_inflows=forecasted_inflows,
            forecasted_outflows=forecasted_outflows,
            forecast_net=forecasted_inflows - forecasted_outflows,
            projected_balance=projected_balance,
            cash_gap=cash_gap
        )
    except Exception as e:
        current_app.logger.error(f"Cashflow error: {str(e)}")
        import traceback
        current_app.logger.error(traceback.format_exc())
        flash(f"Error loading cashflow: {str(e)}", "error")
        return redirect(url_for('finance.dashboard'))


@finance_bp.route('/receivables')
@login_required
@role_required([Roles.SUPER_HQ, Roles.HQ_FINANCE, Roles.FINANCE_MANAGER, Roles.ADMIN])
def receivables():
    """Accounts receivable management."""
    try:
        page = request.args.get('page', 1, type=int)
        
        receivables = PaymentRequest.query.filter(
            PaymentRequest.approval_state != ApprovalState.APPROVED
        ).order_by(PaymentRequest.created_at.desc()).paginate(page=page, per_page=20)
        
        total_outstanding = db.session.query(func.sum(PaymentRequest.invoice_amount)).filter(
            PaymentRequest.approval_state != ApprovalState.APPROVED
        ).scalar() or 0.0
        if total_outstanding:
            total_outstanding = float(total_outstanding)

        supplier_history = {}
        for item in receivables.items:
            supplier_name = '-'
            if item.counterparty_name:
                supplier_name = item.counterparty_name
            elif item.purchase_order and item.purchase_order.vendor:
                supplier_name = item.purchase_order.vendor.name
            if supplier_name != '-':
                history_count = PaymentRequest.query.filter(
                    or_(
                        PaymentRequest.counterparty_name == supplier_name,
                        PaymentRequest.purchase_order.has(PurchaseOrder.vendor.has(Vendor.name == supplier_name))
                    )
                ).count()
            else:
                history_count = 0
            supplier_history[item.id] = {
                'supplier_name': supplier_name,
                'history_count': history_count
            }
        
        return render_template(
            'finance/receivables.html',
            receivables=receivables,
            total_outstanding=total_outstanding or 0.0,
            supplier_history=supplier_history
        )
    except Exception as e:
        current_app.logger.error(f"Receivables error: {str(e)}")
        flash(f"Error loading receivables: {str(e)}", "error")
        return redirect(url_for('finance.dashboard'))


@finance_bp.route('/receivables/create', methods=['GET', 'POST'])
@login_required
@role_required([Roles.SUPER_HQ, Roles.HQ_FINANCE, Roles.FINANCE_MANAGER, Roles.ADMIN])
def create_receivable():
    """Create a new receivable - GET shows form, POST processes submission."""
    if request.method == 'GET':
        # Display the create receivable form
        try:
            # Get available purchase orders that can be linked
            purchase_orders = PurchaseOrder.query.order_by(PurchaseOrder.created_at.desc()).limit(50).all()
            return render_template(
                'finance/create_receivable.html',
                purchase_orders=purchase_orders,
                form_data={}
            )
        except Exception as e:
            current_app.logger.error(f"Error loading create receivable form: {str(e)}")
            flash(f"Error loading form: {str(e)}", "error")
            return redirect(url_for('finance.receivables'))
    
    # Handle POST - Form submission
    try:
        customer_name = request.form.get('customer_name', '').strip()
        invoice_number = request.form.get('invoice_number', '').strip()
        po_id = request.form.get('po_id', type=int)
        amount = request.form.get('amount', type=float)
        description = request.form.get('description', '').strip()
        
        # Server-side validation
        if not customer_name:
            flash('Customer name is required', 'error')
            return render_template(
                'finance/create_receivable.html',
                purchase_orders=PurchaseOrder.query.order_by(PurchaseOrder.created_at.desc()).limit(50).all(),
                form_data=request.form
            )
        
        if not invoice_number:
            flash('Invoice number is required', 'error')
            return render_template(
                'finance/create_receivable.html',
                purchase_orders=PurchaseOrder.query.order_by(PurchaseOrder.created_at.desc()).limit(50).all(),
                form_data=request.form
            )
        
        if not amount or amount <= 0:
            flash('Amount must be greater than 0', 'error')
            return render_template(
                'finance/create_receivable.html',
                purchase_orders=PurchaseOrder.query.order_by(PurchaseOrder.created_at.desc()).limit(50).all(),
                form_data=request.form
            )
        
        # Check if invoice number already exists
        existing = PaymentRequest.query.filter_by(invoice_number=invoice_number).first()
        if existing:
            flash(f'Invoice number "{invoice_number}" already exists in the system', 'error')
            return render_template(
                'finance/create_receivable.html',
                purchase_orders=PurchaseOrder.query.order_by(PurchaseOrder.created_at.desc()).limit(50).all(),
                form_data=request.form
            )
        
        # Get the purchase order if provided
        po = None
        if po_id:
            po = PurchaseOrder.query.get(po_id)
            if not po:
                flash('Selected purchase order not found', 'error')
                return render_template(
                    'finance/create_receivable.html',
                    purchase_orders=PurchaseOrder.query.order_by(PurchaseOrder.created_at.desc()).limit(50).all(),
                    form_data=request.form
                )
        
        # Create new payment request (receivable)
        new_receivable = PaymentRequest(
            po_id=po.id if po else None,
            counterparty_name=customer_name,
            notes=description or None,
            invoice_number=invoice_number,
            invoice_amount=amount,
            approval_state=ApprovalState.PENDING,
            verified_by=current_user.id,
            created_at=datetime.utcnow()
        )
        
        db.session.add(new_receivable)
        db.session.flush()  # Generate ID before audit log
        
        # Log the action
        audit_log = ApprovalLog(
            entity_type='PaymentRequest',
            entity_id=new_receivable.id,
            action='CREATED',
            actor_id=current_user.id,
            comment=f'Receivable created for {customer_name}. Invoice: {invoice_number}. Amount: ₦{amount:,.2f}',
            timestamp=datetime.utcnow()
        )
        db.session.add(audit_log)
        db.session.commit()
        
        flash(f'✓ Receivable created successfully! Invoice {invoice_number} for ₦{amount:,.2f} added.', 'success')
        return redirect(url_for('finance.receivables'))
        
    except Exception as e:
        db.session.rollback()
        current_app.logger.error(f"Error creating receivable: {str(e)}")
        import traceback
        current_app.logger.error(traceback.format_exc())
        if 'NOT NULL constraint failed: payment_request.po_id' in str(e):
            flash('Database schema still requires PO for receivables. Run migrate_payment_request_po_nullable.py, then try again.', 'error')
        else:
            flash(f"Error creating receivable: {str(e)}", "error")
        return render_template(
            'finance/create_receivable.html',
            purchase_orders=PurchaseOrder.query.order_by(PurchaseOrder.created_at.desc()).limit(50).all(),
            form_data=request.form
        )

# ===== PAYROLL MANAGEMENT =====

@finance_bp.route('/payroll-approval')
@login_required
@role_required([Roles.SUPER_HQ, Roles.HQ_FINANCE, Roles.FINANCE_MANAGER, Roles.ADMIN])
def payroll_approval():
    """Approve pending payroll submissions."""
    try:
        page = request.args.get('page', 1, type=int)
        
        # Get all pending payroll records for approval
        payroll_records = Payroll.query.filter(
            Payroll.approval_state == ApprovalState.PENDING
        ).order_by(Payroll.created_at.desc()).paginate(page=page, per_page=20)
        
        # Calculate total pending payroll amount
        total_pending = db.session.query(func.sum(Payroll.total_net_salary)).filter(
            Payroll.approval_state == ApprovalState.PENDING
        ).scalar() or 0.0
        
        if total_pending:
            total_pending = float(total_pending)
        
        return render_template(
            'finance/payroll_approval.html',
            payroll_records=payroll_records,
            total_pending=total_pending
        )
    except Exception as e:
        current_app.logger.error(f"Payroll approval error: {str(e)}")
        flash(f"Error loading payroll: {str(e)}", "error")
        return redirect(url_for('finance.dashboard'))


@finance_bp.route('/payroll-approval/<int:payroll_id>/approve', methods=['POST'])
@login_required
@role_required([Roles.SUPER_HQ, Roles.HQ_FINANCE, Roles.FINANCE_MANAGER, Roles.ADMIN])
def approve_payroll(payroll_id):
    """Approve a payroll record."""
    try:
        payroll = Payroll.query.get(payroll_id)
        if not payroll:
            flash('Payroll record not found', 'error')
            return redirect(url_for('finance.payroll_approval'))
        
        if payroll.approval_state != ApprovalState.PENDING:
            flash(f'Payroll {payroll.payroll_number} is not pending approval', 'error')
            return redirect(url_for('finance.payroll_approval'))
        
        # Update payroll status
        payroll.approval_state = ApprovalState.APPROVED
        payroll.approved_by = current_user.id
        payroll.approved_at = datetime.utcnow()
        
        db.session.add(payroll)
        db.session.flush()
        
        # Log the approval
        audit_log = ApprovalLog(
            entity_type='Payroll',
            entity_id=payroll.id,
            action='APPROVED',
            actor_id=current_user.id,
            comment=f'Payroll {payroll.payroll_number} approved. Net Salary: ₦{float(payroll.total_net_salary):,.2f}',
            timestamp=datetime.utcnow()
        )
        db.session.add(audit_log)
        db.session.commit()
        
        flash(f'✓ Payroll {payroll.payroll_number} approved successfully!', 'success')
        return redirect(url_for('finance.payroll_approval'))
        
    except Exception as e:
        db.session.rollback()
        current_app.logger.error(f"Error approving payroll: {str(e)}")
        flash(f"Error approving payroll: {str(e)}", "error")
        return redirect(url_for('finance.payroll_approval'))


@finance_bp.route('/payroll-approval/<int:payroll_id>/reject', methods=['POST'])
@login_required
@role_required([Roles.SUPER_HQ, Roles.HQ_FINANCE, Roles.FINANCE_MANAGER, Roles.ADMIN])
def reject_payroll(payroll_id):
    """Reject a payroll record."""
    try:
        payroll = Payroll.query.get(payroll_id)
        if not payroll:
            flash('Payroll record not found', 'error')
            return redirect(url_for('finance.payroll_approval'))
        
        if payroll.approval_state != ApprovalState.PENDING:
            flash(f'Payroll {payroll.payroll_number} is not pending approval', 'error')
            return redirect(url_for('finance.payroll_approval'))
        
        rejection_reason = request.form.get('rejection_reason', '').strip()
        if not rejection_reason:
            flash('Rejection reason is required', 'error')
            return redirect(url_for('finance.payroll_approval'))
        
        # Update payroll status
        payroll.approval_state = ApprovalState.REJECTED
        payroll.rejection_reason = rejection_reason
        
        db.session.add(payroll)
        db.session.flush()
        
        # Log the rejection
        audit_log = ApprovalLog(
            entity_type='Payroll',
            entity_id=payroll.id,
            action='REJECTED',
            actor_id=current_user.id,
            comment=f'Payroll {payroll.payroll_number} rejected. Reason: {rejection_reason}',
            timestamp=datetime.utcnow()
        )
        db.session.add(audit_log)
        db.session.commit()
        
        flash(f'✗ Payroll {payroll.payroll_number} rejected.', 'warning')
        return redirect(url_for('finance.payroll_approval'))
        
    except Exception as e:
        db.session.rollback()
        current_app.logger.error(f"Error rejecting payroll: {str(e)}")
        flash(f"Error rejecting payroll: {str(e)}", "error")
        return redirect(url_for('finance.payroll_approval'))


@finance_bp.route('/payroll-pending')
@login_required
@role_required([Roles.SUPER_HQ, Roles.HQ_FINANCE, Roles.FINANCE_MANAGER, Roles.ADMIN])
def payroll_pending():
    """View all pending payroll records."""
    try:
        page = request.args.get('page', 1, type=int)
        
        # Get all payroll records with different states
        payroll_records = Payroll.query.filter(
            Payroll.approval_state == ApprovalState.PENDING
        ).order_by(Payroll.created_at.desc()).paginate(page=page, per_page=20)
        
        # Calculate totals
        total_net_salary = db.session.query(func.sum(Payroll.total_net_salary)).filter(
            Payroll.approval_state == ApprovalState.PENDING
        ).scalar() or 0.0
        
        total_deductions = db.session.query(func.sum(Payroll.total_deductions)).filter(
            Payroll.approval_state == ApprovalState.PENDING
        ).scalar() or 0.0
        
        total_basic_salary = db.session.query(func.sum(Payroll.total_basic_salary)).filter(
            Payroll.approval_state == ApprovalState.PENDING
        ).scalar() or 0.0
        
        return render_template(
            'finance/payroll_pending.html',
            payroll_records=payroll_records,
            total_net_salary=float(total_net_salary) if total_net_salary else 0.0,
            total_deductions=float(total_deductions) if total_deductions else 0.0,
            total_basic_salary=float(total_basic_salary) if total_basic_salary else 0.0
        )
    except Exception as e:
        current_app.logger.error(f"Payroll pending error: {str(e)}")
        flash(f"Error loading pending payroll: {str(e)}", "error")
        return redirect(url_for('finance.dashboard'))


# ===== APPROVALS =====

@finance_bp.route('/cost-control-approvals')
@login_required
@role_required([Roles.SUPER_HQ, Roles.HQ_FINANCE, Roles.FINANCE_MANAGER, Roles.ADMIN])
def cost_control_approvals():
    """Approve pending expense/cost control requests."""
    try:
        page = request.args.get('page', 1, type=int)
        
        # Get all pending expenses for approval
        expenses = Expense.query.filter(
            Expense.status == 'pending'
        ).order_by(Expense.date.desc()).paginate(page=page, per_page=20)
        
        # Calculate totals
        total_pending = db.session.query(func.sum(Expense.amount)).filter(
            Expense.status == 'pending'
        ).scalar() or 0.0
        
        total_approved = db.session.query(func.sum(Expense.amount)).filter(
            Expense.status == 'approved'
        ).scalar() or 0.0
        
        # Count records
        total_pending_count = db.session.query(func.count(Expense.id)).filter(
            Expense.status == 'pending'
        ).scalar() or 0
        
        total_approved_count = db.session.query(func.count(Expense.id)).filter(
            Expense.status == 'approved'
        ).scalar() or 0
        
        return render_template(
            'finance/cost_control_approvals.html',
            expenses=expenses,
            total_pending=float(total_pending) if total_pending else 0.0,
            total_approved=float(total_approved) if total_approved else 0.0,
            total_pending_count=total_pending_count,
            total_approved_count=total_approved_count
        )
    except Exception as e:
        current_app.logger.error(f"Cost control approvals error: {str(e)}")
        flash(f"Error loading approvals: {str(e)}", "error")
        return redirect(url_for('finance.dashboard'))


@finance_bp.route('/cost-control-approvals/<int:expense_id>/approve', methods=['POST'])
@login_required
@role_required([Roles.SUPER_HQ, Roles.HQ_FINANCE, Roles.FINANCE_MANAGER, Roles.ADMIN])
def approve_expense(expense_id):
    """Approve an expense."""
    try:
        expense = Expense.query.get(expense_id)
        if not expense:
            flash('Expense not found', 'error')
            return redirect(url_for('finance.cost_control_approvals'))
        
        if expense.status != 'pending':
            flash(f'Expense is not pending approval', 'error')
            return redirect(url_for('finance.cost_control_approvals'))

        expense.status = 'approved'
        db.session.add(expense)

        existing_post = LedgerEntry.query.filter_by(entity_type='expense', entity_id=expense.id).first()
        if not existing_post:
            clean_description, _, meta = _split_note_fields(expense.description)
            paye_amount = _as_float(meta.get('paye_amount'))
            expense_account = ChartOfAccount.query.filter(
                ChartOfAccount.account_type == 'expense',
                func.lower(ChartOfAccount.account_name) == (expense.category or '').lower()
            ).first() or ChartOfAccount.query.filter_by(account_type='expense').first()
            cash_account = ChartOfAccount.query.filter_by(account_code='1000').first()
            if expense_account and cash_account:
                if paye_amount > 0:
                    paye_account = ChartOfAccount.query.filter_by(account_code='2100').first()
                    if not paye_account:
                        paye_account = ChartOfAccount(
                            account_code='2100',
                            account_name='PAYE Payable',
                            account_type='liability'
                        )
                        db.session.add(paye_account)
                        db.session.flush()
                    db.session.add(LedgerEntry(
                        account_id=expense_account.id,
                        description=clean_description,
                        reference=f"EXP-{expense.id}",
                        debit=expense.amount,
                        credit=0,
                        entity_type='expense',
                        entity_id=expense.id,
                        created_by=current_user.id
                    ))
                    net_cash = max(_as_float(expense.amount) - paye_amount, 0)
                    db.session.add(LedgerEntry(
                        account_id=cash_account.id,
                        description=clean_description,
                        reference=f"EXP-{expense.id}",
                        debit=0,
                        credit=net_cash,
                        entity_type='expense',
                        entity_id=expense.id,
                        created_by=current_user.id
                    ))
                    if paye_amount > 0:
                        db.session.add(LedgerEntry(
                            account_id=paye_account.id,
                            description="PAYE withholding",
                            reference=f"EXP-{expense.id}",
                            debit=0,
                            credit=paye_amount,
                            entity_type='expense',
                            entity_id=expense.id,
                            created_by=current_user.id
                        ))
                else:
                    _post_double_entry(
                        debit_account_id=expense_account.id,
                        credit_account_id=cash_account.id,
                        amount=expense.amount,
                        description=clean_description or f"Expense {expense.id}",
                        reference=f"EXP-{expense.id}",
                        entity_type='expense',
                        entity_id=expense.id,
                        category=expense.category
                    )

        db.session.commit()

        flash(f'✓ Expense ₦{expense.amount:,.2f} approved successfully!', 'success')
        return redirect(url_for('finance.cost_control_approvals'))
        
    except Exception as e:
        db.session.rollback()
        current_app.logger.error(f"Error approving expense: {str(e)}")
        flash(f"Error approving expense: {str(e)}", "error")
        return redirect(url_for('finance.cost_control_approvals'))


@finance_bp.route('/cost-control-approvals/<int:expense_id>/reject', methods=['POST'])
@login_required
@role_required([Roles.SUPER_HQ, Roles.HQ_FINANCE, Roles.FINANCE_MANAGER, Roles.ADMIN])
def reject_expense(expense_id):
    """Reject an expense."""
    try:
        expense = Expense.query.get(expense_id)
        if not expense:
            flash('Expense not found', 'error')
            return redirect(url_for('finance.cost_control_approvals'))
        
        if expense.status != 'pending':
            flash(f'Expense is not pending approval', 'error')
            return redirect(url_for('finance.cost_control_approvals'))
        
        expense.status = 'rejected'
        db.session.add(expense)
        db.session.commit()
        
        flash(f'✗ Expense ₦{expense.amount:,.2f} rejected.', 'warning')
        return redirect(url_for('finance.cost_control_approvals'))
        
    except Exception as e:
        db.session.rollback()
        current_app.logger.error(f"Error rejecting expense: {str(e)}")
        flash(f"Error rejecting expense: {str(e)}", "error")
        return redirect(url_for('finance.cost_control_approvals'))


@finance_bp.route('/purchase-order-approval')
@login_required
@role_required([Roles.SUPER_HQ, Roles.HQ_FINANCE, Roles.FINANCE_MANAGER, Roles.ADMIN])
def purchase_order_approval():
    """Approve pending purchase orders."""
    try:
        page = request.args.get('page', 1, type=int)
        
        # Get cost-control reviewed purchase orders awaiting finance approval
        purchase_orders = PurchaseOrder.query.filter(
            PurchaseOrder.approval_state == ApprovalState.REVIEW
        ).order_by(PurchaseOrder.created_at.desc()).paginate(page=page, per_page=20)
        
        # Calculate totals
        total_pending = db.session.query(func.sum(PurchaseOrder.total_amount)).filter(
            PurchaseOrder.approval_state == ApprovalState.REVIEW
        ).scalar() or 0.0
        
        total_approved = db.session.query(func.sum(PurchaseOrder.total_amount)).filter(
            PurchaseOrder.approval_state == ApprovalState.APPROVED
        ).scalar() or 0.0
        
        # Count records
        total_pending_count = db.session.query(func.count(PurchaseOrder.id)).filter(
            PurchaseOrder.approval_state == ApprovalState.REVIEW
        ).scalar() or 0
        
        total_approved_count = db.session.query(func.count(PurchaseOrder.id)).filter(
            PurchaseOrder.approval_state == ApprovalState.APPROVED
        ).scalar() or 0
        
        return render_template(
            'finance/purchase_order_approval.html',
            purchase_orders=purchase_orders,
            total_pending=float(total_pending) if total_pending else 0.0,
            total_approved=float(total_approved) if total_approved else 0.0,
            total_pending_count=total_pending_count,
            total_approved_count=total_approved_count
        )
    except Exception as e:
        current_app.logger.error(f"Purchase order approval error: {str(e)}")
        flash(f"Error loading PO approvals: {str(e)}", "error")
        return redirect(url_for('finance.dashboard'))


@finance_bp.route('/purchase-order-approval/<int:po_id>/approve', methods=['POST'])
@login_required
@role_required([Roles.SUPER_HQ, Roles.HQ_FINANCE, Roles.FINANCE_MANAGER, Roles.ADMIN])
def approve_purchase_order(po_id):
    """Approve a purchase order."""
    try:
        po = PurchaseOrder.query.get(po_id)
        if not po:
            flash('Purchase order not found', 'error')
            return redirect(url_for('finance.purchase_order_approval'))
        
        if po.approval_state != ApprovalState.REVIEW:
            flash(f'Purchase order {po.po_number} is not awaiting finance approval', 'error')
            return redirect(url_for('finance.purchase_order_approval'))
        
        # Update PO status
        po.approval_state = ApprovalState.APPROVED
        po.issued_at = datetime.utcnow()
        
        db.session.add(po)
        db.session.flush()
        
        # Log the approval
        audit_log = ApprovalLog(
            entity_type='PurchaseOrder',
            entity_id=po.id,
            action='APPROVED',
            actor_id=current_user.id,
            comment=f'Purchase Order {po.po_number} approved by Finance. Amount: ₦{float(po.total_amount):,.2f}',
            timestamp=datetime.utcnow()
        )
        db.session.add(audit_log)
        db.session.commit()
        
        flash(f'✓ Purchase Order {po.po_number} approved successfully!', 'success')
        return redirect(url_for('finance.purchase_order_approval'))
        
    except Exception as e:
        db.session.rollback()
        current_app.logger.error(f"Error approving PO: {str(e)}")
        flash(f"Error approving PO: {str(e)}", "error")
        return redirect(url_for('finance.purchase_order_approval'))


@finance_bp.route('/purchase-order-approval/<int:po_id>/reject', methods=['POST'])
@login_required
@role_required([Roles.SUPER_HQ, Roles.HQ_FINANCE, Roles.FINANCE_MANAGER, Roles.ADMIN])
def reject_purchase_order(po_id):
    """Reject a purchase order."""
    try:
        po = PurchaseOrder.query.get(po_id)
        if not po:
            flash('Purchase order not found', 'error')
            return redirect(url_for('finance.purchase_order_approval'))
        
        if po.approval_state != ApprovalState.REVIEW:
            flash(f'Purchase order {po.po_number} is not awaiting finance approval', 'error')
            return redirect(url_for('finance.purchase_order_approval'))
        
        # Update PO status
        po.approval_state = ApprovalState.REJECTED
        
        db.session.add(po)
        db.session.flush()
        
        # Log the rejection
        audit_log = ApprovalLog(
            entity_type='PurchaseOrder',
            entity_id=po.id,
            action='REJECTED',
            actor_id=current_user.id,
            comment=f'Purchase Order {po.po_number} rejected.',
            timestamp=datetime.utcnow()
        )
        db.session.add(audit_log)
        db.session.commit()
        
        flash(f'✗ Purchase Order {po.po_number} rejected.', 'warning')
        return redirect(url_for('finance.purchase_order_approval'))
        
    except Exception as e:
        db.session.rollback()
        current_app.logger.error(f"Error rejecting PO: {str(e)}")
        flash(f"Error rejecting PO: {str(e)}", "error")
        return redirect(url_for('finance.purchase_order_approval'))


# ===== BUDGETS & ANALYSIS =====

@finance_bp.route('/budgets')
@login_required
@role_required([Roles.SUPER_HQ, Roles.HQ_FINANCE, Roles.FINANCE_MANAGER, Roles.ADMIN])
def budgets():
    """Budget tracking and variance analysis."""
    try:
        from app.models import Project
        
        projects = get_user_finance_projects(current_user)
        budget_data = []
        
        for project in projects:
            budget = float(project.budget or 0)
            spent = float(db.session.query(func.sum(Expense.amount)).filter_by(
                project_id=project.id,
                status='approved'
            ).scalar() or 0)
            
            variance = budget - spent
            variance_percent = ((variance / budget) * 100) if budget > 0 else 0
            
            budget_data.append({
                'project': project,
                'budget': float(budget),
                'spent': float(spent),
                'remaining': float(variance),
                'variance_percent': variance_percent,
                'status': 'on-track' if variance_percent >= 0 else 'over-budget'
            })
        
        return render_template(
            'finance/budgets.html',
            budget_data=budget_data
        )
    except Exception as e:
        current_app.logger.error(f"Budgets error: {str(e)}")
        flash(f"Error loading budgets: {str(e)}", "error")
        return redirect(url_for('finance.dashboard'))


@finance_bp.route('/budgets', methods=['POST'])
@login_required
@role_required([Roles.SUPER_HQ, Roles.HQ_FINANCE, Roles.FINANCE_MANAGER, Roles.ADMIN])
def create_budget():
    """Create a new budget."""
    try:
        from app.models import Project
        from datetime import datetime
        
        project_name = request.form.get('project_name', '').strip()
        budget_amount = request.form.get('budget_amount', type=float)
        fiscal_year = request.form.get('fiscal_year', type=int)
        category = request.form.get('category', 'General').strip()
        
        if not all([project_name, budget_amount, fiscal_year]):
            flash('Project name, budget amount, and fiscal year are required', 'error')
            return redirect(url_for('finance.budgets'))
        
        if budget_amount <= 0:
            flash('Budget amount must be greater than 0', 'error')
            return redirect(url_for('finance.budgets'))
        
        # Check if project exists, if not create it
        project = Project.query.filter_by(name=project_name).first()
        
        if not project:
            project = Project(
                name=project_name,
                budget=budget_amount,
                fiscal_year=fiscal_year,
                category=category,
                created_by=current_user.id,
                created_at=datetime.utcnow()
            )
            db.session.add(project)
        else:
            # Update existing project budget
            project.budget = budget_amount
            project.fiscal_year = fiscal_year
            if category:
                project.category = category
        
        db.session.commit()
        
        # Log the action
        audit_log = ApprovalLog(
            entity_type='Project',
            entity_id=project.id,
            action='BUDGET_CREATED' if not project else 'BUDGET_UPDATED',
            actor_id=current_user.id,
            comment=f'Budget set for {project_name}: ₦{budget_amount:,.2f}',
            timestamp=datetime.utcnow()
        )
        db.session.add(audit_log)
        db.session.commit()
        
        flash(f'✓ Budget created successfully for {project_name}!', 'success')
        return redirect(url_for('finance.budgets'))
    except Exception as e:
        db.session.rollback()
        current_app.logger.error(f"Error creating budget: {str(e)}")
        flash(f"Error creating budget: {str(e)}", "error")
        return redirect(url_for('finance.budgets'))


# ===== INVOICES & PAYMENTS =====

@finance_bp.route('/invoice')
@login_required
@role_required([Roles.SUPER_HQ, Roles.HQ_FINANCE, Roles.FINANCE_MANAGER, Roles.ACCOUNTS_PAYABLE, Roles.ADMIN])
def invoice():
    """Invoice management."""
    try:
        page = request.args.get('page', 1, type=int)
        status = request.args.get('status', 'all')
        
        query = PaymentRequest.query.order_by(PaymentRequest.created_at.desc())
        
        if status != 'all':
            query = query.filter_by(approval_state=status)
        
        invoices = query.paginate(page=page, per_page=20)
        
        total_invoiced = db.session.query(func.sum(PaymentRequest.invoice_amount)).scalar() or 0
        
        # Get purchase orders for create invoice modal
        purchase_orders = PurchaseOrder.query.filter_by(approval_state=ApprovalState.APPROVED).all()
        
        return render_template(
            'finance/invoice.html',
            invoices=invoices,
            total_invoiced=float(total_invoiced),
            status_filter=status,
            purchase_orders=purchase_orders
        )
    except Exception as e:
        current_app.logger.error(f"Invoice error: {str(e)}")
        flash(f"Error loading invoices: {str(e)}", "error")
        return redirect(url_for('finance.dashboard'))


@finance_bp.route('/invoice', methods=['POST'])
@login_required
@role_required([Roles.SUPER_HQ, Roles.HQ_FINANCE, Roles.FINANCE_MANAGER, Roles.ACCOUNTS_PAYABLE, Roles.ADMIN])
def create_invoice():
    """Create new invoice from purchase order."""
    try:
        from datetime import datetime
        
        po_id = request.form.get('po_id', type=int)
        invoice_number = request.form.get('invoice_number', '').strip()
        invoice_amount = request.form.get('invoice_amount', type=float)
        due_date = request.form.get('due_date')
        
        # Validation checks
        if not po_id:
            flash('Please select a purchase order', 'error')
            return redirect(url_for('finance.invoice'))
        
        if not invoice_number:
            flash('Invoice number is required', 'error')
            return redirect(url_for('finance.invoice'))
        
        if not invoice_amount or invoice_amount <= 0:
            flash('Invoice amount must be greater than 0', 'error')
            return redirect(url_for('finance.invoice'))
        
        # Check if invoice number already exists
        existing = PaymentRequest.query.filter_by(invoice_number=invoice_number).first()
        if existing:
            flash('Invoice number already exists', 'error')
            return redirect(url_for('finance.invoice'))
        
        # Verify PO exists
        po = PurchaseOrder.query.get(po_id)
        if not po:
            flash('Purchase order not found', 'error')
            return redirect(url_for('finance.invoice'))
        
        # Create new invoice (payment request)
        new_invoice = PaymentRequest(
            po_id=po_id,
            invoice_number=invoice_number,
            invoice_amount=invoice_amount,
            approval_state=ApprovalState.PENDING,
            verified_by=current_user.id,
            created_at=datetime.utcnow()
        )
        
        db.session.add(new_invoice)
        db.session.flush()

        # Post invoice to ledger: Dr Operating Expenses, Cr Accounts Payable.
        expense_account = ChartOfAccount.query.filter_by(account_code='5000').first()
        payable_account = ChartOfAccount.query.filter_by(account_code='2000').first()
        if expense_account and payable_account:
            _post_double_entry(
                debit_account_id=expense_account.id,
                credit_account_id=payable_account.id,
                amount=invoice_amount,
                description=f"Invoice {invoice_number}",
                reference=f"INV-{new_invoice.id}",
                entity_type='payment_request',
                entity_id=new_invoice.id,
                category='invoice'
            )
        db.session.commit()
        
        # Log the action
        audit_log = ApprovalLog(
            entity_type='PaymentRequest',
            entity_id=new_invoice.id,
            action='CREATED',
            actor_id=current_user.id,
            comment=f'Invoice created: {invoice_number}',
            timestamp=datetime.utcnow()
        )
        db.session.add(audit_log)
        db.session.commit()
        
        flash(f'Invoice {invoice_number} created successfully', 'success')
        return redirect(url_for('finance.invoice'))
    except Exception as e:
        db.session.rollback()
        current_app.logger.error(f"Invoice creation error: {str(e)}")
        flash(f"Error creating invoice: {str(e)}", "error")
        return redirect(url_for('finance.invoice'))


@finance_bp.route('/invoice/<int:invoice_id>/send', methods=['POST'])
@login_required
@role_required([Roles.SUPER_HQ, Roles.HQ_FINANCE, Roles.FINANCE_MANAGER, Roles.ACCOUNTS_PAYABLE, Roles.ADMIN])
def send_invoice(invoice_id):
    """Send invoice to departments (Admin, Cost Control, Procurement)."""
    try:
        invoice = PaymentRequest.query.get(invoice_id)
        if not invoice:
            flash('Invoice not found', 'error')
            return redirect(url_for('finance.invoice'))
        
        # Get selected departments from form
        send_to_admin = request.form.get('send_to_admin') == 'on'
        send_to_cost_control = request.form.get('send_to_cost_control') == 'on'
        send_to_procurement = request.form.get('send_to_procurement') == 'on'
        
        if not any([send_to_admin, send_to_cost_control, send_to_procurement]):
            flash('Please select at least one department to send the invoice to', 'error')
            return redirect(url_for('finance.invoice'))
        
        # Update invoice sent flags
        if send_to_admin:
            invoice.sent_to_admin = True
        if send_to_cost_control:
            invoice.sent_to_cost_control = True
        if send_to_procurement:
            invoice.sent_to_procurement = True
        
        invoice.sent_date = datetime.utcnow()
        db.session.commit()
        
        # Log the action
        departments = []
        if send_to_admin:
            departments.append('Admin')
        if send_to_cost_control:
            departments.append('Cost Control')
        if send_to_procurement:
            departments.append('Procurement')
        
        audit_log = ApprovalLog(
            entity_type='payment_request',
            entity_id=invoice.id,
            action='SENT',
            actor_id=current_user.id,
            comment=f'Invoice sent to: {", ".join(departments)}',
            timestamp=datetime.utcnow()
        )
        db.session.add(audit_log)
        db.session.commit()
        
        flash(f'Invoice {invoice.invoice_number} sent to {", ".join(departments)} successfully', 'success')
        return redirect(url_for('finance.invoice'))
    except Exception as e:
        db.session.rollback()
        current_app.logger.error(f"Send invoice error: {str(e)}")
        flash(f"Error sending invoice: {str(e)}", "error")
        return redirect(url_for('finance.invoice'))


@finance_bp.route('/payment')
@login_required
@role_required([Roles.SUPER_HQ, Roles.HQ_FINANCE, Roles.FINANCE_MANAGER, Roles.ACCOUNTS_PAYABLE, Roles.ADMIN])
def payment():
    """Payment management and tracking."""
    try:
        page = request.args.get('page', 1, type=int)
        vendor_id = request.args.get('vendor_id')
        
        query = PaymentRecord.query.order_by(PaymentRecord.payment_date.desc())
        
        if vendor_id:
            # This assumes PaymentRecord has a vendor_id or we filter through payment_request
            query = query.filter(PaymentRecord.payment_request_id.in_(
                db.session.query(PaymentRequest.id).filter_by(vendor_id=vendor_id)
            ))
        
        payments = query.paginate(page=page, per_page=20)
        vendors = Vendor.query.all()
        
        # Get pending and approved payment requests for payment selection
        payment_requests = PaymentRequest.query.filter(
            PaymentRequest.approval_state.in_([ApprovalState.APPROVED, ApprovalState.PENDING])
        ).order_by(PaymentRequest.created_at.desc()).all()
        
        bank_accounts = BankAccount.query.filter_by(is_active=True).all()
        
        total_paid = db.session.query(func.sum(PaymentRecord.amount_paid)).scalar() or 0
        
        return render_template(
            'finance/payment.html',
            payments=payments,
            vendors=vendors,
            payment_requests=payment_requests,
            bank_accounts=bank_accounts,
            total_paid=float(total_paid),
            selected_vendor=vendor_id
        )
    except Exception as e:
        current_app.logger.error(f"Payment error: {str(e)}")
        flash(f"Error loading payments: {str(e)}", "error")
        return redirect(url_for('finance.dashboard'))


# ===== DOCUMENTS & REPORTS =====

@finance_bp.route('/documents', methods=['GET', 'POST'])
@login_required
@role_required([Roles.SUPER_HQ, Roles.HQ_FINANCE, Roles.FINANCE_MANAGER, Roles.ADMIN])
def documents():
    """Redirect to uploads page."""
    return redirect(url_for('finance.view_uploads'))


@finance_bp.route('/audit-trail')
@login_required
@role_required([Roles.SUPER_HQ, Roles.HQ_FINANCE, Roles.FINANCE_MANAGER, Roles.ADMIN])
def audit_trail():
    """Audit log and transaction history."""
    try:
        page = request.args.get('page', 1, type=int)
        
        from app.models import ApprovalLog
        
        audit_logs = ApprovalLog.query.order_by(
            ApprovalLog.timestamp.desc()
        ).paginate(page=page, per_page=30)
        
        return render_template(
            'finance/audit_trail.html',
            audit_logs=audit_logs
        )
    except Exception as e:
        current_app.logger.error(f"Audit trail error: {str(e)}")
        flash(f"Error loading audit trail: {str(e)}", "error")
        return redirect(url_for('finance.dashboard'))


# ===== PAYMENT EVIDENCE UPLOAD =====

@finance_bp.route('/payment/<int:payment_id>/upload-evidence', methods=['POST'])
@login_required
@role_required([Roles.SUPER_HQ, Roles.HQ_FINANCE, Roles.FINANCE_MANAGER, Roles.ACCOUNTS_PAYABLE])
def upload_payment_evidence():
    """Upload payment evidence/proof document."""
    try:
        payment_id = request.form.get('payment_id')
        
        if 'proof_document' not in request.files:
            return jsonify({'status': 'error', 'message': 'No file provided'}), 400
        
        file = request.files['proof_document']
        
        if file.filename == '':
            return jsonify({'status': 'error', 'message': 'No file selected'}), 400
        
        # Validate file type
        allowed_extensions = {'pdf', 'jpg', 'jpeg', 'png', 'doc', 'docx'}
        if not ('.' in file.filename and file.filename.rsplit('.', 1)[1].lower() in allowed_extensions):
            return jsonify({'status': 'error', 'message': 'Invalid file type'}), 400
        
        # Create uploads directory if it doesn't exist
        upload_dir = os.path.join(current_app.root_path, 'uploads', 'payment_evidence')
        os.makedirs(upload_dir, exist_ok=True)
        
        # Save file with secure name
        filename = secure_filename(f"payment_{payment_id}_{datetime.now().timestamp()}_{file.filename}")
        filepath = os.path.join(upload_dir, filename)
        file.save(filepath)
        
        # Update payment record with proof document path
        payment = PaymentRecord.query.get(payment_id)
        if payment:
            payment.proof_document = f'/uploads/payment_evidence/{filename}'
            db.session.commit()
            current_app.logger.info(f"Payment evidence uploaded: {payment_id}")
            return jsonify({'status': 'success', 'message': 'Evidence uploaded successfully'})
        else:
            return jsonify({'status': 'error', 'message': 'Payment not found'}), 404
            
    except Exception as e:
        current_app.logger.error(f"Payment evidence upload error: {str(e)}")
        return jsonify({'status': 'error', 'message': str(e)}), 500


@finance_bp.route('/upload', methods=['POST'])
@login_required
@role_required([Roles.SUPER_HQ, Roles.HQ_FINANCE, Roles.FINANCE_MANAGER, Roles.ADMIN])
def upload_document():
    """Upload finance document with project linking."""
    try:
        from app.models import ProjectDocument
        
        if 'file' not in request.files:
            return jsonify({'status': 'error', 'message': 'No file provided'}), 400
        
        file = request.files['file']
        title = request.form.get('title', 'Finance Document')
        description = request.form.get('description', '')
        tags = [t.strip() for t in request.form.get('tags', '').split(',') if t.strip()]
        project_id = request.form.get('project_id')
        
        if file.filename == '':
            return jsonify({'status': 'error', 'message': 'No file selected'}), 400
        
        # Validate file type
        allowed_extensions = {'pdf', 'jpg', 'jpeg', 'png', 'doc', 'docx', 'xls', 'xlsx', 'csv'}
        file_ext = file.filename.rsplit('.', 1)[1].lower() if '.' in file.filename else ''
        if file_ext not in allowed_extensions:
            return jsonify({'status': 'error', 'message': f'Invalid file type. Allowed: {", ".join(allowed_extensions)}'}), 400
        
        # Require project for document upload
        if not project_id:
            return jsonify({'status': 'error', 'message': 'Project is required'}), 400
        
        # Verify project exists and user has access
        project = Project.query.get(project_id)
        if not project:
            return jsonify({'status': 'error', 'message': 'Project not found'}), 404
        
        # Create uploads directory if it doesn't exist
        upload_dir = os.path.join(current_app.root_path, 'uploads', 'finance_documents')
        os.makedirs(upload_dir, exist_ok=True)
        
        # Save file with secure name
        filename = secure_filename(f"{project_id}_{datetime.now().strftime('%Y%m%d_%H%M%S')}_{file.filename}")
        filepath = os.path.join(upload_dir, filename)
        file.save(filepath)
        
        # Create record for uploaded file
        uploaded_file = ProjectDocument(
            project_id=int(project_id),
            title=title,
            description=_compose_note(description, tags=tags),
            file_name=filename,
            file_path=f'/uploads/finance_documents/{filename}',
            document_type=file_ext,
            uploaded_by_id=current_user.id,
            created_at=datetime.utcnow()
        )
        db.session.add(uploaded_file)
        db.session.commit()
        
        current_app.logger.info(f"Finance document uploaded: {filename} by {current_user.email} for project {project_id}")
        
        return jsonify({
            'status': 'success',
            'message': 'Document uploaded successfully',
            'file_id': uploaded_file.id,
            'file_name': filename,
            'file_path': f'/uploads/finance_documents/{filename}'
        }), 201
            
    except Exception as e:
        db.session.rollback()
        current_app.logger.error(f"Document upload error: {str(e)}")
        return jsonify({'status': 'error', 'message': str(e)}), 500


@finance_bp.route('/uploads', methods=['GET'])
@login_required
@role_required([Roles.SUPER_HQ, Roles.HQ_FINANCE, Roles.FINANCE_MANAGER, Roles.ADMIN])
def view_uploads():
    """View all uploaded finance documents."""
    try:
        from app.models import ProjectDocument
        
        page = request.args.get('page', 1, type=int)
        project_id = request.args.get('project_id')
        q = request.args.get('q', '').strip()
        tag = request.args.get('tag', '').strip()
        document_type = request.args.get('document_type', '').strip()

        query = ProjectDocument.query.order_by(ProjectDocument.created_at.desc())

        if project_id:
            query = query.filter_by(project_id=project_id)
        if q:
            query = query.filter(or_(
                ProjectDocument.title.ilike(f"%{q}%"),
                ProjectDocument.file_name.ilike(f"%{q}%"),
                ProjectDocument.description.ilike(f"%{q}%")
            ))
        if tag:
            query = query.filter(ProjectDocument.description.ilike(f"%{tag}%"))
        if document_type:
            query = query.filter(ProjectDocument.document_type == document_type)

        uploads = query.paginate(page=page, per_page=20)
        projects = get_user_finance_projects(current_user)
        for upload in uploads.items:
            _, parsed_tags, _ = _split_note_fields(upload.description)
            upload.parsed_tags = parsed_tags
        doc_types = [row[0] for row in db.session.query(ProjectDocument.document_type).distinct().all() if row[0]]

        return render_template(
            'finance/uploads.html',
            uploads=uploads,
            projects=projects,
            selected_project=project_id,
            selected_q=q,
            selected_tag=tag,
            selected_document_type=document_type,
            document_types=sorted(doc_types)
        )
    except Exception as e:
        current_app.logger.error(f"View uploads error: {str(e)}")
        flash(f'Error loading documents: {str(e)}', 'error')
        return redirect(url_for('finance.dashboard'))


# ===== BANK RECONCILIATION =====



