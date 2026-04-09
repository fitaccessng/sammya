"""
Payroll Reports Module - Generate pay slips, tax reports, summaries
"""

from datetime import datetime, date
from decimal import Decimal
from typing import List, Dict, Tuple, Optional
import csv
import io
from reportlab.lib.pagesizes import letter, A4
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.units import inch
from reportlab.lib import colors
from reportlab.platypus import SimpleDocTemplate, Table, TableStyle, Paragraph, Spacer, PageBreak
from reportlab.lib.enums import TA_CENTER, TA_LEFT, TA_RIGHT
from pathlib import Path

from app.payroll_models import PayrollRecord, PayrollBatch, SalaryMapping
from app.models import User, db

# ==============================================================================
# PAY SLIP GENERATION
# ==============================================================================

class PaySlipGenerator:
    """Generate individual pay slips"""
    
    def __init__(self, company_name: str = "Default Company", company_details: Optional[Dict] = None):
        self.company_name = company_name
        self.company_details = company_details or {}
    
    def generate_pay_slip(self, record: PayrollRecord, output_path: str) -> Tuple[bool, str]:
        """Generate PDF pay slip for a record"""
        try:
            doc = SimpleDocTemplate(output_path, pagesize=A4)
            story = []
            styles = getSampleStyleSheet()
            
            # Title
            story.append(Paragraph(f"<b>{self.company_name}</b>", styles['Heading1']))
            story.append(Paragraph(f"<b>PAY SLIP</b>", styles['Heading2']))
            story.append(Spacer(1, 0.3 * inch))
            
            # Staff and Period Info
            info_data = [
                ['Staff Name:', record.user.full_name, 'Staff ID:', record.user.id],
                ['Department:', record.user.department or 'N/A', 'Position:', record.user.designation or 'N/A'],
                ['Email:', record.user.email, 'Period:', record.payroll_period],
            ]
            
            info_table = Table(info_data, colWidths=[1.2*inch, 2*inch, 1.2*inch, 2*inch])
            info_table.setStyle(TableStyle([
                ('FONTNAME', (0, 0), (-1, -1), 'Helvetica'),
                ('FONTSIZE', (0, 0), (-1, -1), 9),
                ('BOTTOMPADDING', (0, 0), (-1, -1), 12),
            ]))
            story.append(info_table)
            story.append(Spacer(1, 0.3 * inch))
            
            # Earnings
            earnings_data = [
                ['EARNINGS', '', 'Amount'],
                ['Basic Salary', '', f"₦{record.basic_salary:,.2f}"],
                ['House Allowance', '', f"₦{record.house_allowance:,.2f}"],
                ['Transport Allowance', '', f"₦{record.transport_allowance:,.2f}"],
                ['Meal Allowance', '', f"₦{record.meal_allowance:,.2f}"],
                ['Risk Allowance', '', f"₦{record.risk_allowance:,.2f}"],
                ['Performance Allowance', '', f"₦{record.performance_allowance:,.2f}"],
                ['', '', ''],
                ['GROSS SALARY', '', f"₦{record.gross_salary:,.2f}"],
            ]
            
            earnings_table = Table(earnings_data, colWidths=[2.5*inch, 1*inch, 2*inch])
            earnings_table.setStyle(TableStyle([
                ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
                ('FONTSIZE', (0, 0), (-1, 0), 10),
                ('BACKGROUND', (0, 0), (-1, 0), colors.lightgrey),
                ('GRID', (0, 0), (-1, -1), 1, colors.black),
                ('ALIGN', (2, 0), (2, -1), 'RIGHT'),
                ('FONTNAME', (0, 8), (-1, 8), 'Helvetica-Bold'),
                ('BACKGROUND', (0, 8), (-1, 8), colors.lightgrey),
                ('TOPPADDING', (0, 7), (-1, 7), 12),
            ]))
            story.append(earnings_table)
            story.append(Spacer(1, 0.2 * inch))
            
            # Deductions
            deductions_data = [
                ['DEDUCTIONS', '', 'Amount'],
                ['Income Tax', '', f"₦{record.tax_amount:,.2f}"],
                ['Pension Contribution', '', f"₦{record.pension_amount:,.2f}"],
                ['Insurance', '', f"₦{record.insurance_amount:,.2f}"],
                ['Loan Deduction', '', f"₦{record.loan_deduction:,.2f}"],
                ['Other Deduction', '', f"₦{record.other_deduction:,.2f}"],
                ['', '', ''],
                ['TOTAL DEDUCTIONS', '', f"₦{record.total_deductions:,.2f}"],
            ]
            
            deductions_table = Table(deductions_data, colWidths=[2.5*inch, 1*inch, 2*inch])
            deductions_table.setStyle(TableStyle([
                ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
                ('FONTSIZE', (0, 0), (-1, 0), 10),
                ('BACKGROUND', (0, 0), (-1, 0), colors.lightgrey),
                ('GRID', (0, 0), (-1, -1), 1, colors.black),
                ('ALIGN', (2, 0), (2, -1), 'RIGHT'),
                ('FONTNAME', (0, 7), (-1, 7), 'Helvetica-Bold'),
                ('BACKGROUND', (0, 7), (-1, 7), colors.lightgrey),
                ('TOPPADDING', (0, 6), (-1, 6), 12),
            ]))
            story.append(deductions_table)
            story.append(Spacer(1, 0.2 * inch))
            
            # Net Salary
            net_data = [
                ['NET SALARY PAYABLE', '', f"₦{record.net_salary:,.2f}"],
            ]
            net_table = Table(net_data, colWidths=[2.5*inch, 1*inch, 2*inch])
            net_table.setStyle(TableStyle([
                ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
                ('FONTSIZE', (0, 0), (-1, 0), 12),
                ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor('#0066cc')),
                ('TEXTCOLOR', (0, 0), (-1, 0), colors.white),
                ('GRID', (0, 0), (-1, -1), 2, colors.black),
                ('ALIGN', (2, 0), (2, -1), 'RIGHT'),
                ('LEFTPADDING', (0, 0), (-1, 0), 12),
                ('RIGHTPADDING', (0, 0), (-1, 0), 12),
                ('TOPPADDING', (0, 0), (-1, 0), 12),
                ('BOTTOMPADDING', (0, 0), (-1, 0), 12),
            ]))
            story.append(net_table)
            
            # Footer
            story.append(Spacer(1, 0.5 * inch))
            footer_text = f"Generated on {datetime.now().strftime('%Y-%m-%d %H:%M:%S')} | Payroll System"
            story.append(Paragraph(f"<i>{footer_text}</i>", styles['Normal']))
            
            doc.build(story)
            return True, output_path
            
        except Exception as e:
            return False, str(e)
    
    def generate_batch_pay_slips(self, batch: PayrollBatch, output_dir: str) -> Tuple[bool, Dict]:
        """Generate pay slips for all records in batch"""
        try:
            Path(output_dir).mkdir(parents=True, exist_ok=True)
            
            success_count = 0
            failed_count = 0
            generated_files = []
            
            for record in batch.payroll_records:
                filename = f"{record.user.id}_{record.user.last_name}_payslip_{batch.payroll_period}.pdf"
                output_path = str(Path(output_dir) / filename)
                
                success, _ = self.generate_pay_slip(record, output_path)
                
                if success:
                    success_count += 1
                    generated_files.append(output_path)
                else:
                    failed_count += 1
            
            return True, {
                'success_count': success_count,
                'failed_count': failed_count,
                'generated_files': generated_files,
                'output_dir': output_dir
            }
            
        except Exception as e:
            return False, str(e)


