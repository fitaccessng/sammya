from datetime import datetime
from io import BytesIO

from app.factory import create_app
from app.models import (
    ApprovalState,
    BankAccount,
    BankStatementImport,
    BankTransaction,
    Bill,
    BillPayment,
    PurchaseOrder,
    PurchaseOrderItem,
    User,
    Vendor,
    db,
)


def _build_app():
    app = create_app('development')
    app.config['TESTING'] = True
    app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///:memory:'
    with app.app_context():
        db.drop_all()
        db.create_all()

        user = User(
            name='Finance Manager',
            email='finance@example.com',
            role='finance_manager',
        )
        user.set_password('secret')
        db.session.add(user)

        vendor = Vendor(name='Nigerian Supplies', email='vendor@example.com')
        db.session.add(vendor)
        db.session.flush()

        po = PurchaseOrder(
            project_id=1,
            vendor_id=vendor.id,
            po_number='PO-1001',
            total_amount=500000,
            approval_state=ApprovalState.APPROVED,
            issued_at=datetime.utcnow(),
        )
        db.session.add(po)
        db.session.flush()

        item = PurchaseOrderItem(
            po_id=po.id,
            description='Cement Bags',
            quantity=10,
            unit_rate=50000,
            amount=500000,
        )
        db.session.add(item)

        account = BankAccount(
            account_name='Test Bank',
            account_number='1234567890',
            bank_name='Test Bank Plc',
            balance=1000000,
            currency='NGN',
        )
        db.session.add(account)
        db.session.commit()

        bill = Bill.create_from_purchase_order(po, created_by=user.id)
        db.session.add(bill)
        db.session.commit()

        return app, user.id, account.id, bill.id


def test_bill_created_from_approved_po_and_tracks_payment_status():
    app, _, account_id, bill_id = _build_app()

    with app.app_context():
        bill = db.session.get(Bill, bill_id)
        account = db.session.get(BankAccount, account_id)

        payment = BillPayment.create_payment(
            bill_id=bill.id,
            account_id=account.id,
            amount=150000,
            reference='PAY-001',
            created_by=1,
            notes='Initial payment',
        )
        db.session.commit()

        assert payment.amount == 150000
        assert bill.outstanding_amount == 350000
        assert bill.status == 'Partially Paid'
        assert BillPayment.query.filter_by(reference='PAY-001').count() == 1


def test_bank_transaction_model_tracks_debit_and_credit_balances():
    app, _, account_id, _ = _build_app()

    with app.app_context():
        account = db.session.get(BankAccount, account_id)
        tx = BankTransaction(
            account_id=account.id,
            transaction_type='debit',
            amount=25000,
            description='Supplier payment',
            reference_number='BT-001',
            created_by=1,
        )
        db.session.add(tx)
        db.session.commit()

        assert tx.transaction_type == 'debit'
        assert tx.amount == 25000
        assert BankTransaction.query.filter_by(reference_number='BT-001').count() == 1


def test_bank_statement_upload_wizard_acceptance_flow():
    app = create_app('development')
    app.config['TESTING'] = True
    app.config['WTF_CSRF_ENABLED'] = False
    app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///:memory:'

    with app.app_context():
        db.drop_all()
        db.create_all()

        user = User(
            name='Finance Manager',
            email='finance-flow@example.com',
            role='finance_manager',
        )
        user.set_password('secret')
        db.session.add(user)

        account = BankAccount(
            account_name='Sammya Alternative Bank',
            account_number='4567890123',
            bank_name='Alternative Bank',
            balance=1000000,
            currency='NGN',
            is_active=True,
        )
        db.session.add(account)
        db.session.commit()
        user_id = user.id
        account_id = account.id

    with app.test_client() as client:
        with client.session_transaction() as session:
            session['_user_id'] = str(user_id)
            session['_fresh'] = True

        get_response = client.get('/finance/bank-import')
        assert get_response.status_code == 200
        assert b'Bank Statement Import' in get_response.data

        upload_response = client.post(
            '/finance/bank-import',
            data={
                'account_id': account_id,
                'has_header': 'yes',
                'statement_file': (BytesIO(b'Date,Description,Debit,Credit,Balance\n01/09/2026,Salary,,500000,1000000\n02/09/2026,Vendor Payment,250000,,750000\n'), 'demo.csv'),
            },
            content_type='multipart/form-data',
        )

        assert upload_response.status_code == 200
        assert b'Transaction Preview' in upload_response.data
        assert b'Salary' in upload_response.data
        assert b'Vendor Payment' in upload_response.data

        commit_response = client.post('/finance/bank-import/commit')
        assert commit_response.status_code == 302
        assert '/finance/bank-transactions' in commit_response.headers['Location']

    with app.app_context():
        assert BankStatementImport.query.count() == 1
        assert BankTransaction.query.count() == 2
        assert BankTransaction.query.filter_by(description='Salary').count() == 1
        assert BankTransaction.query.filter_by(description='Vendor Payment').count() == 1
