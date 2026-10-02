from io import BytesIO

from openpyxl import Workbook
import pytest

from app.imports import UniversalImportService, get_import_schema
from app.finance.services import BankImportService


def _upload(text, filename='sample.csv'):
    uploaded = BytesIO(text.encode('utf-8'))
    uploaded.filename = filename
    return uploaded


def _analyze(text, module, entity):
    return UniversalImportService().analyze_file(
        _upload(text),
        get_import_schema(module, entity),
    )


def _targets(analysis):
    return {
        UniversalImportService.normalize_header(source): mapping['target']
        for source, mapping in analysis['mappings'].items()
    }


def test_bank_statement_aliases_are_contextual_across_external_formats():
    samples = [
        ('Date,Description,Debit,Credit,Balance\n01/09/2026,Salary,,500000,1000000\n', {
            'date': 'date', 'description': 'description', 'debit': 'debit',
            'credit': 'credit', 'balance': 'balance',
        }),
        ('Posting Date,Transaction Details,Withdrawal,Deposit,Closing Balance\n01/09/2026,Vendor,,250000,750000\n', {
            'posting date': 'date', 'transaction details': 'description',
            'withdrawal': 'debit', 'deposit': 'credit', 'closing balance': 'balance',
        }),
        ('Tran Date,Particulars,DR,CR,Running Bal.\n01/09/2026,Fee,50,,950\n', {
            'tran date': 'date', 'particulars': 'description', 'dr': 'debit',
            'cr': 'credit', 'running bal.': 'balance',
        }),
    ]

    for text, expected in samples:
        analysis = _analyze(text, 'finance', 'bank_transaction')
        actual = _targets(analysis)
        assert all(actual[UniversalImportService.normalize_header(key)] == value for key, value in expected.items())
        assert analysis['records_found'] == 1
        assert analysis['ready_count'] == 0
        assert analysis['rows'][0]['date_confirmation_required'] is True


def test_schema_context_resolves_same_header_differently():
    boq = _analyze(
        'Description,Unit,Qty,Rate,Amount\nConcrete,m3,10,5000,50000\n',
        'quantity_surveying', 'boq',
    )
    bank = _analyze(
        'Date,Description,Amount\n01/09/2026,Transfer,5000\n',
        'finance', 'bank_transaction',
    )

    assert _targets(boq)['rate'] == 'rate'
    assert _targets(boq)['amount'] == 'amount'
    assert _targets(bank)['amount'] == 'amount'
    assert boq['rows'][0]['data']['rate'] == 5000
    assert bank['rows'][0]['data']['amount'] == 5000


def test_supplier_and_project_schedule_aliases_map_to_schema_fields():
    supplier = _analyze(
        'Company,Representative,Telephone,E-mail,Office Address\nAcme,Sam,+2348000000000,sam@example.com,Lagos\n',
        'procurement', 'supplier',
    )
    schedule = _analyze(
        'PROJECT ABC\nMonthly schedule\nActivity Code,Activity Description,Commencement,Completion,Responsible Person,Current Status,% Complete\nT1,Pour concrete,01/09/2026,30/09/2026,Alex,Active,25%\n',
        'project_management', 'project_task',
    )

    supplier_targets = _targets(supplier)
    assert supplier_targets['company'] == 'supplier_name'
    assert supplier_targets['telephone'] == 'phone'
    assert supplier_targets['e mail'] == 'email'
    assert schedule['header_row'] == 2
    row = schedule['rows'][0]
    assert row['data']['task_name'] == 'Pour concrete'
    assert row['data']['progress'] == 25
    assert row['original']['% Complete'] == '25%'


def test_boq_currency_values_normalize_and_bad_values_are_row_errors():
    analysis = _analyze(
        'Item No,Work Description,UOM,Measured Qty,Unit Rate,Value\n'
        'A1,Concrete,m3,"1,500","₦5,000","₦7,500,000"\n'
        'A2,Steel,kg,TBD,1200,1000\n',
        'quantity_surveying', 'boq',
    )

    assert analysis['rows'][0]['original']['Value'] == '₦7,500,000'
    assert analysis['rows'][0]['data']['quantity'] == 1500
    assert analysis['rows'][0]['data']['rate'] == 5000
    assert analysis['rows'][0]['data']['amount'] == 7500000
    assert analysis['rows'][1]['errors'] == [{
        'row': 3,
        'column': 'Measured Qty',
        'field': 'quantity',
        'value': 'TBD',
        'code': 'INVALID_NUMBER',
        'message': 'Invalid number: TBD',
    }]
    assert analysis['error_count'] == 1