# ==============================================================================
# PAYROLL SUMMARY REPORTS
# ==============================================================================

class PayrollSummaryReport:
    """Generate payroll summary reports"""
    
    @staticmethod
    def generate_batch_summary(batch: PayrollBatch) -> Dict:
        """Generate summary statistics for batch"""
        records = batch.payroll_records
        
        if not records:
            return {}
        
        total_basic = sum(r.basic_salary for r in records)
        total_allowances = sum(
            r.house_allowance + r.transport_allowance + r.meal_allowance + 
            r.risk_allowance + r.performance_allowance
            for r in records
        )
        total_gross = sum(r.gross_salary for r in records)
        total_deductions = sum(r.total_deductions for r in records)
        total_net = sum(r.net_salary for r in records)
        
        return {
            'batch_id': batch.id,
            'batch_name': batch.batch_name,
            'payroll_period': batch.payroll_period,
            'record_count': len(records),
            'total_basic': total_basic,
            'total_house_allowance': sum(r.house_allowance for r in records),
            'total_transport_allowance': sum(r.transport_allowance for r in records),
            'total_meal_allowance': sum(r.meal_allowance for r in records),
            'total_allowances': total_allowances,
            'total_gross': total_gross,
            'total_tax': sum(r.tax_amount for r in records),
            'total_pension': sum(r.pension_amount for r in records),
            'total_insurance': sum(r.insurance_amount for r in records),
            'total_deductions': total_deductions,
            'total_net': total_net,
            'average_net': total_net / len(records) if records else 0,
        }
    
    @staticmethod
    def export_summary_csv(batch: PayrollBatch, output_path: str) -> Tuple[bool, str]:
        """Export batch summary to CSV"""
        try:
            summary = PayrollSummaryReport.generate_batch_summary(batch)
            
            with open(output_path, 'w', newline='') as f:
                writer = csv.writer(f)
                
                # Header
                writer.writerow(['Payroll Batch Summary Report'])
                writer.writerow([f"Period: {summary['payroll_period']}"])
                writer.writerow([])
                
                # Summary Statistics
                writer.writerow(['Metric', 'Amount'])
                writer.writerow(['Record Count', summary['record_count']])
                writer.writerow(['Total Basic Salary', f"₦{summary['total_basic']:,.2f}"])
                writer.writerow(['Total Allowances', f"₦{summary['total_allowances']:,.2f}"])
                writer.writerow(['Total Gross', f"₦{summary['total_gross']:,.2f}"])
                writer.writerow(['Total Tax', f"₦{summary['total_tax']:,.2f}"])
                writer.writerow(['Total Pension', f"₦{summary['total_pension']:,.2f}"])
                writer.writerow(['Total Insurance', f"₦{summary['total_insurance']:,.2f}"])
                writer.writerow(['Total Deductions', f"₦{summary['total_deductions']:,.2f}"])
                writer.writerow(['Total Net Payable', f"₦{summary['total_net']:,.2f}"])
                writer.writerow(['Average Net Salary', f"₦{summary['average_net']:,.2f}"])
            
            return True, output_path
            
        except Exception as e:
            return False, str(e)


