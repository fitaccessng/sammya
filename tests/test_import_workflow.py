from io import BytesIO
import json

from openpyxl import Workbook
import pytest

from app.factory import create_app
from app.models import (
    AuditLog,
    BankAccount,
    BankStatementImport,
    BankTransaction,
    ImportJob,
    ImportJobFile,
    ImportMappingLearning,
    ImportTemplate,
    Inventory,
    Milestone,
    Project,
    ProjectStaff,
    PurchaseOrder,
    PurchaseOrderItem,
    Vendor,
    User,
    db,
)


def _app_with_users():
    app = create_app('development')
    app.config.update(
        TESTING=True,
        WTF_CSRF_ENABLED=False,
        SQLALCHEMY_DATABASE_URI='sqlite:///:memory:',
    )
    with app.app_context():
        db.drop_all()
        db.create_all()
        finance = User(name='Finance Manager', email='import-finance@example.com', role='finance_manager')
        finance.set_password('password')
        procurement = User(name='Procurement Manager', email='import-procurement@example.com', role='procurement_manager')
        procurement.set_password('password')
        unauthorized = User(name='Project Staff', email='import-staff@example.com', role='project_staff')
        unauthorized.set_password('password')
        project_manager = User(name='Project Manager', email='import-pm@example.com', role='project_manager')
        project_manager.set_password('password')
        qs_manager = User(name='QS Manager', email='import-qs@example.com', role='qs_manager')
        qs_manager.set_password('password')
        account = BankAccount(
            account_name='Operations Account', account_number='IMPORT-001',
            bank_name='Test Bank', balance=1000, currency='NGN', is_active=True,
        )
        project = Project(name='Import Project', budget=100000, project_manager=project_manager)
        db.session.add_all([finance, procurement, unauthorized, project_manager, qs_manager, account, project])
        db.session.commit()
        project.project_manager_id = project_manager.id
        db.session.commit()
        db.session.add(ProjectStaff(
            user_id=qs_manager.id, project_id=project.id, role='QS Manager', is_active=True,
        ))
        db.session.commit()
        ids = {
            'finance': finance.id,
            'procurement': procurement.id,
            'unauthorized': unauthorized.id,
            'project_manager': project_manager.id,
            'qs_manager': qs_manager.id,
            'account': account.id,
            'project': project.id,
        }
    return app, ids


def _mapping_data(analysis, explicit=None):
    explicit = explicit or {}
    data = {}
    for index, source in enumerate(analysis['headers']):
        data[f'mapping_{index}'] = explicit.get(source, analysis['mappings'][source].get('target') or '__ignore__')
    return data


def _login(client, user_id):
    with client.session_transaction() as session:
        session['_user_id'] = str(user_id)
        session['_fresh'] = True


def test_shared_finance_import_wizard_persists_and_audits_transactions():
    app, ids = _app_with_users()
    with app.test_client() as client:
        _login(client, ids['finance'])
        upload_page = client.get(
            f"/imports/new?module=finance&entity=bank_transaction&account_id={ids['account']}"
        )
        assert upload_page.status_code == 200
        assert b'Operations Account' in upload_page.data

        response = client.post('/imports/analyze', data={
            'module': 'finance',
            'entity': 'bank_transaction',
            'account_id': str(ids['account']),
            'import_file': (BytesIO(
                b'Tran Date,Particulars,DR,CR,Running Bal.,Ref\n'
                b'01/09/2026,Vendor payment,250,,750,REF-101\n'
            ), 'statement.csv'),
        }, content_type='multipart/form-data')
        assert response.status_code == 200, response.get_data(as_text=True)
        assert b'Confirm mapping and validate' in response.data

        with app.app_context():
            job = ImportJob.query.one()
            job_id = job.id
            analysis = json.loads(job.analysis_json)

        mapping_data = _mapping_data(analysis)
        response = client.post(f'/imports/{job_id}/mapping', data=mapping_data)
        assert response.status_code == 400
        assert b'Choose DD/MM/YYYY or MM/DD/YYYY' in response.data
        assert b'Row 2, Tran Date' in response.data
        mapping_data['date_format'] = 'DD/MM/YYYY'
        response = client.post(f'/imports/{job_id}/mapping', data=mapping_data)
        assert response.status_code == 200
        assert b'Import 1 records' in response.data

        response = client.post(f'/imports/{job_id}/confirm')
        assert response.status_code == 200
        assert b'Import result' in response.data

    with app.app_context():
        job = ImportJob.query.one()
        account = db.session.get(BankAccount, ids['account'])
        transaction = BankTransaction.query.one()
        assert job.status == 'completed'
        assert job.records_imported == 1
        assert transaction.account_id == ids['account']
        assert transaction.bank_statement_import_id is not None
        assert transaction.reference_number == 'REF-101'
        assert float(account.balance) == 750
        assert BankStatementImport.query.count() == 1
        assert AuditLog.query.filter_by(entity_type='import_job', action='import_confirmed').count() == 1


