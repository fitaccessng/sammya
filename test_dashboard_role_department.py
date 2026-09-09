from app.utils import resolve_dashboard_endpoint_for_role


def test_department_aware_dashboard_endpoint_selection():
    assert resolve_dashboard_endpoint_for_role('hr_staff', 'HR') == 'hr.hr_home'
    assert resolve_dashboard_endpoint_for_role('project_staff', 'Projects') == 'project.dashboard'
    assert resolve_dashboard_endpoint_for_role('finance_manager', 'Finance') == 'finance.finance_home'
