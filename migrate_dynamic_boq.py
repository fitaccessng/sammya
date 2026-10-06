"""Create the flexible BOQ import tables without modifying existing tables."""

import os

from app.factory import create_app
from app.models import BOQImport, BOQImportRow, BOQImportSheet, db


def create_dynamic_boq_tables():
    """Create only the tables used to retain uploaded BOQ layouts."""
    if not os.environ.get('DATABASE_URL'):
        raise RuntimeError('DATABASE_URL must be configured before running this migration.')

    app = create_app('production')
    tables = [
        BOQImport.__table__,
        BOQImportSheet.__table__,
        BOQImportRow.__table__,
    ]
    with app.app_context():
        db.metadata.create_all(bind=db.engine, tables=tables)


if __name__ == '__main__':
    create_dynamic_boq_tables()
