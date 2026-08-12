#!/usr/bin/env python3
"""Sync Sammya staff records from the staff Excel template.

Dry-run by default:
    python scripts/sync_staff_from_excel.py "/path/to/SAMMYA ERP STAFF LIST.xlsx"

Apply changes:
    DATABASE_URL="postgresql://..." python scripts/sync_staff_from_excel.py "/path/to/SAMMYA ERP STAFF LIST.xlsx" --apply
"""

from __future__ import annotations

import argparse
from collections import Counter
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any

import pandas as pd

from app.excel_import import StaffExcelParser
from app.factory import create_app
from app.models import (
    DepartmentAccess,
    NextOfKin,
    PayrollDeduction,
    StaffCompensation,
    User,
    db,
)


@dataclass
class SyncStats:
    created: int = 0
    updated: int = 0
    skipped: int = 0
    nok_synced: int = 0
    deductions_synced: int = 0


def clean(value: Any) -> str | None:
    if pd.isna(value):
        return None
    text = str(value).strip()
    if text.lower() in {"", "nan", "none", "null"}:
        return None
    return text


def parse_money(value: Any) -> Decimal:
    text = clean(value)
    if not text:
        return Decimal("0.00")
    try:
        return Decimal(str(float(text.replace(",", "")))).quantize(Decimal("0.01"))
    except (InvalidOperation, ValueError):
        return Decimal("0.00")


def parse_date(value: Any):
    valid, parsed = StaffExcelParser.validate_date(value)
    return parsed if valid else None


def normalize_phone(value: Any) -> str | None:
    text = clean(value)
    if not text:
        return None
    return text[:-2] if text.endswith(".0") else text


def collect_email_counts(df: pd.DataFrame) -> Counter:
    emails = []
    for value in df.get("email", []):
        email = clean(value)
        if email:
            emails.append(email.lower())
    return Counter(emails)


def staff_email(employee_id: str, raw_email: Any, email_counts: Counter) -> str:
    email = (clean(raw_email) or "").lower()
    is_real_unique_email = (
        email
        and not email.endswith("@company.com")
        and email_counts[email] == 1
        and StaffExcelParser.validate_email(email)
    )
    return email if is_real_unique_email else f"{employee_id.lower()}@sammya.local"


def role_for_department(department: str | None) -> str:
    dept = (department or "").strip().lower()
    if dept == "finance":
        return "finance_manager"
    if dept in {"procurement", "store", "workshop", "transport"}:
        return "procurement_staff"
    if dept in {"technical", "projects", "site"}:
        return "project_staff"
    return "hr_staff"


def find_user(row: pd.Series, email_counts: Counter) -> User | None:
    employee_id = clean(row.get("employee_id"))
    if employee_id:
        user = User.query.filter_by(employee_id=employee_id).first()
        if user:
            return user

    raw_email = clean(row.get("email"))
    if raw_email:
        user = User.query.filter(User.email.ilike(raw_email.strip())).first()
        if user:
            return user

    name = clean(row.get("last_name.1")) or clean(row.get("last_name"))
    if name:
        user = User.query.filter(User.name.ilike(name.strip())).first()
        if user:
            return user

    if employee_id:
        generated_email = staff_email(employee_id, row.get("email"), email_counts)
        return User.query.filter_by(email=generated_email).first()

    return None


def sync_staff_row(row: pd.Series, email_counts: Counter, create_missing: bool) -> tuple[str, User | None]:
    employee_id = clean(row.get("employee_id"))
    if not employee_id:
        return "skipped", None

    user = find_user(row, email_counts)
    action = "updated"

    if not user:
        if not create_missing:
            return "skipped", None
        email = staff_email(employee_id, row.get("email"), email_counts)
        user = User(email=email, employee_id=employee_id, role="hr_staff", is_active=True)
        user.set_password(StaffExcelParser.prepare_password(email))
        db.session.add(user)
        action = "created"

    department = StaffExcelParser.normalize_department(clean(row.get("department")))
    user.name = clean(row.get("last_name.1")) or clean(row.get("last_name")) or user.name or employee_id
    user.employee_id = employee_id
    user.email = staff_email(employee_id, row.get("email"), email_counts)
    user.phone = normalize_phone(row.get("phone_number"))
    user.date_of_birth = parse_date(row.get("date_of_birth"))
    user.gender = clean(row.get("gender"))
    user.date_of_employment = parse_date(row.get("joining_date"))
    user.department = department
    user.position = clean(row.get("position"))
    user.employment_type = clean(row.get("employment_type"))
    user.basic_salary = parse_money(row.get("basic_salary"))
    if not user.role:
        user.role = role_for_department(department)
    user.is_active = True
    db.session.flush()

    compensation = StaffCompensation.query.filter_by(user_id=user.id).first()
    if not compensation:
        compensation = StaffCompensation(user_id=user.id, basic_salary=0, allowances=0, gross_salary=0)
        db.session.add(compensation)

    compensation.basic_salary = parse_money(row.get("basic_salary"))
    compensation.allowances = parse_money(row.get("allowances"))
    compensation.gross_salary = compensation.basic_salary + compensation.allowances
    db.session.flush()

    PayrollDeduction.query.filter_by(compensation_id=compensation.id).delete()
    for number in ("1", "2"):
        deduction_type = clean(row.get(f"deduction_type_{number}"))
        amount = parse_money(row.get(f"deduction_amount_{number}"))
        if deduction_type and amount > 0:
            db.session.add(
                PayrollDeduction(
                    compensation_id=compensation.id,
                    deduction_type=deduction_type,
                    amount=amount,
                    is_recurring=True,
                    effective_from=user.date_of_employment,
                )
            )

    nok_values = {
        "full_name": clean(row.get("nok_full_name")),
        "relationship": clean(row.get("nok_relationship")),
        "phone": normalize_phone(row.get("nok_phone")),
        "email": clean(row.get("nok_email")),
        "address": clean(row.get("nok_address")),
        "city": clean(row.get("nok_city")),
        "state": clean(row.get("nok_state")),
    }
    if any(nok_values.values()):
        nok = NextOfKin.query.filter_by(user_id=user.id, is_primary=True).first()
        if not nok:
            nok = NextOfKin(
                user_id=user.id,
                full_name=nok_values["full_name"] or f"NOK {employee_id}",
                relationship=nok_values["relationship"] or "Not specified",
                phone=nok_values["phone"] or "N/A",
                is_primary=True,
            )
            db.session.add(nok)
        nok.full_name = nok_values["full_name"] or nok.full_name
        nok.relationship = nok_values["relationship"] or nok.relationship
        nok.phone = nok_values["phone"] or nok.phone
        nok.email = nok_values["email"]
        nok.address = nok_values["address"]
        nok.city = nok_values["city"]
        nok.state = nok_values["state"]
        nok.is_primary = True

    access = DepartmentAccess.query.filter_by(user_id=user.id, department=department).first()
    if not access:
        db.session.add(DepartmentAccess(user_id=user.id, department=department, access_level="view", is_active=True))
    else:
        access.is_active = True

    return action, user


