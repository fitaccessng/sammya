# FitAccess Construction ERP

A comprehensive Construction Enterprise Resource Planning platform with full role-based approval workflows.

## Features

- **Role-Based Access Control**: 20+ roles with granular permissions
- **Multi-Stage Approval Workflows**: BOQ, PO, Payments, QC, Change Orders, IPC
- **Budget Management**: Real-time tracking and threshold-based escalation
- **Quality Control Integration**: Delivery inspections gate payments
- **Audit Trail**: Complete approval history for compliance
- **Responsive Dashboard**: Tailwind CSS UI with role-specific views

## Tech Stack

**Backend**: Flask + SQLAlchemy + Python
**Frontend**: HTML5 + Tailwind CSS + Vanilla JavaScript
**Database**: SQLite (development) / PostgreSQL (production)

## Project Structure

```
new_sammya/
├── app/
│   ├── __init__.py
│   ├── factory.py              # Flask app factory
│   ├── models.py               # SQLAlchemy models
│   ├── approvals.py            # Approval state machine
│   ├── auth/
│   │   ├── __init__.py
│   │   ├── routes.py           # Login/logout
│   │   └── decorators.py       # @role_required decorator
│   ├── procurement/            # PR & PO workflows
│   ├── qc/                     # Quality control
│   ├── finance/                # Payment & budget
│   ├── api/                    # Generic approval endpoints
│   ├── templates/              # Jinja2 templates
│   │   ├── base.html           # Base layout with modal
│   │   ├── auth/
│   │   ├── dashboard/
│   │   ├── procurement/
│   │   ├── qc/
│   │   ├── finance/
│   │   └── errors/
│   └── static/
│       ├── css/style.css
│       └── js/main.js
├── migrations/                 # Alembic migrations (if using)
├── tests/                      # Unit tests
├── run.py                      # Entry point
├── seed.py                     # Seed test data
└── requirements.txt
```

## Installation

### 1. Clone and Setup

```bash
cd new_sammya
python -m venv venv
source venv/bin/activate  # On Windows: venv\Scripts\activate
pip install -r requirements.txt
```

### 2. Initialize Database

```bash
python seed.py
```

This creates:
- 10 test users with different roles
- 3 sample projects
- 4 sample vendors

### 3. Run Application

```bash
python run.py
```

Visit `http://localhost:5000`

## Test Users

All users have password: `password123`

| Email | Role | Usage |
|-------|------|-------|
| admin@fitaccess.com | admin | Full system access |
| john@fitaccess.com | executive | Approves high-value items |
| sarah@fitaccess.com | cost_control_manager | Approves budgets |
| mike@fitaccess.com | procurement_manager | Manages POs |
| lisa@fitaccess.com | qc_manager | Approves inspections |
| david@fitaccess.com | finance_manager | Verifies & approves payments |
| emma@fitaccess.com | project_manager | Project oversight |
| tom@fitaccess.com | qc_staff | Performs inspections |
| amy@fitaccess.com | procurement_staff | Creates RFQs/POs |
| staff@fitaccess.com | project_staff | Submits requests |

## Core Workflows

### 1. Material Request → PO → Delivery → QC → Payment

```
Project Staff (Draft)
    ↓
Project Manager (Approve)
    ↓
Cost Control Manager (Budget Check)
    ↓
Procurement Manager (Create PO)
    ↓
(Optional) Executive (High-value approval)
    ↓
Store Manager (GRN)
    ↓
QC Staff → QC Manager (Inspection approval)
    ↓
Finance Manager (Verify funds) → Accounts Payable (Process)
```

### 2. BOQ Approval

```
QS Staff (Draft)
    ↓
Cost Control Manager (Review)
    ↓
QS Manager (Validate)
    ↓
Admin/Project Manager (Publish)
```

### 3. Change Order

```
Project Manager (Draft)
    ↓
QS Manager (Quantify)
    ↓
Cost Control Manager (Cost review)
    ↓
Procurement (If materials needed)
    ↓
Executive (If > threshold)
```

## API Endpoints

### Generic Approval API

```bash
# Approve an entity
POST /api/approve
{
  "entity_type": "purchase_order",
  "entity_id": 123,
  "comment": "Approved - all docs in order"
}

# Reject an entity
POST /api/reject
{
  "entity_type": "purchase_order",
  "entity_id": 123,
  "comment": "Budget exceeded, please revise"
}

# Get pending approvals for current user
GET /api/pending-approvals

# Get approval audit trail
GET /api/approval-history/<entity_type>/<entity_id>
```

### Procurement Endpoints

```bash
POST   /procurement/pr/create/<project_id>              # Create material request
POST   /procurement/pr/<pr_id>/submit                   # Submit PR
POST   /procurement/pr/<pr_id>/approve                  # PM approval
POST   /procurement/pr/<pr_id>/cost-control-approve     # Cost control approval

POST   /procurement/po/create                           # Create PO
POST   /procurement/po/<po_id>/approve                  # Procurement manager approval
POST   /procurement/po/<po_id>/executive-approve        # Executive approval
POST   /procurement/po/<po_id>/issue                    # Issue to vendor
```