# ==============================================================================
# TAX REPORTS
# ==============================================================================

class TaxReport:
    """Generate tax compliance reports"""
    
    @staticmethod
    def generate_tax_deduction_report(batch: PayrollBatch, output_path: str) -> Tuple[bool, str]:
        """Export tax deductions for reporting to revenue authority"""
        try:
            records = batch.payroll_records
            
            with open(output_path, 'w', newline='') as f:
                writer = csv.writer(f)
                
                # Headers
                writer.writerow([
                    'Staff ID', 'Staff Name', 'Email', 'Department',
                    'Basic Salary', 'Allowances', 'Gross Income',
                    'Taxable Income', 'Tax Amount', 'Period'
                ])
                
                # Data
                for record in records:
                    taxable_income = record.gross_salary
                    writer.writerow([
                        record.user.id,
                        record.user.full_name,
                        record.user.email,
                        record.user.department or 'N/A',
                        f"{record.basic_salary:,.2f}",
                        f"{record.gross_salary - record.basic_salary:,.2f}",
                        f"{record.gross_salary:,.2f}",
                        f"{taxable_income:,.2f}",
                        f"{record.tax_amount:,.2f}",
                        batch.payroll_period
                    ])
            
            return True, output_path
            
        except Exception as e:
            return False, str(e)
    
    @staticmethod
    def generate_tax_summary(batch: PayrollBatch) -> Dict:
        """Generate tax summary statistics"""
        records = batch.payroll_records
        
        total_tax = sum(r.tax_amount for r in records)
        taxable_staff = len([r for r in records if r.tax_amount > 0])
        
        return {
            'payroll_period': batch.payroll_period,
            'total_taxable_income': sum(r.gross_salary for r in records),
            'total_tax_deducted': total_tax,
            'taxable_staff_count': taxable_staff,
            'average_tax_rate': (total_tax / sum(r.gross_salary for r in records) * 100) if records else 0,
            'records_count': len(records),
        }


