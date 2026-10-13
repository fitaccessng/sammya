from datetime import datetime
from io import BytesIO
import pytest

from app.factory import create_app
from app.models import (
    AuditLog, Expense, PaymentRecord, Project, ProjectDocument, User, db,
    ensure_project_document_record_fields,
)
from sqlalchemy import inspect, text


def _document_app(upload_dir):
    app = create_app('development')
    app.config.update(
        TESTING=True,
        WTF_CSRF_ENABLED=False,
        SQLALCHEMY_DATABASE_URI='sqlite:///:memory:',
        FINANCE_DOCUMENT_UPLOAD_FOLDER=str(upload_dir),
    )
    with app.app_context():
        db.drop_all()
        db.create_all()
        finance_user = User(
            name='Finance User', email='docs-finance@example.test', role='finance_manager',
        )
        finance_user.set_password('password')
        project_staff = User(
            name='Project Staff', email='docs-staff@example.test', role='project_staff',
        )
        project_staff.set_password('password')
        expense = Expense(
            category='Travel', description='Pending travel expense', amount=125,
            date=datetime(2026, 10, 1), status='pending',
        )
        payment = PaymentRecord(
            amount_paid=250, payment_date=datetime(2026, 10, 2),
            payment_method='bank_transfer', reference_number='PAY-DOC-1',
        )
        db.session.add_all([finance_user, project_staff, expense, payment])
        db.session.commit()
        ids = {
            'finance': finance_user.id,
            'project_staff': project_staff.id,
            'expense': expense.id,
            'payment': payment.id,
        }
    return app, ids


@pytest.fixture
def document_app(tmp_path):
    return _document_app(tmp_path / 'finance_documents')


def _login(client, user_id):
    with client.session_transaction() as session:
        session['_user_id'] = str(user_id)
        session['_fresh'] = True


def test_upload_requires_record_and_persists_multiple_typed_documents(document_app):
    app, ids = document_app
    with app.test_client() as client:
        _login(client, ids['finance'])
        missing_record = client.post('/finance/upload', data={
            'source_module': 'finance',
            'title': 'Receipt',
            'files': (BytesIO(b'file'), 'receipt.pdf'),
        }, content_type='multipart/form-data')
        assert missing_record.status_code == 400

        response = client.post('/finance/upload', data={
            'source_module': 'finance',
            'source_record_type': 'expense',
            'source_record_id': str(ids['expense']),
            'title': 'Travel evidence',
            'document_type': 'Receipt',
            'description': 'Taxi and hotel receipts',
            'files': [
                (BytesIO(b'receipt one'), 'taxi.pdf'),
                (BytesIO(b'receipt two'), 'hotel.pdf'),
            ],
        }, content_type='multipart/form-data')
        assert response.status_code == 201
        assert len(response.json['file_ids']) == 2

        with app.app_context():
            documents = ProjectDocument.query.order_by(ProjectDocument.id).all()
            assert len(documents) == 2
            assert all(document.project_id is None for document in documents)
            assert all(document.source_module == 'finance' for document in documents)
            assert all(document.source_record_type == 'expense' for document in documents)
            assert all(document.source_record_id == ids['expense'] for document in documents)
            first_id = documents[0].id

        preview = client.get(f'/finance/uploads/{first_id}/view')
        assert preview.status_code == 200
        assert preview.data == b'receipt one'
        download = client.get(f'/finance/uploads/{first_id}/file')
        assert download.status_code == 200
        assert download.data == b'receipt one'

        update = client.post(f'/finance/uploads/{first_id}/manage', data={
            'action': 'update',
            'title': 'Updated travel evidence',
            'description': 'Updated description',
            'document_type': 'Invoice',
            'file': (BytesIO(b'replaced'), 'replacement.pdf'),
        }, content_type='multipart/form-data')
        assert update.status_code == 200
        with app.app_context():
            updated = db.session.get(ProjectDocument, first_id)
            assert updated.title == 'Updated travel evidence'
            assert updated.document_type == 'Invoice'
            assert updated.file_data == b'replaced'
            assert updated.version == 2


