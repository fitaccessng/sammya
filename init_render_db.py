"""
Initialize the Render/PostgreSQL database for production.

Usage:
    DATABASE_URL=postgresql://... python init_render_db.py
"""

import os
import sys

from flask import Flask

from app.models import db

# Import model modules so every table is registered with SQLAlchemy metadata.
import app.models  # noqa: F401
import app.payroll_models  # noqa: F401


def main():
    database_url = os.environ.get("DATABASE_URL")
    if not database_url:
        print("DATABASE_URL is not set.", file=sys.stderr)
        sys.exit(1)

    if database_url.startswith("postgres://"):
        database_url = database_url.replace("postgres://", "postgresql://", 1)

    app = Flask(__name__)
    app.config["SQLALCHEMY_DATABASE_URI"] = database_url
    app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False
    db.init_app(app)

    with app.app_context():
        db.create_all()
        table_names = sorted(db.metadata.tables.keys())
        print(f"Created/verified {len(table_names)} tables in production database.")
        for name in table_names:
            print(f" - {name}")


if __name__ == "__main__":
    main()
