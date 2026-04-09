"""
SQLite migration for payment_request:
1) Allow po_id to be nullable
2) Add counterparty_name and notes columns if missing

Usage:
    python migrate_payment_request_po_nullable.py
"""

import os
import sqlite3
from app.factory import create_app

app = create_app()


def get_db_path():
    raw = app.config["SQLALCHEMY_DATABASE_URI"].replace("sqlite:///", "")
    candidates = []
    if os.path.isabs(raw):
        candidates.append(raw)
    else:
        candidates.append(os.path.join(os.getcwd(), raw))
        candidates.append(os.path.join(os.getcwd(), "instance", raw))

    # Prefer a DB path that already has payment_request table.
    for path in candidates:
        if not os.path.exists(path):
            continue
        try:
            conn = sqlite3.connect(path)
            cur = conn.cursor()
            cur.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='payment_request'")
            hit = cur.fetchone() is not None
            conn.close()
            if hit:
                return path
        except sqlite3.Error:
            continue

    # Fallback to configured path in cwd.
    return candidates[0]


def table_columns(cursor, table_name):
    cursor.execute(f"PRAGMA table_info({table_name})")
    return cursor.fetchall()


def migrate():
    db_path = get_db_path()
    print(f"Database path: {db_path}")

    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()

    try:
        columns = table_columns(cursor, "payment_request")
        col_names = [c[1] for c in columns]
        po_col = next((c for c in columns if c[1] == "po_id"), None)

        needs_rebuild = po_col is not None and int(po_col[3]) == 1  # NOT NULL flag
        needs_extra_cols = "counterparty_name" not in col_names or "notes" not in col_names

        if not needs_rebuild and not needs_extra_cols:
            print("No migration needed.")
            return True

        print("Migrating payment_request table...")
        cursor.execute("PRAGMA foreign_keys=OFF")
        cursor.execute("BEGIN TRANSACTION")

        cursor.execute(
            """
            CREATE TABLE payment_request_new (
                id INTEGER PRIMARY KEY,
                po_id INTEGER,
                qc_inspection_id INTEGER,
                counterparty_name VARCHAR(255),
                notes TEXT,
                invoice_number VARCHAR(50),
                invoice_amount NUMERIC(15, 2) NOT NULL,
                approval_state VARCHAR(20),
                verified_by INTEGER,
                created_at DATETIME,
                sent_to_admin BOOLEAN DEFAULT 0,
                sent_to_cost_control BOOLEAN DEFAULT 0,
                sent_to_procurement BOOLEAN DEFAULT 0,
                sent_date DATETIME,
                FOREIGN KEY(po_id) REFERENCES purchase_order (id),
                FOREIGN KEY(qc_inspection_id) REFERENCES qc_inspection (id),
                FOREIGN KEY(verified_by) REFERENCES user (id)
            )
            """
        )

        # Build robust copy list from existing columns.
        source_cols = set(col_names)
        select_parts = [
            "id",
            "po_id",
            "qc_inspection_id",
            "counterparty_name" if "counterparty_name" in source_cols else "NULL AS counterparty_name",
            "notes" if "notes" in source_cols else "NULL AS notes",
            "invoice_number",
            "invoice_amount",
            "approval_state",
            "verified_by",
            "created_at",
            "sent_to_admin" if "sent_to_admin" in source_cols else "0 AS sent_to_admin",
            "sent_to_cost_control" if "sent_to_cost_control" in source_cols else "0 AS sent_to_cost_control",
            "sent_to_procurement" if "sent_to_procurement" in source_cols else "0 AS sent_to_procurement",
            "sent_date" if "sent_date" in source_cols else "NULL AS sent_date",
        ]

        cursor.execute(
            f"""
            INSERT INTO payment_request_new
            (id, po_id, qc_inspection_id, counterparty_name, notes, invoice_number, invoice_amount,
             approval_state, verified_by, created_at, sent_to_admin, sent_to_cost_control,
             sent_to_procurement, sent_date)
            SELECT {", ".join(select_parts)}
            FROM payment_request
            """
        )

        cursor.execute("DROP TABLE payment_request")
        cursor.execute("ALTER TABLE payment_request_new RENAME TO payment_request")
        cursor.execute("CREATE UNIQUE INDEX IF NOT EXISTS ix_payment_request_invoice_number ON payment_request (invoice_number)")

        cursor.execute("COMMIT")
        cursor.execute("PRAGMA foreign_keys=ON")
        print("Migration completed successfully.")
        return True
    except sqlite3.Error as exc:
        cursor.execute("ROLLBACK")
        print(f"Migration failed: {exc}")
        return False
    finally:
        conn.close()


if __name__ == "__main__":
    raise SystemExit(0 if migrate() else 1)
