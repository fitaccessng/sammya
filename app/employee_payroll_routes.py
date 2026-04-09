"""
Employee Self-Service Payroll Portal Routes
Employees can view pay stubs, salary mapping, and payroll history
"""

from flask import Blueprint, render_template, request, jsonify, send_file
from flask_login import login_required, current_user
from datetime import datetime, date, timedelta
from decimal import Decimal
from sqlalchemy import desc, and_

from app.payroll_models import (
    PayrollRecord, SalaryMapping, PayrollBatch, PayrollExport
)
from app.payroll_reports import PaySlipGenerator
from app.models import db
import tempfile
import os

employee_payroll_bp = Blueprint(
    'employee_payroll',
    __name__,
    url_prefix='/employee/payroll',
    template_folder='../templates/employee/payroll'
)

# ==============================================================================
# DASHBOARD
# ==============================================================================

@employee_payroll_bp.route('/dashboard')
@login_required
def payroll_dashboard():
    """Employee payroll dashboard"""
    
    # Get current salary mapping
    current_mapping = SalaryMapping.query.filter_by(
        user_id=current_user.id,
        is_active=True
    ).first()
    
    # Get recent payroll records (last 12 months)
    twelve_months_ago = date.today().replace(day=1) - timedelta(days=1)
    twelve_months_ago = twelve_months_ago.replace(day=1)
    
    recent_records = PayrollRecord.query.filter(
        PayrollRecord.user_id == current_user.id,
        PayrollRecord.created_at >= twelve_months_ago
    ).order_by(desc(PayrollRecord.payroll_period)).limit(12).all()
    
    # Calculate YTD totals
    ytd_start = date.today().replace(month=1, day=1)
    ytd_records = PayrollRecord.query.filter(
        PayrollRecord.user_id == current_user.id,
        PayrollRecord.created_at >= ytd_start
    ).all()
    
    ytd_summary = {
        'gross': sum(r.gross_salary for r in ytd_records),
        'deductions': sum(r.total_deductions for r in ytd_records),
        'net': sum(r.net_salary for r in ytd_records),
        'tax': sum(r.tax_amount for r in ytd_records),
    }
    
    # Latest pay slip (most recent record)
    latest_record = recent_records[0] if recent_records else None
    
    context = {
        'current_mapping': current_mapping,
        'latest_record': latest_record,
        'recent_records': recent_records,
        'ytd_summary': ytd_summary,
        'has_salary_mapping': current_mapping is not None,
    }
    
    return render_template('employee_payroll/dashboard.html', **context)


# ==============================================================================
# SALARY INFORMATION
# ==============================================================================

@employee_payroll_bp.route('/salary')
@login_required
def view_salary():
    """View current salary mapping"""
    
    current_mapping = SalaryMapping.query.filter_by(
        user_id=current_user.id,
        is_active=True
    ).first()
    
    # Get salary history (previous versions)
    salary_history = SalaryMapping.query.filter_by(
        user_id=current_user.id
    ).order_by(desc(SalaryMapping.effective_date)).limit(12).all()
    
    if not current_mapping:
        return render_template(
            'employee_payroll/no_salary.html',
            message='Your salary mapping has not been configured yet.'
        )
    
    context = {
        'current_mapping': current_mapping,
        'salary_history': salary_history,
    }
    
    return render_template('employee_payroll/view_salary.html', **context)


# ==============================================================================
# PAY STUBS
# ==============================================================================

@employee_payroll_bp.route('/pay-stubs')
@login_required
def pay_stubs():
    """List employee's pay stubs"""
    
    page = request.args.get('page', 1, type=int)
    per_page = request.args.get('per_page', 10, type=int)
    
    # Get all payroll records for employee
    records = PayrollRecord.query.filter_by(
        user_id=current_user.id
    ).order_by(
        desc(PayrollRecord.payroll_period)
    ).paginate(page=page, per_page=per_page, error_out=False)
    
    context = {
        'records_pagination': records,
    }
    
    return render_template('employee_payroll/pay_stubs.html', **context)


@employee_payroll_bp.route('/pay-stubs/<int:record_id>')
@login_required
def view_pay_stub(record_id):
    """View detailed pay stub"""
    
    record = PayrollRecord.query.get_or_404(record_id)
    
    # Verify ownership
    if record.user_id != current_user.id:
        return jsonify({'error': 'Unauthorized'}), 403
    
    context = {
        'record': record,
        'batch': record.batch,
    }
    
    return render_template('employee_payroll/pay_stub_detail.html', **context)


