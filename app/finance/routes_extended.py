"""
Finance dashboard with QuickBooks-like accounting features.
Payment management, cash flow, financial reports, invoice tracking, expense management.
"""

from flask import Blueprint, render_template, request, redirect, url_for, flash, jsonify
from flask_login import current_user, login_required
from app.models import db, PaymentRequest, PaymentRecord, PurchaseOrder, Project, ApprovalState, Vendor
from app.auth.decorators import role_required
from datetime import datetime, timedelta
from decimal import Decimal

bp = Blueprint('finance', __name__, url_prefix='/finance')


@bp.route('/dashboard')
@login_required
@role_required(['finance_manager', 'accounts_payable'])
def dashboard():
    """Finance dashboard with QuickBooks-like features."""
    
    # Cash Summary
    pending_payments = PaymentRequest.query.filter_by(approval_state=ApprovalState.PENDING).all()
    pending_amount = sum(float(p.amount or 0) for p in pending_payments)
    
    approved_payments = PaymentRequest.query.filter_by(approval_state=ApprovalState.APPROVED).all()
    approved_amount = sum(float(p.amount or 0) for p in approved_payments)
    
    processed_payments = PaymentRecord.query.all()
    total_paid = sum(float(p.amount or 0) for p in processed_payments)
    
    # Calculate cash flow
    invoices_30_days = PaymentRequest.query.filter(
        PaymentRequest.created_at >= datetime.utcnow() - timedelta(days=30),
        PaymentRequest.approval_state == ApprovalState.APPROVED
    ).all()
    invoices_30_value = sum(float(p.amount or 0) for p in invoices_30_days)
    
    # Aging analysis (invoices by age)
    all_invoices = PaymentRequest.query.all()
    current_30 = len([p for p in all_invoices if (datetime.utcnow() - p.created_at).days <= 30])
    overdue_30_60 = len([p for p in all_invoices if 30 < (datetime.utcnow() - p.created_at).days <= 60])
    overdue_60_90 = len([p for p in all_invoices if 60 < (datetime.utcnow() - p.created_at).days <= 90])
    overdue_90 = len([p for p in all_invoices if (datetime.utcnow() - p.created_at).days > 90])
    
    # Top vendors by spend
    vendors = Vendor.query.all()
    vendor_spend = {}
    for vendor in vendors:
        spend = sum(float(p.amount or 0) for p in processed_payments if p.payment_request and p.payment_request.purchase_order and p.payment_request.purchase_order.vendor_id == vendor.id)
        if spend > 0:
            vendor_spend[vendor.name] = spend
    
    top_vendors = sorted(vendor_spend.items(), key=lambda x: x[1], reverse=True)[:5]
    
    # Recent transactions
    recent_payments = PaymentRecord.query.order_by(PaymentRecord.created_at.desc()).limit(10).all()
    
    return render_template(
        'finance/dashboard.html',
        pending_amount=pending_amount,
        approved_amount=approved_amount,
        total_paid=total_paid,
        pending_count=len(pending_payments),
        approved_count=len(approved_payments),
        processed_count=len(processed_payments),
        invoices_30_value=invoices_30_value,
        current_30=current_30,
        overdue_30_60=overdue_30_60,
        overdue_60_90=overdue_60_90,
        overdue_90=overdue_90,
        top_vendors=top_vendors,
        recent_payments=recent_payments
    )


@bp.route('/invoices')
@login_required
@role_required(['finance_manager', 'accounts_payable'])
def invoices():
    """View all invoices with aging and status tracking."""
    status_filter = request.args.get('status', None)
    page = request.args.get('page', 1, type=int)
    
    query = PaymentRequest.query
    if status_filter:
        query = query.filter_by(approval_state=status_filter)
    
    invoices = query.order_by(PaymentRequest.created_at.desc()).paginate(page=page, per_page=20)
    
    # Add aging calculation for each invoice
    for invoice in invoices.items:
        days_old = (datetime.utcnow() - invoice.created_at).days
        if days_old <= 30:
            invoice.aging = 'Current'
        elif days_old <= 60:
            invoice.aging = '31-60 days'
        elif days_old <= 90:
            invoice.aging = '61-90 days'
        else:
            invoice.aging = 'Over 90 days'
    
    return render_template(
        'finance/invoices.html',
        invoices=invoices,
        selected_status=status_filter
    )


@bp.route('/payments')
@login_required
@role_required(['finance_manager', 'accounts_payable'])
def payments():
    """View payment records (check register)."""
    page = request.args.get('page', 1, type=int)
    
    payments = PaymentRecord.query.order_by(PaymentRecord.created_at.desc()).paginate(page=page, per_page=20)
    
    return render_template('finance/payments.html', payments=payments)


@bp.route('/expense-tracker')
@login_required
@role_required(['finance_manager', 'accounts_payable'])
def expense_tracker():
    """Track expenses by category and project."""
    projects = Project.query.all()
    
    project_expenses = []
    for project in projects:
        # Get all payments for this project
        project_pos = PurchaseOrder.query.filter_by(project_id=project.id).all()
        po_ids = [po.id for po in project_pos]
        
        project_payments = PaymentRequest.query.filter(
            PaymentRequest.purchase_order_id.in_(po_ids) if po_ids else False
        ).all()
        
        total_expense = sum(float(p.amount or 0) for p in project_payments)
        
        project_expenses.append({
            'project': project,
            'total_expense': total_expense,
            'invoice_count': len(project_payments)
        })
    
    total_expenses = sum(item['total_expense'] for item in project_expenses)
    
    return render_template(
        'finance/expense_tracker.html',
        project_expenses=project_expenses,
        total_expenses=total_expenses
    )


