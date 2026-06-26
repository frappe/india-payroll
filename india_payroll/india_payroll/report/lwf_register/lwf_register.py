# Copyright (c) 2026, Frappe Technologies Pvt. Ltd. and contributors
# For license information, please see license.txt

import frappe
from frappe import _
from frappe.query_builder import DocType
from frappe.utils import flt, getdate

from india_payroll.india_payroll.lwf import LWF_STATE_CONFIG, _is_deduction_month
from india_payroll.india_payroll.utils import get_effective_ssa_values

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
			"label": _("LWF Frequency"),
			"fieldname": "frequency",
			"fieldtype": "Data",
			"width": 110,
		},
		{
			"label": _("Employee LWF (₹)"),
			"fieldname": "employee_lwf",
			"fieldtype": "Currency",
			"options": "currency",
			"width": 150,
		},
		{
			"label": _("Employer LWF (₹)"),
			"fieldname": "employer_lwf",
			"fieldtype": "Currency",
			"options": "currency",
			"width": 150,
		},
		{
			"label": _("Total LWF (₹)"),
			"fieldname": "total_lwf",
			"fieldtype": "Currency",
			"options": "currency",
			"width": 130,
		},
		{
			"label": _("Deduction Status"),
			"fieldname": "deduction_status",
			"fieldtype": "Data",
			"width": 140,
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
			SS.employee,
			SS.start_date,
			SS.salary_structure,
			SS.company,
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
		# Employment state and the LWF exemption flag both live on the Salary
		# Structure Assignment effective for the slip's period.
		ssa = get_effective_ssa_values(
			row.employee,
			row.company,
			row.salary_structure,
			row.start_date,
			["employment_state", "lwf_exempted"],
		)
		work_state = ssa.get("employment_state")

		if state_filter and work_state != state_filter:
			continue

		# Determine deduction status
		payroll_month = getdate(row.start_date).month
		lwf_exempted = bool(ssa.get("lwf_exempted"))

		if not work_state or work_state not in LWF_STATE_CONFIG:
			deduction_status = "No LWF State"
			employee_lwf = employer_lwf = total_lwf = 0.0
			frequency = ""
		elif lwf_exempted:
			state_config = LWF_STATE_CONFIG[work_state]
			frequency = state_config["frequency"].replace("-", "-").title()
			deduction_status = "Exempted"
			employee_lwf = employer_lwf = total_lwf = 0.0
		else:
			state_config = LWF_STATE_CONFIG[work_state]
			frequency = state_config["frequency"].replace("-", "-").title()
			if _is_deduction_month(state_config["frequency"], payroll_month):
				deduction_status = "Deducted"
				employee_lwf = flt(state_config["employee"], 2)
				employer_lwf = flt(state_config["employer"], 2)
				total_lwf = flt(employee_lwf + employer_lwf, 2)
			else:
				deduction_status = "Non-Deduction Month"
				employee_lwf = employer_lwf = total_lwf = 0.0

		if status_filter and deduction_status != status_filter:
			continue

		data.append(
			{
				"employee": row.employee,
				"department": row.get("department") or "",
				"designation": row.get("designation") or "",
				"work_state": work_state or "",
				"frequency": frequency,
				"employee_lwf": employee_lwf,
				"employer_lwf": employer_lwf,
				"total_lwf": total_lwf,
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
