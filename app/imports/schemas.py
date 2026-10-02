"""Context-aware schemas and aliases for structured imports."""

from dataclasses import dataclass, field


@dataclass(frozen=True)
class ImportField:
    name: str
    label: str
    required: bool = False
    kind: str = 'text'
    aliases: tuple = ()
    persisted: bool = True


@dataclass(frozen=True)
class ImportSchema:
    module: str
    entity: str
    fields: tuple
    duplicate_fields: tuple = field(default_factory=tuple)
    duplicate_mode: str = 'composite'
    import_modes: tuple = ('create_only',)
    required_roles: tuple = ()

    @property
    def field_map(self):
        return {item.name: item for item in self.fields}


SCHEMAS = {
    ('finance', 'bank_transaction'): ImportSchema(
        module='finance',
        entity='bank_transaction',
        fields=(
            ImportField('date', 'Transaction Date', True, 'date', (
                'transaction date', 'tran date', 'posting date', 'posted date', 'value date',
            )),
            ImportField('description', 'Description', True, 'text', (
                'narration', 'transaction details', 'details', 'particulars', 'memo',
            )),
            ImportField('debit', 'Debit', False, 'number', (
                'withdrawal', 'dr', 'dr amount', 'debit amount',
            )),
            ImportField('credit', 'Credit', False, 'number', (
                'deposit', 'cr', 'cr amount', 'credit amount',
            )),
            ImportField('amount', 'Transaction Amount', False, 'number', (
                'transaction amount', 'amount', 'value',
            )),
            ImportField('balance', 'Balance', False, 'number', (
                'running balance', 'closing balance', 'running bal', 'available balance',
            )),
            ImportField('reference', 'Reference', False, 'text', (
                'ref', 'transaction id', 'transaction reference', 'transaction ref',
            )),
        ),
        duplicate_fields=('date', 'reference', 'amount', 'description'),
        required_roles=('super_hq', 'hq_finance', 'finance_manager', 'accounts_payable', 'admin'),
    ),
    ('procurement', 'supplier'): ImportSchema(
        module='procurement',
        entity='supplier',
        fields=(
            ImportField('supplier_name', 'Supplier Name', True, 'text', (
                'supplier', 'supplier name', 'vendor', 'vendor name', 'company',
                'company name', 'business name',
            )),
            ImportField('contact_name', 'Contact Person', False, 'text', (
                'contact', 'representative', 'contact person',
            )),
            ImportField('phone', 'Phone', False, 'text', (
                'mobile', 'mobile number', 'telephone', 'phone number',
            )),
            ImportField('email', 'Email', False, 'text', (
                'e mail', 'email address', 'e mail address',
            )),
            ImportField('address', 'Address', False, 'text', (
                'location', 'office address', 'business address',
            )),
            ImportField('city', 'City', False, 'text', ('town', 'city name')),
            ImportField('registration_number', 'Registration / Tax Number', False, 'text', (
                'tax id', 'tax number', 'registration number', 'business number',
            )),
        ),
        duplicate_fields=('supplier_name', 'email', 'phone'),
        duplicate_mode='any',
        required_roles=('super_hq', 'hq_procurement', 'procurement_manager', 'admin'),
    ),
    ('quantity_surveying', 'boq'): ImportSchema(
        module='quantity_surveying',
        entity='boq',
        fields=(
            ImportField('item_number', 'Item Number', False, 'text', (
                'item no', 'item no.', 'item code', 'ref', 'bill item',
            )),
            ImportField('description', 'Description', True, 'text', (
                'details', 'particulars', 'item description', 'work description',
            )),
            ImportField('unit', 'Unit', True, 'unit', (
                'uom', 'unit of measure', 'unit of measurement', 'measurement unit',
            )),
            ImportField('quantity', 'Quantity', True, 'number', (
                'qty', 'required qty', 'order quantity', 'measured quantity', 'measured qty',
            )),
            ImportField('rate', 'Unit Rate', True, 'number', (
                'unit rate', 'rate', 'unit price', 'price',
            )),
            ImportField('amount', 'Amount', False, 'number', (
                'total', 'value', 'line total',
            )),
        ),
        duplicate_fields=('item_number', 'description'),
        required_roles=('super_hq', 'qs_manager', 'qs_staff', 'project_manager'),
    ),
    ('project_management', 'project_task'): ImportSchema(
        module='project_management',
        entity='project_task',
        fields=(
            ImportField('task_code', 'Task Code', False, 'text', (
                'task id', 'activity code', 'wbs',
            )),
            ImportField('task_name', 'Task Name', True, 'text', (
                'task', 'activity', 'activity description', 'task description',
            )),
            ImportField('start_date', 'Start Date', False, 'date', (
                'start', 'commencement', 'planned start',
            )),
            ImportField('end_date', 'End Date', False, 'date', (
                'finish', 'completion', 'planned finish',
            )),
            ImportField('assigned_to', 'Assigned To', False, 'text', (
                'owner', 'responsible person', 'assignee',
            )),
            ImportField('status', 'Status', False, 'text', (
                'current status', 'task status',
            )),
            ImportField('progress', 'Progress', False, 'number', (
                'progress percent', 'progress %', '% complete', 'percent complete',
            )),
        ),
        duplicate_fields=('task_code', 'task_name'),
        required_roles=('super_hq', 'project_manager'),
    ),
    ('procurement', 'purchase_item'): ImportSchema(
        module='procurement',
        entity='purchase_item',
        fields=(
            ImportField('item', 'Item', True, 'text', ('product', 'description', 'particulars')),
            ImportField('quantity', 'Quantity', True, 'number', ('qty', 'quantity required')),
            ImportField('unit_price', 'Unit Price', True, 'number', ('rate', 'unit cost', 'price')),
            ImportField('amount', 'Amount', False, 'number', ('total', 'line total', 'value')),
            ImportField('supplier', 'Supplier', False, 'text', ('vendor', 'supplier name')),
            ImportField('delivery_date', 'Delivery Date', False, 'date', ('expected delivery', 'delivery')),
        ),
        duplicate_fields=('supplier', 'item'),
        required_roles=('super_hq', 'hq_procurement', 'procurement_manager', 'admin'),
    ),
    ('inventory', 'stock_item'): ImportSchema(
        module='inventory',
        entity='stock_item',
        fields=(
            ImportField('sku', 'SKU', True, 'text', ('item code', 'stock code', 'product code')),
            ImportField('product', 'Product', True, 'text', ('item name', 'product name', 'item')),
            ImportField('description', 'Description', False, 'text', ('details', 'particulars')),
            ImportField('unit', 'Unit', False, 'unit', ('uom', 'unit of measure')),
            ImportField('quantity', 'Quantity', True, 'number', ('qty', 'stock', 'stock quantity')),
            ImportField('reorder_level', 'Reorder Level', False, 'number', ('minimum stock', 'min stock')),
            ImportField('unit_cost', 'Unit Cost', False, 'number', ('cost price', 'unit price', 'rate')),
            ImportField('warehouse', 'Warehouse', False, 'text', ('location', 'store', 'warehouse location')),
        ),
        duplicate_fields=('sku',),
        duplicate_mode='any',
        required_roles=('super_hq', 'admin', 'hq_procurement', 'procurement_manager', 'procurement_staff'),
    ),
    ('project_management', 'milestone'): ImportSchema(
        module='project_management',
        entity='milestone',
        fields=(
            ImportField('task_name', 'Milestone / Task Name', True, 'text', (
                'task', 'activity', 'activity description', 'task description', 'milestone',
            )),
            ImportField('description', 'Description', False, 'text', (
                'details', 'task details', 'milestone description', 'work description',
            )),
            ImportField('start_date', 'Planned Start Date', False, 'date', (
                'start', 'commencement', 'planned start',
            )),
            ImportField('end_date', 'Planned End Date', False, 'date', (
                'finish', 'completion', 'planned finish', 'end date',
            )),
            ImportField('status', 'Status', False, 'text', ('current status', 'task status')),
            ImportField('progress', 'Completion Percentage', False, 'number', (
                'progress', 'progress percent', 'progress %', '% complete', 'percent complete',
            )),
            ImportField('task_code', 'Task / Activity Code', False, 'text', (
                'task id', 'activity code', 'wbs',
            )),
            ImportField('assigned_to', 'Responsible Person', False, 'text', (
                'owner', 'responsible', 'responsible person', 'assignee', 'assigned to',
            )),
        ),
        duplicate_fields=('task_name', 'start_date'),
        required_roles=('super_hq', 'project_manager', 'project_staff'),
    ),
}


def get_import_schema(module, entity):
    """Return a registered import schema or raise a descriptive error."""
    key = (str(module).strip().lower(), str(entity).strip().lower())
    try:
        return SCHEMAS[key]
    except KeyError as exc:
        raise ValueError(f'No import schema registered for {key[0]}/{key[1]}.') from exc