def test_finance_partial_import_skips_invalid_rows_with_structured_errors():
    app, ids = _app_with_users()
    with app.test_client() as client:
        _login(client, ids['finance'])
        response = client.post('/imports/analyze', data={
            'module': 'finance',
            'entity': 'bank_transaction',
            'account_id': str(ids['account']),
            'import_file': (BytesIO(
                b'Date,Description,Debit,Credit,Reference\n'
                b'01/09/2026,Valid fee,25,,VALID-1\n'
                b'01/09/2026,Invalid fee,TBD,,INVALID-1\n'
            ), 'partial.csv'),
        }, content_type='multipart/form-data')
        assert response.status_code == 200
        with app.app_context():
            job = ImportJob.query.one()
            job_id = job.id
            analysis = json.loads(job.analysis_json)
        invalid = next(row for row in analysis['rows'] if row['errors'])
        assert invalid['errors'][0]['code'] == 'INVALID_NUMBER'
        assert invalid['errors'][0]['column'] == 'Debit'
        assert invalid['errors'][0]['value'] == 'TBD'
        mapping_data = _mapping_data(analysis)
        mapping_data['date_format'] = 'DD/MM/YYYY'
        response = client.post(f'/imports/{job_id}/mapping', data=mapping_data)
        assert response.status_code == 200
        response = client.post(f'/imports/{job_id}/confirm')
        assert response.status_code == 200

    with app.app_context():
        job = ImportJob.query.one()
        assert job.status == 'partially_imported'
        assert job.records_imported == 1
        assert job.records_skipped == 1
        assert BankTransaction.query.count() == 1


def test_finance_multi_file_batch_uses_shared_mapping_and_keeps_file_results():
    app, ids = _app_with_users()
    with app.test_client() as client:
        _login(client, ids['finance'])
        response = client.post('/imports/analyze', data={
            'module': 'finance',
            'entity': 'bank_transaction',
            'account_id': str(ids['account']),
            'import_files': [
                (BytesIO(b'Date,Description,Debit,Credit,Reference\n15/09/2026,Monthly fee,10,,BATCH-1\n'), 'september.csv'),
                (BytesIO(b'Transaction Date,Particulars,Withdrawal,Deposit,Transaction Reference\n16/09/2026,Client payment,,40,BATCH-2\n'), 'october.csv'),
            ],
        }, content_type='multipart/form-data')
        assert response.status_code == 200
        assert b'2 files' in response.data
        assert b'september.csv' in response.data
        assert b'october.csv' in response.data

        with app.app_context():
            job = ImportJob.query.one()
            children = list(job.files)
            assert len(children) == 2
            job_id = job.id
            analysis = json.loads(job.analysis_json)

        mapping_data = _mapping_data(analysis)
        mapping_data['date_format'] = 'DD/MM/YYYY'
        response = client.post(f'/imports/{job_id}/mapping', data=mapping_data)
        assert response.status_code == 200
        assert b'Import 2 records' in response.data
        response = client.post(f'/imports/{job_id}/confirm')
        assert response.status_code == 200
        assert b'september.csv' in response.data
        assert b'october.csv' in response.data

    with app.app_context():
        job = ImportJob.query.one()
        children = list(job.files)
        assert job.records_imported == 2
        assert BankTransaction.query.count() == 2
        assert all(child.records_imported == 1 for child in children)
        file_results = [json.loads(child.result_json) for child in children]
        assert [item['file_name'] for item in file_results] == ['september.csv', 'october.csv']
        assert [item['records_imported'] for item in file_results] == [1, 1]


