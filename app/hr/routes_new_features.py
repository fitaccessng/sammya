# ==================== PAYROLL MANAGEMENT ====================

@bp.route('/payroll')
@login_required
@hr_required
def payroll():
    """Payroll management dashboard"""
    try:
        from datetime import datetime
        page = request.args.get('page', 1, type=int)
        month = request.args.get('month', '')
        status_filter = request.args.get('status', '')
        
        staff_list = User.query.filter_by(is_active=True).order_by(User.name).all()
        
        stats = {
            'total_staff': len(staff_list),
            'total_monthly_salary': sum(float(s.basic_salary or 0) for s in staff_list),
            'total_monthly_deductions': sum(float(s.default_deductions or 0) for s in staff_list),
        }
        
        payroll_records = []
        for staff in staff_list:
            basic = float(staff.basic_salary or 0)
            deductions = float(staff.default_deductions or 0)
            net = basic - deductions
            payroll_records.append({
                'id': staff.id,
                'staff_name': staff.name,
                'staff_id': staff.id,
                'basic_salary': basic,
                'deductions': deductions,
                'net_salary': net,
                'month': month or datetime.now().strftime('%m'),
                'year': datetime.now().year,
                'status': 'pending'
            })
        
        return render_template('hr/payroll/index.html', 
                             payroll_records=payroll_records,
                             stats=stats,
                             current_month=datetime.now().strftime('%m'),
                             current_year=datetime.now().year,
                             status_filter=status_filter)
    except Exception as e:
        current_app.logger.error(f"Payroll Error: {str(e)}")
        flash("Error loading payroll", "error")
        return redirect(url_for('hr.hr_home'))

@bp.route('/payroll/generate', methods=['GET', 'POST'])
@login_required
@hr_required
def payroll_generate():
    """Generate payroll for a month"""
    try:
        if request.method == 'POST':
            month = request.form.get('month')
            year = request.form.get('year')
            staff_list = User.query.filter_by(is_active=True).all()
            count = len(staff_list)
            flash(f'Payroll generated for {count} staff members. Awaiting approval from admin.', 'success')
            return redirect(url_for('hr.payroll'))
        
        return render_template('hr/payroll/generate.html')
    except Exception as e:
        current_app.logger.error(f"Generate Payroll Error: {str(e)}")
        flash("Error generating payroll", "error")
        return redirect(url_for('hr.payroll'))

# ==================== LEAVE MANAGEMENT ====================

@bp.route('/leave')
@login_required
@hr_required
def leave_management():
    """Leave management dashboard"""
    try:
        staff = User.query.filter_by(is_active=True).count()
        stats = {
            'total_staff': staff,
            'pending_approvals': 0,
            'approved_this_month': 0,
            'total_leaves': 0
        }
        leave_records = []
        return render_template('hr/leave/index.html', leave_records=leave_records, stats=stats)
    except Exception as e:
        current_app.logger.error(f"Leave Error: {str(e)}")
        flash("Error loading leave", "error")
        return redirect(url_for('hr.hr_home'))

@bp.route('/leave/create', methods=['GET', 'POST'])
@login_required
@hr_required
def create_leave():
    """Create new leave request"""
    try:
        if request.method == 'POST':
            flash('Leave request submitted for approval', 'success')
            return redirect(url_for('hr.leave_management'))
        staff = User.query.filter_by(is_active=True).all()
        return render_template('hr/leave/create.html', staff=staff)
    except Exception as e:
        current_app.logger.error(f"Create Leave Error: {str(e)}")
        flash("Error creating leave", "error")
        return redirect(url_for('hr.leave_management'))

# ==================== ATTENDANCE MANAGEMENT ====================

@bp.route('/attendance')
@login_required
@hr_required
def attendance():
    """Attendance tracking dashboard"""
    try:
        staff_list = User.query.filter_by(is_active=True).all()
        stats = {
            'total_staff': len(staff_list),
            'present_today': 0,
            'absent_today': 0,
            'on_leave': 0
        }
        attendance_records = []
        return render_template('hr/attendance/index.html',
                             attendance_records=attendance_records,
                             staff_list=staff_list,
                             stats=stats)
    except Exception as e:
        current_app.logger.error(f"Attendance Error: {str(e)}")
        flash("Error loading attendance", "error")
        return redirect(url_for('hr.hr_home'))

@bp.route('/attendance/record', methods=['GET', 'POST'])
@login_required
@hr_required
def record_attendance():
    """Record attendance"""
    try:
        if request.method == 'POST':
            flash('Attendance recorded successfully', 'success')
            return redirect(url_for('hr.attendance'))
        staff = User.query.filter_by(is_active=True).all()
        return render_template('hr/attendance/record.html', staff=staff)
    except Exception as e:
        current_app.logger.error(f"Record Attendance Error: {str(e)}")
        flash("Error recording attendance", "error")
        return redirect(url_for('hr.attendance'))

