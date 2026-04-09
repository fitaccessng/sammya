"""
Seed script to initialize database with test data.
Usage: python seed.py
"""

from app.factory import create_app
from app.models import db, User, Project, Vendor
from datetime import datetime, timedelta

app = create_app()

def seed_database():
    with app.app_context():
        # Clear existing data
        print("Clearing existing data...")
        db.drop_all()
        db.create_all()
        
        # Create users with different roles
        print("Creating users...")
        users = [
            User(
                name='Admin User',
                email='admin@fitaccess.com',
                role='admin',
                is_active=True
            ),
            User(
                name='John Executive',
                email='john@fitaccess.com',
                role='executive',
                is_active=True
            ),
            User(
                name='Sarah Cost Manager',
                email='sarah@fitaccess.com',
                role='cost_control_manager',
                is_active=True
            ),
            User(
                name='Mike Procurement',
                email='mike@fitaccess.com',
                role='procurement_manager',
                is_active=True
            ),
            User(
                name='Lisa QC Manager',
                email='lisa@fitaccess.com',
                role='qc_manager',
                is_active=True
            ),
            User(
                name='David Finance',
                email='david@fitaccess.com',
                role='finance_manager',
                is_active=True
            ),
            User(
                name='Emma Project',
                email='emma@fitaccess.com',
                role='project_manager',
                is_active=True
            ),
            User(
                name='Tom QC Staff',
                email='tom@fitaccess.com',
                role='qc_staff',
                is_active=True
            ),
            User(
                name='Amy Procurement Staff',
                email='amy@fitaccess.com',
                role='procurement_staff',
                is_active=True
            ),
            User(
                name='Project Staff',
                email='staff@fitaccess.com',
                role='project_staff',
                is_active=True
            ),
        ]
        
        # Set passwords for all users
        for user in users:
            user.set_password('password123')
        
        db.session.add_all(users)
        db.session.commit()
        print(f"Created {len(users)} users")
        
        # Create projects
        print("Creating projects...")
        today = datetime.now().date()
        projects = [
            Project(
                name='Central Hospital Extension',
                description='Extension of 5-story hospital building',
                budget=5000000,
                start_date=today,
                end_date=today + timedelta(days=365),
                status='active'
            ),
            Project(
                name='Downtown Shopping Mall',
                description='Construction of commercial shopping complex',
                budget=8000000,
                start_date=today,
                end_date=today + timedelta(days=450),
                status='active'
            ),
            Project(
                name='Residential Towers',
                description='2 residential towers with 200 units',
                budget=12000000,
                start_date=today - timedelta(days=60),
                end_date=today + timedelta(days=540),
                status='active'
            ),
        ]
        
        # Assign team members to projects
        projects[0].team_members.extend([users[0], users[4], users[6], users[8], users[9]])
        projects[1].team_members.extend([users[0], users[3], users[5], users[6]])
        projects[2].team_members.extend([users[0], users[2], users[3], users[6], users[9]])
        
        db.session.add_all(projects)
        db.session.commit()
        print(f"Created {len(projects)} projects")
        
        # Create vendors
        print("Creating vendors...")
        vendors = [
            Vendor(
                name='BuildCo Materials',
                email='info@buildco.com',
                phone='+1-555-0101',
                address='123 Industrial Ave',
                city='New York',
                registration_number='VEN-001',
                is_active=True
            ),
            Vendor(
                name='Steel Supplies Ltd',
                email='sales@steelsupplies.com',
                phone='+1-555-0102',
                address='456 Factory Road',
                city='New Jersey',
                registration_number='VEN-002',
                is_active=True
            ),
            Vendor(
                name='Concrete & Aggregates',
                email='orders@conaggregate.com',
                phone='+1-555-0103',
                address='789 Supply St',
                city='Philadelphia',
                registration_number='VEN-003',
                is_active=True
            ),
            Vendor(
                name='Electrical Equipment Co',
                email='contact@elecequip.com',
                phone='+1-555-0104',
                address='321 Tech Park',
                city='Boston',
                registration_number='VEN-004',
                is_active=True
            ),
        ]
        
        db.session.add_all(vendors)
        db.session.commit()
        print(f"Created {len(vendors)} vendors")
        
        print("\nDatabase seeded successfully!")
        print("\nTest Users Created:")
        print("=" * 60)
        for user in users:
            print(f"  Email: {user.email:30} Role: {user.role:25}")
        print(f"\nPassword for all: password123")
        print("=" * 60)

if __name__ == '__main__':
    seed_database()
