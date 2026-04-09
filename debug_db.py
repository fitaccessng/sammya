"""Debug database creation."""
import os
import sqlite3

# Remove existing database
db_path = 'fitaccess_dev.db'
if os.path.exists(db_path):
    os.remove(db_path)
    print(f"✓ Removed existing {db_path}")

# Create app and force table creation
from app.factory import create_app
from app.models import db

print("Creating app instance...")
app = create_app()

print("App created, checking database...")
conn = sqlite3.connect(db_path)
cursor = conn.cursor()
cursor.execute("SELECT name FROM sqlite_master WHERE type='table'")
tables = cursor.fetchall()
print(f"Tables in database: {len(tables)}")

if len(tables) == 0:
    print("\nWarning: No tables were created!")
    print("Attempting manual table creation...")
    
    with app.app_context():
        # Try to list all models registered with db
        print(f"Number of models registered: {len(db.Model.registry.mappers)}")
        for mapper in db.Model.registry.mappers:
            print(f"  - {mapper.class_.__name__}")
        
        # Force create tables
        print("\nCalling db.create_all()...")
        db.create_all()
        print("✓ db.create_all() called")
        
        # Check again
        cursor.execute("SELECT name FROM sqlite_master WHERE type='table'")
        tables = cursor.fetchall()
        print(f"Tables after manual creation: {len(tables)}")

conn.close()
