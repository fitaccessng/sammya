"""Shared file analysis, mapping, normalization, and validation engine."""

import csv
import io
import re
from difflib import SequenceMatcher
from decimal import Decimal, InvalidOperation
from datetime import date, datetime

import pandas as pd


class UniversalImportService:
    """Analyze tabular files against a module-specific import schema."""

    HIGH_CONFIDENCE = 0.90
    REVIEW_CONFIDENCE = 0.70
    HEADER_SCAN_ROWS = 30

    def analyze_file(self, file_storage, schema, sheet_name=None, header_row=None, mapping_overrides=None,
                     date_format=None, learned_mappings=None):
        filename = getattr(file_storage, 'filename', '') or ''
        extension = filename.rsplit('.', 1)[-1].lower() if '.' in filename else ''
        if extension not in {'csv', 'txt', 'xlsx', 'xls', 'xlsm'}:
            raise ValueError('Unsupported structured-data file. Use CSV, TXT, XLS, XLSX, or XLSM.')
        content = file_storage.read()
        if not content:
            raise ValueError('The uploaded file is empty.')

        sheets = []
        header_preview = []
        if extension in {'csv', 'txt'}:
            matrix = self._read_delimited(content)
        else:
            try:
                workbook = pd.ExcelFile(io.BytesIO(content))
                sheets = workbook.sheet_names
                if sheet_name == '__list_sheets__':
                    return {
                        'file_type': extension,
                        'sheets': sheets,
                        'sheet_name': None,
                        'header_row': None,
                        'headers': [],
                        'rows': [],
                    }
                if sheet_name and sheet_name not in sheets:
                    raise ValueError(f"Worksheet '{sheet_name}' was not found. Available sheets: {', '.join(sheets)}.")
                selected_sheet = sheet_name or sheets[0]
                matrix = pd.read_excel(workbook, sheet_name=selected_sheet, header=None, dtype=object).values.tolist()
                sheet_name = selected_sheet
            except Exception as exc:
                raise ValueError(f'Unable to read the spreadsheet: {exc}') from exc

        detected_row, headers, raw_rows = self._detect_header(matrix, schema, header_row)
        header_preview = [
            {'row': index + 1, 'values': [self._json_safe(value) for value in values[:12]], 'selected': index == detected_row}
            for index, values in enumerate(matrix[max(0, detected_row - 2):detected_row + 3], start=max(0, detected_row - 2))
        ]
        mappings = self.suggest_mappings(headers, schema, learned_mappings=learned_mappings)
        for source, target in (mapping_overrides or {}).items():
            if source not in headers:
                continue
            if target not in schema.field_map and target != '__ignore__':
                raise ValueError(f'Unknown target field: {target}')
            mappings[source] = self._mapping_result(source, target, 1.0, schema) if target != '__ignore__' else {
                'source': source, 'target': '__ignore__', 'confidence': 1.0, 'level': 'confirmed',
            }

        normalized_rows = []
        for offset, values in enumerate(raw_rows, start=detected_row + 2):
            if not any(self._has_value(value) for value in values):
                continue
            original = {headers[index]: values[index] if index < len(values) else None for index in range(len(headers))}
            normalized = {}
            for source, mapping in mappings.items():
                target = mapping.get('target')
                if target in (None, '__ignore__'):
                    continue
                normalized[target] = self._normalize_value(
                    original.get(source), schema.field_map[target].kind, date_format=date_format,
                )
            errors = self._validate_row(normalized, schema)
            errors = self._structured_errors(errors, original, mappings, offset)
            date_warnings = self._ambiguous_date_warnings(original, mappings, schema, offset)
            normalized_rows.append({
                'row_number': offset,
                'original': {key: self._json_safe(value) for key, value in original.items()},
                'data': normalized,
                'errors': errors,
                'warnings': date_warnings,
                'date_confirmation_required': bool(date_warnings),
            })

        duplicate_rows = self._find_duplicates(normalized_rows, schema)
        for row in normalized_rows:
            row['potential_duplicate'] = row['row_number'] in duplicate_rows

        mapped_targets = {item.get('target') for item in mappings.values() if item.get('target') not in (None, '__ignore__')}
        missing_required = [field.name for field in schema.fields if field.required and field.name not in mapped_targets]
        confirmation_required = [
            source for source, item in mappings.items()
            if item.get('level') == 'review' or item.get('target') is None
        ]
        unsupported_columns = [
            {'source': source, 'reason': 'No destination field is mapped.'}
            for source, item in mappings.items() if item.get('target') is None
        ]
        unsupported_columns.extend(
            {
                'source': source,
                'target': item['target'],
                'reason': f"{schema.field_map[item['target']].label} is not stored by the existing {schema.entity} model.",
            }
            for source, item in mappings.items()
            if item.get('target') in schema.field_map and not schema.field_map[item['target']].persisted
        )
        return {
            'module': schema.module,
            'entity': schema.entity,
            'file_type': extension,
            'sheet_name': sheet_name,
            'sheets': sheets,
            'header_preview': header_preview,
            'date_format': date_format,
            'header_row': detected_row,
            'headers': headers,
            'mappings': mappings,
            'missing_required': missing_required,
            'confirmation_required': confirmation_required,
            'unsupported_columns': unsupported_columns,
            'rows': normalized_rows,
            'records_found': len(normalized_rows),
            'ready_count': sum(not row['errors'] and not row['potential_duplicate'] and not row['date_confirmation_required'] for row in normalized_rows)
            if not missing_required and not confirmation_required else 0,
            'error_count': sum(bool(row['errors']) for row in normalized_rows),
            'duplicate_count': len(duplicate_rows),
        }

    def remap_analysis(self, analysis, schema, mapping_overrides, date_format=None):
        """Apply user mapping choices to the preserved original row values."""
        mappings = dict(analysis.get('mappings') or {})
        claimed_targets = set()
        for source, target in mapping_overrides.items():
            if source not in analysis.get('headers', []):
                raise ValueError(f'Unknown uploaded column: {source}')
            if target not in schema.field_map and target != '__ignore__':
                raise ValueError(f'Unknown destination field: {target}')
            if target not in (None, '__ignore__') and target in claimed_targets:
                raise ValueError(f'More than one source column maps to {schema.field_map[target].label}.')
            if target not in (None, '__ignore__'):
                claimed_targets.add(target)
            if target == '__ignore__':
                mappings[source] = {'source': source, 'target': '__ignore__', 'confidence': 1.0, 'level': 'confirmed'}
            elif target:
                mappings[source] = self._mapping_result(source, target, 1.0, schema)
            else:
                mappings[source] = {'source': source, 'target': None, 'confidence': 0, 'level': 'unmapped'}

        rows = []
        for old_row in analysis.get('rows', []):
            original = old_row.get('original') or {}
            data = {}
            for source, mapping in mappings.items():
                target = mapping.get('target')
                if target in (None, '__ignore__'):
                    continue
                data[target] = self._normalize_value(
                    original.get(source), schema.field_map[target].kind, date_format=date_format,
                )
            rows.append({
                'row_number': old_row['row_number'],
                'original': original,
                'data': data,
                'errors': self._structured_errors(
                    self._validate_row(data, schema), original, mappings, old_row['row_number'],
                ),
                'potential_duplicate': False,
                'warnings': self._ambiguous_date_warnings(
                    original, mappings, schema, old_row['row_number'], date_format=date_format,
                ),
            })
            rows[-1]['date_confirmation_required'] = bool(rows[-1]['warnings'])

        duplicate_rows = self._find_duplicates(rows, schema)
        for row in rows:
            row['potential_duplicate'] = row['row_number'] in duplicate_rows
        mapped_targets = {item.get('target') for item in mappings.values() if item.get('target') not in (None, '__ignore__')}
        missing_required = [item.name for item in schema.fields if item.required and item.name not in mapped_targets]
        unsupported_columns = []
        for source, mapping in mappings.items():
            target = mapping.get('target')
            if target is None:
                unsupported_columns.append({'source': source, 'reason': 'No destination field is mapped.'})
            elif target != '__ignore__' and not schema.field_map[target].persisted:
                unsupported_columns.append({
                    'source': source,
                    'target': target,
                    'reason': f'{schema.field_map[target].label} is not stored by the existing {schema.entity} model.',
                })
        result = dict(analysis)
        result.update({
            'mappings': mappings,
            'rows': rows,
            'missing_required': missing_required,
            'confirmation_required': [source for source, mapping in mappings.items() if mapping.get('target') is None],
            'unsupported_columns': unsupported_columns,
            'records_found': len(rows),
            'ready_count': sum(not row['errors'] and not row['potential_duplicate'] and not row['date_confirmation_required'] for row in rows)
            if not missing_required else 0,
            'error_count': sum(bool(row['errors']) for row in rows),
            'duplicate_count': len(duplicate_rows),
        })
        return result

    def suggest_mappings(self, headers, schema, learned_mappings=None):
        mappings = {}
        claimed = set()
        learned_mappings = learned_mappings or {}
        for header in headers:
            source_normalized = self.normalize_header(header)
            candidates = []
            for field in schema.fields:
                aliases = (field.name, field.label) + field.aliases
                scores = [self._similarity(source_normalized, self.normalize_header(alias)) for alias in aliases]
                candidates.append((max(scores, default=0.0), field.name))
            score, target = max(candidates, default=(0.0, None))
            learned = learned_mappings.get(self.normalize_header(header))
            if learned and learned.get('target') in schema.field_map:
                target = learned['target']
                score = max(score, min(0.99, 0.88 + min(learned.get('confirmations', 1), 10) * 0.01))
            if target in claimed or score < self.REVIEW_CONFIDENCE:
                mappings[header] = {'source': header, 'target': None, 'suggestion': target if score >= self.REVIEW_CONFIDENCE else None,
                                    'confidence': round(score, 3), 'level': 'unmapped'}
            else:
                mappings[header] = self._mapping_result(header, target, score, schema)
                if learned:
                    mappings[header]['basis'] = 'previous confirmed mappings in this context'
                claimed.add(target)
        return mappings

    @staticmethod
    def normalize_header(value):
        text = str(value or '').strip().lower()
        text = text.replace('%', ' percent ').replace('&', ' and ')
        return re.sub(r'[^a-z0-9]+', ' ', text).strip()

    def _detect_header(self, matrix, schema, requested_row):
        if not matrix:
            raise ValueError('The file does not contain any tabular rows.')
        if requested_row is not None:
            if requested_row < 0 or requested_row >= len(matrix):
                raise ValueError('Selected header row is outside the file.')
            header_index = requested_row
        else:
            best = (-1, 0)
            for index, values in enumerate(matrix[:self.HEADER_SCAN_ROWS]):
                headers = [str(value).strip() if self._has_value(value) else '' for value in values]
                mapped = self.suggest_mappings(headers, schema)
                targets = {item['target'] for item in mapped.values() if item.get('target')}
                required_hits = sum(field.name in targets for field in schema.fields if field.required)
                score = len(targets) + required_hits * 2
                if score > best[0]:
                    best = (score, index)
            header_index = best[1]
            if best[0] <= 0:
                raise ValueError('Could not detect a header row. Select or rename the columns before importing.')

        headers = [str(value).strip() if self._has_value(value) else '' for value in matrix[header_index]]
        if not any(headers):
            raise ValueError('The selected header row is empty.')
        return header_index, headers, matrix[header_index + 1:]

    @staticmethod
    def _read_delimited(content):
        text = content.decode('utf-8-sig', errors='replace')
        try:
            dialect = csv.Sniffer().sniff(text[:4096], delimiters=',;\t|')
        except csv.Error:
            dialect = csv.excel
        try:
            return list(csv.reader(io.StringIO(text), dialect))
        except csv.Error as exc:
            raise ValueError(f'Unable to parse delimited file: {exc}') from exc

    @staticmethod
    def _similarity(left, right):
        if not left or not right:
            return 0.0
        if left == right:
            return 1.0
        left_words, right_words = set(left.split()), set(right.split())
        token_score = len(left_words & right_words) / max(len(left_words | right_words), 1)
        sequence_score = SequenceMatcher(None, left, right).ratio()
        return max(token_score, sequence_score * 0.92)

    @staticmethod
    def _mapping_result(source, target, confidence, schema):
        level = 'high' if confidence >= UniversalImportService.HIGH_CONFIDENCE else 'review'
        return {'source': source, 'target': target, 'label': schema.field_map[target].label,
                'confidence': round(confidence, 3), 'level': level}

    @staticmethod
    def _normalize_value(value, kind, date_format=None):
        if not UniversalImportService._has_value(value):
            return None
        if kind == 'text':
            return re.sub(r'\s+', ' ', str(value).strip())
        if kind == 'number':
            if isinstance(value, (int, float, Decimal)):
                return float(value)
            text = str(value).strip().replace('₦', '').replace('NGN', '').replace('$', '').replace('€', '').replace('£', '').replace('%', '')
            text = re.sub(r'\s+', '', text)
            if text.startswith('(') and text.endswith(')'):
                text = '-' + text[1:-1]
            if ',' in text and '.' in text:
                decimal_separator = ',' if text.rfind(',') > text.rfind('.') else '.'
                thousands_separator = '.' if decimal_separator == ',' else ','
                text = text.replace(thousands_separator, '').replace(decimal_separator, '.')
            elif ',' in text:
                parts = text.split(',')
                text = ''.join(parts) if len(parts[-1]) == 3 else '.'.join(parts)
            elif text.count('.') > 1:
                parts = text.split('.')
                if all(len(part) == 3 for part in parts[1:]):
                    text = ''.join(parts)
            try:
                return float(Decimal(text))
            except (InvalidOperation, ValueError):
                return {'invalid_number': str(value)}
        if kind == 'unit':
            normalized = re.sub(r'\s+', ' ', str(value).strip().lower().replace('.', ''))
            unit_aliases = {
                'm': 'm', 'meter': 'm', 'meters': 'm', 'metre': 'm', 'metres': 'm',
                'sqm': 'm2', 'm2': 'm2', 'm²': 'm2', 'square metre': 'm2',
                'square meter': 'm2', 'square metres': 'm2', 'square meters': 'm2',
            }
            return unit_aliases.get(normalized, str(value).strip())
        if kind == 'date':
            if isinstance(value, (datetime, date)):
                return value.date().isoformat() if isinstance(value, datetime) else value.isoformat()
            value_text = str(value).strip()
            if date_format in {'DD/MM/YYYY', 'DD-MM-YYYY'}:
                parsed = pd.to_datetime(value_text, errors='coerce', dayfirst=True)
            elif date_format in {'MM/DD/YYYY', 'MM-DD-YYYY'}:
                parsed = pd.to_datetime(value_text, errors='coerce', dayfirst=False)
            else:
                parsed = pd.to_datetime(value_text, errors='coerce', dayfirst=True)
            return parsed.date().isoformat() if not pd.isna(parsed) else {'invalid_date': str(value)}
        return value

    @staticmethod
    def _validate_row(data, schema):
        errors = []
        for item in schema.fields:
            value = data.get(item.name)
            if item.required and value in (None, ''):
                errors.append({'field': item.name, 'message': f'{item.label} is required.'})
            if isinstance(value, dict) and 'invalid_number' in value:
                errors.append({'field': item.name, 'message': f'Invalid number: {value["invalid_number"]}'})
            if isinstance(value, dict) and 'invalid_date' in value:
                errors.append({'field': item.name, 'message': f'Invalid date: {value["invalid_date"]}'})
        if schema.entity == 'bank_transaction':
            has_amount = any(data.get(name) not in (None, '') for name in ('debit', 'credit', 'amount'))
            if not has_amount:
                errors.append({'field': 'amount', 'message': 'A debit, credit, or transaction amount is required.'})
        if schema.entity == 'boq' and not any(error['field'] == 'quantity' for error in errors):
            quantity = data.get('quantity')
            if quantity is not None and isinstance(quantity, (int, float)) and quantity <= 0:
                errors.append({'field': 'quantity', 'message': 'Quantity must be greater than zero.'})
        if schema.entity == 'boq' and not any(error['field'] == 'rate' for error in errors):
            rate = data.get('rate')
            if rate is not None and isinstance(rate, (int, float)) and rate < 0:
                errors.append({'field': 'rate', 'message': 'Unit rate cannot be negative.'})
        if schema.entity == 'project_task':
            start = data.get('start_date')
            end = data.get('end_date')
            if start and end and isinstance(start, str) and isinstance(end, str) and start > end:
                errors.append({'field': 'end_date', 'message': 'End date must not precede start date.'})
            progress = data.get('progress')
            if isinstance(progress, (int, float)) and not 0 <= progress <= 100:
                errors.append({'field': 'progress', 'message': 'Progress must be between 0 and 100.'})
        return errors

    @staticmethod
    def _structured_errors(errors, original, mappings, row_number):
        structured = []
        for error in errors:
            field_name = error['field']
            source = next(
                (column for column, mapping in mappings.items() if mapping.get('target') == field_name),
                field_name,
            )
            message = error['message']
            if message.startswith('Invalid number'):
                code = 'INVALID_NUMBER'
            elif message.startswith('Invalid date'):
                code = 'INVALID_DATE'
            elif message.endswith('is required.'):
                code = 'REQUIRED_FIELD'
            else:
                code = 'VALIDATION_ERROR'
            structured.append({
                'row': row_number,
                'column': source,
                'field': field_name,
                'value': original.get(source),
                'code': code,
                'message': message,
            })
        return structured

    @staticmethod
    def _ambiguous_date_warnings(original, mappings, schema, row_number, date_format=None):
        warnings = []
        for source, mapping in mappings.items():
            target = mapping.get('target')
            if target not in schema.field_map or schema.field_map[target].kind != 'date':
                continue
            value = str(original.get(source) or '').strip()
            match = re.fullmatch(r'(\d{1,2})[/-](\d{1,2})[/-](\d{2,4})', value)
            if not match:
                continue
            first, second = int(match.group(1)), int(match.group(2))
            if not date_format and 1 <= first <= 12 and 1 <= second <= 12 and first != second:
                day_month = pd.to_datetime(value, dayfirst=True).date().isoformat()
                month_day = pd.to_datetime(value, dayfirst=False).date().isoformat()
                warnings.append({
                    'row': row_number,
                    'column': source,
                    'field': target,
                    'value': original.get(source),
                    'interpretations': {'DD/MM/YYYY': day_month, 'MM/DD/YYYY': month_day},
                    'code': 'AMBIGUOUS_DATE',
                    'message': 'Date could be day/month or month/day. Confirm the normalized date before import.',
                })
        return warnings

    @staticmethod
    def _find_duplicates(rows, schema):
        """Return row numbers duplicated by this schema's configured fields."""
        if not schema.duplicate_fields:
            return set()
        first_seen = {}
        duplicates = set()
        for row in rows:
            values = row['data']
            key_values = [values.get(name) for name in schema.duplicate_fields]
            if schema.duplicate_mode == 'any':
                keys = [
                    (field_name, str(value).strip().casefold())
                    for field_name, value in zip(schema.duplicate_fields, key_values)
                    if value not in (None, '')
                ]
                if any(key in first_seen for key in keys):
                    duplicates.add(row['row_number'])
                for key in keys:
                    first_seen.setdefault(key, row['row_number'])
            else:
                if not any(value not in (None, '') for value in key_values):
                    continue
                key = tuple(str(value).strip().casefold() if value is not None else '' for value in key_values)
                if key in first_seen:
                    duplicates.add(row['row_number'])
                else:
                    first_seen[key] = row['row_number']
        return duplicates

    @staticmethod
    def _has_value(value):
        if value is None:
            return False
        try:
            return not pd.isna(value) and str(value).strip() != ''
        except (TypeError, ValueError):
            return bool(str(value).strip())

    @staticmethod
    def _json_safe(value):
        if isinstance(value, (datetime, date)):
            return value.isoformat()
        if hasattr(value, 'item'):
            return value.item()
        return value