@bp.route('/financial-reports')
@login_required
@role_required(['finance_manager'])
def financial_reports():
    """Generate financial reports (Income Statement, Trial Balance, etc.)."""
    
    # Calculate financial metrics
    all_payments = PaymentRecord.query.all()
    total_disbursements = sum(float(p.amount or 0) for p in all_payments)
    
    pending_invoices = PaymentRequest.query.filter_by(approval_state=ApprovalState.PENDING).all()
    pending_total = sum(float(p.amount or 0) for p in pending_invoices)
    
    approved_not_paid = PaymentRequest.query.filter_by(approval_state=ApprovalState.APPROVED).all()
    approved_amount = sum(float(p.amount or 0) for p in approved_not_paid)
    
    # Accounts payable summary
    accounts_payable_amount = pending_total + approved_amount
    
    return render_template(
        'finance/financial_reports.html',
        total_disbursements=total_disbursements,
        pending_total=pending_total,
        approved_amount=approved_amount,
        accounts_payable=accounts_payable_amount,
        paid_invoices=len(all_payments),
        pending_invoices=len(pending_invoices)
    )


@bp.route('/cash-flow')
@login_required
@role_required(['finance_manager'])
def cash_flow():
    """Cash flow analysis and forecasting."""
    
    # Calculate cash flow for last 30 days
    today = datetime.utcnow()
    cash_flow_data = []
    
    for i in range(30):
        date = today - timedelta(days=i)
        day_payments = PaymentRecord.query.filter(
            db.func.date(PaymentRecord.created_at) == date.date()
        ).all()
        
        day_total = sum(float(p.amount or 0) for p in day_payments)
        cash_flow_data.append({
            'date': date.strftime('%b %d'),
            'amount': day_total
        })
    
    cash_flow_data.reverse()
    
    return render_template('finance/cash_flow.html', cash_flow_data=cash_flow_data)


@bp.route('/vendor-payments')
@login_required
@role_required(['finance_manager', 'accounts_payable'])
def vendor_payments():
    """Track payments to each vendor."""
    vendors = Vendor.query.all()
    
    vendor_summary = []
    for vendor in vendors:
        # Get all POs from this vendor
        vendor_pos = PurchaseOrder.query.filter_by(vendor_id=vendor.id).all()
        
        # Get all payments to this vendor
        total_amount = 0
        payment_count = 0
        for po in vendor_pos:
            payments = PaymentRequest.query.filter_by(purchase_order_id=po.id).all()
            for payment in payments:
                total_amount += float(payment.amount or 0)
                payment_count += 1
        
        # Outstanding amount (approved but not paid)
        outstanding = sum(float(p.amount or 0) for po in vendor_pos for p in PaymentRequest.query.filter(
            PaymentRequest.purchase_order_id == po.id,
            PaymentRequest.approval_state == ApprovalState.APPROVED
        ).all())
        
        if total_amount > 0 or len(vendor_pos) > 0:
            vendor_summary.append({
                'vendor': vendor,
                'total_paid': total_amount,
                'po_count': len(vendor_pos),
                'payment_count': payment_count,
                'outstanding': outstanding
            })
    
    return render_template('finance/vendor_payments.html', vendors=vendor_summary)


@bp.route('/api/cash-balance')
@login_required
@role_required(['finance_manager', 'accounts_payable'])
def api_cash_balance():
    """API endpoint for cash balance."""
    processed = sum(float(p.amount or 0) for p in PaymentRecord.query.all())
    approved = sum(float(p.amount or 0) for p in PaymentRequest.query.filter_by(approval_state=ApprovalState.APPROVED).all())
    pending = sum(float(p.amount or 0) for p in PaymentRequest.query.filter_by(approval_state=ApprovalState.PENDING).all())
    
    return jsonify({
        'total_disbursed': processed,
        'approved_pending': approved,
        'pending_approval': pending,
        'total_committed': processed + approved + pending
    })


@bp.route('/api/invoice-aging')
@login_required
@role_required(['finance_manager', 'accounts_payable'])
def api_invoice_aging():
    """API endpoint for invoice aging breakdown."""
    all_invoices = PaymentRequest.query.all()
    
    current_30 = sum(float(p.amount or 0) for p in all_invoices if (datetime.utcnow() - p.created_at).days <= 30)
    days_30_60 = sum(float(p.amount or 0) for p in all_invoices if 30 < (datetime.utcnow() - p.created_at).days <= 60)
    days_60_90 = sum(float(p.amount or 0) for p in all_invoices if 60 < (datetime.utcnow() - p.created_at).days <= 90)
    over_90 = sum(float(p.amount or 0) for p in all_invoices if (datetime.utcnow() - p.created_at).days > 90)
    
    return jsonify({
        'current_30': current_30,
        'days_30_60': days_30_60,
        'days_60_90': days_60_90,
        'over_90': over_90
    })
