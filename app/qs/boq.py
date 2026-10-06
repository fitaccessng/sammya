"""
QS Bill of Quantities (BOQ) endpoints
"""
from flask import Blueprint, render_template, request, redirect, url_for, flash, jsonify, current_app
from flask_login import login_required, current_user
from app.models import (
    BOQImport,
    BOQImportRow,
    BOQImportSheet,
    BOQItem,
    Project,
    ProjectDocument,
    db,
)
from app.utils import role_required, Roles
from .utils import check_project_access, get_user_qs_projects
import pandas as pd
from werkzeug.utils import secure_filename
import os
from io import BytesIO

ALLOWED_UPLOAD_EXTENSIONS = {
    'csv', 'xlsx', 'xls', 'xlsm', 'pdf', 'doc', 'docx', 'png', 'jpg', 'jpeg'
}

boq_bp = Blueprint('qs_boq', __name__)


def _normalise_excel_column(value):
    return ''.join(ch for ch in str(value or '').lower() if ch.isalnum())


def _row_values(row):
    values = [value for value in row.tolist() if pd.notna(value) and str(value).strip()]
    text_values = [str(value).strip() for value in values if not isinstance(value, (int, float))]
    numeric_values = []
    for value in values:
        try:
            numeric_values.append(float(value))
        except (TypeError, ValueError):
            continue
    description = next((value for value in text_values if not value.replace('.', '', 1).isdigit()), '')
    return values, description, numeric_values


def _mapped_column(df, names):
    wanted = {_normalise_excel_column(name) for name in names}
    for column in df.columns:
        if _normalise_excel_column(column) in wanted:
            return column
    return None


def _json_cell(value):
    if pd.isna(value):
        return None
    if hasattr(value, 'item'):
        value = value.item()
    if hasattr(value, 'isoformat') and not isinstance(value, str):
        return value.isoformat()
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    return str(value)


@boq_bp.route('/project/<int:project_id>/boq', methods=['GET'])
@login_required
@role_required([Roles.SUPER_HQ, Roles.QS_MANAGER, Roles.QS_STAFF])
def project_boq(project_id):
    """View and manage Bill of Quantities for a project"""
    try:
        project = check_project_access(project_id)
        if not project:
            return redirect(url_for('qs_dashboard.dashboard'))
        
        # Get BOQ items
        boq_items = BOQItem.query.filter_by(project_id=project_id).all()
        boq_imports = BOQImport.query.filter_by(project_id=project_id).order_by(
            BOQImport.created_at.desc()
        ).all()
        imported_boq_row_count = sum(
            len(sheet.rows)
            for boq_import in boq_imports
            for sheet in boq_import.sheets
        )
        
        # Calculate totals
        total_boq_value = sum(float(item.amount or 0) for item in boq_items)
        
        # Get all assigned projects for sidebar
        projects = get_user_qs_projects()
        
        # Prepare bill summary with proper data structure
        bill_summaries = {}
        if boq_items:
            bill_summaries['All Items'] = {
                'items': list(boq_items),
                'total': total_boq_value,
                'count': len(boq_items)
            }
        
        return render_template('qs/project_boq.html',
            project=project,
            projects=projects,
            boq_items=boq_items,
            boq_imports=boq_imports,
            imported_boq_row_count=imported_boq_row_count,
            bill_summaries=bill_summaries,
            total_boq=total_boq_value,
            total_items=len(boq_items)
        )
    except Exception as e:
        current_app.logger.error(f"Error loading BOQ for project {project_id}: {str(e)}", exc_info=True)
        flash(f'Error loading BOQ: {str(e)}', 'error')
        return redirect(url_for('qs_dashboard.dashboard'))


