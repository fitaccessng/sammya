"""
Migration script to add missing columns to payment_request table.
Usage: python migrate_payment_request.py
"""

import sqlite3
from app.factory import create_app

app = create_app()

COLUMNS_TO_ADD = [
    ('sent_to_admin', 'BOOLEAN DEFAULT 0'),
    ('sent_to_cost_control', 'BOOLEAN DEFAULT 0'),
    ('sent_to_procurement', 'BOOLEAN DEFAULT 0'),
    ('sent_date', 'DATETIME'),
]

def get_db_path():
    """Get the database path from the app config."""
    return app.config['SQLALCHEMY_DATABASE_URI'].replace('sqlite:///', '')

def column_exists(cursor, table_name, column_name):
    """Check if a column exists in a table."""
    cursor.execute(f"PRAGMA table_info({table_name})")
    columns = cursor.fetchall()
    return any(col[1] == column_name for col in columns)

def add_missing_columns():
    """Add missing columns to payment_request table."""
    db_path = get_db_path()
    print(f"Database path: {db_path}")
    
    try:
        conn = sqlite3.connect(db_path)
        cursor = conn.cursor()
        
        print("Checking for missing columns in payment_request table...")
        
        for column_name, column_type in COLUMNS_TO_ADD:
            if column_exists(cursor, 'payment_request', column_name):
                print(f"✓ Column '{column_name}' already exists")
            else:
                print(f"⚠ Adding missing column '{column_name}'...")
                cursor.execute(f"ALTER TABLE payment_request ADD COLUMN {column_name} {column_type}")
                print(f"✓ Column '{column_name}' added successfully")
        
        conn.commit()
        print("\n✓ Migration completed successfully!")
        
    except sqlite3.Error as e:
        print(f"✗ Database error: {e}")
        return False
    finally:
        if conn:
            conn.close()
    
    return True

if __name__ == '__main__':
    success = add_missing_columns()
    exit(0 if success else 1)