def test_excel_sheet_and_header_selection_reanalyzes_private_source(monkeypatch, tmp_path):
    app, ids = _app_with_users()
    monkeypatch.setenv('IMPORT_PRIVATE_STORAGE', str(tmp_path / 'private-imports'))
    workbook = Workbook()
    notes = workbook.active
    notes.title = 'Notes'
    notes.append(['Internal cover sheet'])
    transactions = workbook.create_sheet('Transactions')
    transactions.append(['September Statement'])
    transactions.append(['Generated 30 September 2026'])
    transactions.append(['Tran Date', 'Particulars', 'DR', 'CR', 'Reference'])
    transactions.append(['15/09/2026', 'Monthly fee', 10, None, 'XLSX-1'])
    output = BytesIO()
    workbook.save(output)

    with app.test_client() as client:
        _login(client, ids['finance'])
        response = client.post('/imports/analyze', data={
            'module': 'finance',
            'entity': 'bank_transaction',
            'account_id': str(ids['account']),
            'import_files': [(BytesIO(output.getvalue()), 'statement.xlsx')],
        }, content_type='multipart/form-data')
        assert response.status_code == 200
        assert b'Notes' in response.data
        assert b'Transactions' in response.data
        assert b'Select worksheet' in response.data

        with app.app_context():
            job = ImportJob.query.one()
            child = ImportJobFile.query.one()
            job_id, file_id = job.id, child.id
            assert child.storage_path.startswith(str(tmp_path))

        response = client.post(
            f'/imports/{job_id}/files/{file_id}/analyze',
            data={'sheet_name': 'Transactions', 'header_row': '3', 'date_format': 'DD/MM/YYYY'},
        )
        assert response.status_code == 200
        assert b'Transactions' in response.data
        assert b'Row 3' in response.data
        assert b'Tran Date' in response.data

    with app.app_context():
        job_file = db.session.get(ImportJobFile, file_id)
        analysis = json.loads(job_file.analysis_json)
        assert analysis['sheet_name'] == 'Transactions'
        assert analysis['header_row'] == 2
        assert analysis['headers'][0] == 'Tran Date'
        assert analysis['rows'][0]['data']['date'] == '2026-09-15'


