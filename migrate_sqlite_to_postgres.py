"""
Copy data from a local SQLite database into the configured PostgreSQL database.

Usage:
    DATABASE_URL=postgresql://... python migrate_sqlite_to_postgres.py
    DATABASE_URL=postgresql://... python migrate_sqlite_to_postgres.py --sqlite-path /path/to/fitaccess_dev.db

Notes:
    - This script assumes the target PostgreSQL schema already exists.
    - Tables are copied in dependency order where possible.
    - Existing rows in PostgreSQL are preserved; tables with data are skipped by default.
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

from sqlalchemy import MetaData, create_engine, func, inspect, select, text


DEFAULT_SQLITE_CANDIDATES = [
    Path("instance/fitaccess_dev.db"),
    Path("fitaccess_dev.db"),
]


def resolve_sqlite_path(cli_value: str | None) -> Path:
    if cli_value:
        candidate = Path(cli_value).expanduser().resolve()
        if not candidate.exists():
            raise FileNotFoundError(f"SQLite database not found: {candidate}")
        return candidate

    for candidate in DEFAULT_SQLITE_CANDIDATES:
        resolved = candidate.resolve()
        if resolved.exists():
            return resolved

    raise FileNotFoundError(
        "Could not find a SQLite database automatically. "
        "Pass --sqlite-path explicitly."
    )


def load_table_metadata(engine):
    metadata = MetaData()
    metadata.reflect(bind=engine)
    return metadata


def copy_table(source_conn, target_conn, source_table, target_table, batch_size: int, force: bool):
    target_count = target_conn.execute(select(func.count()).select_from(target_table)).scalar_one()
    if target_count and not force:
        return ("skipped", target_count, f"target already has {target_count} rows")

    source_rows = source_conn.execute(select(source_table)).mappings()

    inserted = 0
    batch = []
    for row in source_rows:
        payload = {column.name: row[column.name] for column in target_table.columns if column.name in row}
        batch.append(payload)

        if len(batch) >= batch_size:
            target_conn.execute(target_table.insert(), batch)
            inserted += len(batch)
            batch.clear()

    if batch:
        target_conn.execute(target_table.insert(), batch)
        inserted += len(batch)

    return ("copied", inserted, None)


def reset_sequences(target_conn, metadata):
    for table in metadata.sorted_tables:
        pk_columns = list(table.primary_key.columns)
        if len(pk_columns) != 1:
            continue

        pk_column = pk_columns[0]
        try:
            python_type = pk_column.type.python_type
        except (AttributeError, NotImplementedError):
            continue

        if python_type is not int:
            continue

        max_id = target_conn.execute(select(func.max(pk_column)).select_from(table)).scalar()
        if max_id is None:
            continue

        target_conn.execute(
            text("SELECT setval(pg_get_serial_sequence(:table_name, :column_name), :value, true)"),
            {
                "table_name": table.name,
                "column_name": pk_column.name,
                "value": max_id,
            },
        )


def main():
    parser = argparse.ArgumentParser(description="Migrate SQLite data into PostgreSQL.")
    parser.add_argument("--sqlite-path", help="Path to the source SQLite database file.")
    parser.add_argument(
        "--batch-size",
        type=int,
        default=500,
        help="Number of rows to insert per batch.",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Insert even when the target table already has rows.",
    )
    args = parser.parse_args()

    database_url = os.environ.get("DATABASE_URL")
    if not database_url:
        print("DATABASE_URL is not set.", file=sys.stderr)
        sys.exit(1)

    sqlite_path = resolve_sqlite_path(args.sqlite_path)
    sqlite_url = f"sqlite:///{sqlite_path}"

    source_engine = create_engine(sqlite_url)
    target_engine = create_engine(database_url)

    source_metadata = load_table_metadata(source_engine)
    target_metadata = load_table_metadata(target_engine)

    source_tables = source_metadata.tables
    print(f"Source SQLite DB: {sqlite_path}")
    print(f"Target PostgreSQL DB: {database_url.split('@')[-1]}")

    with source_engine.connect() as source_conn, target_engine.begin() as target_conn:
        for target_table in target_metadata.sorted_tables:
            source_table = source_tables.get(target_table.name)
            if source_table is None:
                print(f"[skip] {target_table.name}: not found in SQLite source")
                continue

            status, count, message = copy_table(
                source_conn,
                target_conn,
                source_table,
                target_table,
                args.batch_size,
                args.force,
            )
            if message:
                print(f"[{status}] {target_table.name}: {message}")
            else:
                print(f"[{status}] {target_table.name}: {count} rows")

        reset_sequences(target_conn, target_metadata)

    print("Migration completed.")


if __name__ == "__main__":
    main()
