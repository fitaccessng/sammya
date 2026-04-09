#!/usr/bin/env python
"""Quick test script for finance activities route."""

from app.factory import create_app
from app.models import User, PaymentRequest, ApprovalState, ApprovalLog

app = create_app('development')

with app.app_context():
    try:
        # Test 1: Check if User has has_any_role method
        user = User.query.first()
        if user:
            print(f"✓ User found: {user.name}")
            print(f"✓ has_any_role method exists: {hasattr(user, 'has_any_role')}")
            print(f"✓ has_any_role('admin'): {user.has_any_role('admin')}")
        else:
            print("⚠ No users in database")
        
        # Test 2: Check PaymentRequest queries
        pending = PaymentRequest.query.filter(PaymentRequest.approval_state == ApprovalState.PENDING).count()
        approved = PaymentRequest.query.filter(PaymentRequest.approval_state == ApprovalState.APPROVED).count()
        print(f"✓ Pending payments: {pending}")
        print(f"✓ Approved payments: {approved}")
        
        # Test 3: Check ApprovalLog queries
        logs = ApprovalLog.query.filter(
            ApprovalLog.entity_type.in_(['payroll', 'payment', 'expense', 'invoice'])
        ).count()
        print(f"✓ Finance-related approval logs: {logs}")
        
        print("\n✅ All tests passed!")
        
    except Exception as e:
        print(f"❌ Error: {str(e)}")
        import traceback
        traceback.print_exc()
