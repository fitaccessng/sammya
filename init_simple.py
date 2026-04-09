"""Initialize the database - simpler version."""
import os
import sys

# Ensure we're in the right directory
os.chdir(os.path.dirname(os.path.abspath(__file__)))

try:
    from app.factory import create_app
    from app.models import db
    
    print("Creating app instance...")
    app = create_app('development')
    
    print("Creating database tables...")
    with app.app_context():
        db.create_all()
        print("✓ Tables created successfully!")
        
        # Check what was created
        import sqlite3
        conn = sqlite3.connect('fitaccess_dev.db')
        cursor = conn.cursor()
        cursor.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")
        tables = cursor.fetchall()
        print(f"\nTotal tables created: {len(tables)}")
        if tables:
            print("Tables:")
            for table in tables[:5]:  # Show first 5
                print(f"  - {table[0]}")
            if len(tables) > 5:
                print(f"  ... and {len(tables) - 5} more")
        conn.close()
        
except Exception as e:
    print(f"Error: {e}")
    import traceback
    traceback.print_exc()
    sys.exit(1)
