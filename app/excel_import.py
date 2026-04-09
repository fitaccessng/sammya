"""
Excel Staff Import Utility
Handles parsing, validation, and creation of staff records from Excel files.
"""

import pandas as pd
from datetime import datetime, date
from typing import Dict, List, Tuple
from werkzeug.utils import secure_filename
import os
from app.models import (
    db, User, StaffCompensation, PayrollDeduction, 
    StaffImportBatch, StaffImportItem, NextOfKin, DepartmentAccess, ApprovalState
)
from werkzeug.security import generate_password_hash


class ExcelImportError(Exception):
    """Custom exception for Excel import errors."""
    pass


class StaffExcelParser:
    """Parse and validate staff data from Excel files."""
    
    ALLOWED_EXTENSIONS = {'xlsx', 'xls'}
    REQUIRED_COLUMNS = {
        'first_name', 'last_name', 'email', 'basic_salary'
    }
    OPTIONAL_COLUMNS = {
        'phone', 'gender', 'date_of_birth', 'address', 'city', 'state', 'marital_status',
        'employee_id', 'date_of_employment', 'department', 'position', 'role', 'allowances',
        'nok_full_name', 'nok_relationship', 'nok_phone', 'nok_email', 
        'nok_address', 'nok_city', 'nok_state'
    }
    
    VALID_DEPARTMENTS = {
        'HR', 'Finance', 'Procurement', 'QC', 'Projects', 'Cost Control', 'Admin',
        'IT', 'Marketing', 'Operations', 'Legal', 'Strategy', 'Engineering',
        'Sales', 'Support', 'Operations', 'Management', 'Human Resources',
        'Information Technology', 'Quality Control', 'Project Management'
    }
    
    # Department mapping for common variations
    DEPARTMENT_MAPPING = {
        'it': 'IT',
        'information technology': 'IT',
        'marketing': 'Marketing',
        'operations': 'Operations',
        'hr': 'HR',
        'human resources': 'HR',
        'finance': 'Finance',
        'financial': 'Finance',
        'procurement': 'Procurement',
        'qc': 'QC',
        'quality control': 'QC',
        'projects': 'Projects',
        'project management': 'Projects',
        'cost control': 'Cost Control',
        'admin': 'Admin',
        'administration': 'Admin',
        'legal': 'Legal',
        'strategy': 'Strategy',
        'strategic': 'Strategy',
        'engineering': 'Engineering',
        'sales': 'Sales',
        'support': 'Support',
        'management': 'Management'
    }
    
    VALID_ROLES = {
        'admin', 'hr_manager', 'hr_staff', 'finance_manager', 'finance_staff',
        'procurement_manager', 'procurement_staff', 'qc_staff', 'project_manager',
        'cost_control_manager', 'cost_control_staff'
    }
    
    @staticmethod
    def validate_file(file) -> bool:
        """Validate that file is an Excel file."""
        if not file or file.filename == '':
            raise ExcelImportError('No file provided')
        
        file_ext = file.filename.rsplit('.', 1)[1].lower() if '.' in file.filename else ''
        if file_ext not in StaffExcelParser.ALLOWED_EXTENSIONS:
            raise ExcelImportError(f'Invalid file type. Allowed: {", ".join(StaffExcelParser.ALLOWED_EXTENSIONS)}')
        
        return True
    
    @staticmethod
    def parse_excel_file(file_path: str) -> pd.DataFrame:
        """Read Excel file and return DataFrame."""
        try:
            df = pd.read_excel(file_path)
            # Convert column names to lowercase for consistency
            df.columns = df.columns.str.lower().str.strip()
            return df
        except Exception as e:
            raise ExcelImportError(f'Failed to read Excel file: {str(e)}')
    
    @staticmethod
    def validate_columns(df: pd.DataFrame) -> Tuple[bool, List[str]]:
        """Validate that DataFrame has required columns."""
        df_columns = set(df.columns)
        missing_columns = StaffExcelParser.REQUIRED_COLUMNS - df_columns
        
        if missing_columns:
            raise ExcelImportError(f'Missing required columns: {", ".join(missing_columns)}')
        
        return True, list(df_columns)
    
    @staticmethod
    def normalize_department(department: str) -> str:
        """Normalize department name to valid option."""
        if not department:
            return 'General'
        
        dept_lower = str(department).strip().lower()
        
        # Try exact mapping first
        if dept_lower in StaffExcelParser.DEPARTMENT_MAPPING:
            return StaffExcelParser.DEPARTMENT_MAPPING[dept_lower]
        
        # Try to find closest match
        for key, value in StaffExcelParser.DEPARTMENT_MAPPING.items():
            if key in dept_lower or dept_lower in key:
                return value
        
        # If not found, return the department as-is (will validate later)
        return str(department).strip()
    
    @staticmethod
    def validate_email(email: str) -> bool:
        """Validate email format."""
        import re
        pattern = r'^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$'
        return re.match(pattern, email) is not None
    
    @staticmethod
    def validate_phone(phone: str) -> bool:
        """Validate phone number format (basic check)."""
        if not phone:
            return True  # Phone is optional
        import re
        pattern = r'^\+?[1-9]\d{1,14}$'
        return re.match(pattern, str(phone).replace('-', '').replace(' ', '')) is not None
    
    @staticmethod
    def validate_date(date_str) -> Tuple[bool, date]:
        """Validate and convert date string."""
        if pd.isna(date_str) or date_str == '':
            return True, None
        
        if isinstance(date_str, date):
            return True, date_str
        
        for fmt in ['%d-%m-%Y', '%Y-%m-%d', '%d/%m/%Y', '%m/%d/%Y']:
            try:
                return True, datetime.strptime(str(date_str), fmt).date()
            except ValueError:
                continue
        
        return False, None
    
    @staticmethod
    def validate_salary(salary) -> Tuple[bool, float]:
        """Validate and convert salary to float."""
        try:
            if pd.isna(salary) or salary == '':
                return False, None
            salary_float = float(salary)
            if salary_float < 0:
                return False, None
            return True, salary_float
        except (ValueError, TypeError):
            return False, None
    
    @staticmethod
    def prepare_password(email: str) -> str:
        """Generate a default password based on email."""
        # Password format: FirstPart@123 (e.g., john@company.com -> john@123)
        local_part = email.split('@')[0]
        default_password = f"{local_part}@{datetime.now().year}"
        return default_password
    
    @staticmethod
    def validate_and_normalize_row(row: pd.Series, row_index: int) -> Tuple[bool, Dict, str]:
        """Validate and normalize a single row of data."""
        errors = []
        normalized = {}
        
        # Required fields
        first_name = str(row.get('first_name', '')).strip()
        if not first_name:
            errors.append('First name is required')
        normalized['first_name'] = first_name
        
        last_name = str(row.get('last_name', '')).strip()
        if not last_name:
            errors.append('Last name is required')
        normalized['last_name'] = last_name
        
        email = str(row.get('email', '')).strip().lower()
        if not email:
            errors.append('Email is required')
        elif not StaffExcelParser.validate_email(email):
            errors.append('Invalid email format')
        
        # Check for duplicate email
        existing_user = User.query.filter_by(email=email).first()
        if existing_user:
            errors.append(f'Email already exists in system')
        
        normalized['email'] = email
        
        # Salary validation
        salary_valid, basic_salary = StaffExcelParser.validate_salary(row.get('basic_salary'))
        if not salary_valid:
            errors.append('Basic salary is required and must be a valid number')
        normalized['basic_salary'] = basic_salary or 0
        
        # Optional fields with validation
        phone = str(row.get('phone', '')).strip() if pd.notna(row.get('phone')) else ''
        if phone and not StaffExcelParser.validate_phone(phone):
            errors.append(f'Invalid phone format: {phone}')
        normalized['phone'] = phone or None
        
        # Gender validation
        gender = str(row.get('gender', '')).strip() if pd.notna(row.get('gender')) else ''
        if gender and gender.lower() not in ['male', 'female', 'other']:
            errors.append(f'Invalid gender: {gender}. Must be Male, Female, or Other')
        normalized['gender'] = gender or None
        
        # Date of birth
        dob_valid, dob = StaffExcelParser.validate_date(row.get('date_of_birth'))
        if not dob_valid and pd.notna(row.get('date_of_birth')):
            errors.append(f'Invalid date of birth format: {row.get("date_of_birth")}')
        normalized['date_of_birth'] = dob
        
        # Address
        address = str(row.get('address', '')).strip() if pd.notna(row.get('address')) else ''
        normalized['address'] = address or None
        
        # City
        city = str(row.get('city', '')).strip() if pd.notna(row.get('city')) else ''
        normalized['city'] = city or None
        
        # State
        state = str(row.get('state', '')).strip() if pd.notna(row.get('state')) else ''
        normalized['state'] = state or None
        
        # Marital Status
        marital_status = str(row.get('marital_status', '')).strip() if pd.notna(row.get('marital_status')) else ''
        normalized['marital_status'] = marital_status or None
        
        # Employment fields
        employee_id = str(row.get('employee_id', '')).strip() if pd.notna(row.get('employee_id')) else ''
        normalized['employee_id'] = employee_id or None
        
        doe_valid, doe = StaffExcelParser.validate_date(row.get('date_of_employment'))
        if not doe_valid and pd.notna(row.get('date_of_employment')):
            errors.append(f'Invalid date of employment format')
        normalized['date_of_employment'] = doe
        
        # Department
        department = str(row.get('department', '')).strip() if pd.notna(row.get('department')) else ''
        if department:
            # Normalize department name
            normalized_dept = StaffExcelParser.normalize_department(department)
            if normalized_dept not in StaffExcelParser.VALID_DEPARTMENTS:
                errors.append(f'Invalid department: {department}. Valid departments: {", ".join(sorted(StaffExcelParser.VALID_DEPARTMENTS))}')
                normalized['department'] = department or 'General'
            else:
                normalized['department'] = normalized_dept
        else:
            normalized['department'] = 'General'
        
        position = str(row.get('position', '')).strip() if pd.notna(row.get('position')) else ''
        normalized['position'] = position or None
        
        # Role
        role = str(row.get('role', 'hr_staff')).strip().lower() if pd.notna(row.get('role')) else 'hr_staff'
        if role not in StaffExcelParser.VALID_ROLES:
            errors.append(f'Invalid role: {role}. Valid roles: {", ".join(StaffExcelParser.VALID_ROLES)}')
        normalized['role'] = role
        
        # Allowances
        allow_valid, allowances = StaffExcelParser.validate_salary(row.get('allowances', 0))
        normalized['allowances'] = allowances or 0
        
        # Next of Kin fields
        nok_full_name = str(row.get('nok_full_name', '')).strip() if pd.notna(row.get('nok_full_name')) else ''
        normalized['nok_full_name'] = nok_full_name or None
        
        nok_relationship = str(row.get('nok_relationship', '')).strip() if pd.notna(row.get('nok_relationship')) else ''
        normalized['nok_relationship'] = nok_relationship or None
        
        nok_phone = str(row.get('nok_phone', '')).strip() if pd.notna(row.get('nok_phone')) else ''
        if nok_phone and not StaffExcelParser.validate_phone(nok_phone):
            errors.append(f'Invalid next of kin phone format')
        normalized['nok_phone'] = nok_phone or None
        
        nok_email = str(row.get('nok_email', '')).strip() if pd.notna(row.get('nok_email')) else ''
        if nok_email and not StaffExcelParser.validate_email(nok_email):
            errors.append(f'Invalid next of kin email format')
        normalized['nok_email'] = nok_email or None
        
        nok_address = str(row.get('nok_address', '')).strip() if pd.notna(row.get('nok_address')) else ''
        normalized['nok_address'] = nok_address or None
        
        nok_city = str(row.get('nok_city', '')).strip() if pd.notna(row.get('nok_city')) else ''
        normalized['nok_city'] = nok_city or None
        
        nok_state = str(row.get('nok_state', '')).strip() if pd.notna(row.get('nok_state')) else ''
        normalized['nok_state'] = nok_state or None
        
        error_message = '; '.join(errors) if errors else ''
        is_valid = len(errors) == 0
        
        return is_valid, normalized, error_message
    
    @staticmethod
    def parse_and_validate(file_path: str) -> Tuple[List[Dict], List[Dict]]:
        """Parse Excel file and validate all rows."""
        df = StaffExcelParser.parse_excel_file(file_path)
        StaffExcelParser.validate_columns(df)
        
        valid_rows = []
        invalid_rows = []
        
        for idx, row in df.iterrows():
            is_valid, normalized, error_msg = StaffExcelParser.validate_and_normalize_row(row, idx)
            if is_valid:
                valid_rows.append(normalized)
            else:
                invalid_rows.append({
                    'row': idx + 2,  # +2 because Excel is 1-indexed and has header
                    'data': normalized,
                    'error': error_msg
                })
        
        return valid_rows, invalid_rows


