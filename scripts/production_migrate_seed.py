#!/usr/bin/env python3
"""Run idempotent production database migrations and optional seed checks.

Usage:
    DATABASE_URL="postgresql://..." python scripts/production_migrate_seed.py --dry-run
    DATABASE_URL="postgresql://..." python scripts/production_migrate_seed.py --apply

Optional admin seed:
    SEED_ADMIN_EMAIL="admin@example.com" SEED_ADMIN_PASSWORD="..." \
      DATABASE_URL="postgresql://..." python scripts/production_migrate_seed.py --apply --seed-admin
"""

from __future__ import annotations

import argparse
import os
import sys

from app.factory import create_app
from app.models import (
    User,
    db,
    ensure_password_reset_request_table,
    ensure_staff_import_item_columns,
    ensure_staff_profile_columns,
)

# Register all model metadata before db.create_all().
import app.models  # noqa: F401
import app.payroll_models  # noqa: F401


def require_database_url() -> None:
    database_url = os.environ.get("DATABASE_URL")
    if not database_url:
        raise SystemExit("DATABASE_URL is required for production migration.")
    if "sqlite" in database_url.lower():
        raise SystemExit("Refusing to run production migration against SQLite.")


def seed_admin() -> str:
    email = os.environ.get("SEED_ADMIN_EMAIL")
    password = os.environ.get("SEED_ADMIN_PASSWORD")
    if not email or not password:
        return "admin seed skipped: SEED_ADMIN_EMAIL and SEED_ADMIN_PASSWORD are not both set"

    user = User.query.filter_by(email=email.strip().lower()).first()
    if user:
        user.role = "admin"
        user.is_active = True
        return f"admin verified: {email}"

    user = User(
        name=os.environ.get("SEED_ADMIN_NAME", "System Admin"),
        email=email.strip().lower(),
        role="admin",
        is_active=True,
    )
    user.set_password(password)
    db.session.add(user)
    return f"admin created: {email}"


def main() -> int:
    parser = argparse.ArgumentParser(description="Run production database migrations and seeds.")
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--dry-run", action="store_true", help="Validate connection and report pending actions.")
    mode.add_argument("--apply", action="store_true", help="Apply migrations and seed data.")
    parser.add_argument("--seed-admin", action="store_true", help="Create/verify an admin user from SEED_ADMIN_* env vars.")
    args = parser.parse_args()

    require_database_url()
    app = create_app("production")

    with app.app_context():
        table_names = sorted(db.metadata.tables.keys())
        print(f"Registered tables: {len(table_names)}")

        if args.dry_run:
            print("DRY RUN: would create missing tables and ensure staff/profile/import columns.")
            if args.seed_admin:
                print("DRY RUN: would create/verify admin user from SEED_ADMIN_* env vars.")
            return 0

        db.create_all()
        ensure_password_reset_request_table()
        ensure_staff_profile_columns()
        ensure_staff_import_item_columns()

        seed_message = "admin seed not requested"
        if args.seed_admin:
            seed_message = seed_admin()

        db.session.commit()
        print("APPLIED: database schema verified.")
        print(f"APPLIED: {seed_message}.")
        return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        db.session.rollback()
        print(f"Migration failed: {exc}", file=sys.stderr)
        raise
