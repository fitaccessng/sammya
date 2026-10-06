"""Shared, permission-checked import wizard and history endpoints."""

import json
import os
import tempfile
from datetime import datetime
from decimal import Decimal
from uuid import uuid4

from flask import Blueprint, abort, flash, redirect, render_template, request, url_for
from flask_login import current_user, login_required
from sqlalchemy import func, or_
from werkzeug.utils import secure_filename

from app.imports import UniversalImportService, get_import_schema
from app.models import (
    ApprovalState,
    AuditLog,
    BankAccount,
    BankStatementImport,
    BankTransaction,
    Bill,
    BOQItem,
    ImportJob,
    ImportJobFile,
    ImportMappingLearning,
    ImportTemplate,
    Inventory,
    Milestone,
    BankStatementImport,
    Project,
    ProjectStaff,
    PurchaseOrder,
    PurchaseOrderItem,
    Vendor,
    db,
)
from app.utils import normalize_role

bp = Blueprint('imports', __name__, url_prefix='/imports')
IMPORTER = UniversalImportService()
SCHEMA_VERSION = '1'


def _private_import_directory():
    root = os.path.abspath(os.environ.get('IMPORT_PRIVATE_STORAGE', os.path.join(tempfile.gettempdir(), 'sammyaerp-imports')))
    os.makedirs(root, mode=0o700, exist_ok=True)
    return root


def _save_private_upload(upload, job_code, ordinal):
    filename = secure_filename(upload.filename or '')
    if not filename:
        abort(400, 'The uploaded filename is invalid.')
    if request.content_length and request.content_length > 50 * 1024 * 1024:
        abort(413, 'Import uploads are limited to 50 MB per request.')
    directory = os.path.join(_private_import_directory(), job_code)
    os.makedirs(directory, mode=0o700, exist_ok=True)
    path = os.path.join(directory, f'{ordinal:03d}-{uuid4().hex}-{filename}')
    upload.save(path)
    os.chmod(path, 0o600)
    return filename, path, os.path.getsize(path)


def _learned_mappings(schema, scope_key):
    records = ImportMappingLearning.query.filter_by(
        module=schema.module,
        entity=schema.entity,
        schema_version=SCHEMA_VERSION,
        scope_key=scope_key,
    ).all()
    return {
        item.source_header_key: {'target': item.target_field, 'confirmations': item.confirmations}
        for item in records
    }


def _mapping_scope(schema, project_id=None, account_id=None, purchase_order_id=None):
    return _template_scope(schema.module, schema.entity, project_id, account_id, purchase_order_id)


def _combine_file_analyses(file_analyses):
    ready_files = [item for item in file_analyses
                   if not item.get('needs_sheet_selection') and item.get('compatible', True)
                   and not item.get('analysis_error')]
    if not ready_files:
        empty_view = dict(file_analyses[0]) if file_analyses else {}
        empty_view['file_analyses'] = file_analyses
        empty_view['needs_sheet_selection'] = any(item.get('needs_sheet_selection') for item in file_analyses)
        empty_view['incompatible_files'] = [item for item in file_analyses if not item.get('compatible', True) or item.get('analysis_error')]
        return empty_view
    combined = dict(ready_files[0])
    headers = list(dict.fromkeys(header for item in ready_files for header in item.get('headers', [])))
    mappings = {}
    for item in ready_files:
        mappings.update(item.get('mappings', {}))
    rows = []
    for item in ready_files:
        for row in item.get('rows', []):
            copied = dict(row)
            copied['source_file'] = item.get('filename')
            copied['file_id'] = item.get('file_id')
            rows.append(copied)
    combined.update({
        'headers': headers,
        'mappings': mappings,
        'rows': rows,
        'records_found': sum(item.get('records_found', 0) for item in ready_files),
        'ready_count': sum(item.get('ready_count', 0) for item in ready_files),
        'error_count': sum(item.get('error_count', 0) for item in ready_files),
        'duplicate_count': sum(item.get('duplicate_count', 0) for item in ready_files),
        'warning_count': sum(item.get('warning_count', 0) for item in ready_files),
        'file_analyses': file_analyses,
        'needs_sheet_selection': any(item.get('needs_sheet_selection') for item in file_analyses),
        'incompatible_files': [item for item in file_analyses if not item.get('compatible', True) or item.get('analysis_error')],
    })
    return combined


def _schema_for(module, entity):
    schema = get_import_schema(module, entity)
    role = normalize_role(current_user.role)
    if role not in schema.required_roles and role not in {'admin', 'super_hq'}:
        abort(403)
    return schema


def _template_scope(module, entity, project_id=None, account_id=None, purchase_order_id=None):
    if account_id:
        context = f'account:{account_id}'
    elif project_id:
        context = f'project:{project_id}'
    elif purchase_order_id:
        context = f'purchase_order:{purchase_order_id}'
    else:
        context = 'global'
    return f'{module}.{entity}:{context}'


def _validate_context(schema, project_id=None, account_id=None, purchase_order_id=None):
    project = None
    account = None
    purchase_order = None
    if project_id:
        project = Project.query.get(project_id)
        if project is None:
            abort(404)
        role = normalize_role(current_user.role)
        if role not in {'admin', 'super_hq'}:
            if role == 'project_manager' and project.project_manager_id != current_user.id:
                assigned_manager = ProjectStaff.query.filter_by(
                    project_id=project.id, user_id=current_user.id, is_active=True,
                ).filter(func.lower(ProjectStaff.role).like('%manager%')).first()
                if assigned_manager is None:
                    abort(403)
            elif role == 'project_staff':
                assigned = ProjectStaff.query.filter_by(
                    project_id=project.id, user_id=current_user.id, is_active=True,
                ).first()
                if assigned is None:
                    abort(403)
            elif role in {'qs_manager', 'qs_staff', 'quantity_surveyor'}:
                assigned = ProjectStaff.query.filter_by(
                    project_id=project.id, user_id=current_user.id, is_active=True,
                ).filter(func.lower(ProjectStaff.role).like('%qs%')).first()
                if assigned is None:
                    abort(403)
    if schema.module == 'finance':
        if not account_id:
            abort(400, 'Select a bank account before starting a bank transaction import.')
        account = BankAccount.query.filter_by(id=account_id, is_active=True).first()
        if account is None:
            abort(404)
    if schema.entity == 'purchase_item':
        if not purchase_order_id:
            abort(400, 'Purchase item imports must be associated with a draft purchase order.')
        purchase_order = PurchaseOrder.query.get(purchase_order_id)
        if purchase_order is None:
            abort(404)
        if purchase_order.approval_state != ApprovalState.DRAFT:
            abort(409, 'Items can only be imported into a draft purchase order.')
        if project and purchase_order.project_id != project.id:
            abort(403)
    if schema.entity in {'boq', 'milestone', 'project_task'} and not project_id:
        abort(400, 'Choose a project before importing project records.')
    return project, account, purchase_order


def _load_analysis(job):
    try:
        return json.loads(job.analysis_json or '{}')
    except (TypeError, ValueError):
        abort(409, 'This import preview is no longer available. Upload the file again.')


