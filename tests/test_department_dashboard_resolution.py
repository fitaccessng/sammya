from app.utils import normalize_department, department_dashboard_endpoint


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