def test_record_upload_permissions_and_posted_attachment_lock(document_app):
    app, ids = document_app
    with app.test_client() as client:
        _login(client, ids['project_staff'])
        denied = client.post('/finance/upload', data={
            'source_module': 'finance',
            'source_record_type': 'expense',
            'source_record_id': str(ids['expense']),
            'title': 'Unauthorized',
            'files': (BytesIO(b'blocked'), 'blocked.pdf'),
        }, content_type='multipart/form-data')
        assert denied.status_code == 403

        _login(client, ids['finance'])
        response = client.post('/finance/upload', data={
            'source_module': 'finance',
            'source_record_type': 'payment_record',
            'source_record_id': str(ids['payment']),
            'title': 'Payment proof',
            'files': (BytesIO(b'proof'), 'proof.pdf'),
        }, content_type='multipart/form-data')
        assert response.status_code == 201
        document_id = response.json['file_ids'][0]
        legacy_response = client.post(
            f"/finance/payment/{ids['payment']}/upload-evidence",
            data={'proof_document': (BytesIO(b'legacy proof'), 'legacy-proof.pdf')},
            content_type='multipart/form-data',
        )
        assert legacy_response.status_code == 200
        assert legacy_response.json['document_id']
        locked = client.post(f'/finance/uploads/{document_id}/manage', data={
            'action': 'delete',
        })
        assert locked.status_code == 409
        with app.app_context():
            assert ProjectDocument.query.filter_by(
                source_record_type='payment_record', source_record_id=ids['payment'],
            ).count() == 2
            assert AuditLog.query.filter_by(entity_type='document', action='UPLOADED').count() == 2


def test_documents_library_filters_and_links_record_context(document_app):
    app, ids = document_app
    with app.test_client() as client:
        _login(client, ids['finance'])
        response = client.get(
            f"/finance/uploads?source_module=finance&source_record_type=expense&source_record_id={ids['expense']}&open_upload=1"
        )
        assert response.status_code == 200
        assert b'Attach to Record' in response.data
        assert b'sourceModule' in response.data


def test_transaction_ledger_links_documents_to_payment_record(document_app):
    app, ids = document_app
    with app.app_context():
        db.session.add(ProjectDocument(
            source_module='finance',
            source_record_type='payment_record',
            source_record_id=ids['payment'],
            title='Ledger payment evidence',
            file_name='evidence.pdf',
            file_data=b'evidence',
            uploaded_by_id=ids['finance'],
        ))
        db.session.commit()

    with app.test_client() as client:
        _login(client, ids['finance'])
        response = client.get('/finance/transactions')

    assert response.status_code == 200
    assert b'Ledger payment evidence' in response.data
    assert f"source_record_id={ids['payment']}".encode() in response.data
    assert b'source_record_type=payment_record' in response.data


def test_existing_project_documents_migrate_to_optional_typed_scope(tmp_path):
    app, _ = _document_app(tmp_path / 'finance_documents')
    with app.app_context():
        user = User(name='Uploader', email='migration@example.test', role='finance_manager')
        user.set_password('password')
        project = Project(name='Migration Project', budget=1000)
        db.session.add_all([user, project])
        db.session.commit()

        ProjectDocument.__table__.drop(bind=db.engine)
        with db.engine.begin() as connection:
            connection.execute(text(
                'CREATE TABLE project_document ('
                'id INTEGER PRIMARY KEY, project_id INTEGER NOT NULL, title VARCHAR(255) NOT NULL, '
                'description TEXT, document_type VARCHAR(100), file_path VARCHAR(500), file_data BLOB, '
                'file_name VARCHAR(255), uploaded_by_id INTEGER, version INTEGER, '
                'created_at DATETIME, updated_at DATETIME, '
                'FOREIGN KEY(project_id) REFERENCES project(id), '
                'FOREIGN KEY(uploaded_by_id) REFERENCES user(id))'
            ))
            connection.execute(text(
                "INSERT INTO project_document (id, project_id, title, file_name) "
                "VALUES (1, :project_id, 'Legacy project file', 'legacy.pdf')"
            ), {'project_id': project.id})

        ensure_project_document_record_fields()
        columns = {column['name']: column for column in inspect(db.engine).get_columns('project_document')}
        assert columns['project_id']['nullable'] is True
        assert {'source_module', 'source_record_type', 'source_record_id'} <= set(columns)
        migrated = db.session.get(ProjectDocument, 1)
        assert migrated.title == 'Legacy project file'
        assert migrated.project_id == project.id