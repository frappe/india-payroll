# Copyright (c) 2026, Frappe Technologies Pvt. Ltd. and contributors
# For license information, please see license.txt

import frappe
from frappe import _
from frappe.query_builder import DocType
from frappe.utils import flt

from india_payroll.india_payroll.professional_tax import PT_SALARY_COMPONENT, STATE_PT_CONFIG

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
			"label": _("Work State"),
			"fieldname": "work_state",
			"fieldtype": "Data",
			"width": 140,
		},
		{
			"label": _("PT Frequency"),
			"fieldname": "frequency",
			"fieldtype": "Data",
			"width": 110,
		},
		{
			"label": _("Gross Wages"),
			"fieldname": "gross_wages",
			"fieldtype": "Currency",
			"options": "currency",
			"width": 130,
		},
		{
			"label": _("Professional Tax (₹)"),
			"fieldname": "professional_tax",
			"fieldtype": "Currency",
			"options": "currency",
			"width": 160,
		},
		{
			"label": _("Deduction Status"),
			"fieldname": "deduction_status",
			"fieldtype": "Data",
			"width": 150,
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
	state_filter = filters.get("work_state")
	status_filter = filters.get("deduction_status")

	SS = DocType("Salary Slip")
	Emp = DocType("Employee")

	query = (
		frappe.qb.from_(SS)
		.join(Emp)
		.on(Emp.name == SS.employee)
		.select(
			SS.name,
			SS.employee,
			SS.start_date,
			SS.salary_structure,
			SS.company,
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

	rows = query.run(as_dict=True)

	data = []
	for row in rows:
		# Resolve employment_state from the effective Salary Structure Assignment
		work_state = frappe.db.get_value(
			"Salary Structure Assignment",
			filters={
				"employee": row.employee,
				"company": row.company,
				"salary_structure": row.salary_structure,
				"from_date": ("<=", row.start_date),
			},
			fieldname="employment_state",
			order_by="from_date desc",
		)

		if state_filter and work_state != state_filter:
			continue

		# Actual Professional Tax deducted on this slip — the challan basis is what
		# was really withheld (handles half-yearly months, Feb rule, exemptions, etc.)
		pt_amount = flt(
			sum(
				frappe.get_all(
					"Salary Detail",
					filters={
						"parent": row.name,
						"parentfield": "deductions",
						"salary_component": PT_SALARY_COMPONENT,
					},
					pluck="amount",
				)
			),
			2,
		)

		if not work_state or work_state not in STATE_PT_CONFIG:
			deduction_status = "No PT State"
			frequency = ""
		else:
			frequency = STATE_PT_CONFIG[work_state]["frequency"].title()
			deduction_status = "Deducted" if pt_amount else "Nil"

		if status_filter and deduction_status != status_filter:
			continue

		data.append(
			{
				"employee": row.employee,
				"department": row.get("department") or "",
				"designation": row.get("designation") or "",
				"work_state": work_state or "",
				"frequency": frequency,
				"gross_wages": flt(row.gross_pay),
				"professional_tax": pt_amount,
				"deduction_status": deduction_status,
				"currency": row.currency or "INR",
			}
		)

	return data


def _get_date_range(filters):
	"""
	Derive a start_date range from the report filters.

	Priority:
	  1. Month + Year  (single month)
	  2. No date filter (all submitted slips)
	"""
	year = filters.get("year")
	month_name = filters.get("month")

	if year and month_name and month_name in _MONTHS:
		import calendar

		month_num = _MONTHS[month_name]
		year_int = int(year)
		last_day = calendar.monthrange(year_int, month_num)[1]
		return {
			"from_date": f"{year_int:04d}-{month_num:02d}-01",
			"to_date": f"{year_int:04d}-{month_num:02d}-{last_day:02d}",
		}

	return None
