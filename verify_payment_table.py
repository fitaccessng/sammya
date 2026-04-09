"""Check payment_request table columns."""
import sqlite3

db_path = 'instance/fitaccess_dev.db'
conn = sqlite3.connect(db_path)
cursor = conn.cursor()

cursor.execute("PRAGMA table_info(payment_request)")
columns = cursor.fetchall()

print("payment_request table columns:")
for col in columns:
    col_id, col_name, col_type, not_null, default_val, pk = col
    print(f"  {col_name:<25} {col_type:<15} {'NOT NULL' if not_null else 'nullable':<12} default: {default_val}")

conn.close()