def test_medium_confidence_mapping_requires_confirmation():
    analysis = _analyze(
        'Date,Description,Transaction Value\n01/09/2026,Transfer,5000\n',
        'finance', 'bank_transaction',
    )

    assert analysis['confirmation_required'] == ['Transaction Value']
    assert analysis['ready_count'] == 0
    confirmed = UniversalImportService().analyze_file(
        _upload('Date,Description,Transaction Value\n01/09/2026,Transfer,5000\n'),
        get_import_schema('finance', 'bank_transaction'),
        mapping_overrides={'Transaction Value': 'amount'},
    )
    assert confirmed['confirmation_required'] == []
    assert confirmed['ready_count'] == 0
    assert confirmed['rows'][0]['date_confirmation_required'] is True


def test_normalizes_common_number_formats_and_safe_unit_synonyms():
    service = UniversalImportService()
    assert service._normalize_value('₦1,500.00', 'number') == 1500
    assert service._normalize_value('1.500,50', 'number') == 1500.5
    assert service._normalize_value('1,5', 'number') == 1.5
    assert service._normalize_value('1.250.000', 'number') == 1250000
    assert service._normalize_value('square metre', 'unit') == 'm2'
    assert service._normalize_value('metres', 'unit') == 'm'


def test_duplicate_detection_uses_entity_schema_rules():
    analysis = _analyze(
        'Company,Representative,Telephone,E-mail\n'
        'Acme,Sam,+2348000000000,sam@example.com\n'
        'Acme Trading,Jo,+2348000000000,jo@example.com\n',
        'procurement', 'supplier',
    )

    assert analysis['duplicate_count'] == 1
    assert analysis['rows'][0]['potential_duplicate'] is False
    assert analysis['rows'][1]['potential_duplicate'] is True


def test_procurement_purchase_and_inventory_schemas_map_contextual_aliases():
    procurement = _analyze(
        'Product,Quantity Required,Rate,Amount,Vendor,Expected Delivery\n'
        'Safety boots,10,15000,150000,Acme,01/10/2026\n',
        'procurement', 'purchase_item',
    )
    inventory = _analyze(
        'Item Code,Item Name,Stock,Cost Price,Location\nSKU-1,Gloves,50,500,Main Store\n',
        'inventory', 'stock_item',
    )

    assert _targets(procurement)['rate'] == 'unit_price'
    assert procurement['rows'][0]['data']['amount'] == 150000
    assert _targets(inventory)['stock'] == 'quantity'
    assert inventory['rows'][0]['data']['sku'] == 'SKU-1'


def test_bank_import_adapter_normalizes_alternate_headers_to_transaction_rows():
    uploaded = _upload(
        'Posting Date,Transaction Details,Withdrawal,Deposit,Closing Balance\n'
        '01/09/2026,Vendor payment,"₦25,000",,975000\n'
    )

    rows = BankImportService().parse_uploaded_file(uploaded, account_id=1)

    assert rows[0]['date'] == '2026-09-01'
    assert rows[0]['description'] == 'Vendor payment'
    assert rows[0]['amount'] == 25000
    assert rows[0]['transaction_type'] == 'debit'
    assert rows[0]['original']['Withdrawal'] == '₦25,000'


def test_excel_sheet_selection_is_explicit_and_validated():
    workbook = Workbook()
    first = workbook.active
    first.title = 'Summary'
    first.append(['Generated for review'])
    second = workbook.create_sheet('Transactions')
    second.append(['Posting Date', 'Transaction Details', 'Deposit'])
    second.append(['01/09/2026', 'Transfer', 100])
    content = BytesIO()
    workbook.save(content)
    uploaded = BytesIO(content.getvalue())
    uploaded.filename = 'statement.xlsx'
    service = UniversalImportService()
    schema = get_import_schema('finance', 'bank_transaction')

    selected = service.analyze_file(uploaded, schema, sheet_name='Transactions')
    assert selected['sheet_name'] == 'Transactions'
    assert selected['records_found'] == 1

    invalid_upload = BytesIO(content.getvalue())
    invalid_upload.filename = 'statement.xlsx'
    with pytest.raises(ValueError, match="Worksheet 'Missing'"):
        service.analyze_file(invalid_upload, schema, sheet_name='Missing')