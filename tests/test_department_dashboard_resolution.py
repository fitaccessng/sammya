from app.utils import normalize_department, department_dashboard_endpoint


def test_department_endpoint_resolution_for_known_departments():
    assert department_dashboard_endpoint('HR') == 'hr.hr_home'
    assert department_dashboard_endpoint('Finance') == 'finance.finance_home'
    assert department_dashboard_endpoint('Procurement') == 'procurement.dashboard'
    assert department_dashboard_endpoint('Projects') == 'project.dashboard'


def test_department_endpoint_resolution_ignores_unknown_department():
    assert department_dashboard_endpoint('Unknown Department') is None
    assert normalize_department('unknown department') == 'Unknown Department'