def _apply_saved_mapping(analysis, schema, saved_mapping):
    matched = {
        source: target for source, target in saved_mapping.items()
        if source in analysis.get('headers', [])
        and (target == '__ignore__' or target in schema.field_map)
    }
    analysis['template_match'] = {
        'matched': len(matched),
        'missing': [source for source in saved_mapping if source not in analysis.get('headers', [])],
        'new': [source for source in analysis.get('headers', []) if source not in saved_mapping],
    }
    if matched:
        analysis = IMPORTER.remap_analysis(analysis, schema, matched)
    return analysis


def _dump(value):
    return json.dumps(value, ensure_ascii=False, default=str, separators=(',', ':'))


def _add_row_error(row, field, message, code='BUSINESS_RULE'):
    row.setdefault('errors', []).append({
        'field': field,
        'code': code,
        'message': message,
    })


def _mark_database_duplicates(analysis, schema, context):
    for row in analysis.get('rows', []):
        if row.get('errors'):
            continue
        data = row['data']
        duplicate = False
        if schema.entity == 'bank_transaction':
            account_id = context['account_id']
            reference = str(data.get('reference') or '').strip()
            amount = abs(float(data.get('debit') or data.get('credit') or data.get('amount') or 0))
            date_value = data.get('date')
            if reference:
                duplicate = BankTransaction.query.filter_by(
                    account_id=account_id, reference_number=reference,
                ).first() is not None
            if not duplicate and date_value:
                tx_date = datetime.strptime(date_value, '%Y-%m-%d')
                duplicate = BankTransaction.query.filter(
                    BankTransaction.account_id == account_id,
                    BankTransaction.transaction_date == tx_date,
                    func.lower(BankTransaction.description) == str(data.get('description') or '').lower(),
                    BankTransaction.amount == amount,
                ).first() is not None
        elif schema.entity == 'supplier':
            terms = []
            if data.get('supplier_name'):
                terms.append(func.lower(Vendor.name) == str(data['supplier_name']).strip().lower())
            if data.get('email'):
                terms.append(func.lower(Vendor.email) == str(data['email']).strip().lower())
            if data.get('phone'):
                terms.append(Vendor.phone == str(data['phone']).strip())
            duplicate = Vendor.query.filter(or_(*terms)).first() is not None if terms else False
        elif schema.entity == 'boq':
            duplicate_query = BOQItem.query.filter(BOQItem.project_id == context['project_id'])
            if data.get('item_number'):
                duplicate_query = duplicate_query.filter(BOQItem.item_no == data['item_number'])
            else:
                duplicate_query = duplicate_query.filter(
                    func.lower(BOQItem.description) == str(data.get('description') or '').lower(),
                )
            duplicate = duplicate_query.first() is not None
        elif schema.entity == 'milestone' or schema.entity == 'project_task':
            duplicate = Milestone.query.filter(
                Milestone.project_id == context['project_id'],
                func.lower(Milestone.name) == str(data.get('task_name') or '').lower(),
            ).first() is not None
        elif schema.entity == 'stock_item':
            duplicate_query = Inventory.query.filter(Inventory.project_id == context.get('project_id'))
            if data.get('sku'):
                duplicate_query = duplicate_query.filter(Inventory.sku == data['sku'])
            else:
                duplicate_query = duplicate_query.filter(
                    func.lower(Inventory.item_description) == str(data.get('product') or '').lower(),
                )
            duplicate = duplicate_query.first() is not None
        elif schema.entity == 'purchase_item':
            duplicate = PurchaseOrderItem.query.filter(
                PurchaseOrderItem.po_id == context['purchase_order_id'],
                func.lower(PurchaseOrderItem.description) == str(data.get('item') or '').lower(),
            ).first() is not None
        if duplicate:
            row['potential_duplicate'] = True
            row.setdefault('warnings', []).append({
                'field': None, 'code': 'EXISTING_RECORD', 'message': 'A matching record already exists.',
            })


def _validate_domain_rows(analysis, schema, context):
    for row in analysis.get('rows', []):
        row['errors'] = [
            error for error in row.get('errors', [])
            if error.get('code') not in {'BUSINESS_RULE', 'AMOUNT_MISMATCH', 'UNRESOLVED_SUPPLIER'}
        ]
        data = row['data']
        if row['errors']:
            continue
        if schema.entity == 'bank_transaction':
            debit, credit = data.get('debit'), data.get('credit')
            if isinstance(debit, (int, float)) and isinstance(credit, (int, float)) and debit > 0 and credit > 0:
                _add_row_error(row, 'amount', 'A row cannot contain both a debit and a credit amount.')
        if schema.entity == 'boq':
            quantity, rate, amount = data.get('quantity'), data.get('rate'), data.get('amount')
            if isinstance(quantity, (int, float)) and isinstance(rate, (int, float)) and isinstance(amount, (int, float)):
                calculated = quantity * rate
                if abs(amount - calculated) > 0.01:
                    _add_row_error(row, 'amount', f'Uploaded amount {amount:,.2f} differs from quantity x rate ({calculated:,.2f}).', 'AMOUNT_MISMATCH')
        if schema.entity == 'purchase_item':
            quantity, rate, amount = data.get('quantity'), data.get('unit_price'), data.get('amount')
            if isinstance(quantity, (int, float)) and quantity <= 0:
                _add_row_error(row, 'quantity', 'Quantity must be greater than zero.')
            if isinstance(rate, (int, float)) and rate < 0:
                _add_row_error(row, 'unit_price', 'Unit price cannot be negative.')
            if isinstance(quantity, (int, float)) and isinstance(rate, (int, float)) and isinstance(amount, (int, float)):
                if abs(amount - quantity * rate) > 0.01:
                    _add_row_error(row, 'amount', 'Uploaded amount differs from quantity x unit price.', 'AMOUNT_MISMATCH')
            supplied_vendor = str(data.get('supplier') or '').strip()
            order_vendor = context.get('purchase_order').vendor if context.get('purchase_order') else None
            if supplied_vendor and (not order_vendor or supplied_vendor.lower() != order_vendor.name.lower()):
                _add_row_error(row, 'supplier', 'Supplier does not match the draft purchase order vendor.', 'UNRESOLVED_SUPPLIER')
        if schema.entity == 'stock_item':
            quantity = data.get('quantity')
            if isinstance(quantity, (int, float)) and quantity < 0:
                _add_row_error(row, 'quantity', 'Stock quantity cannot be negative.')
        if schema.entity in {'milestone', 'project_task'}:
            start, end = data.get('start_date'), data.get('end_date')
            if start and end and start > end:
                _add_row_error(row, 'end_date', 'End date must not precede start date.')
            if schema.entity == 'milestone':
                status = str(data.get('status') or '').strip().lower().replace('-', '_')
                allowed_statuses = {
                    'not_started', 'planned', 'pending', 'in_progress', 'active',
                    'ongoing', 'completed', 'complete', 'delayed',
                }
                if status and status not in allowed_statuses:
                    _add_row_error(row, 'status', f'Unsupported milestone status: {data.get("status")}.')
                progress = data.get('progress')
                if isinstance(progress, (int, float)) and not 0 <= progress <= 100:
                    _add_row_error(row, 'progress', 'Completion percentage must be between 0 and 100.')


