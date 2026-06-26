"""Build the Sandbox "Sheet JSON" workbook for a Form 24Q return.

Sandbox's TXT/validation APIs accept a structured JSON workbook describing the
deductor, the challans, and the deductee-wise deductions (and, for Q4, the
annual salary annexure). The exact schema can evolve; this module centralises
the mapping from a TDS Return document to that payload so it can be adjusted in
one place.
"""

import frappe
from frappe.utils import flt, formatdate

from india_payroll.india_payroll.tds.csi import build_challan_section
from india_payroll.india_payroll.tds.validators import normalize_financial_year


def build_sheet_json(doc) -> dict:
	"""Return the Sheet JSON dict for the given TDS Return document."""
	return {
		"form": doc.form_type,
		"financial_year": normalize_financial_year(doc.financial_year),
		"quarter": doc.quarter,
		"return_type": "Regular" if doc.return_type == "Original" else "Correction",
		"previous_receipt_number": doc.previous_receipt_number or None,
		"deductor": _deductor(doc),
		"challans": build_challan_section(doc.company, doc.financial_year, doc.quarter),
		"deductees": _deductees(doc),
		"salary_details": _salary_annexure(doc) if doc.quarter == "Q4" else [],
	}


def _deductor(doc) -> dict:
	company = frappe.get_cached_doc("Company", doc.company)
	return {
		"tan": doc.tan,
		"pan": doc.pan,
		"name": doc.deductor_name or company.company_name,
		"deductor_type": company.get("deductor_type"),
		"responsible_person": {
			"name": doc.responsible_person_name,
			"pan": doc.responsible_person_pan,
			"designation": doc.responsible_person_designation,
		},
	}


def _deductees(doc) -> list[dict]:
	rows = []
	for d in doc.deductees:
		rows.append(
			{
				"employee": d.employee,
				"name": d.employee_name,
				"pan": d.pan,
				"deductee_status": d.deductee_status,
				"month": d.month,
				"date_of_payment": formatdate(d.date_of_payment, "dd-MM-yyyy") if d.date_of_payment else None,
				"date_of_deduction": formatdate(d.date_of_deduction, "dd-MM-yyyy")
				if d.date_of_deduction
				else None,
				"amount_paid": flt(d.amount_paid),
				"tax_deducted": flt(d.tax_deducted),
				"challan_bsr": frappe.db.get_value("TDS Challan", d.challan, "bsr_code")
				if d.challan
				else None,
				"challan_serial_no": frappe.db.get_value("TDS Challan", d.challan, "challan_serial_no")
				if d.challan
				else None,
				"remarks": d.remarks,
			}
		)
	return rows


def _salary_annexure(doc) -> list[dict]:
	"""Annexure II (Q4 only): annual taxable salary + total tax per employee.

	Sourced from the latest submitted Salary Slip of each deductee in the
	financial year, which carries the computed annual figures.
	"""
	annexure = []
	employees = list({d.employee for d in doc.deductees})
	if not employees:
		return annexure

	fy_start, fy_end = frappe.db.get_value(
		"Fiscal Year", doc.financial_year, ["year_start_date", "year_end_date"]
	)

	for employee in employees:
		latest = frappe.get_all(
			"Salary Slip",
			filters={
				"employee": employee,
				"company": doc.company,
				"docstatus": 1,
				"start_date": [">=", fy_start],
				"end_date": ["<=", fy_end],
			},
			fields=[
				"employee_name",
				"annual_taxable_amount",
				"total_income_tax",
				"gross_year_to_date",
			],
			order_by="end_date desc",
			limit=1,
		)
		if not latest:
			continue
		slip = latest[0]
		pan = frappe.db.get_value("Employee", employee, "pan_number")
		annexure.append(
			{
				"employee": employee,
				"name": slip.employee_name,
				"pan": (pan or "").strip().upper(),
				"gross_salary": flt(slip.gross_year_to_date),
				"taxable_income": flt(slip.annual_taxable_amount),
				"total_tax": flt(slip.total_income_tax),
			}
		)
	return annexure