@boq_bp.route('/project/<int:project_id>/boq/add', methods=['POST'])
@login_required
@role_required([Roles.SUPER_HQ, Roles.QS_MANAGER, Roles.QS_STAFF])
def add_boq_item(project_id):
    """Add a new BOQ item"""
    try:
        project = check_project_access(project_id)
        if not project:
            return jsonify({'success': False, 'message': 'Access denied'}), 403
        
        data = request.get_json()
        
        boq_item = BOQItem(
            project_id=project_id,
            description=data.get('description'),
            quantity=float(data.get('quantity', 0)),
            unit=data.get('unit'),
            unit_rate=float(data.get('unit_rate', 0)),
            created_by=current_user.id
        )
        
        db.session.add(boq_item)
        db.session.commit()
        
        return jsonify({'success': True, 'message': 'BOQ item added successfully'})
    except Exception as e:
        current_app.logger.error(f"Error adding BOQ item: {str(e)}", exc_info=True)
        return jsonify({'success': False, 'message': str(e)}), 400


@boq_bp.route('/boq-item/<int:item_id>/edit', methods=['POST'])
@login_required
@role_required([Roles.SUPER_HQ, Roles.QS_MANAGER, Roles.QS_STAFF])
def edit_boq_item(item_id):
    """Edit an existing BOQ item"""
    try:
        boq_item = BOQItem.query.get(item_id)
        if not boq_item:
            return jsonify({'success': False, 'message': 'BOQ item not found'}), 404
        
        # Check project access
        project = check_project_access(boq_item.project_id)
        if not project:
            return jsonify({'success': False, 'message': 'Access denied'}), 403
        
        data = request.get_json()
        
        # Update BOQ item
        boq_item.description = data.get('description', boq_item.description)
        boq_item.quantity = float(data.get('quantity', boq_item.quantity))
        boq_item.unit = data.get('unit', boq_item.unit)
        boq_item.unit_rate = float(data.get('unit_rate', boq_item.unit_rate))
        
        db.session.commit()
        
        return jsonify({'success': True, 'message': 'BOQ item updated successfully'})
    except Exception as e:
        current_app.logger.error(f"Error editing BOQ item: {str(e)}", exc_info=True)
        return jsonify({'success': False, 'message': str(e)}), 400


@boq_bp.route('/boq-item/<int:item_id>/delete', methods=['POST'])
@login_required
@role_required([Roles.SUPER_HQ, Roles.QS_MANAGER, Roles.QS_STAFF])
def delete_boq_item(item_id):
    """Delete a BOQ item"""
    try:
        boq_item = BOQItem.query.get(item_id)
        if not boq_item:
            return jsonify({'success': False, 'message': 'BOQ item not found'}), 404
        
        # Check project access
        project = check_project_access(boq_item.project_id)
        if not project:
            return jsonify({'success': False, 'message': 'Access denied'}), 403
        
        project_id = boq_item.project_id
        db.session.delete(boq_item)
        db.session.commit()
        
        return jsonify({'success': True, 'message': 'BOQ item deleted successfully', 'project_id': project_id})
    except Exception as e:
        current_app.logger.error(f"Error deleting BOQ item: {str(e)}")
        return jsonify({'success': False, 'message': str(e)}), 400


@boq_bp.route('/project/<int:project_id>/material-schedule', methods=['GET'])
@login_required
@role_required([Roles.SUPER_HQ, Roles.QS_MANAGER, Roles.QS_STAFF])
def material_schedule(project_id):
    """View and manage material schedule for a project"""
    try:
        project = check_project_access(project_id)
        if not project:
            return redirect(url_for('qs_dashboard.dashboard'))
        
        # Get material schedule items (from BOQ grouped by material type)
        boq_items = BOQItem.query.filter_by(project_id=project_id).all()
        
        # Group materials by category
        materials_by_category = {}
        for item in boq_items:
            category = item.category or 'General'
            if category not in materials_by_category:
                materials_by_category[category] = []
            materials_by_category[category].append(item)
        
        # Calculate totals
        total_items = len(boq_items)
        categories = list(materials_by_category.keys())
        total_value = sum(float(item.amount or 0) for item in boq_items)
        avg_rate = total_value / total_items if total_items > 0 else 0
        
        # Get all assigned projects for sidebar
        projects = get_user_qs_projects()
        uploaded_documents = ProjectDocument.query.filter_by(
            project_id=project_id
        ).order_by(ProjectDocument.created_at.desc()).all()
        
        return render_template('qs/material_schedule.html',
            project=project,
            projects=projects,
            materials_by_category=materials_by_category,
            boq_items=boq_items,
            total_items=total_items,
            categories=categories,
            total_value=total_value,
            avg_rate=avg_rate,
            uploaded_documents=uploaded_documents
        )
    except Exception as e:
        current_app.logger.error(f"Error loading material schedule for project {project_id}: {str(e)}")
        flash('Error loading material schedule', 'error')
        return redirect(url_for('qs_dashboard.dashboard'))


