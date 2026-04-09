"""Test importing the app and all its blueprints."""
try:
    print("Importing factory...")
    from app.factory import create_app
    print("✓ Factory imported")
    
    print("Creating app...")
    app = create_app()
    print("✓ App created")
    
    print("Checking database...")
    import sqlite3
    conn = sqlite3.connect('fitaccess_dev.db')
    cursor = conn.cursor()
    cursor.execute("SELECT name FROM sqlite_master WHERE type='table'")
    tables = cursor.fetchall()
    print(f"✓ Database has {len(tables)} tables")
    conn.close()
    
except Exception as e:
    print(f"✗ Error: {e}")
    import traceback
    traceback.print_exc()