def test_supplier_import_uses_vendor_model_and_explicitly_ignores_extra_column():
    app, ids = _app_with_users()
    with app.test_client() as client:
        _login(client, ids['procurement'])
        response = client.post('/imports/analyze', data={
            'module': 'procurement',
            'entity': 'supplier',
            'import_file': (BytesIO(
                b'Vendor Name,Contact Person,Phone,Email Address,Tax ID,City,Branch\n'
                b'Acme Works,Jordan,+2348000000000,accounts@acme.test,TAX-1,Lagos,North\n'
            ), 'suppliers.csv'),
        }, content_type='multipart/form-data')
        assert response.status_code == 200
        with app.app_context():
            job = ImportJob.query.one()
            job_id = job.id
            analysis = json.loads(job.analysis_json)

        mapping_data = _mapping_data(analysis, {'Branch': '__ignore__'})
        mapping_data['acknowledge_unsupported'] = 'yes'
        response = client.post(f'/imports/{job_id}/mapping', data=mapping_data)
        assert response.status_code == 200, response.get_data(as_text=True)
        assert b'Import 1 records' in response.data
        save_response = client.post('/imports/templates', data={
            'job_id': job_id,
            'name': 'Supplier Export Format',
        })
        assert save_response.status_code == 302
        with app.app_context():
            template_id = ImportTemplate.query.one().id
        response = client.post(f'/imports/{job_id}/confirm')
        assert response.status_code == 200

    with app.app_context():
        vendor = Vendor.query.filter_by(name='Acme Works').one()
        assert vendor.phone == '+2348000000000'
        assert vendor.contact_person == 'Jordan'
        assert vendor.email == 'accounts@acme.test'
        assert vendor.registration_number == 'TAX-1'
        assert vendor.city == 'Lagos'
        job = ImportJob.query.one()
        assert job.records_imported == 1
        template = ImportTemplate.query.one()
        assert job.template_id == template.id
        assert template.scope_key == 'procurement.supplier:global'
        assert json.loads(template.mapping_json)['Vendor Name'] == 'supplier_name'
        assert ImportMappingLearning.query.filter_by(
            module='procurement', entity='supplier', source_header='Vendor Name', target_field='supplier_name',
        ).one().confirmations >= 1

    with app.test_client() as client:
        _login(client, ids['procurement'])
        library = client.get('/imports/templates')
        assert library.status_code == 200
        assert b'Supplier Export Format' in library.data
        detail = client.get(f'/imports/templates/{template_id}')
        assert detail.status_code == 200
        assert b'Vendor Name' in detail.data
        use = client.get(f'/imports/templates/{template_id}/use')
        assert use.status_code == 302
        assert 'template_id=' in use.headers['Location']


def test_project_schedule_import_persists_supported_fields_to_milestone():
    app, ids = _app_with_users()
    with app.test_client() as client:
        _login(client, ids['procurement'])
        denied = client.get(
            f"/imports/new?module=project_management&entity=milestone&project_id={ids['project']}"
        )
        assert denied.status_code == 403

    with app.test_client() as client:
        _login(client, ids['project_manager'])
        response = client.post('/imports/analyze', data={
            'module': 'project_management',
            'entity': 'milestone',
            'project_id': str(ids['project']),
            'import_file': (BytesIO(
                b'WBS,Activity,Commencement,Completion,Current Status,% Complete,Assigned To\n'
                b'1.1,Foundation,01/09/2026,30/09/2026,Active,25,Jordan\n'
            ), 'schedule.csv'),
        }, content_type='multipart/form-data')
        assert response.status_code == 200, response.get_data(as_text=True)
        with app.app_context():
            job = ImportJob.query.one()
            job_id = job.id
            analysis = json.loads(job.analysis_json)
        mapping_data = _mapping_data(analysis)
        mapping_data['acknowledge_unsupported'] = 'yes'
        mapping_data['date_format'] = 'DD/MM/YYYY'
        response = client.post(f'/imports/{job_id}/mapping', data=mapping_data)
        assert response.status_code == 200
        assert b'Import 1 records' in response.data
        response = client.post(f'/imports/{job_id}/confirm')
        assert response.status_code == 200

    with app.app_context():
        milestone = Milestone.query.filter_by(project_id=ids['project']).one()
        assert milestone.name == 'Foundation'
        assert milestone.planned_start_date.isoformat() == '2026-09-01'
        assert milestone.planned_end_date.isoformat() == '2026-09-30'
        assert milestone.completion_percentage == 25
        assert milestone.status == 'in_progress'
        assert milestone.task_code == '1.1'
        assert milestone.assignee_name == 'Jordan'


def test_non_import_role_cannot_analyze_finance_files():
    app, ids = _app_with_users()
    with app.test_client() as client:
        _login(client, ids['unauthorized'])
        response = client.post('/imports/analyze', data={
            'module': 'finance',
            'entity': 'bank_transaction',
            'account_id': str(ids['account']),
            'import_file': (BytesIO(b'Date,Description,Credit\n01/09/2026,Receipt,10\n'), 'bank.csv'),
        }, content_type='multipart/form-data')
        assert response.status_code == 403