def _decorate_analysis(analysis, schema, context):
    _validate_domain_rows(analysis, schema, context)
    _mark_database_duplicates(analysis, schema, context)
    analysis['error_count'] = sum(bool(row.get('errors')) for row in analysis.get('rows', []))
    analysis['duplicate_count'] = sum(bool(row.get('potential_duplicate')) for row in analysis.get('rows', []))
    analysis['warning_count'] = sum(len(row.get('warnings', [])) for row in analysis.get('rows', [])) + len(analysis.get('unsupported_columns', []))
    for row in analysis.get('rows', []):
        for error in row.get('errors', []):
            error.setdefault('row', row.get('row_number'))
            error.setdefault('column', next((
                source for source, mapping in analysis.get('mappings', {}).items()
                if mapping.get('target') == error.get('field')
            ), error.get('field')))
            error.setdefault('value', row.get('original', {}).get(error['column']))
            error.setdefault('code', 'BUSINESS_RULE')
    analysis['ready_count'] = sum(
        not row.get('errors') and not row.get('potential_duplicate') and not row.get('date_confirmation_required')
        for row in analysis.get('rows', [])
    ) if not analysis.get('missing_required') and not analysis.get('confirmation_required') and not analysis.get('date_confirmation_required') else 0
    return analysis


def _persist_job(job, analysis, schema, context):
    rows = [row for row in analysis['rows'] if not row.get('errors') and not row.get('potential_duplicate')]
    if not rows:
        raise ValueError('There are no valid, non-duplicate rows to import.')
    imported = 0
    bank_imports = {}

    for row in rows:
        data = row['data']
        if schema.entity == 'bank_transaction':
            file_id = row.get('file_id')
            if file_id not in bank_imports:
                source_record = db.session.get(ImportJobFile, file_id) if file_id else None
                bank_import = BankStatementImport(
                    bank_account_id=job.bank_account_id,
                    uploaded_by=current_user.id,
                    file_name=source_record.file_name if source_record else job.file_name,
                    file_type=source_record.file_type if source_record else job.file_type,
                    status='importing',
                    total_rows=source_record.records_found if source_record else analysis['records_found'],
                    notes=f'Universal import job {job.import_code}',
                )
                db.session.add(bank_import)
                db.session.flush()
                bank_imports[file_id] = bank_import
            amount = data.get('debit') or data.get('credit') or data.get('amount') or 0
            transaction_type = 'debit' if data.get('debit') else ('credit' if data.get('credit') else ('debit' if amount < 0 else 'credit'))
            tx = BankTransaction(
                account_id=job.bank_account_id,
                bank_statement_import_id=bank_imports[file_id].id,
                transaction_type=transaction_type,
                amount=abs(float(amount)),
                description=data['description'],
                reference_number=data.get('reference') or f'{job.import_code}-{row["row_number"]}',
                transaction_date=datetime.strptime(data['date'], '%Y-%m-%d'),
                balance_after=Decimal(str(data.get('balance') or 0)),
                related_entity_type='universal_import',
                related_entity_id=job.id,
                source='universal_import',
                created_by=current_user.id,
            )
            account = context['account']
            account.balance = float(account.balance or 0) + (abs(float(amount)) if transaction_type == 'credit' else -abs(float(amount)))
            db.session.add(tx)
        elif schema.entity == 'supplier':
            db.session.add(Vendor(
                name=data['supplier_name'],
                contact_person=data.get('contact_name'),
                email=data.get('email'),
                phone=data.get('phone'),
                address=data.get('address'),
                city=data.get('city'),
                registration_number=data.get('registration_number'),
                is_active=True,
            ))
        elif schema.entity == 'boq':
            item = BOQItem(
                project_id=job.project_id,
                item_no=data.get('item_number'),
                description=data['description'],
                unit=data['unit'],
                quantity=Decimal(str(data['quantity'])),
                unit_rate=Decimal(str(data['rate'])),
                amount=Decimal(str(data.get('amount') if data.get('amount') is not None else data['quantity'] * data['rate'])),
                created_by=current_user.id,
            )
            db.session.add(item)
        elif schema.entity == 'purchase_item':
            item = PurchaseOrderItem(
                po_id=job.purchase_order_id,
                description=data['item'],
                quantity=Decimal(str(data['quantity'])),
                unit_rate=Decimal(str(data['unit_price'])),
                amount=Decimal(str(data.get('amount') if data.get('amount') is not None else data['quantity'] * data['unit_price'])),
                expected_delivery_date=datetime.strptime(data['delivery_date'], '%Y-%m-%d').date() if data.get('delivery_date') else None,
            )
            db.session.add(item)
        elif schema.entity == 'stock_item':
            db.session.add(Inventory(
                project_id=job.project_id,
                sku=data.get('sku'),
                item_description=data['product'],
                unit=data.get('unit'),
                unit_cost=Decimal(str(data['unit_cost'])) if data.get('unit_cost') is not None else None,
                warehouse_name=data.get('warehouse'),
                quantity_on_hand=Decimal(str(data['quantity'])),
                reorder_level=Decimal(str(data['reorder_level'])) if data.get('reorder_level') is not None else None,
            ))
        elif schema.entity in {'milestone', 'project_task'}:
            data_start = data.get('start_date')
            data_end = data.get('end_date')
            status = str(data.get('status') or 'not_started').strip().lower().replace('-', '_')
            status = {
                'planned': 'not_started', 'pending': 'not_started',
                'active': 'in_progress', 'ongoing': 'in_progress',
                'complete': 'completed',
            }.get(status, status)
            db.session.add(Milestone(
                project_id=job.project_id,
                task_code=data.get('task_code'),
                assignee_name=data.get('assigned_to'),
                name=data['task_name'],
                description=data.get('description'),
                planned_start_date=datetime.strptime(data_start, '%Y-%m-%d').date() if data_start else None,
                planned_end_date=datetime.strptime(data_end, '%Y-%m-%d').date() if data_end else None,
                status=status,
                completion_percentage=int(data.get('progress') or 0),
            ))
        else:
            raise ValueError(f'No persistence handler is registered for {schema.module}/{schema.entity}.')
        imported += 1

    for file_id, bank_import in bank_imports.items():
        file_rows = [row for row in analysis['rows'] if row.get('file_id') == file_id]
        valid_rows = [row for row in file_rows if not row.get('errors') and not row.get('potential_duplicate')]
        bank_import.status = 'completed'
        bank_import.valid_rows = len(valid_rows)
        bank_import.imported_rows = len(valid_rows)
        bank_import.duplicate_rows = sum(bool(row.get('potential_duplicate')) for row in file_rows)
        bank_import.invalid_rows = sum(bool(row.get('errors')) for row in file_rows)
    return imported


def _get_job(job_id):
    job = ImportJob.query.get_or_404(job_id)
    schema = _schema_for(job.module, job.entity)
    if job.uploaded_by != current_user.id and normalize_role(current_user.role) not in {'admin', 'super_hq'}:
        abort(403)
    context = _validate_context(
        schema,
        project_id=job.project_id,
        account_id=job.bank_account_id,
        purchase_order_id=job.purchase_order_id,
    )
    return job, schema, {
        'project_id': job.project_id,
        'account_id': job.bank_account_id,
        'purchase_order_id': job.purchase_order_id,
        'project': context[0],
        'account': context[1],
        'purchase_order': context[2],
    }


