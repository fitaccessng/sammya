"""
QS Bill of Quantities (BOQ) endpoints
"""
from flask import Blueprint, render_template, request, redirect, url_for, flash, jsonify, current_app
from flask_login import login_required, current_user
from app.models import Project, BOQItem, db
from app.utils import role_required, Roles
from .utils import check_project_access, get_user_qs_projects
import pandas as pd
from werkzeug.utils import secure_filename
import os
from io import BytesIO

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
        
        return render_template('qs/material_schedule.html',
            project=project,
            projects=projects,
            materials_by_category=materials_by_category,
            boq_items=boq_items,
            total_items=total_items,
            categories=categories,
            total_value=total_value,
            avg_rate=avg_rate
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
        
        # Expected columns (flexible matching)
        column_mapping = {
            'bill_no': ['bill no', 'bill_no', 'bill no.', 'billno'],
            'item_no': ['item no', 'item_no', 'item no.', 'itemno'],
            'description': ['description', 'desc', 'item description'],
            'quantity': ['quantity', 'qty', 'quantity (nos)', 'quantity (m)', 'quantity (sqm)'],
            'unit': ['unit', 'unit of measurement', 'uom'],
            'unit_rate': ['unit rate', 'rate', 'unit price', 'rate (₦)', 'unit rate (₦)'],
            'amount': ['amount', 'line total', 'total amount', 'total'],
            'category': ['category', 'type', 'category']
        }
        
        mapped_columns = {
            target_col: _mapped_column(df, possible_names)
            for target_col, possible_names in column_mapping.items()
        }
        
        # Extract and create BOQ items
        created_count = 0
        error_rows = []
        
        for idx, row in df.iterrows():
            try:
                # Get values with defaults
                values, fallback_description, numeric_values = _row_values(row)
                get_value = lambda key: row[mapped_columns[key]] if mapped_columns.get(key) else None
                description = str(get_value('description') or fallback_description or f'Imported row {idx + 1}').strip()
                quantity = float(get_value('quantity')) if get_value('quantity') is not None and pd.notna(get_value('quantity')) else (numeric_values[0] if numeric_values else 1)
                unit = str(get_value('unit') or 'item').strip()
                unit_rate = float(get_value('unit_rate')) if get_value('unit_rate') is not None and pd.notna(get_value('unit_rate')) else (numeric_values[1] if len(numeric_values) > 1 else 0)
                amount = float(get_value('amount')) if get_value('amount') is not None and pd.notna(get_value('amount')) else (quantity * unit_rate)
                bill_no = str(get_value('bill_no') or 'Imported BOQ').strip()
                item_no = str(get_value('item_no') or idx + 1).strip()
                category = str(get_value('category') or 'General').strip()
                
                # Create BOQ item
                boq_item = BOQItem(
                    project_id=project_id,
                    bill_no=bill_no,
                    item_no=item_no,
                    description=description,
                    quantity=quantity,
                    unit=unit,
                    unit_rate=unit_rate,
                    amount=amount,
                    category=category
                )
                db.session.add(boq_item)
                created_count += 1
            
            except Exception as e:
                current_app.logger.error(f"Error processing row {idx+1}: {str(e)}")
                error_rows.append(f"Row {idx+1}: {str(e)}")
        
        db.session.commit()
        
        message = f"Successfully imported {created_count} BOQ items"
        if error_rows:
            message += f". {len(error_rows)} rows had errors"
        
        return jsonify({
            'success': True,
            'message': message,
            'created': created_count,
            'errors': error_rows if error_rows else None
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
        
        message = f"Successfully imported {created_count} materials"
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