class StaffImportManager:
    """Manage staff import batches and record creation."""
    
    @staticmethod
    def create_import_batch(
        batch_name: str,
        file_path: str,
        file_name: str,
        valid_records: List[Dict],
        created_by_id: int,
        invalid_records: List[Dict] = None
    ) -> StaffImportBatch:
        """Create a new import batch."""
        if invalid_records is None:
            invalid_records = []
        
        batch = StaffImportBatch(
            batch_name=batch_name,
            file_path=file_path,
            file_name=file_name,
            total_records=len(valid_records) + len(invalid_records),
            created_by=created_by_id
        )
        db.session.add(batch)
        db.session.flush()
        
        # Create import items for valid records
        for record in valid_records:
            item = StaffImportItem(
                batch_id=batch.id,
                first_name=record.get('first_name'),
                last_name=record.get('last_name'),
                email=record.get('email'),
                phone=record.get('phone'),
                gender=record.get('gender'),
                date_of_birth=record.get('date_of_birth'),
                address=record.get('address'),
                city=record.get('city'),
                state=record.get('state'),
                marital_status=record.get('marital_status'),
                employee_id=record.get('employee_id'),
                date_of_employment=record.get('date_of_employment'),
                department=record.get('department'),
                position=record.get('position'),
                role=record.get('role'),
                basic_salary=record.get('basic_salary'),
                allowances=record.get('allowances'),
                nok_full_name=record.get('nok_full_name'),
                nok_relationship=record.get('nok_relationship'),
                nok_phone=record.get('nok_phone'),
                nok_email=record.get('nok_email'),
                nok_address=record.get('nok_address'),
                nok_city=record.get('nok_city'),
                nok_state=record.get('nok_state'),
                status='pending'
            )
            db.session.add(item)
        
        # Create import items for invalid records with error messages
        for record in invalid_records:
            item = StaffImportItem(
                batch_id=batch.id,
                first_name=record.get('data', {}).get('first_name'),
                last_name=record.get('data', {}).get('last_name'),
                email=record.get('data', {}).get('email'),
                phone=record.get('data', {}).get('phone'),
                gender=record.get('data', {}).get('gender'),
                date_of_birth=record.get('data', {}).get('date_of_birth'),
                address=record.get('data', {}).get('address'),
                city=record.get('data', {}).get('city'),
                state=record.get('data', {}).get('state'),
                marital_status=record.get('data', {}).get('marital_status'),
                employee_id=record.get('data', {}).get('employee_id'),
                date_of_employment=record.get('data', {}).get('date_of_employment'),
                department=record.get('data', {}).get('department'),
                position=record.get('data', {}).get('position'),
                role=record.get('data', {}).get('role'),
                basic_salary=record.get('data', {}).get('basic_salary'),
                allowances=record.get('data', {}).get('allowances'),
                nok_full_name=record.get('data', {}).get('nok_full_name'),
                nok_relationship=record.get('data', {}).get('nok_relationship'),
                nok_phone=record.get('data', {}).get('nok_phone'),
                nok_email=record.get('data', {}).get('nok_email'),
                nok_address=record.get('data', {}).get('nok_address'),
                nok_city=record.get('data', {}).get('nok_city'),
                nok_state=record.get('data', {}).get('nok_state'),
                status='invalid',
                error_message=record.get('error', '')
            )
            db.session.add(item)
        
        db.session.commit()
        return batch
    
    @staticmethod
    def process_import_item(item: StaffImportItem) -> Tuple[bool, str]:
        """Process a single import item and create user + related records."""
        try:
            # Create User account
            user = User(
                name=f"{item.first_name} {item.last_name}",
                email=item.email,
                role=item.role,
                phone=item.phone,
                date_of_birth=item.date_of_birth,
                date_of_employment=item.date_of_employment,
                employee_id=item.employee_id,
                address=item.address,
                city=item.city,
                state=item.state,
                gender=item.gender,
                marital_status=item.marital_status,
                is_active=True
            )
            
            # Set default password
            default_password = StaffExcelParser.prepare_password(item.email)
            user.set_password(default_password)
            
            db.session.add(user)
            db.session.flush()
            
            # Create Staff Compensation
            compensation = StaffCompensation(
                user_id=user.id,
                basic_salary=float(item.basic_salary or 0),
                allowances=float(item.allowances or 0)
            )
            compensation.calculate_gross_salary()
            db.session.add(compensation)
            db.session.flush()
            
            # Create Next of Kin if provided
            if item.nok_full_name and item.nok_relationship:
                nok = NextOfKin(
                    user_id=user.id,
                    full_name=item.nok_full_name,
                    relationship=item.nok_relationship,
                    phone=item.nok_phone or '',
                    email=item.nok_email,
                    address=item.nok_address,
                    city=item.nok_city,
                    state=item.nok_state,
                    is_primary=True
                )
                db.session.add(nok)
                db.session.flush()
            
            # Create Department Access
            dept_access = DepartmentAccess(
                user_id=user.id,
                department=item.department,
                access_level='view' if item.role != 'admin' else 'approve',
                is_active=True
            )
            db.session.add(dept_access)
            
            # Update import item
            item.user_id = user.id
            item.status = 'imported'
            
            db.session.commit()
            return True, 'Successfully imported'
            
        except Exception as e:
            db.session.rollback()
            error_msg = f'Error importing record: {str(e)}'
            item.status = 'failed'
            item.error_message = error_msg
            db.session.commit()
            return False, error_msg
    
    @staticmethod
    def approve_batch(batch_id: int, approved_by_id: int) -> Tuple[bool, str]:
        """Process and approve an entire import batch."""
        batch = StaffImportBatch.query.get(batch_id)
        if not batch:
            return False, 'Batch not found'
        
        if batch.approval_state != ApprovalState.PENDING:
            return False, 'Batch is not pending approval'
        
        batch.approved_by = approved_by_id
        batch.approved_at = datetime.utcnow()
        batch.approval_state = ApprovalState.APPROVED
        db.session.commit()
        
        # Process all items - this creates User records
        successful = 0
        failed = 0
        
        for item in batch.items:
            success, msg = StaffImportManager.process_import_item(item)
            if success:
                successful += 1
            else:
                failed += 1
        
        # Update batch with final counts
        batch.imported_records = successful
        batch.failed_records = failed
        db.session.commit()
        
        return True, f'Batch approved. {successful} imported, {failed} failed'
    
    @staticmethod
    def reject_batch(batch_id: int, rejection_reason: str) -> Tuple[bool, str]:
        """Reject an import batch."""
        batch = StaffImportBatch.query.get(batch_id)
        if not batch:
            return False, 'Batch not found'
        
        if batch.approval_state not in [ApprovalState.DRAFT, ApprovalState.PENDING]:
            return False, 'Batch cannot be rejected in current state'
        
        batch.approval_state = ApprovalState.REJECTED
        batch.rejection_reason = rejection_reason
        db.session.commit()
        
        return True, 'Batch rejected'