@bp.route('/')
@login_required
def index():
    role = normalize_role(current_user.role)
    visible = [schema for schema in __import__('app.imports.schemas', fromlist=['SCHEMAS']).SCHEMAS.values()
               if role in schema.required_roles or role in {'admin', 'super_hq'}]
    return render_template('imports/index.html', schemas=visible)


@bp.route('/new')
@login_required
def new_import():
    module = request.args.get('module', '')
    entity = request.args.get('entity', '')
    schema = _schema_for(module, entity)
    project_id = request.args.get('project_id', type=int)
    account_id = request.args.get('account_id', type=int)
    purchase_order_id = request.args.get('purchase_order_id', type=int)
    template_id = request.args.get('template_id', type=int)
    selected_template = ImportTemplate.query.get_or_404(template_id) if template_id else None
    if selected_template:
        _template_access(selected_template)
        if selected_template.module != schema.module or selected_template.entity != schema.entity:
            abort(403)
    context_needed = (
        (schema.module == 'finance' and not account_id)
        or (schema.entity in {'boq', 'milestone', 'project_task'} and not project_id)
        or (schema.entity == 'purchase_item' and not purchase_order_id)
    )
    if context_needed:
        role = normalize_role(current_user.role)
        projects = Project.query.order_by(Project.name.asc()).all() if schema.entity in {'boq', 'milestone', 'project_task', 'stock_item'} else []
        accounts = BankAccount.query.filter_by(is_active=True).order_by(BankAccount.account_name.asc()).all() if schema.module == 'finance' else []
        purchase_orders = PurchaseOrder.query.filter_by(approval_state=ApprovalState.DRAFT).order_by(PurchaseOrder.created_at.desc()).all() if schema.entity == 'purchase_item' else []
        return render_template(
            'imports/wizard.html', state='context', schema=schema, analysis=None, job=None,
            context={'project_id': project_id, 'account_id': account_id, 'purchase_order_id': purchase_order_id,
                     'project': None, 'account': None, 'purchase_order': None},
            projects=projects, accounts=accounts, purchase_orders=purchase_orders,
            selected_template=selected_template,
        )
    project, account, purchase_order = _validate_context(schema, project_id, account_id, purchase_order_id)
    return render_template(
        'imports/wizard.html', state='upload', schema=schema, analysis=None, job=None,
        context={'project_id': project_id, 'account_id': account_id, 'purchase_order_id': purchase_order_id,
                 'project': project, 'account': account, 'purchase_order': purchase_order},
        selected_template=selected_template,
    )


@bp.route('/analyze', methods=['POST'])
@login_required
def analyze():
    module, entity = request.form.get('module', ''), request.form.get('entity', '')
    schema = _schema_for(module, entity)
    project_id = request.form.get('project_id', type=int)
    account_id = request.form.get('account_id', type=int)
    purchase_order_id = request.form.get('purchase_order_id', type=int)
    template_id = request.form.get('template_id', type=int)
    project, account, purchase_order = _validate_context(schema, project_id, account_id, purchase_order_id)
    files = [file for file in request.files.getlist('import_files') if file and file.filename]
    if not files:
        legacy_file = request.files.get('import_file')
        files = [legacy_file] if legacy_file and legacy_file.filename else []
    if not files:
        flash('Select a structured data file to continue.', 'danger')
        return redirect(url_for('imports.new_import', module=module, entity=entity,
                                project_id=project_id, account_id=account_id, purchase_order_id=purchase_order_id))
    file_sizes = []
    for source_file in files:
        source_file.stream.seek(0, os.SEEK_END)
        file_sizes.append(source_file.stream.tell())
        source_file.stream.seek(0)
    if sum(file_sizes) > 200 * 1024 * 1024:
        abort(413, 'The combined size of an import batch must not exceed 200 MB.')
    job_code = f"IMP-{datetime.utcnow().strftime('%Y%m%d%H%M%S%f')}"
    context = {
        'project_id': project_id, 'account_id': account_id, 'purchase_order_id': purchase_order_id,
        'project': project, 'account': account, 'purchase_order': purchase_order,
    }
    staged_files = []
    selected_template = ImportTemplate.query.get(template_id) if template_id else None
    if selected_template:
        _template_access(selected_template)
        if selected_template.module != module or selected_template.entity != entity:
            abort(403)
        expected_scope = _template_scope(module, entity, project_id, account_id, purchase_order_id)
        if selected_template.scope_key != expected_scope:
            abort(403)
    saved_mapping = json.loads(selected_template.mapping_json or '{}') if selected_template else {}
    try:
        for ordinal, (source_file, file_size) in enumerate(zip(files, file_sizes)):
            filename, storage_path, actual_size = _save_private_upload(source_file, job_code, ordinal)
            filename_extension = filename.rsplit('.', 1)[-1].lower() if '.' in filename else ''
            is_workbook = filename_extension in {'xls', 'xlsx', 'xlsm'}
            try:
                with open(storage_path, 'rb') as stored_file:
                    stored_file.filename = filename
                    analysis = IMPORTER.analyze_file(
                        stored_file,
                        schema,
                        sheet_name='__list_sheets__' if is_workbook else None,
                        learned_mappings=_learned_mappings(
                            schema, _mapping_scope(schema, project_id, account_id, purchase_order_id),
                        ),
                    )
                    analysis = _apply_saved_mapping(analysis, schema, saved_mapping)
            except ValueError as exc:
                analysis = {
                    'file_type': filename_extension,
                    'sheets': [],
                    'sheet_name': None,
                    'headers': [],
                    'rows': [],
                    'mappings': {},
                    'records_found': 0,
                    'ready_count': 0,
                    'error_count': 1,
                    'duplicate_count': 0,
                    'warning_count': 0,
                    'analysis_error': str(exc),
                    'compatible': False,
                }
            if is_workbook and not analysis.get('analysis_error'):
                analysis.update({
                    'filename': filename,
                    'ordinal': ordinal,
                    'needs_sheet_selection': True,
                    'compatible': True,
                    'schema_key': f'{module}.{entity}',
                    'file_size': actual_size,
                })
            elif not analysis.get('analysis_error'):
                analysis = _decorate_analysis(_apply_saved_mapping(analysis, schema, saved_mapping), schema, context)
                analysis.update({
                    'filename': filename,
                    'ordinal': ordinal,
                    'compatible': True,
                    'schema_key': f'{module}.{entity}',
                    'file_size': actual_size,
                })
            else:
                analysis.update({
                    'filename': filename,
                    'ordinal': ordinal,
                    'compatible': False,
                    'schema_key': f'{module}.{entity}',
                    'file_size': actual_size,
                })
            staged_files.append((storage_path, filename, actual_size, analysis))
        primary = next((entry[3] for entry in staged_files if not entry[3].get('needs_sheet_selection')), staged_files[0][3])
        combined_analysis = _combine_file_analyses([entry[3] for entry in staged_files])
        job = ImportJob(
            import_code=job_code,
            module=module,
            entity=entity,
            schema_version=SCHEMA_VERSION,
            status='awaiting_mapping',
            file_name=staged_files[0][1] if len(staged_files) == 1 else f'{len(staged_files)} files',
            file_type=primary['file_type'] if len(staged_files) == 1 else 'batch',
            file_size=sum(entry[2] for entry in staged_files),
            uploaded_by=current_user.id,
            project_id=project_id,
            bank_account_id=account_id,
            purchase_order_id=purchase_order_id,
            template_id=selected_template.id if selected_template else None,
            date_format=selected_template.date_format if selected_template else None,
            analysis_json=_dump(combined_analysis),
            records_found=sum(entry[3].get('records_found', 0) for entry in staged_files),
            duplicate_count=sum(entry[3].get('duplicate_count', 0) for entry in staged_files),
            warning_count=sum(entry[3].get('warning_count', 0) for entry in staged_files),
            error_count=sum(entry[3].get('error_count', 0) for entry in staged_files),
        )
        db.session.add(job)
        db.session.flush()
        for ordinal, (storage_path, filename, file_size, file_analysis) in enumerate(staged_files):
            db.session.add(ImportJobFile(
                job_id=job.id,
                ordinal=ordinal,
                file_name=filename,
                file_type=file_analysis['file_type'],
                file_size=file_size,
                storage_path=storage_path,
                analysis_json=_dump(file_analysis),
                status='incompatible' if file_analysis.get('analysis_error') else ('needs_sheet_selection' if file_analysis.get('needs_sheet_selection') else 'analyzed'),
                records_found=file_analysis.get('records_found', 0),
                duplicate_count=file_analysis.get('duplicate_count', 0),
                warning_count=file_analysis.get('warning_count', 0),
                error_count=file_analysis.get('error_count', 0),
            ))
        db.session.commit()
        templates = _available_templates(schema, project_id, account_id, purchase_order_id)
        file_analyses = [json.loads(item.analysis_json or '{}') for item in job.files]
        for file_analysis, child in zip(file_analyses, job.files):
            file_analysis['file_id'] = child.id
        return render_template(
            'imports/wizard.html', state='mapping', schema=schema, analysis=combined_analysis, job=job,
            context=context, file_analyses=file_analyses, selected_template=selected_template,
            templates=templates,
        )
    except ValueError as exc:
        db.session.rollback()
        for storage_path, _filename, _size, _analysis in staged_files:
            try:
                os.remove(storage_path)
            except OSError:
                pass
        flash(str(exc), 'danger')
        return render_template(
            'imports/wizard.html', state='upload', schema=schema, analysis=None, job=None,
            context=context,
        ), 400


