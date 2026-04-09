"""
Initialize the database with all tables from models.
Usage: python init_db.py
"""

from app.factory import create_app
from app.models import db

app = create_app()

def init_database():
    """Create all database tables."""
    with app.app_context():
        print("Creating all database tables...")
        db.create_all()
        print("✓ Database initialized successfully!")
        
        # Verify payment_request table structure
        import sqlite3
        db_path = app.config['SQLALCHEMY_DATABASE_URI'].replace('sqlite:///', '')
        conn = sqlite3.connect(db_path)
        cursor = conn.cursor()
        cursor.execute("PRAGMA table_info(payment_request)")
        columns = cursor.fetchall()
        print("\nPayment Request table columns:")
        for col in columns:
            print(f"  - {col[1]} ({col[2]})")
        conn.close()

if __name__ == '__main__':
    init_database()