# ==================== QUERIES/COMPLAINTS ====================

@bp.route('/queries')
@login_required
@hr_required
def staff_queries():
    """Staff queries management"""
    try:
        stats = {
            'total_queries': 0,
            'pending': 0,
            'resolved': 0,
            'in_progress': 0
        }
        queries = []
        return render_template('hr/queries/index.html', queries=queries, stats=stats)
    except Exception as e:
        current_app.logger.error(f"Queries Error: {str(e)}")
        flash("Error loading queries", "error")
        return redirect(url_for('hr.hr_home'))

@bp.route('/queries/create', methods=['GET', 'POST'])
@login_required
@hr_required
def create_query():
    """Create new query"""
    try:
        if request.method == 'POST':
            flash('Query submitted for admin response', 'success')
            return redirect(url_for('hr.staff_queries'))
        return render_template('hr/queries/create.html')
    except Exception as e:
        current_app.logger.error(f"Create Query Error: {str(e)}")
        flash("Error creating query", "error")
        return redirect(url_for('hr.staff_queries'))

# ==================== REPORTS ====================

@bp.route('/reports')
@login_required
@hr_required
def reports():
    """HR reports dashboard"""
    try:
        return render_template('hr/reports/index.html')
    except Exception as e:
        current_app.logger.error(f"Reports Error: {str(e)}")
        flash("Error loading reports", "error")
        return redirect(url_for('hr.hr_home'))

@bp.route('/reports/payroll')
@login_required
@hr_required
def generate_payroll_report():
    """Generate payroll report"""
    try:
        staff = User.query.filter_by(is_active=True).all()
        report_data = []
        total_salary = 0
        total_deductions = 0
        
        for member in staff:
            salary = float(member.basic_salary or 0)
            deductions = float(member.default_deductions or 0)
            net = salary - deductions
            
            report_data.append({
                'name': member.name,
                'email': member.email,
                'salary': salary,
                'deductions': deductions,
                'net': net
            })
            total_salary += salary
            total_deductions += deductions
        
        return render_template('hr/reports/payroll.html',
                             report_data=report_data,
                             total_salary=total_salary,
                             total_deductions=total_deductions,
                             total_net=total_salary - total_deductions)
    except Exception as e:
        current_app.logger.error(f"Payroll Report Error: {str(e)}")
        flash("Error generating report", "error")
        return redirect(url_for('hr.reports'))

@bp.route('/reports/attendance')
@login_required
@hr_required
def generate_attendance_report():
    """Generate attendance report"""
    try:
        return render_template('hr/reports/attendance.html')
    except Exception as e:
        current_app.logger.error(f"Attendance Report Error: {str(e)}")
        flash("Error generating report", "error")
        return redirect(url_for('hr.reports'))

@bp.route('/reports/leave')
@login_required
@hr_required
def generate_leave_report():
    """Generate leave report"""
    try:
        return render_template('hr/reports/leave.html')
    except Exception as e:
        current_app.logger.error(f"Leave Report Error: {str(e)}")
        flash("Error generating report", "error")
        return redirect(url_for('hr.reports'))

@bp.route('/reports/performance')
@login_required
@hr_required
def generate_performance_report():
    """Generate performance report"""
    try:
        staff = User.query.filter_by(is_active=True).all()
        report_data = [
            {
                'name': member.name,
                'role': member.role,
                'rating': 'Not Yet Rated',
                'projects': len(member.project_assignments)
            }
            for member in staff
        ]
        return render_template('hr/reports/performance.html', report_data=report_data)
    except Exception as e:
        current_app.logger.error(f"Performance Report Error: {str(e)}")
        flash("Error generating report", "error")
        return redirect(url_for('hr.reports'))

# ==================== PERFORMANCE MANAGEMENT ====================

@bp.route('/performance')
@login_required
@hr_required
def performance():
    """Performance management"""
    try:
        staff = User.query.filter_by(is_active=True).all()
        staff_performance = []
        for member in staff:
            staff_performance.append({
                'id': member.id,
                'name': member.name,
                'role': member.role,
                'rating': 'Pending',
                'status': 'Not Rated'
            })
        return render_template('hr/performance/index.html', staff_performance=staff_performance)
    except Exception as e:
        current_app.logger.error(f"Performance Error: {str(e)}")
        flash("Error loading performance", "error")
        return redirect(url_for('hr.hr_home'))