def _available_templates(schema, project_id, account_id, purchase_order_id):
    scope = _template_scope(schema.module, schema.entity, project_id, account_id, purchase_order_id)
    return ImportTemplate.query.filter_by(
        module=schema.module, entity=schema.entity, scope_key=scope,
    ).order_by(ImportTemplate.name.asc()).all()


@bp.route('/<int:job_id>/files/<int:file_id>/analyze', methods=['POST'])
@login_required
def reanalyze_file(job_id, file_id):
    job, schema, context = _get_job(job_id)
    if job.status not in {'awaiting_mapping', 'awaiting_review'}:
        abort(409, 'This import is no longer accepting file analysis changes.')
    file_record = ImportJobFile.query.filter_by(id=file_id, job_id=job.id).first_or_404()
    if not os.path.isfile(file_record.storage_path):
        abort(410, 'The private source file has expired. Upload it again to reanalyze.')
    sheet_name = request.form.get('sheet_name') or None
    header_value = request.form.get('header_row', '').strip()
    header_row = int(header_value) - 1 if header_value else None
    date_format = request.form.get('date_format') or None
    try:
        with open(file_record.storage_path, 'rb') as source_file:
            source_file.filename = file_record.file_name
            analysis = IMPORTER.analyze_file(
                source_file,
                schema,
                sheet_name=sheet_name,
                header_row=header_row,
                date_format=date_format,
                learned_mappings=_learned_mappings(
                    schema, _mapping_scope(schema, job.project_id, job.bank_account_id, job.purchase_order_id),
                ),
            )
        if job.template_id:
            saved_template = db.session.get(ImportTemplate, job.template_id)
            analysis = _apply_saved_mapping(analysis, schema, json.loads(saved_template.mapping_json or '{}'))
        analysis = _decorate_analysis(analysis, schema, context)
        analysis.update({
            'filename': file_record.file_name,
            'file_id': file_record.id,
            'ordinal': file_record.ordinal,
            'compatible': True,
            'schema_key': f'{schema.module}.{schema.entity}',
            'date_format': date_format,
        })
        file_record.analysis_json = _dump(analysis)
        file_record.status = 'analyzed'
        file_record.records_found = analysis['records_found']
        file_record.duplicate_count = analysis['duplicate_count']
        file_record.warning_count = analysis['warning_count']
        file_record.error_count = analysis['error_count']
        primary = ImportJobFile.query.filter_by(job_id=job.id).order_by(ImportJobFile.ordinal.asc()).first()
        file_analyses = []
        for item in job.files:
            item_analysis = json.loads(item.analysis_json or '{}')
            item_analysis['file_id'] = item.id
            file_analyses.append(item_analysis)
        job.analysis_json = _dump(_combine_file_analyses(file_analyses))
        job.date_format = date_format
        db.session.commit()
        combined = _combine_file_analyses(file_analyses)
        return render_template(
            'imports/wizard.html', state='mapping', schema=schema, analysis=combined, job=job,
            context=context, templates=_available_templates(
                schema, job.project_id, job.bank_account_id, job.purchase_order_id,
            ), file_analyses=file_analyses, active_file=file_record,
        )
    except ValueError as exc:
        flash(str(exc), 'danger')
        analysis = _load_analysis(job)
        return render_template(
            'imports/wizard.html', state='mapping', schema=schema, analysis=analysis, job=job,
            context=context, templates=_available_templates(
                schema, job.project_id, job.bank_account_id, job.purchase_order_id,
            ), file_analyses=[json.loads(item.analysis_json or '{}') for item in job.files],
        ), 400


@bp.route('/<int:job_id>/files/<int:file_id>/remove', methods=['POST'])
@login_required
def remove_import_file(job_id, file_id):
    job, schema, context = _get_job(job_id)
    if job.status not in {'awaiting_mapping', 'awaiting_review'}:
        abort(409, 'Files can only be removed before import confirmation.')
    file_record = ImportJobFile.query.filter_by(id=file_id, job_id=job.id).first_or_404()
    root = os.path.realpath(_private_import_directory())
    path = os.path.realpath(file_record.storage_path)
    if os.path.commonpath([root, path]) != root:
        abort(403)
    try:
        os.remove(path)
    except FileNotFoundError:
        pass
    db.session.delete(file_record)
    db.session.flush()
    remaining = ImportJobFile.query.filter_by(job_id=job.id).order_by(ImportJobFile.ordinal.asc()).all()
    if not remaining:
        job.status = 'cancelled'
        job.failure_reason = 'All source files were removed before import.'
        job.completed_at = datetime.utcnow()
        job.analysis_json = '{}'
    else:
        analyses = []
        for item in remaining:
            parsed = json.loads(item.analysis_json or '{}')
            parsed['file_id'] = item.id
            analyses.append(parsed)
        combined = _combine_file_analyses(analyses)
        job.analysis_json = _dump(combined)
        job.file_name = remaining[0].file_name if len(remaining) == 1 else f'{len(remaining)} files'
        job.file_size = sum(item.file_size or 0 for item in remaining)
        job.records_found = sum(item.records_found or 0 for item in remaining)
    db.session.commit()
    flash('The selected source file was removed from this import.', 'info')
    if job.status == 'cancelled':
        return redirect(url_for('imports.history'))
    analyses = [json.loads(item.analysis_json or '{}') for item in job.files]
    combined = _combine_file_analyses(analyses)
    return render_template('imports/wizard.html', state='mapping', schema=schema,
                           analysis=combined, file_analyses=analyses, job=job, context=context,
                           templates=_available_templates(schema, job.project_id, job.bank_account_id, job.purchase_order_id))


