"""Real-browser acceptance coverage for the universal import wizard.

Run with:
    pytest tests/test_import_browser.py -q
"""

from io import BytesIO
import re
import threading
from contextlib import contextmanager

from playwright.sync_api import sync_playwright
from werkzeug.serving import make_server

from app.factory import create_app
from app.models import (
    BankAccount,
    BankTransaction,
    BOQItem,
    ImportJob,
    ImportJobFile,
    Project,
    ProjectStaff,
    User,
    db,
)


@contextmanager
def _browser_app(tmp_path, monkeypatch):
    database_path = tmp_path / 'browser-import.sqlite'
    monkeypatch.setenv('DATABASE_URL', f'sqlite:///{database_path}')
    monkeypatch.setenv('IMPORT_PRIVATE_STORAGE', str(tmp_path / 'private-imports'))
    app = create_app('development')
    app.config.update(TESTING=True, WTF_CSRF_ENABLED=False)
    with app.app_context():
        user = User(name='Finance Manager', email='browser-finance@example.test', role='finance_manager')
        user.set_password('browser-secret')
        qs_user = User(name='QS Manager', email='browser-qs@example.test', role='qs_manager')
        qs_user.set_password('browser-secret')
        account = BankAccount(
            account_name='Browser Operations', account_number='BROWSER-001',
            bank_name='Test Bank', balance=1000000, currency='NGN', is_active=True,
        )
        project = Project(name='Browser BOQ Project', budget=500000)
        db.session.add_all([user, qs_user, account, project])
        db.session.commit()
        db.session.add(ProjectStaff(
            user_id=qs_user.id, project_id=project.id, role='QS Manager', is_active=True,
        ))
        db.session.commit()
        ids = {'finance_user': user.id, 'qs_user': qs_user.id, 'account': account.id, 'project': project.id}

    server = make_server('127.0.0.1', 0, app, threaded=True)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield app, ids, f'http://127.0.0.1:{server.server_port}'
    finally:
        server.shutdown()
        thread.join(timeout=5)


def _login(page, base_url, email):
    page.goto(f'{base_url}/auth/login')
    page.get_by_label('Email Address').fill(email)
    page.get_by_label('Password').fill('browser-secret')
    page.get_by_role('button', name='Sign In').click()
    page.wait_for_load_state('domcontentloaded')


def _accept_confirm(page):
    page.on('dialog', lambda dialog: dialog.accept())


def test_browser_finance_mapping_template_and_multi_file_import(tmp_path, monkeypatch):
    with _browser_app(tmp_path, monkeypatch) as (app, ids, base_url):
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(headless=True)
            page = browser.new_page()
            _login(page, base_url, 'browser-finance@example.test')
            _accept_confirm(page)

            page.goto(f'{base_url}/finance/bank-transactions')
            page.get_by_role('link', name='Import Statement').click()
            page.get_by_label('Bank account').select_option(str(ids['account']))
            page.get_by_role('button', name='Continue').click()
            page.locator('#import_files').set_input_files({
                'name': 'bank-statement.csv',
                'mimeType': 'text/csv',
                'buffer': b'Date,Description,Transaction Value,Reference\n01/09/2026,Service charge,100,BROWSER-1\n',
            })
            page.get_by_role('button', name='Analyze file').click()
            page.get_by_label('Map Transaction Value').select_option('amount')
            assert page.get_by_text('Transaction Value').count() >= 1
            page.locator('input[name="date_format"][value="DD/MM/YYYY"]').check(force=True)
            page.get_by_label(
                'I reviewed these columns and acknowledge that unsupported values will not be stored in the destination record.'
            ).check(force=True)
            page.get_by_role('button', name='Confirm mapping and validate').click()
            assert '1 ready' in page.locator('body').inner_text()

            page.get_by_placeholder('Template name').fill('Browser bank mapping')
            page.get_by_role('button', name='Save mapping').click()
            page.get_by_role('button', name=re.compile('Import 1 records')).click()
            assert page.get_by_role('heading', name='Import result').is_visible()

            with app.app_context():
                assert BankTransaction.query.filter_by(reference_number='BROWSER-1').count() == 1
                template_id = ImportJob.query.order_by(ImportJob.id.desc()).first().template_id
                assert template_id is not None

            page.goto(f'{base_url}/imports/new?module=finance&entity=bank_transaction&account_id={ids["account"]}')
            page.locator('#import_files').set_input_files([
                {
                    'name': 'january.csv', 'mimeType': 'text/csv',
                    'buffer': b'Transaction Date,Narration,Amount,Reference\n15/09/2026,January fee,25,JAN-1\n',
                },
                {
                    'name': 'february.csv', 'mimeType': 'text/csv',
                    'buffer': b'Posting Date,Transaction Details,Withdrawal,Deposit,Ref\n16/09/2026,February fee,30,,FEB-1\n',
                },
            ])
            page.get_by_role('button', name='Analyze file').click()
            template_select = page.locator('#mapping-template')
            template_select.select_option(label='Browser bank mapping')
            assert 'Partial match' in page.locator('#template-match-status').inner_text()
            page.get_by_role('button', name='Confirm mapping and validate').click()
            assert '2 ready' in page.locator('body').inner_text()
            page.get_by_role('button', name=re.compile('Import 2 records')).click()
            assert page.get_by_role('heading', name='Import result').is_visible()
            assert page.get_by_text('january.csv').count() >= 1
            assert page.get_by_text('february.csv').count() >= 1

            with app.app_context():
                job = ImportJob.query.order_by(ImportJob.id.desc()).first()
                assert job.records_imported == 2
                assert ImportJobFile.query.filter_by(job_id=job.id).count() == 2
                assert BankTransaction.query.filter(BankTransaction.reference_number.in_(['JAN-1', 'FEB-1'])).count() == 2
            browser.close()


def test_browser_qs_reviews_amount_mismatch_then_imports_existing_boq(tmp_path, monkeypatch):
    with _browser_app(tmp_path, monkeypatch) as (app, ids, base_url):
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(headless=True)
            page = browser.new_page()
            _login(page, base_url, 'browser-qs@example.test')
            _accept_confirm(page)

            page.goto(f'{base_url}/qs/project/{ids["project"]}/boq', wait_until='domcontentloaded')
            page.get_by_role('link', name='Guided column mapping').click()
            page.locator('#import_files').set_input_files({
            'name': 'contractor-boq.csv',
            'mimeType': 'text/csv',
            'buffer': b'Item No,Work Description,UOM,Measured Qty,Rate,Value\nB-01,Concrete,m3,10,5000,45000\n',
            })
            page.get_by_role('button', name='Analyze file').click()
            page.get_by_role('button', name='Confirm mapping and validate').click()
            assert page.get_by_text(re.compile('differs from quantity x rate')).is_visible()
            assert page.get_by_role('button', name='Import 0 records').is_disabled()

            page.get_by_role('button', name='Back to mapping').click()
            page.locator('select[data-source="Value"]').select_option('__ignore__')
            page.get_by_role('button', name='Confirm mapping and validate').click()
            assert page.get_by_text('1 ready').count() == 1
            page.get_by_role('button', name=re.compile('Import 1 records')).click()
            assert page.get_by_role('heading', name='Import result').is_visible()

            with app.app_context():
                item = BOQItem.query.filter_by(project_id=ids['project']).one()
                assert item.item_no == 'B-01'
                assert float(item.amount) == 50000
            browser.close()
