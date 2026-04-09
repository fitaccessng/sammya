"""
Database models for FitAccess Construction-ERP system.
Implements role-based approvals, workflows, and audit trails.
"""

from datetime import datetime
from enum import Enum
from flask_sqlalchemy import SQLAlchemy
from flask_login import UserMixin
from werkzeug.security import generate_password_hash, check_password_hash

db = SQLAlchemy()


class ApprovalState(str, Enum):
    """Approval workflow states."""
    DRAFT = "draft"
    PENDING = "pending"
    REVIEW = "review"
    APPROVED = "approved"
    REJECTED = "rejected"
    ESCALATED = "escalated"
    CANCELLED = "cancelled"


class User(UserMixin, db.Model):
    """User account with role and project assignments."""
    __tablename__ = 'user'
    
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(255), nullable=False)
    email = db.Column(db.String(255), unique=True, nullable=False, index=True)
    password_hash = db.Column(db.String(255), nullable=False)
    role = db.Column(db.String(50), nullable=False)  # e.g., 'admin', 'cost_control_manager', 'qc_staff'
    is_active = db.Column(db.Boolean, default=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    
    # HR/Payroll fields
    basic_salary = db.Column(db.Numeric(12, 2), default=0)
    default_deductions = db.Column(db.Numeric(12, 2), default=0)  # Tax, insurance, etc.
    passport_document = db.Column(db.String(500))  # Passport file path
    date_of_birth = db.Column(db.Date)  # Employee's date of birth
    date_of_employment = db.Column(db.Date)  # When employee started
    employee_id = db.Column(db.String(50), unique=True)  # Staff/Employee ID
    phone = db.Column(db.String(20))  # Contact phone number
    address = db.Column(db.Text)  # Residential address
    city = db.Column(db.String(100))
    state = db.Column(db.String(100))
    gender = db.Column(db.String(10))  # Male, Female, Other
    marital_status = db.Column(db.String(50))  # Single, Married, etc.
    
    # Relationships
    projects = db.relationship('Project', secondary='user_projects', backref='team_members')
    created_items = db.relationship('BOQItem', backref='creator_user', foreign_keys='BOQItem.created_by')
    approval_logs = db.relationship('ApprovalLog', foreign_keys='ApprovalLog.actor_id')
    next_of_kin = db.relationship('NextOfKin', backref='employee', cascade='all, delete-orphan')
    department_access = db.relationship('DepartmentAccess', backref='user', cascade='all, delete-orphan')
    
    def set_password(self, password):
        """Hash and set password."""
        self.password_hash = generate_password_hash(password)
    
    def check_password(self, password):
        """Verify password against hash."""
        return check_password_hash(self.password_hash, password)
    
    def has_role(self, role_name):
        """Check if user has specific role."""
        return self.role == role_name
    
    def has_any_role(self, role_names):
        """Check if user has any of the specified roles."""
        if isinstance(role_names, str):
            return self.role == role_names
        return self.role in role_names
    
    def __repr__(self):
        return f'<User {self.email} ({self.role})>'


class NextOfKin(db.Model):
    """Employee emergency contact and next of kin information."""
    __tablename__ = 'next_of_kin'
    
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    full_name = db.Column(db.String(255), nullable=False)
    relationship = db.Column(db.String(100), nullable=False)  # e.g., Spouse, Parent, Sibling, Child
    phone = db.Column(db.String(20), nullable=False)
    email = db.Column(db.String(255))
    address = db.Column(db.Text)
    city = db.Column(db.String(100))
    state = db.Column(db.String(100))
    is_primary = db.Column(db.Boolean, default=True)  # Primary contact
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    
    def __repr__(self):
        return f'<NextOfKin {self.full_name} ({self.relationship}) for {self.employee.name}>'


class ProjectStaff(db.Model):
    """Project team member assignments with roles."""
    __tablename__ = 'project_staff'
    
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    project_id = db.Column(db.Integer, db.ForeignKey('project.id'), nullable=False)
    role = db.Column(db.String(100), nullable=False)  # e.g., 'Project Manager', 'Site Engineer', 'Supervisor', 'Laborer'
    start_date = db.Column(db.Date, default=datetime.utcnow)
    end_date = db.Column(db.Date)
    is_active = db.Column(db.Boolean, default=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    
    # Relationships
    user = db.relationship('User', backref='project_assignments')
    project = db.relationship('Project', backref='staff_assignments')
    
    def __repr__(self):
        return f'<ProjectStaff {self.user.name} - {self.role} ({self.project.name})>'


# Legacy association table - kept for backwards compatibility but ProjectStaff is preferred
user_projects = db.Table(
    'user_projects',
    db.Column('user_id', db.Integer, db.ForeignKey('user.id'), primary_key=True),
    db.Column('project_id', db.Integer, db.ForeignKey('project.id'), primary_key=True)
)


class Project(db.Model):
    """Construction project with budget and timeline."""
    __tablename__ = 'project'
    
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(255), nullable=False)
    description = db.Column(db.Text)
    budget = db.Column(db.Numeric(15, 2), default=0)
    start_date = db.Column(db.Date)
    end_date = db.Column(db.Date)
    status = db.Column(db.String(50), default='active')  # active, completed, on_hold
    project_manager_id = db.Column(db.Integer, db.ForeignKey('user.id'))
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    
    # Relationships
    project_manager = db.relationship('User', foreign_keys=[project_manager_id], backref='managed_projects')
    boq_items = db.relationship('BOQItem', backref='project', cascade='all, delete-orphan')
    material_requests = db.relationship('MaterialRequest', backref='project', cascade='all, delete-orphan')
    purchase_orders = db.relationship('PurchaseOrder', backref='project', cascade='all, delete-orphan')
    change_orders = db.relationship('ChangeOrder', backref='project', cascade='all, delete-orphan')
    ipcs = db.relationship('IPC', backref='project', cascade='all, delete-orphan')
    milestones = db.relationship('Milestone', backref='project', cascade='all, delete-orphan')
    daily_reports = db.relationship('DailyProductionReport', backref='project', cascade='all, delete-orphan')
    materials = db.relationship('ProjectMaterial', backref='project', cascade='all, delete-orphan')
    equipment = db.relationship('ProjectEquipment', backref='project', cascade='all, delete-orphan')
    documents = db.relationship('ProjectDocument', backref='project', cascade='all, delete-orphan')
    budget_records = db.relationship('ProjectBudgetRecord', backref='project', cascade='all, delete-orphan')
    
    def __repr__(self):
        return f'<Project {self.name}>'


class Vendor(db.Model):
    """Vendor/supplier master data."""
    __tablename__ = 'vendor'
    
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(255), nullable=False)
    email = db.Column(db.String(255))
    phone = db.Column(db.String(20))
    address = db.Column(db.Text)
    city = db.Column(db.String(100))
    registration_number = db.Column(db.String(100))
    is_active = db.Column(db.Boolean, default=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    
    # Relationships
    purchase_orders = db.relationship('PurchaseOrder', backref='vendor', cascade='all, delete-orphan')
    
    def __repr__(self):
        return f'<Vendor {self.name}>'


class BOQItem(db.Model):
    """Bill of Quantities item."""
    __tablename__ = 'boq_item'
    
    id = db.Column(db.Integer, primary_key=True)
    project_id = db.Column(db.Integer, db.ForeignKey('project.id'), nullable=False)
    description = db.Column(db.String(500), nullable=False)
    unit = db.Column(db.String(50))  # e.g., 'm', 'kg', 'no'
    quantity = db.Column(db.Numeric(10, 2), nullable=False)
    unit_rate = db.Column(db.Numeric(12, 2), nullable=False)
    amount = db.Column(db.Numeric(15, 2))  # quantity * unit_rate
    approval_state = db.Column(db.Enum(ApprovalState), default=ApprovalState.DRAFT)
    created_by = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    
    # Relationships
    approval_logs = db.relationship('ApprovalLog', backref='boq_item_ref', 
                                   foreign_keys='ApprovalLog.entity_id',
                                   primaryjoin='and_(ApprovalLog.entity_type=="boq_item", ApprovalLog.entity_id==BOQItem.id)')
    
    def calculate_amount(self):
        """Calculate total amount from quantity and unit rate."""
        if self.quantity and self.unit_rate:
            self.amount = self.quantity * self.unit_rate
    
    def __repr__(self):
        return f'<BOQItem {self.description} ({self.quantity}{self.unit})>'


class MaterialRequest(db.Model):
    """Material request initiated by project staff, approved through chain."""
    __tablename__ = 'material_request'
    
    id = db.Column(db.Integer, primary_key=True)
    project_id = db.Column(db.Integer, db.ForeignKey('project.id'), nullable=False)
    created_by = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    description = db.Column(db.Text)
    total_value = db.Column(db.Numeric(15, 2))
    approval_state = db.Column(db.Enum(ApprovalState), default=ApprovalState.DRAFT)
    budget_approved = db.Column(db.Boolean, default=False)
    
    # Relationships
    items = db.relationship('MaterialRequestItem', backref='request', cascade='all, delete-orphan')
    approval_logs = db.relationship('ApprovalLog', backref='material_request_ref',
                                   foreign_keys='ApprovalLog.entity_id',
                                   primaryjoin='and_(ApprovalLog.entity_type=="material_request", ApprovalLog.entity_id==MaterialRequest.id)')
    
    def __repr__(self):
        return f'<MaterialRequest {self.id}>'


class MaterialRequestItem(db.Model):
    """Individual items within a material request."""
    __tablename__ = 'material_request_item'
    
    id = db.Column(db.Integer, primary_key=True)
    request_id = db.Column(db.Integer, db.ForeignKey('material_request.id'), nullable=False)
    description = db.Column(db.String(500), nullable=False)
    unit = db.Column(db.String(50))
    quantity = db.Column(db.Numeric(10, 2), nullable=False)
    estimated_unit_cost = db.Column(db.Numeric(12, 2))
    boq_item_id = db.Column(db.Integer, db.ForeignKey('boq_item.id'))  # Link to BOQ if applicable
    
    def __repr__(self):
        return f'<MaterialRequestItem {self.description}>'


class PurchaseOrder(db.Model):
    """Purchase Order generated from material request."""
    __tablename__ = 'purchase_order'
    
    id = db.Column(db.Integer, primary_key=True)
    project_id = db.Column(db.Integer, db.ForeignKey('project.id'), nullable=False)
    material_request_id = db.Column(db.Integer, db.ForeignKey('material_request.id'))
    vendor_id = db.Column(db.Integer, db.ForeignKey('vendor.id'))
    po_number = db.Column(db.String(50), unique=True, index=True)
    total_amount = db.Column(db.Numeric(15, 2), nullable=False)
    issued_by = db.Column(db.Integer, db.ForeignKey('user.id'))
    approval_state = db.Column(db.Enum(ApprovalState), default=ApprovalState.DRAFT)
    requires_executive_approval = db.Column(db.Boolean, default=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    issued_at = db.Column(db.DateTime)
    
    # Relationships
    items = db.relationship('PurchaseOrderItem', backref='po', cascade='all, delete-orphan')
    deliveries = db.relationship('Delivery', backref='po', cascade='all, delete-orphan')
    approval_logs = db.relationship('ApprovalLog', backref='purchase_order_ref',
                                   foreign_keys='ApprovalLog.entity_id',
                                   primaryjoin='and_(ApprovalLog.entity_type=="purchase_order", ApprovalLog.entity_id==PurchaseOrder.id)')
    
    def __repr__(self):
        return f'<PurchaseOrder {self.po_number}>'


class PurchaseOrderItem(db.Model):
    """Items within a purchase order."""
    __tablename__ = 'purchase_order_item'
    
    id = db.Column(db.Integer, primary_key=True)
    po_id = db.Column(db.Integer, db.ForeignKey('purchase_order.id'), nullable=False)
    description = db.Column(db.String(500), nullable=False)
    unit = db.Column(db.String(50))
    quantity = db.Column(db.Numeric(10, 2), nullable=False)
    unit_rate = db.Column(db.Numeric(12, 2), nullable=False)
    amount = db.Column(db.Numeric(15, 2))
    
    def calculate_amount(self):
        if self.quantity and self.unit_rate:
            self.amount = self.quantity * self.unit_rate


class Delivery(db.Model):
    """Delivery/Goods Received Note."""
    __tablename__ = 'delivery'
    
    id = db.Column(db.Integer, primary_key=True)
    po_id = db.Column(db.Integer, db.ForeignKey('purchase_order.id'), nullable=False)
    grn_number = db.Column(db.String(50), unique=True, index=True)
    received_at = db.Column(db.DateTime, default=datetime.utcnow)
    received_by = db.Column(db.Integer, db.ForeignKey('user.id'))
    total_quantity_received = db.Column(db.Numeric(10, 2))
    approval_state = db.Column(db.Enum(ApprovalState), default=ApprovalState.PENDING)
    
    # Relationships
    items = db.relationship('DeliveryItem', backref='delivery', cascade='all, delete-orphan')
    qc_inspections = db.relationship('QCInspection', backref='delivery', cascade='all, delete-orphan')
    
    def __repr__(self):
        return f'<Delivery {self.grn_number}>'


class DeliveryItem(db.Model):
    """Items received in a delivery."""
    __tablename__ = 'delivery_item'
    
    id = db.Column(db.Integer, primary_key=True)
    delivery_id = db.Column(db.Integer, db.ForeignKey('delivery.id'), nullable=False)
    po_item_id = db.Column(db.Integer, db.ForeignKey('purchase_order_item.id'))
    description = db.Column(db.String(500))
    unit = db.Column(db.String(50))
    quantity_received = db.Column(db.Numeric(10, 2))


class QCInspection(db.Model):
    """Quality Control inspection for deliveries."""
    __tablename__ = 'qc_inspection'
    
    id = db.Column(db.Integer, primary_key=True)
    delivery_id = db.Column(db.Integer, db.ForeignKey('delivery.id'), nullable=False)
    inspected_by = db.Column(db.Integer, db.ForeignKey('user.id'))
    inspection_date = db.Column(db.DateTime, default=datetime.utcnow)
    approval_state = db.Column(db.Enum(ApprovalState), default=ApprovalState.PENDING)
    approved_quantity = db.Column(db.Numeric(10, 2))
    rejected_quantity = db.Column(db.Numeric(10, 2))
    rejection_reason = db.Column(db.Text)
    report_attachment = db.Column(db.String(500))
    
    # Relationships
    approval_logs = db.relationship('ApprovalLog', backref='qc_inspection_ref',
                                   foreign_keys='ApprovalLog.entity_id',
                                   primaryjoin='and_(ApprovalLog.entity_type=="qc_inspection", ApprovalLog.entity_id==QCInspection.id)')
    
    def __repr__(self):
        return f'<QCInspection {self.id}>'


class Inventory(db.Model):
    """Stock/inventory management."""
    __tablename__ = 'inventory'
    
    id = db.Column(db.Integer, primary_key=True)
    project_id = db.Column(db.Integer, db.ForeignKey('project.id'))
    item_description = db.Column(db.String(500), nullable=False)
    unit = db.Column(db.String(50))
    quantity_on_hand = db.Column(db.Numeric(10, 2), default=0)
    reorder_level = db.Column(db.Numeric(10, 2))
    last_updated = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class AssetTransfer(db.Model):
    """Transfer inventory assets between projects/sites."""
    __tablename__ = 'asset_transfer'

    id = db.Column(db.Integer, primary_key=True)
    inventory_id = db.Column(db.Integer, db.ForeignKey('inventory.id'), nullable=False)
    from_project_id = db.Column(db.Integer, db.ForeignKey('project.id'))
    to_project_id = db.Column(db.Integer, db.ForeignKey('project.id'))
    quantity = db.Column(db.Numeric(10, 2), nullable=False)
    reason = db.Column(db.Text)
    status = db.Column(db.String(30), default='completed')  # pending, completed, cancelled
    transferred_by = db.Column(db.Integer, db.ForeignKey('user.id'))
    transfer_date = db.Column(db.DateTime, default=datetime.utcnow)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    inventory = db.relationship('Inventory', backref='transfers')
    from_project = db.relationship('Project', foreign_keys=[from_project_id], backref='outgoing_asset_transfers')
    to_project = db.relationship('Project', foreign_keys=[to_project_id], backref='incoming_asset_transfers')
    actor = db.relationship('User', foreign_keys=[transferred_by], backref='asset_transfers')


class PaymentRequest(db.Model):
    """Payment request/invoice for approved deliveries."""
    __tablename__ = 'payment_request'
    
    id = db.Column(db.Integer, primary_key=True)
    # Optional for customer receivables that are not tied to a purchase order.
    po_id = db.Column(db.Integer, db.ForeignKey('purchase_order.id'), nullable=True)
    qc_inspection_id = db.Column(db.Integer, db.ForeignKey('qc_inspection.id'))
    counterparty_name = db.Column(db.String(255))
    notes = db.Column(db.Text)
    invoice_number = db.Column(db.String(50), unique=True, index=True)
    invoice_amount = db.Column(db.Numeric(15, 2), nullable=False)
    approval_state = db.Column(db.Enum(ApprovalState), default=ApprovalState.DRAFT)
    verified_by = db.Column(db.Integer, db.ForeignKey('user.id'))
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    sent_to_admin = db.Column(db.Boolean, default=False)
    sent_to_cost_control = db.Column(db.Boolean, default=False)
    sent_to_procurement = db.Column(db.Boolean, default=False)
    sent_date = db.Column(db.DateTime)
    
    # Relationships
    purchase_order = db.relationship('PurchaseOrder', backref='payment_requests')
    approval_logs = db.relationship('ApprovalLog', backref='payment_request_ref',
                                   foreign_keys='ApprovalLog.entity_id',
                                   primaryjoin='and_(ApprovalLog.entity_type=="payment_request", ApprovalLog.entity_id==PaymentRequest.id)')


class PaymentRecord(db.Model):
    """Actual payment execution record."""
    __tablename__ = 'payment_record'
    
    id = db.Column(db.Integer, primary_key=True)
    payment_request_id = db.Column(db.Integer, db.ForeignKey('payment_request.id'))
    po_id = db.Column(db.Integer, db.ForeignKey('purchase_order.id'))
    amount_paid = db.Column(db.Numeric(15, 2), nullable=False)
    payment_date = db.Column(db.DateTime, default=datetime.utcnow)
    payment_method = db.Column(db.String(50))  # bank_transfer, cheque, etc.
    reference_number = db.Column(db.String(100))
    proof_document = db.Column(db.String(500))  # file path or URL
    processed_by = db.Column(db.Integer, db.ForeignKey('user.id'))
    
    # Relationships
    payment_request = db.relationship('PaymentRequest', backref='payment_records')
    purchase_order = db.relationship('PurchaseOrder', backref='payment_records')
    processor = db.relationship('User', backref='payment_records')


class ChangeOrder(db.Model):
    """Change order for variations to contract."""
    __tablename__ = 'change_order'
    
    id = db.Column(db.Integer, primary_key=True)
    project_id = db.Column(db.Integer, db.ForeignKey('project.id'), nullable=False)
    co_number = db.Column(db.String(50), unique=True, index=True)
    description = db.Column(db.Text, nullable=False)
    justification = db.Column(db.Text)
    cost_impact = db.Column(db.Numeric(15, 2))
    schedule_impact = db.Column(db.String(100))  # e.g., '+5 days'
    approval_state = db.Column(db.Enum(ApprovalState), default=ApprovalState.DRAFT)
    created_by = db.Column(db.Integer, db.ForeignKey('user.id'))
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    
    # Relationships
    approval_logs = db.relationship('ApprovalLog', backref='change_order_ref',
                                   foreign_keys='ApprovalLog.entity_id',
                                   primaryjoin='and_(ApprovalLog.entity_type=="change_order", ApprovalLog.entity_id==ChangeOrder.id)')


class IPC(db.Model):
    """Interim Payment Certificate."""
    __tablename__ = 'ipc'
    
    id = db.Column(db.Integer, primary_key=True)
    project_id = db.Column(db.Integer, db.ForeignKey('project.id'), nullable=False)
    ipc_number = db.Column(db.String(50), unique=True, index=True)
    period_start = db.Column(db.Date)
    period_end = db.Column(db.Date)
    total_amount = db.Column(db.Numeric(15, 2), nullable=False)
    retention_percentage = db.Column(db.Numeric(5, 2), default=10)
    retention_amount = db.Column(db.Numeric(15, 2))
    payment_amount = db.Column(db.Numeric(15, 2))
    approval_state = db.Column(db.Enum(ApprovalState), default=ApprovalState.DRAFT)
    qs_certified = db.Column(db.Boolean, default=False)
    created_by = db.Column(db.Integer, db.ForeignKey('user.id'))
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    
    # Relationships
    approval_logs = db.relationship('ApprovalLog', backref='ipc_ref',
                                   foreign_keys='ApprovalLog.entity_id',
                                   primaryjoin='and_(ApprovalLog.entity_type=="ipc", ApprovalLog.entity_id==IPC.id)')
    
    def calculate_retention(self):
        """Calculate retention amount."""
        if self.total_amount and self.retention_percentage:
            self.retention_amount = self.total_amount * (self.retention_percentage / 100)
            self.payment_amount = self.total_amount - self.retention_amount


class EquipmentRequest(db.Model):
    """Equipment maintenance or rental request."""
    __tablename__ = 'equipment_request'
    
    id = db.Column(db.Integer, primary_key=True)
    project_id = db.Column(db.Integer, db.ForeignKey('project.id'))
    equipment_description = db.Column(db.String(255), nullable=False)
    request_type = db.Column(db.String(50))  # maintenance, rental, repair
    description = db.Column(db.Text)
    estimated_cost = db.Column(db.Numeric(12, 2))
    approval_state = db.Column(db.Enum(ApprovalState), default=ApprovalState.DRAFT)
    requested_by = db.Column(db.Integer, db.ForeignKey('user.id'))
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    
    # Relationships
    approval_logs = db.relationship('ApprovalLog', backref='equipment_request_ref',
                                   foreign_keys='ApprovalLog.entity_id',
                                   primaryjoin='and_(ApprovalLog.entity_type=="equipment_request", ApprovalLog.entity_id==EquipmentRequest.id)')


class DocumentVersion(db.Model):
    """Version control for documents."""
    __tablename__ = 'document_version'
    
    id = db.Column(db.Integer, primary_key=True)
    entity_type = db.Column(db.String(50), nullable=False)  # po, boq, change_order, ipc
    entity_id = db.Column(db.Integer, nullable=False)
    version_number = db.Column(db.Integer, default=1)
    content = db.Column(db.Text)  # JSON snapshot or rich text
    created_by = db.Column(db.Integer, db.ForeignKey('user.id'))
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    change_summary = db.Column(db.Text)


class ApprovalLog(db.Model):
    """Audit trail for all approvals."""
    __tablename__ = 'approval_log'
    
    id = db.Column(db.Integer, primary_key=True)
    entity_type = db.Column(db.String(50), nullable=False)  # e.g., 'po', 'boq', 'pr', 'ipc', 'qc_inspection'
    entity_id = db.Column(db.Integer, nullable=False, index=True)
    action = db.Column(db.String(50), nullable=False)  # approved, rejected, escalated, returned
    actor_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    comment = db.Column(db.Text)
    timestamp = db.Column(db.DateTime, default=datetime.utcnow, index=True)
    
    # Relationship to actor (User who performed the action)
    actor = db.relationship('User', foreign_keys=[actor_id])
    
    def __repr__(self):
        return f'<ApprovalLog {self.entity_type}:{self.entity_id} {self.action}>'


class Notification(db.Model):
    """In-app notifications for approvers."""
    __tablename__ = 'notification'
    
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False, index=True)
    entity_type = db.Column(db.String(50))
    entity_id = db.Column(db.Integer)
    title = db.Column(db.String(255))
    message = db.Column(db.Text)
    read = db.Column(db.Boolean, default=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    
    user = db.relationship('User', backref='notifications')


class ApprovalMessage(db.Model):
    """Messages sent regarding approval logs."""
    __tablename__ = 'approval_message'
    
    id = db.Column(db.Integer, primary_key=True)
    approval_log_id = db.Column(db.Integer, db.ForeignKey('approval_log.id'), nullable=False, index=True)
    sender_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    recipient_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    subject = db.Column(db.String(255), nullable=False)
    message = db.Column(db.Text, nullable=False)
    message_type = db.Column(db.String(50), nullable=False)  # e.g., 'status_update', 'rejection_reason', 'follow_up'
    is_read = db.Column(db.Boolean, default=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow, index=True)
    
    approval_log = db.relationship('ApprovalLog', backref='messages')
    sender = db.relationship('User', foreign_keys=[sender_id], backref='sent_messages')
    recipient = db.relationship('User', foreign_keys=[recipient_id], backref='received_messages')
    
    def __repr__(self):
        return f'<ApprovalMessage {self.subject}>'


class ProjectActivityLog(db.Model):
    """Activity log for project events (staff assignments, updates, etc)."""
    __tablename__ = 'project_activity_log'
    
    id = db.Column(db.Integer, primary_key=True)
    project_id = db.Column(db.Integer, db.ForeignKey('project.id'), nullable=False, index=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    action = db.Column(db.String(100), nullable=False)  # e.g., 'staff_added', 'staff_removed', 'updated'
    description = db.Column(db.Text)
    timestamp = db.Column(db.DateTime, default=datetime.utcnow, index=True)
    
    project = db.relationship('Project', backref='activity_logs')
    user = db.relationship('User', backref='project_activity_logs')
    
    def __repr__(self):
        return f'<ProjectActivityLog {self.project_id} {self.action}>'


class Expense(db.Model):
    """Expense tracking for projects and operations."""
    __tablename__ = 'expense'
    
    id = db.Column(db.Integer, primary_key=True)
    project_id = db.Column(db.Integer, db.ForeignKey('project.id'))
    category = db.Column(db.String(100), nullable=False)
    description = db.Column(db.Text)
    amount = db.Column(db.Float, nullable=False)
    date = db.Column(db.DateTime, default=datetime.utcnow)
    status = db.Column(db.String(20), default='pending')  # pending, approved, rejected
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    
    project = db.relationship('Project', backref='expenses')


class LeaveRequest(db.Model):
    """Employee leave request with HR approval workflow."""
    __tablename__ = 'leave_request'

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    leave_type = db.Column(db.String(50), nullable=False)  # casual, compensate, annual, maternity, paternity
    start_date = db.Column(db.Date, nullable=False)
    end_date = db.Column(db.Date, nullable=False)
    days_requested = db.Column(db.Integer, nullable=False)
    reason = db.Column(db.Text)
    status = db.Column(db.String(30), default='pending')  # pending, approved, rejected
    reviewed_by = db.Column(db.Integer, db.ForeignKey('user.id'))
    reviewed_at = db.Column(db.DateTime)
    rejection_reason = db.Column(db.Text)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    requester = db.relationship('User', foreign_keys=[user_id], backref='leave_requests')
    reviewer = db.relationship('User', foreign_keys=[reviewed_by], backref='leave_reviews')


class ChartOfAccount(db.Model):
    """Chart of accounts for bookkeeping."""
    __tablename__ = 'chart_of_account'

    id = db.Column(db.Integer, primary_key=True)
    account_code = db.Column(db.String(30), unique=True, nullable=False, index=True)
    account_name = db.Column(db.String(255), nullable=False)
    account_type = db.Column(db.String(30), nullable=False)  # asset, liability, revenue, expense, equity
    parent_account_id = db.Column(db.Integer, db.ForeignKey('chart_of_account.id'))
    description = db.Column(db.Text)
    is_active = db.Column(db.Boolean, default=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    parent_account = db.relationship('ChartOfAccount', remote_side=[id], backref='child_accounts')


class LedgerEntry(db.Model):
    """General ledger transactions (double-entry style)."""
    __tablename__ = 'ledger_entry'

    id = db.Column(db.Integer, primary_key=True)
    entry_date = db.Column(db.DateTime, default=datetime.utcnow, index=True)
    reference = db.Column(db.String(100))
    description = db.Column(db.Text, nullable=False)
    account_id = db.Column(db.Integer, db.ForeignKey('chart_of_account.id'), nullable=False)
    debit = db.Column(db.Numeric(15, 2), default=0)
    credit = db.Column(db.Numeric(15, 2), default=0)
    entity_type = db.Column(db.String(50))
    entity_id = db.Column(db.Integer)
    created_by = db.Column(db.Integer, db.ForeignKey('user.id'))
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    account = db.relationship('ChartOfAccount', backref='ledger_entries')
    creator = db.relationship('User', backref='ledger_entries')


class RevenueSale(db.Model):
    """Revenue/Sales records."""
    __tablename__ = 'revenue_sale'

    id = db.Column(db.Integer, primary_key=True)
    project_id = db.Column(db.Integer, db.ForeignKey('project.id'))
    customer_name = db.Column(db.String(255), nullable=False)
    description = db.Column(db.Text)
    amount = db.Column(db.Numeric(15, 2), nullable=False)
    sale_date = db.Column(db.DateTime, default=datetime.utcnow)
    status = db.Column(db.String(30), default='received')  # received, pending, cancelled
    created_by = db.Column(db.Integer, db.ForeignKey('user.id'))
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    project = db.relationship('Project', backref='revenue_sales')
    creator = db.relationship('User', backref='revenue_sales')


class ProjectPaymentRequest(db.Model):
    """Project-specific payment request sent to finance for approval."""
    __tablename__ = 'project_payment_request'

    id = db.Column(db.Integer, primary_key=True)
    project_id = db.Column(db.Integer, db.ForeignKey('project.id'), nullable=False)
    requested_by = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    title = db.Column(db.String(255), nullable=False)
    description = db.Column(db.Text)
    amount = db.Column(db.Numeric(15, 2), nullable=False)
    request_date = db.Column(db.DateTime, default=datetime.utcnow)
    approval_state = db.Column(db.String(30), default='pending')  # pending, approved, rejected
    approved_by = db.Column(db.Integer, db.ForeignKey('user.id'))
    approved_at = db.Column(db.DateTime)
    rejection_reason = db.Column(db.Text)

    project = db.relationship('Project', backref='project_payment_requests')
    requester = db.relationship('User', foreign_keys=[requested_by], backref='project_payment_requests')
    approver = db.relationship('User', foreign_keys=[approved_by], backref='project_payment_requests_approved')


class BankAccount(db.Model):
    """Bank account management."""
    __tablename__ = 'bank_account'
    
    id = db.Column(db.Integer, primary_key=True)
    account_name = db.Column(db.String(255), nullable=False)
    account_number = db.Column(db.String(50), nullable=False, unique=True)
    bank_name = db.Column(db.String(255))
    balance = db.Column(db.Float, default=0.0)
    currency = db.Column(db.String(3), default='NGN')
    is_active = db.Column(db.Boolean, default=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)


class BankReconciliation(db.Model):
    """Bank reconciliation records."""
    __tablename__ = 'bank_reconciliations'
    
    id = db.Column(db.Integer, primary_key=True)
    bank_account_id = db.Column(db.Integer, db.ForeignKey('bank_account.id'))
    statement_date = db.Column(db.DateTime)
    statement_balance = db.Column(db.Float)
    ledger_balance = db.Column(db.Float)
    difference = db.Column(db.Float)
    balance = db.Column(db.Float, default=0.0)
    status = db.Column(db.String(20), default='pending')  # pending, reconciled, discrepancy
    notes = db.Column(db.Text)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    
    bank_account = db.relationship('BankAccount', backref='reconciliations')


# ======================== PROJECT MANAGEMENT MODELS ========================

class Milestone(db.Model):
    """Project milestones and deliverables."""
    __tablename__ = 'milestone'
    
    id = db.Column(db.Integer, primary_key=True)
    project_id = db.Column(db.Integer, db.ForeignKey('project.id'), nullable=False)
    name = db.Column(db.String(255), nullable=False)
    description = db.Column(db.Text)
    planned_start_date = db.Column(db.Date)
    planned_end_date = db.Column(db.Date)
    actual_start_date = db.Column(db.Date)
    actual_end_date = db.Column(db.Date)
    deliverables = db.Column(db.Text)
    status = db.Column(db.String(50), default='not_started')  # not_started, in_progress, completed, delayed
    completion_percentage = db.Column(db.Integer, default=0)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    
    def __repr__(self):
        return f'<Milestone {self.name}>'


class DailyProductionReport(db.Model):
    """Daily Production Report (DPR) for project tracking."""
    __tablename__ = 'daily_production_report'
    
    id = db.Column(db.Integer, primary_key=True)
    project_id = db.Column(db.Integer, db.ForeignKey('project.id'), nullable=False)
    report_date = db.Column(db.Date, nullable=False)
    status = db.Column(db.String(50), default='draft')  # draft, sent_to_staff, completed, rejected
    created_by = db.Column(db.String(255))
    weather_conditions = db.Column(db.Text)
    work_description = db.Column(db.Text)
    unit = db.Column(db.String(50))
    staff_report = db.Column(db.Text)
    general_remarks = db.Column(db.Text)
    rejection_reason = db.Column(db.Text)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    
    def __repr__(self):
        return f'<DPR {self.report_date}>'


class ProjectMaterial(db.Model):
    """Project materials and supplies."""
    __tablename__ = 'project_material'
    
    id = db.Column(db.Integer, primary_key=True)
    project_id = db.Column(db.Integer, db.ForeignKey('project.id'), nullable=False)
    description = db.Column(db.String(255), nullable=False)
    category = db.Column(db.String(100))
    unit = db.Column(db.String(50))
    quantity_allocated = db.Column(db.Float, default=0)
    quantity_used = db.Column(db.Float, default=0)
    unit_cost = db.Column(db.Numeric(15, 2), default=0)
    specification = db.Column(db.Text)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    
    def __repr__(self):
        return f'<Material {self.description}>'


class ProjectEquipment(db.Model):
    """Project equipment and machinery."""
    __tablename__ = 'project_equipment'
    
    id = db.Column(db.Integer, primary_key=True)
    project_id = db.Column(db.Integer, db.ForeignKey('project.id'), nullable=False)
    name = db.Column(db.String(255), nullable=False)
    type = db.Column(db.String(100))
    description = db.Column(db.Text)
    status = db.Column(db.String(50), default='operational')  # operational, maintenance, idle
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    
    def __repr__(self):
        return f'<Equipment {self.name}>'


class ProjectDocument(db.Model):
    """Project documents and files."""
    __tablename__ = 'project_document'
    
    id = db.Column(db.Integer, primary_key=True)
    project_id = db.Column(db.Integer, db.ForeignKey('project.id'), nullable=False)
    title = db.Column(db.String(255), nullable=False)
    description = db.Column(db.Text)
    document_type = db.Column(db.String(100))
    file_path = db.Column(db.String(500))
    file_name = db.Column(db.String(255))
    uploaded_by_id = db.Column(db.Integer, db.ForeignKey('user.id'))
    version = db.Column(db.Integer, default=1)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    
    uploaded_by = db.relationship('User', backref='uploaded_documents')
    
    def __repr__(self):
        return f'<Document {self.title}>'


class ProjectBudgetRecord(db.Model):
    """Project budget tracking and expenditure records."""
    __tablename__ = 'project_budget_record'
    
    id = db.Column(db.Integer, primary_key=True)
    project_id = db.Column(db.Integer, db.ForeignKey('project.id'), nullable=False)
    category = db.Column(db.String(100))
    planned_amount = db.Column(db.Numeric(15, 2), default=0)
    spent_amount = db.Column(db.Numeric(15, 2), default=0)
    forecast_amount = db.Column(db.Numeric(15, 2), default=0)
    variance = db.Column(db.Numeric(15, 2), default=0)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    
    def __repr__(self):
        return f'<BudgetRecord {self.category}>'


class Payroll(db.Model):
    """Payroll document with approval workflow for monthly staff payments."""
    __tablename__ = 'payroll'
    
    id = db.Column(db.Integer, primary_key=True)
    payroll_number = db.Column(db.String(50), unique=True, index=True)
    payroll_month = db.Column(db.Date, nullable=False)  # e.g., 2024-12-01
    approval_state = db.Column(db.Enum(ApprovalState), default=ApprovalState.DRAFT)
    total_basic_salary = db.Column(db.Numeric(15, 2), default=0)
    total_deductions = db.Column(db.Numeric(15, 2), default=0)
    total_net_salary = db.Column(db.Numeric(15, 2), default=0)
    created_by = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    approved_by = db.Column(db.Integer, db.ForeignKey('user.id'))
    rejection_reason = db.Column(db.Text)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    approved_at = db.Column(db.DateTime)
    
    # Relationships
    items = db.relationship('PayrollItem', backref='payroll', cascade='all, delete-orphan')
    creator = db.relationship('User', foreign_keys=[created_by], backref='payrolls_created')
    approver = db.relationship('User', foreign_keys=[approved_by], backref='payrolls_approved')
    approval_logs = db.relationship('ApprovalLog', backref='payroll_ref',
                                   foreign_keys='ApprovalLog.entity_id',
                                   primaryjoin='and_(ApprovalLog.entity_type=="payroll", ApprovalLog.entity_id==Payroll.id)')
    
    def __repr__(self):
        return f'<Payroll {self.payroll_number}>'


class PayrollItem(db.Model):
    """Individual staff payment item in a payroll."""
    __tablename__ = 'payroll_item'
    
    id = db.Column(db.Integer, primary_key=True)
    payroll_id = db.Column(db.Integer, db.ForeignKey('payroll.id'), nullable=False)
    staff_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    basic_salary = db.Column(db.Numeric(12, 2), nullable=False)
    deductions = db.Column(db.Numeric(12, 2), default=0)
    net_salary = db.Column(db.Numeric(12, 2), nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    
    # Relationships
    staff = db.relationship('User', backref='payroll_items')
    
    def __repr__(self):
        return f'<PayrollItem {self.staff.name}>'


class DPRTemplate(db.Model):
    """DPR Template - defines fields for daily production reports."""
    __tablename__ = 'dpr_template'
    
    id = db.Column(db.Integer, primary_key=True)
    project_id = db.Column(db.Integer, db.ForeignKey('project.id'), nullable=False)
    name = db.Column(db.String(255), nullable=False)
    description = db.Column(db.Text)
    is_active = db.Column(db.Boolean, default=True)
    created_by = db.Column(db.Integer, db.ForeignKey('user.id'))
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    
    # Relationships
    template_fields = db.relationship('DPRTemplateField', backref='template', cascade='all, delete-orphan')
    dpr_submissions = db.relationship('DPRSubmission', backref='template', cascade='all, delete-orphan')
    
    def __repr__(self):
        return f'<DPRTemplate {self.name}>'


class DPRTemplateField(db.Model):
    """Fields in DPR Template."""
    __tablename__ = 'dpr_template_field'
    
    id = db.Column(db.Integer, primary_key=True)
    template_id = db.Column(db.Integer, db.ForeignKey('dpr_template.id'), nullable=False)
    field_name = db.Column(db.String(255), nullable=False)
    field_type = db.Column(db.String(50), default='text')  # text, number, select, date, textarea
    is_required = db.Column(db.Boolean, default=False)
    display_order = db.Column(db.Integer, default=0)
    options = db.Column(db.Text)  # JSON for select options
    
    def __repr__(self):
        return f'<TemplateField {self.field_name}>'


class DPRSubmission(db.Model):
    """Daily Production Report Submission by staff."""
    __tablename__ = 'dpr_submission'
    
    id = db.Column(db.Integer, primary_key=True)
    template_id = db.Column(db.Integer, db.ForeignKey('dpr_template.id'), nullable=False)
    project_id = db.Column(db.Integer, db.ForeignKey('project.id'), nullable=False)
    submission_date = db.Column(db.Date, nullable=False)
    submitted_by = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    status = db.Column(db.String(50), default='draft')  # draft, submitted, approved, rejected
    submission_time = db.Column(db.DateTime)
    approved_by = db.Column(db.Integer, db.ForeignKey('user.id'))
    approval_time = db.Column(db.DateTime)
    rejection_reason = db.Column(db.Text)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    
    # Relationships
    submitter = db.relationship('User', foreign_keys=[submitted_by], backref='dpr_submissions')
    approver = db.relationship('User', foreign_keys=[approved_by], backref='approved_dprs')
    field_responses = db.relationship('DPRFieldResponse', backref='submission', cascade='all, delete-orphan')
    
    def __repr__(self):
        return f'<DPRSubmission {self.submission_date}>'


class DPRFieldResponse(db.Model):
    """Response to a specific DPR field."""
    __tablename__ = 'dpr_field_response'
    
    id = db.Column(db.Integer, primary_key=True)
    submission_id = db.Column(db.Integer, db.ForeignKey('dpr_submission.id'), nullable=False)
    template_field_id = db.Column(db.Integer, db.ForeignKey('dpr_template_field.id'), nullable=False)
    response_value = db.Column(db.Text)  # Store value as text, convert as needed
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    
    def __repr__(self):
        return f'<FieldResponse {self.id}>'


class StaffCompensation(db.Model):
    """Staff compensation and salary information with flexible deduction types."""
    __tablename__ = 'staff_compensation'
    
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False, unique=True)
    basic_salary = db.Column(db.Numeric(12, 2), nullable=False)
    allowances = db.Column(db.Numeric(12, 2), default=0)  # Housing, transport, etc.
    gross_salary = db.Column(db.Numeric(12, 2), nullable=False)  # Basic + Allowances
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    
    # Relationships
    user = db.relationship('User', backref='compensation')
    deductions = db.relationship('PayrollDeduction', backref='compensation', cascade='all, delete-orphan')
    
    def calculate_gross_salary(self):
        """Calculate gross salary from basic and allowances."""
        self.gross_salary = float(self.basic_salary or 0) + float(self.allowances or 0)
    
    def get_total_deductions(self):
        """Calculate total deductions from all deduction records."""
        return sum(float(d.amount or 0) for d in self.deductions)
    
    def get_net_salary(self):
        """Calculate net salary: gross - total deductions."""
        return float(self.gross_salary or 0) - self.get_total_deductions()
    
    def __repr__(self):
        return f'<StaffCompensation {self.user.name if self.user else "Unknown"}>'


class PayrollDeduction(db.Model):
    """Flexible deduction entries for staff compensation."""
    __tablename__ = 'payroll_deduction'
    
    id = db.Column(db.Integer, primary_key=True)
    compensation_id = db.Column(db.Integer, db.ForeignKey('staff_compensation.id'), nullable=False)
    deduction_type = db.Column(db.String(100), nullable=False)  # e.g., 'Tax', 'Insurance', 'Loan', 'Union Dues', 'Custom'
    description = db.Column(db.String(255))  # Detailed description
    amount = db.Column(db.Numeric(12, 2), nullable=False)
    is_recurring = db.Column(db.Boolean, default=True)  # Applies to all payroll periods
    effective_from = db.Column(db.Date)  # Date deduction starts
    effective_to = db.Column(db.Date)  # Date deduction ends (if any)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    
    def __repr__(self):
        return f'<PayrollDeduction {self.deduction_type}: {self.amount}>'


class StaffImportBatch(db.Model):
    """Batch of staff imported from Excel file with approval workflow."""
    __tablename__ = 'staff_import_batch'
    
    id = db.Column(db.Integer, primary_key=True)
    batch_name = db.Column(db.String(255), nullable=False)
    file_name = db.Column(db.String(255), nullable=False)
    file_path = db.Column(db.String(500))  # Path to uploaded Excel file
    total_records = db.Column(db.Integer, default=0)
    imported_records = db.Column(db.Integer, default=0)
    failed_records = db.Column(db.Integer, default=0)
    approval_state = db.Column(db.Enum(ApprovalState), default=ApprovalState.DRAFT)
    created_by = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    approved_by = db.Column(db.Integer, db.ForeignKey('user.id'))
    rejection_reason = db.Column(db.Text)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    approved_at = db.Column(db.DateTime)
    
    # Relationships
    items = db.relationship('StaffImportItem', backref='batch', cascade='all, delete-orphan')
    creator = db.relationship('User', foreign_keys=[created_by], backref='import_batches_created')
    approver = db.relationship('User', foreign_keys=[approved_by], backref='import_batches_approved')
    
    def __repr__(self):
        return f'<StaffImportBatch {self.batch_name}>'


class StaffImportItem(db.Model):
    """Individual staff record in an import batch."""
    __tablename__ = 'staff_import_item'
    
    id = db.Column(db.Integer, primary_key=True)
    batch_id = db.Column(db.Integer, db.ForeignKey('staff_import_batch.id'), nullable=False)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'))  # Linked user after import
    
    # Personal Information
    first_name = db.Column(db.String(255), nullable=False)
    last_name = db.Column(db.String(255), nullable=False)
    email = db.Column(db.String(255), nullable=False)
    phone = db.Column(db.String(20))
    gender = db.Column(db.String(10))
    date_of_birth = db.Column(db.Date)
    address = db.Column(db.Text)
    city = db.Column(db.String(100))
    state = db.Column(db.String(100))
    marital_status = db.Column(db.String(50))
    
    # Employment Information
    employee_id = db.Column(db.String(50))
    date_of_employment = db.Column(db.Date)
    department = db.Column(db.String(100))  # HR, Finance, Procurement, QC, etc.
    position = db.Column(db.String(100))
    role = db.Column(db.String(50))  # System role: hr_staff, finance_staff, admin, etc.
    
    # Next of Kin
    nok_full_name = db.Column(db.String(255))
    nok_relationship = db.Column(db.String(100))
    nok_phone = db.Column(db.String(20))
    nok_email = db.Column(db.String(255))
    nok_address = db.Column(db.Text)
    nok_city = db.Column(db.String(100))
    nok_state = db.Column(db.String(100))
    
    # Compensation
    basic_salary = db.Column(db.Numeric(12, 2))
    allowances = db.Column(db.Numeric(12, 2), default=0)
    
    # Import Status
    status = db.Column(db.String(50), default='pending')  # pending, imported, failed, skipped
    error_message = db.Column(db.Text)  # Error details if import failed
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    
    def __repr__(self):
        return f'<StaffImportItem {self.first_name} {self.last_name}>'


class DepartmentAccess(db.Model):
    """Controls which departments a staff member has access to."""
    __tablename__ = 'department_access'
    
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    department = db.Column(db.String(100), nullable=False)  # HR, Finance, Procurement, QC, Projects, etc.
    access_level = db.Column(db.String(50), default='view')  # view, edit, approve
    is_active = db.Column(db.Boolean, default=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    
    # Composite unique constraint on user_id and department
    __table_args__ = (db.UniqueConstraint('user_id', 'department', name='uq_user_department'),)
    
    def __repr__(self):
        return f'<DepartmentAccess {self.department}>'
