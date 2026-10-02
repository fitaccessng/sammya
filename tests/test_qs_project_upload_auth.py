from io import BytesIO

from openpyxl import Workbook

from app.factory import create_app
from app.models import BOQItem, Project, ProjectStaff, User, db


def _create_app_and_db():
    app = create_app('development')
    app.config.update(
        TESTING=True,
        WTF_CSRF_ENABLED=False,
        SQLALCHEMY_DATABASE_URI='sqlite:///:memory:',
    )
    with app.app_context():
        db.drop_all()
        db.create_all()
    return app


def test_qs_manager_can_upload_to_assigned_project():
    app = _create_app_and_db()
    with app.app_context():
        project = Project(name='Alpha', budget=1000)
        db.session.add(project)
        db.session.flush()

        qs_user = User(name='QS Manager', email='qs@example.com', role='qs_manager')
        qs_user.set_password('password')
        db.session.add(qs_user)
        db.session.flush()

        db.session.add(ProjectStaff(user_id=qs_user.id, project_id=project.id, role='QS Manager', is_active=True))
        db.session.commit()
        qs_user_id = qs_user.id
        project_id = project.id

    with app.test_client() as client:
        with client.session_transaction() as session:
            session['_user_id'] = str(qs_user_id)
            session['_fresh'] = True

        response = client.post(
            f'/projects/{project_id}/documents/upload',
            data={
                'title': 'BOQ Upload',
                'document_type': 'XLSX',
                'file': (BytesIO(b'not really an xlsx file'), 'LABOR ONLY FOR GATE HOUSE.xlsx'),
            },
            content_type='multipart/form-data',
        )

        assert response.status_code == 302
        assert f'/projects/{project_id}/documents' in response.headers['Location']


def test_qs_manager_cannot_upload_to_unassigned_project():
    app = _create_app_and_db()
    with app.app_context():
        project = Project(name='Beta', budget=1000)
        db.session.add(project)
        db.session.flush()

        qs_user = User(name='QS Manager', email='qs2@example.com', role='qs_manager')
        qs_user.set_password('password')
        db.session.add(qs_user)
        db.session.commit()
        qs_user_id = qs_user.id
        project_id = project.id

    with app.test_client() as client:
        with client.session_transaction() as session:
            session['_user_id'] = str(qs_user_id)
            session['_fresh'] = True

        response = client.post(
            f'/projects/{project_id}/documents/upload',
            data={
                'file': (BytesIO(b'bad'), 'Other.xlsx'),
            },
            content_type='multipart/form-data',
        )

        assert response.status_code == 403


def test_admin_can_upload_to_project():
    app = _create_app_and_db()
    with app.app_context():
        project = Project(name='Gamma', budget=1000)
        db.session.add(project)
        db.session.flush()

        admin_user = User(name='Admin User', email='admin@example.com', role='admin')
        admin_user.set_password('password')
        db.session.add(admin_user)
        db.session.commit()
        admin_user_id = admin_user.id
        project_id = project.id

    with app.test_client() as client:
        with client.session_transaction() as session:
            session['_user_id'] = str(admin_user_id)
            session['_fresh'] = True

        response = client.post(
            f'/projects/{project_id}/documents/upload',
            data={
                'title': 'Admin Upload',
                'document_type': 'PDF',
                'file': (BytesIO(b'pdf contents'), 'sample.pdf'),
            },
            content_type='multipart/form-data',
        )

        assert response.status_code == 302
        assert f'/projects/{project_id}/documents' in response.headers['Location']


def test_authenticated_project_user_can_upload_unrestricted_extension():
    app = _create_app_and_db()
    with app.app_context():
        project = Project(name='Delta', budget=2000)
        db.session.add(project)
        db.session.flush()

        engineer = User(name='Engineer', email='engineer@example.com', role='engineer')
        engineer.set_password('password')
        db.session.add(engineer)
        db.session.flush()

        db.session.add(
            ProjectStaff(user_id=engineer.id, project_id=project.id, role='Engineer', is_active=True)
        )
        db.session.commit()
        project_id = project.id
        engineer_id = engineer.id

    with app.test_client() as client:
        with client.session_transaction() as session:
            session['_user_id'] = str(engineer_id)
            session['_fresh'] = True

        response = client.post(
            f'/projects/{project_id}/documents/upload',
            data={
                'title': 'DWG Upload',
                'document_type': 'DWG',
                'file': (BytesIO(b'fake dwg content'), 'site_layout.dwg'),
            },
            content_type='multipart/form-data',
        )

        assert response.status_code == 302
        assert f'/projects/{project_id}/documents' in response.headers['Location']


def test_unassigned_user_is_denied_for_any_extension():
    app = _create_app_and_db()
    with app.app_context():
        project = Project(name='Epsilon', budget=3000)
        db.session.add(project)
        db.session.flush()
        project_id = project.id

        site_manager = User(name='Site Manager', email='site@example.com', role='site_manager')
        site_manager.set_password('password')
        db.session.add(site_manager)
        db.session.commit()
        site_manager_id = site_manager.id

    with app.test_client() as client:
        with client.session_transaction() as session:
            session['_user_id'] = str(site_manager_id)
            session['_fresh'] = True

        response = client.post(
            f'/projects/{project_id}/documents/upload',
            data={
                'title': 'Blocked Upload',
                'file': (BytesIO(b'bad'), 'blocked.zip'),
            },
            content_type='multipart/form-data',
        )

        assert response.status_code == 403


def test_project_manager_imports_boq_with_external_headers_into_project_model():
    app = _create_app_and_db()
    with app.app_context():
        project = Project(name='BOQ Import Project', budget=500000)
        manager = User(name='Project Manager', email='boq-manager@example.com', role='project_manager')
        manager.set_password('password')
        db.session.add_all([project, manager])
        db.session.commit()
        project_id = project.id
        manager_id = manager.id

    workbook = Workbook()
    sheet = workbook.active
    sheet.append(['Item No', 'Work Description', 'UOM', 'Measured Qty', 'Unit Rate', 'Value'])
    sheet.append(['A-01', 'Concrete works', 'm3', 10, 5000, 50000])
    file_content = BytesIO()
    workbook.save(file_content)
    file_content.seek(0)

    with app.test_client() as client:
        with client.session_transaction() as session:
            session['_user_id'] = str(manager_id)
            session['_fresh'] = True

        response = client.post(
            f'/projects/{project_id}/boq/import',
            data={'boq_file': (file_content, 'contractor-boq.xlsx')},
            content_type='multipart/form-data',
        )

        assert response.status_code == 302
        assert f'/projects/{project_id}' in response.headers['Location']

    with app.app_context():
        imported = BOQItem.query.filter_by(project_id=project_id).one()
        assert imported.description == 'Concrete works'
        assert imported.unit == 'm3'
        assert float(imported.quantity) == 10
        assert float(imported.unit_rate) == 5000
        assert float(imported.amount) == 50000
