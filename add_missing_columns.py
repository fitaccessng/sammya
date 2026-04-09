"""Add missing columns to payment_request table in existing database."""
import sqlite3
import sys

db_path = 'instance/fitaccess_dev.db'

try:
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()
    
    print("Adding missing columns to payment_request table...")
    
    # Check which columns already exist
    cursor.execute("PRAGMA table_info(payment_request)")
    existing_columns = {row[1] for row in cursor.fetchall()}
    
    columns_to_add = [
        ('sent_to_admin', 'BOOLEAN DEFAULT 0'),
        ('sent_to_cost_control', 'BOOLEAN DEFAULT 0'),
        ('sent_to_procurement', 'BOOLEAN DEFAULT 0'),
        ('sent_date', 'DATETIME'),
    ]
    
    for col_name, col_type in columns_to_add:
        if col_name in existing_columns:
            print(f"✓ Column '{col_name}' already exists")
        else:
            print(f"⚠ Adding column '{col_name}'...")
            cursor.execute(f"ALTER TABLE payment_request ADD COLUMN {col_name} {col_type}")
            print(f"✓ Column '{col_name}' added successfully")
    
    conn.commit()
    
    # Verify all columns
    cursor.execute("PRAGMA table_info(payment_request)")
    columns = cursor.fetchall()
    print(f"\nPayment Request table now has {len(columns)} columns:")
    for col in columns:
        print(f"  - {col[1]}")
    
    print("\n✓ Migration completed successfully!")
    conn.close()
    
except sqlite3.Error as e:
    print(f"✗ Database error: {e}")
    sys.exit(1)