def validate_no_remaining_blanks(df: pd.DataFrame) -> list[tuple[str, str, list[str]]]:
    missing = []
    field_map = {
        "phone": "phone_number",
        "date_of_birth": "date_of_birth",
        "gender": "gender",
        "department": "department",
        "position": "position",
        "employment_type": "employment_type",
        "basic_salary": "basic_salary",
        "allowances": "allowances",
        "nok_full_name": "nok_full_name",
        "nok_relationship": "nok_relationship",
        "nok_phone": "nok_phone",
        "nok_email": "nok_email",
        "nok_address": "nok_address",
        "nok_city": "nok_city",
        "nok_state": "nok_state",
    }

    for _, row in df.iterrows():
        employee_id = clean(row.get("employee_id"))
        if not employee_id:
            continue
        user = User.query.filter_by(employee_id=employee_id).first()
        if not user:
            missing.append((employee_id, clean(row.get("last_name.1")) or "", ["user"]))
            continue
        compensation = StaffCompensation.query.filter_by(user_id=user.id).first()
        nok = NextOfKin.query.filter_by(user_id=user.id, is_primary=True).first()
        app_values = {
            "phone": user.phone,
            "date_of_birth": user.date_of_birth,
            "gender": user.gender,
            "department": user.department,
            "position": user.position,
            "employment_type": user.employment_type,
            "basic_salary": user.basic_salary,
            "allowances": compensation.allowances if compensation else None,
            "nok_full_name": nok.full_name if nok else None,
            "nok_relationship": nok.relationship if nok else None,
            "nok_phone": nok.phone if nok else None,
            "nok_email": nok.email if nok else None,
            "nok_address": nok.address if nok else None,
            "nok_city": nok.city if nok else None,
            "nok_state": nok.state if nok else None,
        }
        row_missing = [
            app_field
            for app_field, excel_field in field_map.items()
            if clean(row.get(excel_field)) and app_values.get(app_field) in (None, "")
        ]
        if row_missing:
            missing.append((employee_id, user.name, row_missing))
    return missing


def main() -> int:
    parser = argparse.ArgumentParser(description="Sync staff records from Sammya Excel template.")
    parser.add_argument("excel_file", type=Path)
    parser.add_argument("--apply", action="store_true", help="Commit changes. Without this, the transaction is rolled back.")
    parser.add_argument("--create-missing", action="store_true", help="Create staff rows that do not already exist.")
    parser.add_argument("--config", default="production", choices=["development", "production"])
    args = parser.parse_args()

    if not args.excel_file.exists():
        raise SystemExit(f"Excel file not found: {args.excel_file}")

    df = pd.read_excel(args.excel_file)
    df.columns = df.columns.str.lower().str.strip()
    email_counts = collect_email_counts(df)
    app = create_app(args.config)
    stats = SyncStats()

    with app.app_context():
        for _, row in df.iterrows():
            action, user = sync_staff_row(row, email_counts, args.create_missing)
            if action == "created":
                stats.created += 1
            elif action == "updated":
                stats.updated += 1
            else:
                stats.skipped += 1
            if user:
                if NextOfKin.query.filter_by(user_id=user.id, is_primary=True).first():
                    stats.nok_synced += 1
                compensation = StaffCompensation.query.filter_by(user_id=user.id).first()
                if compensation:
                    stats.deductions_synced += PayrollDeduction.query.filter_by(
                        compensation_id=compensation.id
                    ).count()

        if args.apply:
            db.session.commit()
        else:
            db.session.rollback()

        remaining = [] if not args.apply else validate_no_remaining_blanks(df)

    mode = "APPLIED" if args.apply else "DRY RUN"
    print(f"{mode}: created={stats.created} updated={stats.updated} skipped={stats.skipped}")
    print(f"{mode}: next_of_kin_synced={stats.nok_synced} deductions_synced={stats.deductions_synced}")
    if remaining:
        print("Rows with template values still blank after sync:")
        for employee_id, name, fields in remaining:
            print(f"- {employee_id} {name}: {', '.join(fields)}")
        return 1
    if args.apply:
        print("Validation passed: no app field is blank where the template has a value.")
    else:
        print("No changes committed. Re-run with --apply to write to the database.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
