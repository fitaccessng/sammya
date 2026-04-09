"""
Procurement-specific dashboard and operations.
Material requests, purchase orders, vendor management, procurement metrics.
"""

from flask import Blueprint, render_template, request, redirect, url_for, flash, jsonify
from flask_login import current_user, login_required
from app.models import db, MaterialRequest, PurchaseOrder, Vendor, ApprovalState, User
from app.auth.decorators import role_required
from datetime import datetime
from decimal import Decimal

bp = Blueprint('procurement', __name__, url_prefix='/procurement')


@bp.route('/dashboard')
@login_required
@role_required(['procurement_manager', 'procurement_staff'])
def dashboard():
    """Procurement dashboard with PR/PO metrics."""
    # Get statistics
    total_pr = MaterialRequest.query.count()
    pending_pr = MaterialRequest.query.filter_by(approval_state=ApprovalState.PENDING).count()
    approved_pr = MaterialRequest.query.filter_by(approval_state=ApprovalState.APPROVED).count()
    
    total_po = PurchaseOrder.query.count()
    pending_po = PurchaseOrder.query.filter_by(approval_state=ApprovalState.PENDING).count()
    approved_po = PurchaseOrder.query.filter_by(approval_state=ApprovalState.APPROVED).count()
    issued_po = PurchaseOrder.query.filter_by(approval_state=ApprovalState.APPROVED, is_issued=True).count()
    
    # Get recent items
    recent_pr = MaterialRequest.query.order_by(MaterialRequest.created_at.desc()).limit(5).all()
    recent_po = PurchaseOrder.query.order_by(PurchaseOrder.created_at.desc()).limit(5).all()
    
    # Vendor statistics
    total_vendors = Vendor.query.count()
    active_vendors = Vendor.query.filter_by(is_active=True).count()
    
    # Calculate average PO value
    avg_po_value = 0
    if total_po > 0:
        total_value = sum(float(po.total_amount or 0) for po in PurchaseOrder.query.all())
        avg_po_value = total_value / total_po
    
    return render_template(
        'procurement/dashboard.html',
        total_pr=total_pr,
        pending_pr=pending_pr,
        approved_pr=approved_pr,
        total_po=total_po,
        pending_po=pending_po,
        approved_po=approved_po,
        issued_po=issued_po,
        total_vendors=total_vendors,
        active_vendors=active_vendors,
        avg_po_value=avg_po_value,
        recent_pr=recent_pr,
        recent_po=recent_po
    )


@bp.route('/material-requests')
@login_required
@role_required(['procurement_manager', 'procurement_staff'])
def material_requests():
    """List all material requests."""
    status_filter = request.args.get('status', None)
    page = request.args.get('page', 1, type=int)
    
    query = MaterialRequest.query
    if status_filter:
        query = query.filter_by(approval_state=status_filter)
    
    mr_list = query.order_by(MaterialRequest.created_at.desc()).paginate(page=page, per_page=15)
    
    return render_template(
        'procurement/material_requests.html',
        material_requests=mr_list,
        selected_status=status_filter
    )


@bp.route('/purchase-orders')
@login_required
@role_required(['procurement_manager', 'procurement_staff'])
def purchase_orders():
    """List all purchase orders."""
    status_filter = request.args.get('status', None)
    page = request.args.get('page', 1, type=int)
    
    query = PurchaseOrder.query
    if status_filter:
        query = query.filter_by(approval_state=status_filter)
    
    po_list = query.order_by(PurchaseOrder.created_at.desc()).paginate(page=page, per_page=15)
    
    return render_template(
        'procurement/purchase_orders.html',
        purchase_orders=po_list,
        selected_status=status_filter
    )


@bp.route('/vendor/<int:vendor_id>')
@login_required
@role_required(['procurement_manager', 'procurement_staff'])
def vendor_detail(vendor_id):
    """View vendor details and performance."""
    vendor = Vendor.query.get_or_404(vendor_id)
    pos = PurchaseOrder.query.filter_by(vendor_id=vendor_id).all()
    total_po_count = len(pos)
    total_po_value = sum(float(po.total_amount or 0) for po in pos)
    
    # Calculate on-time delivery rate (placeholder)
    on_time = len([po for po in pos if po.delivery_date])
    on_time_rate = (on_time / total_po_count * 100) if total_po_count > 0 else 0
    
    return render_template(
        'procurement/vendor_detail.html',
        vendor=vendor,
        pos=pos,
        total_po_count=total_po_count,
        total_po_value=total_po_value,
        on_time_rate=on_time_rate
    )


@bp.route('/analysis')
@login_required
@role_required(['procurement_manager'])
def procurement_analysis():
    """Procurement analysis and metrics."""
    # Spend analysis
    all_po = PurchaseOrder.query.filter_by(approval_state=ApprovalState.APPROVED).all()
    total_spend = sum(float(po.total_amount or 0) for po in all_po)
    
    # Spend by vendor
    vendor_spend = {}
    for po in all_po:
        if po.vendor:
            vendor_spend[po.vendor.name] = vendor_spend.get(po.vendor.name, 0) + float(po.total_amount or 0)
    
    # Top 5 vendors by spend
    top_vendors = sorted(vendor_spend.items(), key=lambda x: x[1], reverse=True)[:5]
    
    return render_template(
        'procurement/analysis.html',
        total_spend=total_spend,
        top_vendors=top_vendors,
        vendor_count=Vendor.query.count()
    )


@bp.route('/api/pr-status')
@login_required
@role_required(['procurement_manager', 'procurement_staff'])
def api_pr_status():
    """API for PR status breakdown."""
    states = [ApprovalState.DRAFT, ApprovalState.PENDING, ApprovalState.REVIEW, 
              ApprovalState.APPROVED, ApprovalState.REJECTED]
    
    data = {}
    for state in states:
        data[state.value] = MaterialRequest.query.filter_by(approval_state=state).count()
    
    return jsonify(data)


@bp.route('/api/po-status')
@login_required
@role_required(['procurement_manager', 'procurement_staff'])
def api_po_status():
    """API for PO status breakdown."""
    states = [ApprovalState.DRAFT, ApprovalState.PENDING, ApprovalState.REVIEW,
              ApprovalState.APPROVED, ApprovalState.REJECTED]
    
    data = {}
    for state in states:
        data[state.value] = PurchaseOrder.query.filter_by(approval_state=state).count()
    
    return jsonify(data)
