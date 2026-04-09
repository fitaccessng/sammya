"""Detailed database creation debugging."""
import os
import sqlite3
import sys

# Remove existing database
db_path = 'fitaccess_dev.db'
if os.path.exists(db_path):
    os.remove(db_path)
    print(f"✓ Removed existing {db_path}")

# Create app
from app.factory import create_app
from app.models import db

print("Creating app...")
app = create_app()

print(f"App config SQLALCHEMY_DATABASE_URI: {app.config['SQLALCHEMY_DATABASE_URI']}")
print(f"App config SQLALCHEMY_TRACK_MODIFICATIONS: {app.config['SQLALCHEMY_TRACK_MODIFICATIONS']}")

print("\nAttempting table creation...")
try:
    with app.app_context():
        # Check if db is properly bound  
        print(f"DB is bound to app: {db.engine is not None}")
        print(f"DB engine URL: {db.engine.url}")
        
        # Try creating tables
        db.create_all()
        print("✓ db.create_all() executed successfully")
        
        # Verify tables were created
        result = db.session.execute(db.text("SELECT name FROM sqlite_master WHERE type='table'"))
        tables = result.fetchall()
        print(f"\nTables created: {len(tables)}")
        
        if len(tables) > 0:
            print("Table names:")
            for table in tables:
                print(f"  - {table[0]}")
        else:
            print("ERROR: No tables were created!")
            
except Exception as e:
    print(f"ERROR: {e}")
    import traceback
    traceback.print_exc()
    sys.exit(1)