@employee_payroll_bp.route('/pay-stubs/<int:record_id>/download')
@login_required
def download_pay_stub(record_id):
    """Download pay stub as PDF"""
    
    record = PayrollRecord.query.get_or_404(record_id)
    
    # Verify ownership
    if record.user_id != current_user.id:
        return jsonify({'error': 'Unauthorized'}), 403
    
    try:
        # Generate PDF
        with tempfile.TemporaryDirectory() as tmpdir:
            filename = f"{current_user.id}_{current_user.last_name}_payslip_{record.payroll_period}.pdf"
            output_path = os.path.join(tmpdir, filename)
            
            generator = PaySlipGenerator()
            success, result = generator.generate_pay_slip(record, output_path)
            
            if success:
                return send_file(output_path, as_attachment=True, download_name=filename)
            else:
                return jsonify({'error': result}), 500
                
    except Exception as e:
        return jsonify({'error': str(e)}), 500


# ==============================================================================
# PAYROLL HISTORY
# ==============================================================================

@employee_payroll_bp.route('/history')
@login_required
def payroll_history():
    """View payroll history with analytics"""
    
    # Get all records
    all_records = PayrollRecord.query.filter_by(
        user_id=current_user.id
    ).order_by(desc(PayrollRecord.payroll_period)).all()
    
    # Monthly trends
    monthly_data = {}
    for record in all_records:
        month = record.payroll_period  # Format: YYYY-MM
        
        if month not in monthly_data:
            monthly_data[month] = {
                'gross': Decimal('0'),
                'deductions': Decimal('0'),
                'net': Decimal('0'),
                'record_id': record.id,
            }
        
        monthly_data[month]['gross'] += record.gross_salary
        monthly_data[month]['deductions'] += record.total_deductions
        monthly_data[month]['net'] += record.net_salary
    
    # Calculate statistics
    if all_records:
        total_gross = sum(r.gross_salary for r in all_records)
        total_deductions = sum(r.total_deductions for r in all_records)
        total_net = sum(r.net_salary for r in all_records)
        
        stats = {
            'total_records': len(all_records),
            'total_gross': total_gross,
            'total_deductions': total_deductions,
            'total_net': total_net,
            'average_gross': total_gross / len(all_records),
            'average_net': total_net / len(all_records),
            'min_net': min(r.net_salary for r in all_records),
            'max_net': max(r.net_salary for r in all_records),
        }
    else:
        stats = {}
    
    context = {
        'records': all_records,
        'monthly_data': monthly_data,
        'stats': stats,
    }
    
    return render_template('employee_payroll/history.html', **context)


# ==============================================================================
# TAX INFORMATION
# ==============================================================================

@employee_payroll_bp.route('/tax-summary')
@login_required
def tax_summary():
    """View tax summary for year"""
    
    ytd_start = date.today().replace(month=1, day=1)
    ytd_records = PayrollRecord.query.filter(
        PayrollRecord.user_id == current_user.id,
        PayrollRecord.created_at >= ytd_start
    ).all()
    
    monthly_tax = {}
    for record in ytd_records:
        month = record.payroll_period
        monthly_tax[month] = {
            'gross': record.gross_salary,
            'tax': record.tax_amount,
            'effective_rate': (record.tax_amount / record.gross_salary * 100) if record.gross_salary > 0 else 0,
        }
    
    total_tax = sum(r.tax_amount for r in ytd_records)
    total_gross = sum(r.gross_salary for r in ytd_records)
    average_rate = (total_tax / total_gross * 100) if total_gross > 0 else 0
    
    context = {
        'tax_data': monthly_tax,
        'total_tax': total_tax,
        'total_gross': total_gross,
        'average_rate': average_rate,
        'monthly_breakdown': sorted(monthly_tax.items(), reverse=True),
    }
    
    return render_template('employee_payroll/tax_summary.html', **context)


# ==============================================================================
# DEDUCTIONS BREAKDOWN
# ==============================================================================

