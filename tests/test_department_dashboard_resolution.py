from pathlib import Path

from app.utils import normalize_department, department_dashboard_endpoint
from app.excel_import import StaffExcelParser


def test_admin_system_settings_template_has_a_single_content_block_closure():
    template_text = Path('app/templates/admin/system_settings.html').read_text()
    assert template_text.count('{% block content %}') == 1
    assert template_text.count('{% endblock %}') == 3
    assert template_text.rstrip().endswith('{% endblock %}')


def test_payroll_upload_staff_creation_does_not_use_a_different_default_password_marker():
    route_text = Path('app/hr/routes.py').read_text()
    assert 'TempPass123!' not in route_text


def test_staff_excel_default_password_is_shared_for_hr_created_accounts():
    assert StaffExcelParser.prepare_password('any.staff@example.com') == '12345678'


def test_department_endpoint_resolution_for_known_departments():
    assert department_dashboard_endpoint('HR') == 'hr.hr_home'
    assert department_dashboard_endpoint('Finance') == 'finance.finance_home'
    assert department_dashboard_endpoint('Procurement') == 'procurement.dashboard'
    assert department_dashboard_endpoint('Projects') == 'project.dashboard'


def test_department_endpoint_resolution_ignores_unknown_department():
    assert department_dashboard_endpoint('Unknown Department') is None
    assert normalize_department('unknown department') == 'Unknown Department'


def test_hr_analytics_data_builder_shape_and_defaults():
    from app.hr.routes import build_hr_analytics_data

    data = build_hr_analytics_data()

    assert 'total_employees' in data
    assert 'average_salary' in data
    assert 'attendance_rate' in data
    assert 'turnover_rate' in data
    assert 'department_stats' in data
    assert 'payroll_processed_count' in data
