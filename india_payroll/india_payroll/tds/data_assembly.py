"""Assemble Form 24Q deductee rows from submitted Salary Slips.

This is the bridge from payroll to the TDS return: for the selected quarter it
finds every submitted Salary Slip of the company, reads the income-tax
deduction component, and produces one deductee line per (employee, month) where
tax was actually deducted. The rows are editable afterwards (manual override).
"""

import frappe
from frappe.utils import add_days, add_months, flt, getdate

# Index of the first month of each quarter, measured from the FY start (April).
QUARTER_OFFSET = {"Q1": 0, "Q2": 3, "Q3": 6, "Q4": 9}
MONTH_NAMES = {
	1: "January",
	2: "February",
	3: "March",
	4: "April",
	5: "May",
	6: "June",
	7: "July",
	8: "August",
	9: "September",
	10: "October",
	11: "November",
	12: "December",
}


def quarter_range_from_start(fy_start, quarter: str) -> tuple:
	"""Pure date math: quarter range given the financial-year start date."""
	start = add_months(getdate(fy_start), QUARTER_OFFSET[quarter])
	end = add_days(add_months(start, 3), -1)
	return start, end


def get_quarter_date_range(financial_year: str, quarter: str) -> tuple:
	fy_start = frappe.db.get_value("Fiscal Year", financial_year, "year_start_date")
	if not fy_start:
		frappe.throw(frappe._("Fiscal Year {0} not found.").format(financial_year))
	return quarter_range_from_start(fy_start, quarter)


def get_income_tax_components() -> list[str]:
	"""Salary components that represent income tax (TDS) deductions."""
	return frappe.get_all(
		"Salary Component",
		filters=[["type", "=", "Deduction"], ["disabled", "=", 0]],
		or_filters=[
			["is_income_tax_component", "=", 1],
			["variable_based_on_taxable_salary", "=", 1],
		],
		pluck="name",
	)


def get_tds_lines(company: str, financial_year: str, quarter: str) -> list[dict]:
	"""Return per-slip TDS lines for the quarter (only slips with TDS > 0)."""
	components = get_income_tax_components()
	if not components:
		frappe.throw(
			frappe._(
				"No income-tax salary component found. Mark the TDS deduction component "
				"as 'Is Income Tax Component' (or variable based on taxable salary)."
			)
		)

	start, end = get_quarter_date_range(financial_year, quarter)

	lines = frappe.db.sql(
		"""
		SELECT
			ss.name AS salary_slip,
			ss.employee,
			ss.employee_name,
			ss.posting_date,
			ss.start_date,
			ss.gross_pay,
			SUM(sd.amount) AS tax_deducted
		FROM `tabSalary Slip` ss
		INNER JOIN `tabSalary Detail` sd
			ON sd.parent = ss.name AND sd.parentfield = 'deductions'
		WHERE ss.company = %(company)s
			AND ss.docstatus = 1
			AND ss.start_date >= %(start)s
			AND ss.end_date <= %(end)s
			AND sd.salary_component IN %(components)s
		GROUP BY ss.name
		HAVING tax_deducted > 0
		ORDER BY ss.employee, ss.start_date
		""",
		{"company": company, "start": start, "end": end, "components": tuple(components)},
		as_dict=True,
	)
	return lines


def build_challan_month_map(company: str, financial_year: str, quarter: str) -> tuple[dict, str | None]:
	"""Return ({salary month: challan name}, sole challan for the quarter).

	Each challan declares the salary month it pays for (defaulted from the challan
	date). Where a month has more than one challan the earliest wins, so the mapping
	stays deterministic; the challan/payment reconciliation reports any shortfall.
	"""
	from india_payroll.india_payroll.doctype.tds_challan.tds_challan import (
		deduction_month_from_date,
		get_challans_for_period,
	)

	challans = get_challans_for_period(company, financial_year, quarter)

	by_month = {}
	for challan in sorted(challans, key=lambda c: (c.challan_date or getdate("1900-01-01"), c.name)):
		month = challan.get("deduction_month") or (
			deduction_month_from_date(challan.challan_date) if challan.challan_date else None
		)
		if month and month not in by_month:
			by_month[month] = challan.name

	sole = challans[0].name if len(challans) == 1 else None
	return by_month, sole


def populate_deductees(doc) -> int:
	"""Clear and repopulate the TDS Return's deductee table from payroll.

	Returns the number of deductee rows created.
	"""
	lines = get_tds_lines(doc.company, doc.financial_year, doc.quarter)
	challan_by_month, sole_challan = build_challan_month_map(doc.company, doc.financial_year, doc.quarter)

	# Map employee -> PAN in one query.
	employees = list({line.employee for line in lines})
	pan_map = {}
	if employees:
		for emp in frappe.get_all(
			"Employee",
			filters={"name": ["in", employees]},
			fields=["name", "pan_number"],
		):
			pan_map[emp.name] = (emp.pan_number or "").strip().upper()

	doc.set("deductees", [])
	for line in lines:
		pay_date = getdate(line.posting_date or line.start_date)
		month = MONTH_NAMES.get(getdate(line.start_date).month)
		doc.append(
			"deductees",
			{
				"employee": line.employee,
				"employee_name": line.employee_name,
				"pan": pan_map.get(line.employee),
				"deductee_status": "Resident",
				"month": month,
				"date_of_payment": pay_date,
				"date_of_deduction": pay_date,
				"amount_paid": flt(line.gross_pay),
				"tax_deducted": flt(line.tax_deducted),
				# Sandbox joins the payment sheet to the challan sheet, so every row
				# needs a challan. Match on the month the challan pays for; with a
				# single challan for the quarter, everything maps to it.
				"challan": challan_by_month.get(month) or sole_challan,
			},
		)
	return len(doc.deductees)