@employee_payroll_bp.route('/deductions')
@login_required
def deductions_breakdown():
    """View deductions breakdown"""
    
    ytd_start = date.today().replace(month=1, day=1)
    ytd_records = PayrollRecord.query.filter(
        PayrollRecord.user_id == current_user.id,
        PayrollRecord.created_at >= ytd_start
    ).all()
    
    deduction_summary = {
        'tax': sum(r.tax_amount for r in ytd_records),
        'pension': sum(r.pension_amount for r in ytd_records),
        'insurance': sum(r.insurance_amount for r in ytd_records),
        'loan': sum(r.loan_deduction for r in ytd_records),
        'other': sum(r.other_deduction for r in ytd_records),
    }
    
    total_deductions = sum(deduction_summary.values())
    
    # Calculate percentages
    deduction_percentages = {}
    for key, value in deduction_summary.items():
        if total_deductions > 0:
            deduction_percentages[key] = (value / total_deductions * 100)
        else:
            deduction_percentages[key] = 0
    
    context = {
        'deduction_summary': deduction_summary,
        'deduction_percentages': deduction_percentages,
        'total_deductions': total_deductions,
    }
    
    return render_template('employee_payroll/deductions.html', **context)


# ==============================================================================
# PAYROLL API ENDPOINTS (JSON)
# ==============================================================================

@employee_payroll_bp.route('/api/current-salary', methods=['GET'])
@login_required
def api_current_salary():
    """Get current salary mapping as JSON"""
    
    mapping = SalaryMapping.query.filter_by(
        user_id=current_user.id,
        is_active=True
    ).first()
    
    if not mapping:
        return jsonify({'error': 'No salary mapping found'}), 404
    
    return jsonify({
        'basic_salary': float(mapping.basic_salary),
        'allowances': {
            'house': float(mapping.house_allowance),
            'transport': float(mapping.transport_allowance),
            'meal': float(mapping.meal_allowance),
            'risk': float(mapping.risk_allowance),
            'performance': float(mapping.performance_allowance),
        },
        'deductions': {
            'tax': float(mapping.tax_amount),
            'pension': float(mapping.pension_amount),
            'insurance': float(mapping.insurance_amount),
            'loan': float(mapping.loan_deduction),
            'other': float(mapping.other_deduction),
        },
        'effective_date': mapping.effective_date.isoformat(),
    })


@employee_payroll_bp.route('/api/recent-records', methods=['GET'])
@login_required
def api_recent_records():
    """Get recent payroll records as JSON"""
    
    limit = request.args.get('limit', 6, type=int)
    
    records = PayrollRecord.query.filter_by(
        user_id=current_user.id
    ).order_by(
        desc(PayrollRecord.payroll_period)
    ).limit(limit).all()
    
    return jsonify([{
        'id': r.id,
        'payroll_period': r.payroll_period,
        'basic': float(r.basic_salary),
        'gross': float(r.gross_salary),
        'deductions': float(r.total_deductions),
        'net': float(r.net_salary),
        'created_at': r.created_at.isoformat(),
    } for r in records])


@employee_payroll_bp.route('/api/ytd-summary', methods=['GET'])
@login_required
def api_ytd_summary():
    """Get YTD summary as JSON"""
    
    ytd_start = date.today().replace(month=1, day=1)
    ytd_records = PayrollRecord.query.filter(
        PayrollRecord.user_id == current_user.id,
        PayrollRecord.created_at >= ytd_start
    ).all()
    
    total_gross = sum(r.gross_salary for r in ytd_records)
    total_deductions = sum(r.total_deductions for r in ytd_records)
    total_net = sum(r.net_salary for r in ytd_records)
    
    return jsonify({
        'records_count': len(ytd_records),
        'total_gross': float(total_gross),
        'total_deductions': float(total_deductions),
        'total_net': float(total_net),
        'average_net': float(total_net / len(ytd_records)) if ytd_records else 0,
    })


@employee_payroll_bp.route('/api/monthly-breakdown', methods=['GET'])
@login_required
def api_monthly_breakdown():
    """Get monthly breakdown as JSON for charting"""
    
    month = request.args.get('month')  # Format: YYYY-MM
    limit = request.args.get('limit', 12, type=int)
    
    records = PayrollRecord.query.filter_by(
        user_id=current_user.id
    ).order_by(
        desc(PayrollRecord.payroll_period)
    ).limit(limit).all()
    
    monthly_data = {}
    for record in sorted(records, key=lambda r: r.payroll_period):
        month_key = record.payroll_period
        monthly_data[month_key] = {
            'basic': float(record.basic_salary),
            'allowances': float(record.gross_salary - record.basic_salary),
            'gross': float(record.gross_salary),
            'tax': float(record.tax_amount),
            'pension': float(record.pension_amount),
            'deductions': float(record.total_deductions),
            'net': float(record.net_salary),
        }
    
    return jsonify(monthly_data)