@bp.route('/<int:job_id>/mapping', methods=['POST'])
@login_required
def apply_mapping(job_id):
    job, schema, context = _get_job(job_id)
    if job.status not in {'awaiting_mapping', 'awaiting_review'}:
        abort(409, 'This import is no longer accepting mapping changes.')
    analysis = _load_analysis(job)
    if request.form.get('return_to_mapping') == '1':
        return render_template(
            'imports/wizard.html', state='mapping', schema=schema, analysis=analysis, job=job,
            context=context,
            templates=_available_templates(schema, job.project_id, job.bank_account_id, job.purchase_order_id),
        )
    child_files = ImportJobFile.query.filter_by(job_id=job.id).order_by(ImportJobFile.ordinal.asc()).all()
    file_analyses = [json.loads(item.analysis_json or '{}') for item in child_files]
    if any(item.status == 'incompatible' for item in child_files):
        flash('Remove incompatible files or cancel this import before mapping the compatible files.', 'warning')
        return render_template(
            'imports/wizard.html', state='mapping', schema=schema, analysis=analysis, job=job,
            context=context, file_analyses=file_analyses,
            templates=_available_templates(schema, job.project_id, job.bank_account_id, job.purchase_order_id),
        ), 400
    if any(item.status == 'needs_sheet_selection' for item in child_files):
        flash('Select a worksheet for every uploaded workbook before mapping columns.', 'warning')
        return render_template(
            'imports/wizard.html', state='mapping', schema=schema, analysis=analysis, job=job,
            context=context, templates=_available_templates(
                schema, job.project_id, job.bank_account_id, job.purchase_order_id,
            ), file_analyses=file_analyses,
        ), 400
    headers = list(dict.fromkeys(
        header for file_analysis in file_analyses or [analysis]
        for header in file_analysis.get('headers', [])
    ))
    previous_mappings = analysis.get('mappings') or {}
    mapping = {}
    for index, source in enumerate(headers):
        target = request.form.get(f'mapping_{index}', '')
        if target in ('', None):
            current_mapping = previous_mappings.get(source) or {}
            current_target = current_mapping.get('target')
            mapping[source] = current_target if current_target in (None, '__ignore__') or current_target in schema.field_map else None
        else:
            mapping[source] = target
    selected_template_id = request.form.get('template_id', type=int)
    selected_date_format = request.form.get('date_format') or job.date_format
    if selected_template_id:
        template = ImportTemplate.query.get_or_404(selected_template_id)
        expected_scope = _template_scope(job.module, job.entity, job.project_id, job.bank_account_id, job.purchase_order_id)
        if template.module != job.module or template.entity != job.entity or template.scope_key != expected_scope:
            abort(403)
        saved_headers = set(json.loads(template.source_headers_json or '[]'))
        current_headers = set(headers)
        if not saved_headers.intersection(current_headers):
            flash('The saved template does not match this file. Review the detected mappings manually.', 'warning')
            selected_template_id = None
        job.template_id = selected_template_id
    if child_files:
        mapped_files = []
        for child, file_analysis in zip(child_files, file_analyses):
            relevant_mapping = {key: value for key, value in mapping.items() if key in file_analysis.get('headers', [])}
            mapped_file = IMPORTER.remap_analysis(
                file_analysis, schema, relevant_mapping,
                date_format=file_analysis.get('date_format') or selected_date_format,
            )
            if selected_date_format and any(row.get('date_confirmation_required') for row in file_analysis.get('rows', [])):
                mapped_file = IMPORTER.remap_analysis(
                    mapped_file, schema, relevant_mapping, date_format=selected_date_format,
                )
            mapped_file = _decorate_analysis(mapped_file, schema, context)
            mapped_file['filename'] = child.file_name
            mapped_file['file_id'] = child.id
            for file_row in mapped_file['rows']:
                file_row['source_file'] = child.file_name
                file_row['file_id'] = child.id
            child.analysis_json = _dump(mapped_file)
            child.mapping_json = _dump(relevant_mapping)
            child.status = 'mapped'
            child.records_found = mapped_file['records_found']
            child.duplicate_count = mapped_file['duplicate_count']
            child.warning_count = mapped_file['warning_count']
            child.error_count = mapped_file['error_count']
            mapped_files.append(mapped_file)
        analysis = dict(mapped_files[0])
        analysis['headers'] = headers
        analysis['mappings'] = {**mapped_files[0].get('mappings', {})}
        analysis['rows'] = []
        for file_analysis in mapped_files:
            for row in file_analysis['rows']:
                row['source_file'] = file_analysis['filename']
                row['file_id'] = file_analysis['file_id']
                analysis['rows'].append(row)
        analysis['records_found'] = sum(item['records_found'] for item in mapped_files)
        analysis['error_count'] = sum(item['error_count'] for item in mapped_files)
        analysis['duplicate_count'] = sum(item['duplicate_count'] for item in mapped_files)
        analysis['warning_count'] = sum(item['warning_count'] for item in mapped_files)
        analysis['ready_count'] = sum(item['ready_count'] for item in mapped_files)
        analysis['missing_required'] = list(dict.fromkeys(
            field for item in mapped_files for field in item.get('missing_required', [])
        ))
        analysis['confirmation_required'] = list(dict.fromkeys(
            source for item in mapped_files for source in item.get('confirmation_required', [])
        ))
        analysis['unsupported_columns'] = [
            column for item in mapped_files for column in item.get('unsupported_columns', [])
        ]
        analysis['date_confirmation_required'] = any(
            row.get('date_confirmation_required') for row in analysis['rows']
        )
        analysis['file_analyses'] = mapped_files
    else:
        analysis = IMPORTER.remap_analysis(analysis, schema, mapping, date_format=selected_date_format)
        analysis = _decorate_analysis(analysis, schema, context)
    has_ambiguous_dates = any(row.get('date_confirmation_required') for row in analysis.get('rows', []))
    dates_acknowledged = not has_ambiguous_dates or selected_date_format in {'DD/MM/YYYY', 'MM/DD/YYYY'}
    if has_ambiguous_dates and not dates_acknowledged:
        flash('Choose DD/MM/YYYY or MM/DD/YYYY to resolve ambiguous dates before proceeding.', 'warning')
        job.analysis_json = _dump(analysis)
        db.session.commit()
        return render_template(
            'imports/wizard.html', state='mapping', schema=schema, analysis=analysis, job=job,
            context=context,
            templates=_available_templates(schema, job.project_id, job.bank_account_id, job.purchase_order_id),
        ), 400
    if analysis['confirmation_required']:
        flash('Map each uncertain or unrecognized column to a field or Ignore it explicitly.', 'warning')
        job.analysis_json = _dump(analysis)
        db.session.commit()
        return render_template('imports/wizard.html', state='mapping', schema=schema, analysis=analysis,
                               job=job, context=context,
                               templates=_available_templates(schema, job.project_id, job.bank_account_id, job.purchase_order_id)), 400
    unsupported = [item for item in analysis['unsupported_columns'] if item.get('target')]
    acknowledged = request.form.get('acknowledge_unsupported') == 'yes'
    if unsupported and not acknowledged:
        flash('Acknowledge that unsupported source values will only be retained in this import record before continuing.', 'warning')
        job.analysis_json = _dump(analysis)
        db.session.commit()
        return render_template('imports/wizard.html', state='mapping', schema=schema, analysis=analysis,
                               job=job, context=context,
                               templates=_available_templates(schema, job.project_id, job.bank_account_id, job.purchase_order_id)), 400
    if analysis['missing_required']:
        labels = ', '.join(schema.field_map[name].label for name in analysis['missing_required'])
        flash(f'Required fields still need mappings: {labels}.', 'danger')
        job.analysis_json = _dump(analysis)
        db.session.commit()
        return render_template('imports/wizard.html', state='mapping', schema=schema, analysis=analysis,
                               job=job, context=context,
                               templates=_available_templates(schema, job.project_id, job.bank_account_id, job.purchase_order_id)), 400

    job.mapping_json = _dump({source: value for source, value in mapping.items()})
    job.date_format = selected_date_format or next((item.get('date_format') for item in (file_analyses or []) if item.get('date_format')), None)
    job.analysis_json = _dump(analysis)
    job.status = 'awaiting_review'
    job.records_found = analysis['records_found']
    job.duplicate_count = analysis['duplicate_count']
    job.warning_count = analysis['warning_count']
    job.error_count = analysis['error_count']
    job.unsupported_acknowledged = acknowledged
    job.ambiguous_dates_acknowledged = dates_acknowledged
    learning_scope = _mapping_scope(schema, job.project_id, job.bank_account_id, job.purchase_order_id)
    for source, target in mapping.items():
        if not target or target == '__ignore__':
            continue
        source_key = IMPORTER.normalize_header(source)
        learned = ImportMappingLearning.query.filter_by(
            module=schema.module,
            entity=schema.entity,
            schema_version=SCHEMA_VERSION,
            scope_key=learning_scope,
            source_header_key=source_key,
        ).first()
        if learned:
            learned.target_field = target
            learned.source_header = source
            learned.confirmations += 1
            learned.created_by = current_user.id
        else:
            db.session.add(ImportMappingLearning(
                module=schema.module,
                entity=schema.entity,
                schema_version=SCHEMA_VERSION,
                scope_key=learning_scope,
                source_header_key=source_key,
                source_header=source,
                target_field=target,
                confirmations=1,
                created_by=current_user.id,
            ))
    if selected_template_id:
        template = db.session.get(ImportTemplate, selected_template_id)
        if template:
            template.use_count = (template.use_count or 0) + 1
            template.last_used_at = datetime.utcnow()
    db.session.commit()
    return render_template('imports/wizard.html', state='review', schema=schema, analysis=analysis,
                           job=job, context=context,
                           templates=_available_templates(schema, job.project_id, job.bank_account_id, job.purchase_order_id))