@boq_bp.route('/project/<int:project_id>/material-takeoff', methods=['GET'])
@login_required
@role_required([Roles.SUPER_HQ, Roles.QS_MANAGER, Roles.QS_STAFF])
def material_takeoff(project_id):
    """View material takeoff for a project"""
    try:
        project = check_project_access(project_id)
        if not project:
            return redirect(url_for('qs_dashboard.dashboard'))
        
        boq_items = BOQItem.query.filter_by(project_id=project_id).all()
        
        return render_template('qs/material_takeoff.html',
            project=project,
            boq_items=boq_items
        )
    except Exception as e:
        current_app.logger.error(f"Error loading material takeoff for project {project_id}: {str(e)}")
        flash('Error loading material takeoff', 'error')
        return redirect(url_for('qs_dashboard.dashboard'))


@boq_bp.route('/project/<int:project_id>/rate-analysis', methods=['GET'])
@login_required
@role_required([Roles.SUPER_HQ, Roles.QS_MANAGER, Roles.QS_STAFF])
def rate_analysis(project_id):
    """View rate analysis for project items"""
    try:
        project = check_project_access(project_id)
        if not project:
            return redirect(url_for('qs_dashboard.dashboard'))
        
        boq_items = BOQItem.query.filter_by(project_id=project_id).all()
        
        return render_template('qs/rate_analysis.html',
            project=project,
            boq_items=boq_items
        )
    except Exception as e:
        current_app.logger.error(f"Error loading rate analysis for project {project_id}: {str(e)}")
        flash('Error loading rate analysis', 'error')
        return redirect(url_for('qs_dashboard.dashboard'))