# ==============================================================================
# PENSION REPORTS
# ==============================================================================

class PensionReport:
    """Generate pension contribution reports"""
    
    @staticmethod
    def generate_pension_contribution_report(batch: PayrollBatch, output_path: str) -> Tuple[bool, str]:
        """Export pension contributions for submission"""
        try:
            records = batch.payroll_records
            
            with open(output_path, 'w', newline='') as f:
                writer = csv.writer(f)
                
                # Headers
                writer.writerow([
                    'Staff ID', 'Staff Name', 'Email', 'Pension ID',
                    'Basic Salary', 'Pension Contribution', 'Period'
                ])
                
                # Data
                for record in records:
                    writer.writerow([
                        record.user.id,
                        record.user.full_name,
                        record.user.email,
                        record.user.pension_id or 'N/A',
                        f"{record.basic_salary:,.2f}",
                        f"{record.pension_amount:,.2f}",
                        batch.payroll_period
                    ])
            
            return True, output_path
            
        except Exception as e:
            return False, str(e)


# ==============================================================================
# DEPARTMENTAL REPORTS
# ==============================================================================

class DepartmentalReport:
    """Generate departmental payroll reports"""
    
    @staticmethod
    def generate_by_department(batch: PayrollBatch) -> Dict[str, Dict]:
        """Generate summary by department"""
        records = batch.payroll_records
        departments = {}
        
        for record in records:
            dept = record.user.department or 'Unassigned'
            
            if dept not in departments:
                departments[dept] = {
                    'staff_count': 0,
                    'total_basic': Decimal('0'),
                    'total_gross': Decimal('0'),
                    'total_deductions': Decimal('0'),
                    'total_net': Decimal('0'),
                    'total_tax': Decimal('0'),
                }
            
            departments[dept]['staff_count'] += 1
            departments[dept]['total_basic'] += record.basic_salary
            departments[dept]['total_gross'] += record.gross_salary
            departments[dept]['total_deductions'] += record.total_deductions
            departments[dept]['total_net'] += record.net_salary
            departments[dept]['total_tax'] += record.tax_amount
        
        return departments
    
    @staticmethod
    def export_departmental_csv(batch: PayrollBatch, output_path: str) -> Tuple[bool, str]:
        """Export departmental summary to CSV"""
        try:
            departments = DepartmentalReport.generate_by_department(batch)
            
            with open(output_path, 'w', newline='') as f:
                writer = csv.writer(f)
                
                writer.writerow(['Departmental Payroll Summary'])
                writer.writerow([f"Period: {batch.payroll_period}"])
                writer.writerow([])
                
                # Headers
                writer.writerow([
                    'Department', 'Staff Count', 'Total Basic',
                    'Total Gross', 'Total Deductions', 'Total Tax', 'Total Net'
                ])
                
                # Data
                for dept, data in sorted(departments.items()):
                    writer.writerow([
                        dept,
                        data['staff_count'],
                        f"₦{data['total_basic']:,.2f}",
                        f"₦{data['total_gross']:,.2f}",
                        f"₦{data['total_deductions']:,.2f}",
                        f"₦{data['total_tax']:,.2f}",
                        f"₦{data['total_net']:,.2f}",
                    ])
            
            return True, output_path
            
        except Exception as e:
            return False, str(e)