@bp.route('/<int:job_id>/confirm', methods=['POST'])
@login_required
def confirm_import(job_id):
    job, schema, context = _get_job(job_id)
    if job.status != 'awaiting_review':
        abort(409, 'This import is not ready for confirmation.')
    analysis = _decorate_analysis(_load_analysis(job), schema, context)
    if any(row.get('date_confirmation_required') for row in analysis.get('rows', [])) and not job.ambiguous_dates_acknowledged:
        abort(409, 'Ambiguous date interpretation must be acknowledged before import.')
    unsupported = [item for item in analysis.get('unsupported_columns', []) if item.get('target')]
    if unsupported and not job.unsupported_acknowledged:
        abort(409, 'Unsupported source values must be acknowledged before import.')
    job.status = 'importing'
    try:
        imported = _persist_job(job, analysis, schema, context)
        skipped = analysis['error_count'] + analysis['duplicate_count']
        file_results = []
        for source_file in job.files:
            file_rows = [row for row in analysis.get('rows', []) if row.get('file_id') == source_file.id]
            imported_for_file = sum(
                not row.get('errors') and not row.get('potential_duplicate')
                for row in file_rows
            )
            duplicate_for_file = sum(bool(row.get('potential_duplicate')) for row in file_rows)
            errors_for_file = sum(bool(row.get('errors')) for row in file_rows)
            skipped_for_file = duplicate_for_file + errors_for_file
            file_result = {
                'file_name': source_file.file_name,
                'status': 'partially_imported' if imported_for_file and skipped_for_file else (
                    'completed' if imported_for_file else 'failed'
                ),
                'records_found': source_file.records_found,
                'records_imported': imported_for_file,
                'records_skipped': skipped_for_file,
                'duplicates': duplicate_for_file,
                'errors': errors_for_file,
                'warnings': source_file.warning_count,
            }
            source_file.status = file_result['status']
            source_file.records_imported = imported_for_file
            source_file.records_skipped = skipped_for_file
            source_file.duplicate_count = duplicate_for_file
            source_file.error_count = errors_for_file
            source_file.result_json = _dump(file_result)
            file_results.append(file_result)
        result = {
            'import_id': job.import_code,
            'status': 'partially_imported' if skipped and imported else (
                'completed_with_warnings' if skipped or analysis['warning_count'] else 'completed'
            ),
            'records_found': analysis['records_found'],
            'records_imported': imported,
            'records_updated': 0,
            'records_skipped': skipped,
            'duplicates': analysis['duplicate_count'],
            'warnings': analysis['warning_count'],
            'errors': analysis['error_count'],
            'files': file_results,
        }
        job.status = result['status']
        job.records_imported = imported
        job.records_skipped = skipped
        job.duplicate_count = result['duplicates']
        job.warning_count = result['warnings']
        job.error_count = result['errors']
        job.result_json = _dump(result)
        job.completed_at = datetime.utcnow()
        db.session.add(AuditLog(
            entity_type='import_job', entity_id=job.id, action='import_confirmed',
            actor_id=current_user.id, reference=job.import_code,
            description=f"Imported {imported} {job.module}/{job.entity} records from {job.file_name}.",
            new_value=_dump({'mapping': json.loads(job.mapping_json or '{}'), 'result': result}),
        ))
        db.session.commit()
        return render_template('imports/wizard.html', state='result', schema=schema, analysis=analysis,
                               job=job, context=context, result=result)
    except Exception as exc:
        db.session.rollback()
        job = db.session.get(ImportJob, job_id)
        job.status = 'failed'
        job.failure_reason = str(exc)
        job.completed_at = datetime.utcnow()
        job.result_json = _dump({'import_id': job.import_code, 'status': 'failed', 'message': str(exc)})
        db.session.add(AuditLog(
            entity_type='import_job', entity_id=job.id, action='import_failed',
            actor_id=current_user.id, reference=job.import_code, description=str(exc),
        ))
        db.session.commit()
        flash(f'Import failed and was rolled back: {exc}', 'danger')
        return redirect(url_for('imports.detail', job_id=job.id))


