"""Check database tables and schema."""
import sqlite3

conn = sqlite3.connect('fitaccess_dev.db')
cursor = conn.cursor()

# Get all tables
cursor.execute("SELECT name FROM sqlite_master WHERE type='table'")
tables = cursor.fetchall()
print(f"Total tables: {len(tables)}")

# Check for payment_request table
payment_request_exists = any(t[0] == 'payment_request' for t in tables)
print(f"payment_request table exists: {payment_request_exists}")

if payment_request_exists:
    cursor.execute("PRAGMA table_info(payment_request)")
    columns = cursor.fetchall()
    print("\nPayment Request table columns:")
    for col in columns:
        col_name = col[1]
        col_type = col[2]
        print(f"  {col_name}: {col_type}")
else:
    print("Available tables:")
    for table in tables:
        print(f"  - {table[0]}")

conn.close()