def test_import_catalog_and_history_render_for_authorized_module_role():
    app, ids = _app_with_users()
    with app.test_client() as client:
        _login(client, ids['finance'])
        catalog = client.get('/imports/')
        assert catalog.status_code == 200
        assert b'Bank Transaction' in catalog.data
        history = client.get('/imports/history')
        assert history.status_code == 200
        assert b'Import History' in history.data


def test_qs_project_exposes_project_scoped_import_history_and_templates():
    app, ids = _app_with_users()
    with app.app_context():
        other_project = Project(name='Other QS Project', budget=50000)
        db.session.add(other_project)
        db.session.flush()
        other_project_id = other_project.id

        db.session.add_all([
            ImportJob(
                import_code='IMP-PROJECT-A', module='quantity_surveying', entity='boq',
                schema_version='1', status='completed', file_name='alpha-boq.csv',
                file_type='csv', uploaded_by=ids['qs_manager'], project_id=ids['project'],
            ),
            ImportJob(
                import_code='IMP-PROJECT-B', module='quantity_surveying', entity='boq',
                schema_version='1', status='completed', file_name='other-boq.csv',
                file_type='csv', uploaded_by=ids['qs_manager'], project_id=other_project_id,
            ),
            ImportTemplate(
                name='Alpha BOQ format', module='quantity_surveying', entity='boq',
                schema_version='1', scope_key=f'quantity_surveying.boq:project:{ids["project"]}',
                source_headers_json='[]', mapping_json='{}', created_by=ids['qs_manager'],
            ),
            ImportTemplate(
                name='Other BOQ format', module='quantity_surveying', entity='boq',
                schema_version='1', scope_key=f'quantity_surveying.boq:project:{other_project_id}',
                source_headers_json='[]', mapping_json='{}', created_by=ids['qs_manager'],
            ),
        ])
        db.session.commit()

    with app.test_client() as client:
        _login(client, ids['qs_manager'])
        project_page = client.get(f'/qs/project/{ids["project"]}')
        assert project_page.status_code == 200
        assert b'Import History' in project_page.data
        assert b'Import Templates' in project_page.data
        assert f'/imports/new?module=quantity_surveying&amp;entity=boq&amp;project_id={ids["project"]}'.encode() in project_page.data

        history = client.get(f'/imports/history?project_id={ids["project"]}')
        templates = client.get(f'/imports/templates?project_id={ids["project"]}')
        forbidden_history = client.get(f'/imports/history?project_id={other_project_id}')

    assert history.status_code == 200
    assert b'alpha-boq.csv' in history.data
    assert b'other-boq.csv' not in history.data
    assert templates.status_code == 200
    assert b'Alpha BOQ format' in templates.data
    assert b'Other BOQ format' not in templates.data
    assert forbidden_history.status_code == 403


def test_qs_boq_import_persists_only_supported_existing_model_fields():
    app, ids = _app_with_users()
    with app.test_client() as client:
        _login(client, ids['qs_manager'])
        response = client.post('/imports/analyze', data={
            'module': 'quantity_surveying',
            'entity': 'boq',
            'project_id': str(ids['project']),
            'import_file': (BytesIO(
                b'Ref,Particulars,Unit,Measured Qty,Rate,Value,Contractor Note\n'
                b'A-10,Concrete,m3,10,5000,50000,Use grade C30\n'
            ), 'contractor-boq.csv'),
        }, content_type='multipart/form-data')
        assert response.status_code == 200
        with app.app_context():
            job = ImportJob.query.one()
            job_id = job.id
            analysis = json.loads(job.analysis_json)
        mapping_data = _mapping_data(analysis, {'Contractor Note': '__ignore__'})
        mapping_data['acknowledge_unsupported'] = 'yes'
        response = client.post(f'/imports/{job_id}/mapping', data=mapping_data)
        assert response.status_code == 200
        assert b'Import 1 records' in response.data
        assert client.post(f'/imports/{job_id}/confirm').status_code == 200

    with app.app_context():
        from app.models import BOQItem
        item = BOQItem.query.filter_by(project_id=ids['project']).one()
        assert item.description == 'Concrete'
        assert item.item_no == 'A-10'
        assert item.unit == 'm3'
        assert float(item.quantity) == 10
        assert float(item.unit_rate) == 5000
        assert float(item.amount) == 50000