@boq_bp.route('/project/<int:project_id>/boq/upload', methods=['POST'])
@login_required
@role_required([Roles.SUPER_HQ, Roles.QS_MANAGER, Roles.QS_STAFF])
def upload_boq(project_id):
    """Upload and parse BOQ file (Excel/CSV)"""
    try:
        project = check_project_access(project_id)
        if not project:
            return jsonify({'success': False, 'message': 'Access denied'}), 403
        
        # Check if file is present
        if 'file' not in request.files:
            return jsonify({'success': False, 'message': 'No file provided'}), 400
        
        file = request.files['file']
        if file.filename == '':
            return jsonify({'success': False, 'message': 'No file selected'}), 400
        
        # Check file extension
        allowed_extensions = ALLOWED_UPLOAD_EXTENSIONS
        file_ext = file.filename.rsplit('.', 1)[1].lower() if '.' in file.filename else ''
        
        if file_ext not in allowed_extensions:
            return jsonify({'success': False, 'message': 'This file type is not allowed'}), 400

        original_filename = file.filename
        is_tabular = file_ext in {'xlsx', 'xls', 'xlsm', 'csv'}
        if is_tabular:
            try:
                file.stream.seek(0)
                if file_ext == 'csv':
                    sheets = {os.path.splitext(original_filename)[0]: pd.read_csv(file)}
                else:
                    sheets = pd.read_excel(file, sheet_name=None)
            except Exception as e:
                current_app.logger.error(f"Error parsing file: {str(e)}")
                return jsonify({'success': False, 'message': 'Error parsing file. Please check the format.'}), 400

        stored_filename = secure_filename(original_filename)
        if not stored_filename:
            return jsonify({'success': False, 'message': 'Invalid file name'}), 400
        upload_folder = current_app.config.get('PROJECT_UPLOAD_FOLDER') or os.path.join(
            current_app.root_path, 'uploads', 'projects'
        )
        os.makedirs(upload_folder, exist_ok=True)
        stored_filename = f"{os.urandom(8).hex()}_{stored_filename}"
        filepath = os.path.join(upload_folder, stored_filename)
        file.stream.seek(0)
        file.save(filepath)

        db.session.add(ProjectDocument(
            project_id=project_id,
            title=original_filename,
            description='Uploaded from the QS BOQ',
            document_type=file_ext.upper(),
            file_path=filepath,
            file_name=original_filename,
            uploaded_by_id=current_user.id
        ))

        if not is_tabular:
            db.session.commit()
            return jsonify({
                'success': True,
                'message': f'File uploaded successfully: {original_filename}',
                'created': 0,
                'file_name': original_filename
            })

        boq_import = BOQImport(
            project_id=project_id,
            file_name=original_filename,
            uploaded_by_id=current_user.id,
        )
        imported_rows = 0
        for sheet_name, dataframe in sheets.items():
            headers = [str(header) for header in dataframe.columns]
            imported_sheet = BOQImportSheet(sheet_name=str(sheet_name), headers=headers)
            boq_import.sheets.append(imported_sheet)
            for index, row in dataframe.iterrows():
                cell_values = [_json_cell(value) for value in row.tolist()]
                if not any(value not in (None, '') for value in cell_values):
                    continue
                imported_sheet.rows.append(BOQImportRow(
                    row_number=int(index) + 2,
                    values=cell_values,
                ))
                imported_rows += 1

        db.session.add(boq_import)
        db.session.commit()

        return jsonify({
            'success': True,
            'message': f'Imported {imported_rows} rows across {len(sheets)} sheet(s), preserving the uploaded columns.',
            'created': imported_rows,
            'sheets': len(sheets),
            'import_id': boq_import.id,
        })
    
    except Exception as e:
        current_app.logger.error(f"Error uploading BOQ: {str(e)}")
        db.session.rollback()
        return jsonify({'success': False, 'message': str(e)}), 500


