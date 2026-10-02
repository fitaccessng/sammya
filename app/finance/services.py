"""Finance service helpers for bank statement import and validation."""

import csv
import io
import re
from datetime import datetime
from decimal import Decimal, InvalidOperation

from app.models import BankAccount, BankStatementImport, BankTransaction, db
from app.imports import UniversalImportService, get_import_schema


class BankImportService:
    """Parse bank statement files and insert valid bank transactions."""

    def parse_uploaded_file(self, file_storage, account_id=None, has_header=True):
        if file_storage is None or not getattr(file_storage, 'filename', ''):
            raise ValueError('Please select a valid bank statement file.')

        content = file_storage.read()
        if not content:
            raise ValueError('The uploaded file is empty.')

        name = (file_storage.filename or '').lower()
        if not any(name.endswith(ext) for ext in ['.csv', '.txt', '.ofx', '.qfx', '.qbo', '.pdf', '.jpg', '.png', '.heic']):
            raise ValueError('Unsupported file type. Use CSV, TXT, OFX, QFX, QBO, PDF, JPG, PNG, or HEIC.')

        if name.endswith('.csv') or name.endswith('.txt'):
            return self.parse_csv_text(content.decode('utf-8', errors='replace'), has_header=has_header)

        if name.endswith(('.ofx', '.qfx', '.qbo')):
            return self.parse_ofx_like_text(content.decode('utf-8', errors='replace'))

        raise ValueError('We couldn\'t read the transactions from this statement. Please upload a supported statement format or check that the file is not corrupted.')

    def parse_csv_text(self, csv_text, has_header=True):
        if has_header:
            return self._parse_mapped_csv(csv_text)

        text_stream = io.StringIO(csv_text)
        rows = []
        try:
            sample = csv_text[:4096]
            dialect = csv.Sniffer().sniff(sample, delimiters=',;\t|')
        except csv.Error:
            dialect = csv.excel

        try:
            reader = csv.DictReader(text_stream, dialect=dialect) if has_header else csv.reader(text_stream, dialect=dialect)
            for raw_row in reader:
                if hasattr(raw_row, 'keys'):
                    row = {str(k).strip().lower(): (v or '').strip() for k, v in raw_row.items() if k is not None}
                    if not any((str(v or '').strip()) for v in row.values()):
                        continue
                    normalized = self._normalize_csv_row(row)
                    if normalized.get('date') and normalized.get('description'):
                        rows.append(normalized)
                else:
                    if not raw_row:
                        continue
                    normalized = self._normalize_csv_row({
                        'date': raw_row[0] if len(raw_row) > 0 else '',
                        'description': raw_row[1] if len(raw_row) > 1 else '',
                        'reference': raw_row[2] if len(raw_row) > 2 else '',
                        'debit': raw_row[3] if len(raw_row) > 3 else '',
                        'credit': raw_row[4] if len(raw_row) > 4 else '',
                        'balance': raw_row[5] if len(raw_row) > 5 else '',
                    })
                    if normalized.get('date') and normalized.get('description'):
                        rows.append(normalized)
        except csv.Error as exc:
            raise ValueError(f'CSV parsing failed: {exc}') from exc

        return rows

    def _parse_mapped_csv(self, csv_text):
        """Use the shared schema-aware mapper for bank statement headers."""
        upload = io.BytesIO(csv_text.encode('utf-8'))
        upload.filename = 'bank-statement.csv'
        schema = get_import_schema('finance', 'bank_transaction')
        analysis = UniversalImportService().analyze_file(upload, schema)
        if analysis['missing_required']:
            missing = ', '.join(schema.field_map[name].label for name in analysis['missing_required'])
            raise ValueError(f'Required bank statement columns were not detected: {missing}.')
        if analysis['confirmation_required']:
            columns = ', '.join(analysis['confirmation_required'])
            raise ValueError(f'Please confirm uncertain bank statement column mappings: {columns}.')

        rows = []
        for parsed in analysis['rows']:
            values = parsed['data']
            debit = values.get('debit')
            credit = values.get('credit')
            amount = values.get('amount')
            if isinstance(debit, (int, float)) and debit:
                amount, transaction_type = abs(debit), 'debit'
            elif isinstance(credit, (int, float)) and credit:
                amount, transaction_type = abs(credit), 'credit'
            elif isinstance(amount, (int, float)):
                transaction_type = 'debit' if amount < 0 else 'credit'
                amount = abs(amount)
            else:
                amount, transaction_type = 0, 'other'
            rows.append({
                'date': values.get('date') or '',
                'description': values.get('description') or '',
                'reference_number': values.get('reference') or '',
                'amount': float(amount or 0),
                'transaction_type': transaction_type,
                'balance': float(values.get('balance') or 0)
                if isinstance(values.get('balance'), (int, float)) else 0,
                'row_number': parsed['row_number'],
                'original': parsed['original'],
                'errors': parsed['errors'],
            })
        return rows

    def _normalize_csv_row(self, row):
        amount_debit = self._get_numeric_value(row, ['debit', 'withdrawal', 'dr', 'debit amount'])
        amount_credit = self._get_numeric_value(row, ['credit', 'deposit', 'cr', 'credit amount'])
        amount = 0.0
        transaction_type = 'other'
        if amount_debit > 0:
            amount = float(amount_debit)
            transaction_type = 'debit'
        elif amount_credit > 0:
            amount = float(amount_credit)
            transaction_type = 'credit'
        elif self._get_numeric_value(row, ['amount', 'transaction amount']) > 0:
            amount = float(self._get_numeric_value(row, ['amount', 'transaction amount']))
            transaction_type = 'debit' if str(self._extract_value(row, ['amount', 'transaction amount'])).startswith('-') else 'credit'

        description = self._extract_value(row, ['narration', 'description', 'details', 'particulars', 'memo', 'transaction description'])
        if not description:
            description = self._extract_value(row, ['reference', 'ref']) or 'Bank transaction'
        reference_number = self._extract_value(row, ['reference', 'ref', 'transaction id', 'transaction_ref', 'transaction reference'])
        date_value = self._extract_date(row)

        return {
            'date': date_value,
            'description': str(description).strip() or 'Bank transaction',
            'reference_number': str(reference_number).strip(),
            'amount': float(amount or 0),
            'transaction_type': transaction_type,
            'balance': self._get_numeric_value(row, ['balance', 'running balance', 'available balance', 'closing balance']),
        }

    def _extract_value(self, row, keys):
        for key in keys:
            for row_key, value in row.items():
                if row_key and key in str(row_key).lower():
                    if value is not None and str(value).strip() != '':
                        return value
            if key in row and row[key] not in (None, ''):
                return row[key]
        return ''

    def _extract_date(self, row):
        for key_name in ['date', 'transaction date', 'posted date', 'value date', 'posting date']:
            value = self._extract_value(row, [key_name])
            if value:
                value = str(value).strip()
                for fmt in ['%Y-%m-%d', '%d/%m/%Y', '%d-%m-%Y', '%m/%d/%Y', '%m-%d-%Y', '%d %b %Y', '%d %B %Y', '%d/%m/%y', '%Y/%m/%d']:
                    try:
                        return datetime.strptime(value, fmt).strftime('%Y-%m-%d')
                    except ValueError:
                        continue
        return ''

    def _get_numeric_value(self, row, keys):
        value = self._extract_value(row, keys)
        if value in ('', None):
            return 0

        cleaned = str(value).replace(',', '').replace('₦', '').replace('NGN', '').replace('$', '').replace('€', '').replace('£', '').strip()
        cleaned = cleaned.replace(' ', '')
        if cleaned.startswith('(') and cleaned.endswith(')'):
            cleaned = '-' + cleaned[1:-1]
        if cleaned.startswith('+'):
            cleaned = cleaned[1:]
        try:
            return float(cleaned)
        except ValueError:
            return 0

    def parse_ofx_like_text(self, text):
        transactions = []
        lines = text.splitlines()
        current = {}
        for line in lines:
            stripped = line.strip()
            if not stripped:
                continue
            if stripped.startswith('<DTPOSTED>'):
                current['date'] = stripped.replace('<DTPOSTED>', '').replace('</DTPOSTED>', '').strip()
            elif stripped.startswith('<MEMO>'):
                current['description'] = stripped.replace('<MEMO>', '').replace('</MEMO>', '').strip()
            elif stripped.startswith('<TRNAMT>'):
                current['amount'] = stripped.replace('<TRNAMT>', '').replace('</TRNAMT>', '').strip()
            elif stripped.startswith('<NAME>'):
                current['reference'] = stripped.replace('<NAME>', '').replace('</NAME>', '').strip()
            elif stripped.startswith('</STMTTRN>'):
                if current.get('date') or current.get('amount'):
                    amount = self._coerce_decimal(current.get('amount') or '0')
                    tx_type = 'debit' if amount < 0 else 'credit'
                    transactions.append({
                        'date': self._normalize_ofx_date(current.get('date') or ''),
                        'description': current.get('description') or 'OFX transaction',
                        'reference_number': current.get('reference') or '',
                        'amount': abs(float(amount)),
                        'transaction_type': tx_type,
                        'balance': 0,
                    })
                current = {}
        return transactions

    def _coerce_decimal(self, value):
        if value in (None, ''):
            return Decimal('0')
        try:
            return Decimal(str(value).replace(',', '').replace('₦', '').strip())
        except (InvalidOperation, ValueError):
            return Decimal('0')

    def _normalize_ofx_date(self, value):
        clean = str(value).strip()
        if len(clean) >= 8 and clean.isdigit():
            try:
                return datetime.strptime(clean, '%Y%m%d').strftime('%Y-%m-%d')
            except ValueError:
                pass
        for fmt in ['%Y-%m-%d', '%d/%m/%Y', '%d-%m-%Y', '%m/%d/%Y']:
            try:
                return datetime.strptime(clean, fmt).strftime('%Y-%m-%d')
            except ValueError:
                continue
        return ''

    def import_transactions(self, account_id, rows, import_record=None):
        account = BankAccount.query.get(account_id)
        if not account:
            raise ValueError('Bank account not found.')

        imported = []
        for row in rows:
            date_value = str(row.get('date') or '').strip()
            description = str(row.get('description') or '').strip() or 'Bank transaction'
            amount = float(row.get('amount') or 0)
            if amount <= 0:
                continue
            reference = str(row.get('reference_number') or '').strip() or f"BANK-{account_id}-{len(imported) + 1}"
            if BankTransaction.query.filter_by(account_id=account_id, reference_number=reference).first():
                continue

            tx_type = str(row.get('transaction_type') or 'other').lower()
            if tx_type not in {'debit', 'credit'}:
                tx_type = 'debit' if amount < 0 else 'credit'

            if tx_type == 'debit':
                account.balance = float(account.balance or 0) - abs(amount)
            else:
                account.balance = float(account.balance or 0) + abs(amount)

            txn = BankTransaction(
                account_id=account_id,
                bank_statement_import_id=import_record.id if import_record else None,
                transaction_type=tx_type,
                amount=float(amount),
                description=description,
                reference_number=reference,
                transaction_date=datetime.strptime(date_value, '%Y-%m-%d') if date_value else datetime.utcnow(),
                balance_after=float(account.balance or 0),
                related_entity_type='bank_statement_import',
                related_entity_id=import_record.id if import_record else account_id,
                source='bank_statement_import',
                created_by=getattr(import_record, 'uploaded_by', None),
            )
            db.session.add(txn)
            imported.append({
                'account_id': account_id,
                'description': txn.description,
                'reference_number': txn.reference_number,
                'amount': float(txn.amount),
                'transaction_type': txn.transaction_type,
                'date': txn.transaction_date.isoformat() if txn.transaction_date else '',
            })
        return imported

    def build_import_record(self, account_id, uploaded_by, file_name, file_type, total_rows=0, status='uploaded', notes=''):
        record = BankStatementImport(
            bank_account_id=account_id,
            uploaded_by=uploaded_by,
            file_name=file_name,
            file_type=file_type,
            status=status,
            total_rows=total_rows,
            notes=notes,
        )
        db.session.add(record)
        db.session.flush()
        return record
