# Copyright (c) 2026, Frappe Technologies Pvt. Ltd. and contributors
# For license information, please see license.txt

import calendar

import frappe
from frappe import _
from frappe.query_builder import DocType
from frappe.query_builder.functions import Sum
from frappe.utils import flt, getdate

from india_payroll.india_payroll.company_settings import get_applicable_companies
from india_payroll.india_payroll.professional_tax import (
	PT_SALARY_COMPONENT,
	STATE_PT_CONFIG,
	_get_state_config,
)
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
			"label": _("Employee Name"),
			"fieldname": "employee_name",
			"fieldtype": "Data",
			"width": 160,
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
			"label": _("Employment State"),
			"fieldname": "employment_state",
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
			"width": 140,
		},
		{
			"label": _("Professional Tax (₹)"),
			"fieldname": "professional_tax",
			"fieldtype": "Currency",
			"options": "currency",
			"width": 150,
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
	state_filter = filters.get("employment_state")
	status_filter = filters.get("deduction_status")

	applicable_companies = get_applicable_companies("professional_tax")

	SS = DocType("Salary Slip")
	Emp = DocType("Employee")

	query = (
		frappe.qb.from_(SS)
		.join(Emp)
		.on(Emp.name == SS.employee)
		.select(
			SS.name.as_("slip"),
			SS.employee,
			SS.employee_name,
			SS.start_date,
			SS.end_date,
			SS.salary_structure,
			SS.company,
			SS.currency,
			SS.gross_pay,
			Emp.department,
			Emp.designation,
		)
		.where(SS.docstatus == 1)
		.orderby(SS.employee)
	)

	if filters.get("company"):
		query = query.where(SS.company == filters["company"])

	if date_range:
		query = query.where(SS.start_date >= date_range["from_date"])
		query = query.where(SS.start_date <= date_range["to_date"])

	rows = query.run(as_dict=True)
	pt_by_slip = _get_pt_by_slip([r.slip for r in rows])
	rows = _filter_in_scope(rows, applicable_companies, pt_by_slip)

	data = []
	for row in rows:
		# The employment state lives on the Salary Structure Assignment effective on
		# the slip's end date, the same one apply_professional_tax reads.
		employment_state = get_effective_ssa_values(
			row.employee,
			row.company,
			row.salary_structure,
			row.end_date,
			["employment_state"],
		).get("employment_state")

		if state_filter and employment_state != state_filter:
			continue

		# The register reports what the slip actually deducted, since that is the
		# amount payable to the state, whatever today's slabs would compute.
		professional_tax = flt(pt_by_slip.get(row.slip), 2)

		frequency = ""
		if employment_state in STATE_PT_CONFIG:
			frequency = _get_state_config(employment_state, getdate(row.start_date))["frequency"].title()

		if professional_tax > 0:
			deduction_status = "Deducted"
		elif employment_state in STATE_PT_CONFIG:
			deduction_status = "Nil / Exempt"
		else:
			deduction_status = "No PT State"

		if status_filter and deduction_status != status_filter:
			continue

		data.append(
			{
				"employee": row.employee,
				"employee_name": row.employee_name or "",
				"department": row.get("department") or "",
				"designation": row.get("designation") or "",
				"employment_state": employment_state or "",
				"frequency": frequency,
				"gross_wages": flt(row.gross_pay, 2),
				"professional_tax": professional_tax,
				"deduction_status": deduction_status,
				"currency": row.currency or "INR",
			}
		)

	return data


def _get_pt_by_slip(slip_names: list[str]) -> dict[str, float]:
	if not slip_names:
		return {}

	SD = DocType("Salary Detail")
	rows = (
		frappe.qb.from_(SD)
		.select(SD.parent, Sum(SD.amount).as_("amount"))
		.where(
			(SD.parenttype == "Salary Slip")
			& (SD.parentfield == "deductions")
			& (SD.salary_component == PT_SALARY_COMPONENT)
			& (SD.parent.isin(slip_names))
		)
		.groupby(SD.parent)
	).run(as_dict=True)

	return {r.parent: flt(r.amount) for r in rows}


def _filter_in_scope(rows, applicable_companies, pt_by_slip):
	"""Drop slips whose company is outside Professional Tax scope, keeping those
	that already recorded a Professional Tax deduction.

	A company removed from Company Payroll Settings stops accruing new PT, but
	the tax it already deducted remains payable to the state and must stay
	reportable.
	"""
	if applicable_companies is None:
		return rows

	return [r for r in rows if r.company in applicable_companies or flt(pt_by_slip.get(r.slip)) > 0]


def _get_date_range(filters):
	"""
	Derive a start_date range from the report filters.

	Priority:
	  1. Month + Year  (single month)
	  2. Year           (whole calendar year)
	  3. No date filter (all submitted slips)
	"""
	year = filters.get("year")
	month_name = filters.get("month")

	if not year:
		return None

	year_int = int(year)
	if month_name in _MONTHS:
		month_num = _MONTHS[month_name]
		last_day = calendar.monthrange(year_int, month_num)[1]
		return {
			"from_date": f"{year_int:04d}-{month_num:02d}-01",
			"to_date": f"{year_int:04d}-{month_num:02d}-{last_day:02d}",
		}

	return {"from_date": f"{year_int:04d}-01-01", "to_date": f"{year_int:04d}-12-31"}
