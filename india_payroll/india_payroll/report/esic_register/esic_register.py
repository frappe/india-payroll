# Copyright (c) 2026, Frappe Technologies Pvt. Ltd. and contributors
# For license information, please see license.txt

import datetime

import frappe
from frappe import _
from frappe.query_builder import DocType
from frappe.utils import flt

from india_payroll.india_payroll.utils import get_effective_ssa_values

# Statutory rates (ESI Act, 1948 — effective July 2019)
EMPLOYEE_ESI_RATE = 0.0075  # 0.75 %
EMPLOYER_ESI_RATE = 0.0325  # 3.25 %

ESI_WAGE_CEILING = 21_000
ESI_WAGE_CEILING_DISABILITY = 25_000

_MONTHS = {
	"January": 1,
	"February": 2,
	"March": 3,
	"April": 4,
	"May": 5,
	"June": 6,
	"July": 7,
	"August": 8,
	"September": 9,
	"October": 10,
	"November": 11,
	"December": 12,
}


def execute(filters=None):
	filters = filters or {}
	columns = get_columns()
	data = get_data(filters)
	return columns, data


def get_columns():
	return [
		{
			"label": _("Employee"),
			"fieldname": "employee",
			"fieldtype": "Link",
			"options": "Employee",
			"width": 110,
		},
		{
			"label": _("ESIC IP Number"),
			"fieldname": "esic_card_no",
			"fieldtype": "Data",
			"width": 130,
		},
		{
			"label": _("Department"),
			"fieldname": "department",
			"fieldtype": "Link",
			"options": "Department",
			"width": 140,
		},
		{
			"label": _("Designation"),
			"fieldname": "designation",
			"fieldtype": "Link",
			"options": "Designation",
			"width": 140,
		},
		{
			"label": _("Gross Wages"),
			"fieldname": "gross_wages",
			"fieldtype": "Currency",
			"options": "currency",
			"width": 130,
		},
		{
			"label": _("Employee Contribution (0.75%)"),
			"fieldname": "employee_esi",
			"fieldtype": "Currency",
			"options": "currency",
			"width": 190,
		},
		{
			"label": _("Employer Contribution (3.25%)"),
			"fieldname": "employer_esi",
			"fieldtype": "Currency",
			"options": "currency",
			"width": 190,
		},
		{
			"label": _("Total Contribution (4%)"),
			"fieldname": "total_esi",
			"fieldtype": "Currency",
			"options": "currency",
			"width": 160,
		},
		{
			"label": _("Coverage Status"),
			"fieldname": "coverage_status",
			"fieldtype": "Data",
			"width": 130,
		},
		{
			"label": _("Currency"),
			"fieldname": "currency",
			"fieldtype": "Data",
			"width": 60,
			"hidden": 1,
		},
	]


def get_data(filters):
	date_range = _get_date_range(filters)

	SS = DocType("Salary Slip")
	Emp = DocType("Employee")

	query = (
		frappe.qb.from_(SS)
		.join(Emp)
		.on(Emp.name == SS.employee)
		.select(
			SS.employee,
			SS.company,
			SS.salary_structure,
			SS.start_date,
			SS.gross_pay,
			SS.currency,
			Emp.department,
			Emp.designation,
		)
		.where(SS.docstatus == 1)
	)

	if filters.get("company"):
		query = query.where(SS.company == filters["company"])

	if date_range:
		query = query.where(SS.start_date >= date_range["from_date"])
		query = query.where(SS.start_date <= date_range["to_date"])

	# ESIC IP number stays on the Employee master; pull it only when present.
	if frappe.db.has_column("Employee", "esic_card_no"):
		query = query.select(Emp.esic_card_no)

	rows = query.run(as_dict=True)

	coverage_filter = filters.get("coverage_status")

	data = []
	for row in rows:
		gross = flt(row.gross_pay)
		# The PwD flag lives on the Salary Structure Assignment effective for the slip.
		ssa = get_effective_ssa_values(
			row.employee, row.company, row.salary_structure, row.start_date, ["is_person_with_disability"]
		)
		is_pwd = bool(ssa.get("is_person_with_disability"))
		ceiling = ESI_WAGE_CEILING_DISABILITY if is_pwd else ESI_WAGE_CEILING

		if gross > ceiling:
			status = "Exempt"
		elif is_pwd:
			status = "PwD"
		else:
			status = "Covered"

		if coverage_filter and status != coverage_filter:
			continue

		if status == "Exempt":
			employee_esi = employer_esi = total_esi = 0.0
		else:
			employee_esi = flt(gross * EMPLOYEE_ESI_RATE, 2)
			employer_esi = flt(gross * EMPLOYER_ESI_RATE, 2)
			total_esi = flt(employee_esi + employer_esi, 2)

		data.append(
			{
				"employee": row.employee,
				"esic_card_no": row.get("esic_card_no") or "",
				"department": row.get("department") or "",
				"designation": row.get("designation") or "",
				"gross_wages": gross,
				"employee_esi": employee_esi,
				"employer_esi": employer_esi,
				"total_esi": total_esi,
				"coverage_status": status,
				"currency": row.currency or "INR",
			}
		)

	return data


def _get_date_range(filters):
	"""
	Derive a start_date range from the report filters.

	Priority:
	  1. Contribution Period + Year  (covers a half-year)
	  2. Month + Year                (covers a single month)
	  3. No date filter              (None — all submitted slips)
	"""
	year = filters.get("year")
	month = filters.get("month")
	contribution_period = filters.get("contribution_period")

	if not year:
		return None

	yr = int(year)

	if contribution_period:
		if contribution_period == "Apr-Sep":
			return {
				"from_date": datetime.date(yr, 4, 1),
				"to_date": datetime.date(yr, 9, 30),
			}
		if contribution_period == "Oct-Mar":
			# Oct of the selected year through Mar of the next year
			return {
				"from_date": datetime.date(yr, 10, 1),
				"to_date": datetime.date(yr + 1, 3, 31),
			}

	if month:
		m = _MONTHS.get(month)
		if not m:
			return None
		first = datetime.date(yr, m, 1)
		if m == 12:
			last = datetime.date(yr + 1, 1, 1) - datetime.timedelta(days=1)
		else:
			last = datetime.date(yr, m + 1, 1) - datetime.timedelta(days=1)
		return {"from_date": first, "to_date": last}

	return None