def test_procurement_purchase_items_attach_only_to_selected_draft_order():
    app, ids = _app_with_users()
    with app.app_context():
        vendor = Vendor(name='Approved Supplier')
        db.session.add(vendor)
        db.session.flush()
        purchase_order = PurchaseOrder(
            project_id=ids['project'], vendor_id=vendor.id, po_number='PO-IMPORT-1',
            total_amount=0, approval_state='draft',
        )
        db.session.add(purchase_order)
        db.session.commit()
        purchase_order_id = purchase_order.id

    with app.test_client() as client:
        _login(client, ids['procurement'])
        response = client.post('/imports/analyze', data={
            'module': 'procurement',
            'entity': 'purchase_item',
            'purchase_order_id': str(purchase_order_id),
            'import_file': (BytesIO(
                b'Product,Quantity Required,Rate,Amount,Vendor,Expected Delivery\n'
                b'Safety boots,10,15000,150000,Approved Supplier,01/10/2026\n'
            ), 'po-items.csv'),
        }, content_type='multipart/form-data')
        assert response.status_code == 200
        with app.app_context():
            job = ImportJob.query.one()
            job_id = job.id
            analysis = json.loads(job.analysis_json)
        mapping_data = _mapping_data(analysis)
        mapping_data['date_format'] = 'DD/MM/YYYY'
        mapping_data['acknowledge_unsupported'] = 'yes'
        response = client.post(f'/imports/{job_id}/mapping', data=mapping_data)
        assert response.status_code == 200
        assert client.post(f'/imports/{job_id}/confirm').status_code == 200

    with app.app_context():
        line = PurchaseOrderItem.query.filter_by(po_id=purchase_order_id).one()
        assert line.description == 'Safety boots'
        assert float(line.quantity) == 10
        assert float(line.unit_rate) == 15000
        assert float(line.amount) == 150000
        assert line.expected_delivery_date.isoformat() == '2026-10-01'


def test_inventory_import_reports_model_limitations_and_persists_supported_values():
    app, ids = _app_with_users()
    with app.test_client() as client:
        _login(client, ids['procurement'])
        response = client.post('/imports/analyze', data={
            'module': 'inventory',
            'entity': 'stock_item',
            'import_file': (BytesIO(
                b'SKU,Product,Unit,Stock,Reorder Level,Cost Price,Warehouse\n'
                b'GLOVE-1,Work gloves,pair,50,10,500,Main Store\n'
            ), 'inventory.csv'),
        }, content_type='multipart/form-data')
        assert response.status_code == 200
        with app.app_context():
            job = ImportJob.query.one()
            job_id = job.id
            analysis = json.loads(job.analysis_json)
        assert analysis['missing_required'] == []
        mapping_data = _mapping_data(analysis)
        mapping_data['acknowledge_unsupported'] = 'yes'
        response = client.post(f'/imports/{job_id}/mapping', data=mapping_data)
        assert response.status_code == 200
        assert client.post(f'/imports/{job_id}/confirm').status_code == 200

    with app.app_context():
        item = Inventory.query.filter_by(item_description='Work gloves').one()
        assert item.sku == 'GLOVE-1'
        assert item.unit == 'pair'
        assert float(item.unit_cost) == 500
        assert item.warehouse_name == 'Main Store'
        assert float(item.quantity_on_hand) == 50
        assert float(item.reorder_level) == 10