### QC Endpoints

```bash
POST   /qc/delivery/create/<po_id>                      # Create GRN
POST   /qc/inspection/create/<delivery_id>              # Create QC inspection
POST   /qc/inspection/<inspection_id>/approve           # Approve inspection
POST   /qc/inspection/<inspection_id>/reject            # Reject inspection
GET    /qc/api/inspection-status/<po_id>               # Check QC status (gates payment)
```

### Finance Endpoints

```bash
POST   /finance/payment/create/<po_id>                  # Create payment request
POST   /finance/payment/<payment_id>/verify             # Finance manager verification
POST   /finance/payment/<payment_id>/executive-approve  # Executive approval
POST   /finance/payment/<payment_id>/process            # Process payment
GET    /finance/api/payment-eligible/<po_id>           # Check payment eligibility
GET    /finance/budget-report/<project_id>             # Budget vs committed spend
```

## Business Rules

### Thresholds (in /app/approvals.py)

```python
THRESHOLDS = {
    'executive_po_approval': 500000,           # POs > $500k need executive approval
    'executive_payment_approval': 1000000,     # Payments > $1M need executive approval
    'executive_change_order': 250000,          # Change orders > $250k need executive approval
    'budget_warning': 0.95,                    # Warn at 95% of project budget
}
```

### Payment Gates

- QC inspection must be **APPROVED** before payment can be released
- Finance manager must **VERIFY** funds before payment is released
- High-value payments require **EXECUTIVE APPROVAL**

### Budget Enforcement

- System warns when committed spend (approved POs) reaches 95% of budget
- Cannot create new large POs once budget threshold is reached (without override)

### State Transitions

```python
DRAFT       → PENDING, CANCELLED
PENDING     → REVIEW, REJECTED, CANCELLED
REVIEW      → APPROVED, REJECTED, ESCALATED
ESCALATED   → REVIEW, APPROVED, REJECTED
APPROVED    → CANCELLED
REJECTED    → PENDING, CANCELLED
```

## Role Permissions Matrix

See `/app/approvals.py` `ROLE_APPROVAL_MATRIX` for complete matrix.

Key approvers by entity:

| Entity | Creator | Reviewer 1 | Reviewer 2 | Final |
|--------|---------|-----------|-----------|-------|
| Material Request | Project Staff | Project Manager | Cost Control Manager | - |
| Purchase Order | Procurement Staff | Procurement Manager | Executive* | Procurement Manager |
| BOQ | QS Staff | Cost Control Manager | QS Manager | Admin |
| QC Inspection | QC Staff | QC Manager | - | - |
| IPC | QS Staff | QS Manager | Cost Control Manager | Executive* |
| Payment | Finance Manager | Finance Manager | Executive* | Accounts Payable |

*If value exceeds threshold

## Error Handling

The application includes:
- Input validation on all forms
- Server-side authorization checks
- Proper HTTP status codes (400, 403, 404, 500)
- Flash messages for user feedback
- Exception handling with database rollback

## Extending the System

### Adding a New Approval Entity

1. Create model in `models.py` inheriting approval fields
2. Add to `ENTITY_MODELS` in `api/routes.py`
3. Create routes in appropriate blueprint
4. Add to `ROLE_APPROVAL_MATRIX` in `approvals.py`
5. Create templates in `templates/`

### Adding a New Role

1. Add role string constant (e.g., `'new_role'`)
2. Update `ROLE_APPROVAL_MATRIX` with approval rights
3. Add UI routes with `@role_required(['new_role'])`
4. Create role-specific views

## Testing

```bash
# Run tests
pytest tests/

# Run specific test
pytest tests/test_approvals.py::test_invalid_transition
```

## Performance Notes

- Approval history indexed on `(entity_type, entity_id, timestamp)`
- Project-user relationship is many-to-many for flexibility
- Consider adding caching for budget calculations on large projects

## Security Considerations

- Passwords hashed with Werkzeug
- CSRF protection on forms (implement with Flask-WTF in production)
- JWT or session-based auth for API
- All inputs validated server-side
- Role checks on every protected route
- SQL injection prevention via SQLAlchemy ORM

## Future Enhancements

- [ ] Email notifications for approvers
- [ ] PDF export for documents
- [ ] Batch approval for multiple items
- [ ] Approval delegations/worflow rules
- [ ] Integration with accounting systems
- [ ] Mobile app
- [ ] Advanced reporting/analytics
- [ ] Document version control
- [ ] Digital signatures
- [ ] Multi-currency support

## License

Proprietary - FitAccess

## Support

For issues or questions, contact: info@fitaccess.com