@bp.route('/templates', methods=['POST'])
@login_required
def save_template():
    job_id = request.form.get('job_id', type=int)
    job, schema, _context = _get_job(job_id)
    if job.status not in {'awaiting_review', 'completed', 'completed_with_warnings', 'partially_imported'}:
        abort(409, 'Confirm the column mapping before saving a template.')
    name = (request.form.get('name') or '').strip()[:120]
    if not name:
        flash('Enter a name for this mapping template.', 'danger')
        return redirect(url_for('imports.detail', job_id=job.id))
    template = ImportTemplate(
        name=name,
        module=job.module,
        entity=job.entity,
        schema_version=job.schema_version,
        scope_key=_template_scope(job.module, job.entity, job.project_id, job.bank_account_id, job.purchase_order_id),
        source_headers_json=_dump(_load_analysis(job).get('headers', [])),
        mapping_json=job.mapping_json or '{}',
        date_format=job.date_format,
        created_by=current_user.id,
    )
    db.session.add(template)
    db.session.flush()
    job.template_id = template.id
    db.session.commit()
    flash('Mapping template saved for this import context.', 'success')
    return redirect(url_for('imports.detail', job_id=job.id))


def _template_access(template):
    schema = _schema_for(template.module, template.entity)
    if template.created_by != current_user.id and normalize_role(current_user.role) not in {'admin', 'super_hq'}:
        abort(403)
    return schema


@bp.route('/templates')
@login_required
def templates():
    role = normalize_role(current_user.role)
    project_id = request.args.get('project_id', type=int)
    project = None
    project_scope = None
    if project_id:
        schema = _schema_for('quantity_surveying', 'boq')
        project, _account, _purchase_order = _validate_context(schema, project_id=project_id)
        project_scope = _template_scope(schema.module, schema.entity, project_id=project_id)
    allowed_schemas = [schema for schema in __import__('app.imports.schemas', fromlist=['SCHEMAS']).SCHEMAS.values()
                       if role in schema.required_roles or role in {'admin', 'super_hq'}]
    allowed_keys = {(schema.module, schema.entity) for schema in allowed_schemas}
    query = ImportTemplate.query
    if project_scope:
        query = query.filter(ImportTemplate.scope_key == project_scope)
    if role not in {'admin', 'super_hq'}:
        query = query.filter(ImportTemplate.created_by == current_user.id)
    items = [item for item in query.order_by(ImportTemplate.module, ImportTemplate.entity, ImportTemplate.name).all()
             if (item.module, item.entity) in allowed_keys]
    return render_template(
        'imports/templates.html', templates=items,
        project=project, project_id=project_id,
    )


@bp.route('/templates/<int:template_id>/use')
@login_required
def use_template(template_id):
    template = ImportTemplate.query.get_or_404(template_id)
    _template_access(template)
    scope_context = template.scope_key.split(':', 1)[1] if ':' in template.scope_key else 'global'
    parameters = {
        'module': template.module,
        'entity': template.entity,
        'template_id': template.id,
    }
    if scope_context.startswith('account:'):
        parameters['account_id'] = scope_context.split(':', 1)[1]
    elif scope_context.startswith('project:'):
        parameters['project_id'] = scope_context.split(':', 1)[1]
    elif scope_context.startswith('purchase_order:'):
        parameters['purchase_order_id'] = scope_context.split(':', 1)[1]
    return redirect(url_for('imports.new_import', **parameters))


@bp.route('/templates/<int:template_id>', methods=['GET', 'POST'])
@login_required
def edit_template(template_id):
    template = ImportTemplate.query.get_or_404(template_id)
    schema = _template_access(template)
    mappings = json.loads(template.mapping_json or '{}')
    if request.method == 'POST':
        requested = {}
        for source in json.loads(template.source_headers_json or '[]'):
            target = request.form.get('mapping_' + str((json.loads(template.source_headers_json or '[]').index(source))), '')
            if target and target != '__ignore__' and target not in schema.field_map:
                abort(400, 'Template contains an unknown schema field.')
            requested[source] = target or '__ignore__'
        template.name = (request.form.get('name') or template.name).strip()[:120]
        template.mapping_json = _dump(requested)
        template.date_format = request.form.get('date_format') or None
        template.updated_at = datetime.utcnow()
        db.session.commit()
        flash('Import template updated.', 'success')
        return redirect(url_for('imports.templates'))
    return render_template('imports/template_edit.html', template=template, schema=schema, mappings=mappings,
                           source_headers=json.loads(template.source_headers_json or '[]'))


@bp.route('/templates/<int:template_id>/delete', methods=['POST'])
@login_required
def delete_template(template_id):
    template = ImportTemplate.query.get_or_404(template_id)
    _template_access(template)
    db.session.delete(template)
    db.session.commit()
    flash('Import template deleted.', 'success')
    return redirect(url_for('imports.templates'))


@bp.route('/history')
@login_required
def history():
    role = normalize_role(current_user.role)
    query = ImportJob.query
    project_id = request.args.get('project_id', type=int)
    project = None
    if project_id:
        schema = _schema_for('quantity_surveying', 'boq')
        project, _account, _purchase_order = _validate_context(schema, project_id=project_id)
        query = query.filter(ImportJob.project_id == project_id)
    if role not in {'admin', 'super_hq'}:
        allowed = [schema.module for schema in __import__('app.imports.schemas', fromlist=['SCHEMAS']).SCHEMAS.values()
                   if role in schema.required_roles]
        query = query.filter(ImportJob.module.in_(set(allowed))).filter(ImportJob.uploaded_by == current_user.id)
    module = request.args.get('module')
    status = request.args.get('status')
    if module:
        query = query.filter_by(module=module)
    if status:
        query = query.filter_by(status=status)
    jobs = query.order_by(ImportJob.created_at.desc()).paginate(
        page=request.args.get('page', 1, type=int), per_page=25, error_out=False,
    )
    legacy_imports = []
    if not project_id and module in (None, '', 'finance') and (role in {'admin', 'super_hq'} or 'finance' in allowed):
        legacy_imports = BankStatementImport.query.order_by(BankStatementImport.uploaded_at.desc()).limit(100).all()
    return render_template(
        'imports/history.html', jobs=jobs, module=module or '', status=status or '',
        legacy_imports=legacy_imports, project=project, project_id=project_id,
    )


@bp.route('/<int:job_id>')
@login_required
def detail(job_id):
    job, schema, context = _get_job(job_id)
    analysis = _load_analysis(job)
    try:
        result = json.loads(job.result_json or '{}')
    except ValueError:
        result = {}
    return render_template('imports/wizard.html', state='result' if job.result_json else 'review',
                           schema=schema, analysis=analysis, job=job, context=context, result=result)