@boq_bp.route('/project/<int:project_id>/material-schedule/upload', methods=['POST'])
@login_required
@role_required([Roles.SUPER_HQ, Roles.QS_MANAGER, Roles.QS_STAFF])
def upload_material_schedule(project_id):
    """Upload and parse Material Schedule file (Excel/CSV)"""
    try:
        project = check_project_access(project_id)
        if not project:
            return jsonify({'success': False, 'message': 'Access denied'}), 403
        
        # Check if file is present
        if 'file' not in request.files:
            return jsonify({'success': False, 'message': 'No file provided'}), 400
        
        file = request.files['file']
        if file.filename == '':
            return jsonify({'success': False, 'message': 'No file selected'}), 400
        
        # Check file extension
        allowed_extensions = {'xlsx', 'xls', 'xlsm', 'csv'}
        file_ext = file.filename.rsplit('.', 1)[1].lower() if '.' in file.filename else ''
        
        if file_ext not in allowed_extensions:
            return jsonify({'success': False, 'message': 'File must be Excel or CSV'}), 400
        
        # Parse file
        try:
            if file_ext == 'csv':
                df = pd.read_csv(file)
            else:
                df = pd.read_excel(file)
        except Exception as e:
            current_app.logger.error(f"Error parsing file: {str(e)}")
            return jsonify({'success': False, 'message': 'Error parsing file. Please check the format.'}), 400

        # Column mapping for material and labour schedule
        column_mapping = {
            'item_id': ['item id', 'item_id', 'item no', 'item'],
            'description': ['description', 'desc', 'item description'],
            'material_qty': ['material qty', 'material quantity', 'material_qty', 'qty material'],
            'material_unit': ['material unit', 'material_unit', 'unit material'],
            'material_rate': ['material rate', 'material_rate', 'rate material'],
            'material_total': ['material total', 'material_total', 'total material'],
            'labour_qty': ['labour qty', 'labor qty', 'labour quantity', 'labour_qty'],
            'labour_unit': ['labour unit', 'labor unit', 'labour_unit'],
            'labour_rate': ['labour rate', 'labor rate', 'labour_rate'],
            'labour_total': ['labour total', 'labor total', 'labour_total'],
            'grand_total': ['grand total', 'grd total', 'total amount', 'grand_total']
        }

        mapped_columns = {
            target_col: _mapped_column(df, possible_names)
            for target_col, possible_names in column_mapping.items()
        }

        created_count = 0
        error_rows = []

        for idx, row in df.iterrows():
            try:
                values, fallback_description, numeric_values = _row_values(row)
                get_value = lambda key: row[mapped_columns[key]] if mapped_columns.get(key) else None
                item_id = str(get_value('item_id') or idx + 1).strip()
                description = str(get_value('description') or fallback_description or f'Imported row {idx + 1}').strip()
                material_qty = float(get_value('material_qty')) if get_value('material_qty') is not None and pd.notna(get_value('material_qty')) else (numeric_values[0] if numeric_values else 0)
                material_unit = str(get_value('material_unit') or 'item').strip()
                material_rate = float(get_value('material_rate')) if get_value('material_rate') is not None and pd.notna(get_value('material_rate')) else (numeric_values[1] if len(numeric_values) > 1 else 0)
                material_total = float(get_value('material_total')) if get_value('material_total') is not None and pd.notna(get_value('material_total')) else (material_qty * material_rate)
                labour_qty = float(get_value('labour_qty')) if get_value('labour_qty') is not None and pd.notna(get_value('labour_qty')) else 0
                labour_unit = str(get_value('labour_unit') or 'item').strip()
                labour_rate = float(get_value('labour_rate')) if get_value('labour_rate') is not None and pd.notna(get_value('labour_rate')) else 0
                labour_total = float(get_value('labour_total')) if get_value('labour_total') is not None and pd.notna(get_value('labour_total')) else (labour_qty * labour_rate)
                grand_total = float(get_value('grand_total')) if get_value('grand_total') is not None and pd.notna(get_value('grand_total')) else (material_total + labour_total)

                if material_qty <= 0 and labour_qty <= 0 and numeric_values:
                    material_qty = numeric_values[0]

                if material_qty > 0:
                    db.session.add(BOQItem(
                        project_id=project_id,
                        bill_no='Material Schedule',
                        item_no=f"{item_id}-M",
                        description=description,
                        quantity=material_qty,
                        unit=material_unit,
                        unit_rate=material_rate,
                        amount=material_total,
                        category='Materials'
                    ))
                    created_count += 1

                if labour_qty > 0:
                    db.session.add(BOQItem(
                        project_id=project_id,
                        bill_no='Labour Schedule',
                        item_no=f"{item_id}-L",
                        description=description,
                        quantity=labour_qty,
                        unit=labour_unit,
                        unit_rate=labour_rate,
                        amount=labour_total,
                        category='Labour'
                    ))
                    created_count += 1

                diff = round(grand_total - (material_total + labour_total), 2)
                if diff != 0:
                    db.session.add(BOQItem(
                        project_id=project_id,
                        bill_no='Material Schedule',
                        item_no=f"{item_id}-A",
                        description=f"{description} (Adjustment)",
                        quantity=1,
                        unit='sum',
                        unit_rate=diff,
                        amount=diff,
                        category='Adjustment'
                    ))
                    created_count += 1

            except Exception as e:
                current_app.logger.error(f"Error processing row {idx+1}: {str(e)}")
                error_rows.append(f"Row {idx+1}: {str(e)}")
        
        db.session.commit()
        
        message = f"File uploaded successfully: {original_filename}. Imported {created_count} material(s)"
        if error_rows:
            message += f". {len(error_rows)} rows had errors"
        
        return jsonify({
            'success': True,
            'message': message,
            'created': created_count,
            'errors': error_rows if error_rows else None
        })
    
    except Exception as e:
        current_app.logger.error(f"Error uploading material schedule: {str(e)}")
        db.session.rollback()
        return jsonify({'success': False, 'message': str(e)}), 500



