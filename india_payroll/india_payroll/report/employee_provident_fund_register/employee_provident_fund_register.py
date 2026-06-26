# Copyright (c) 2026, Frappe Technologies Pvt. Ltd. and contributors
# For license information, please see license.txt

import datetime

import frappe
from frappe import _
from frappe.query_builder import DocType
from frappe.utils import flt

from india_payroll.india_payroll.epf import (
	EPF_EMPLOYEE_COMPONENT,
	EPF_EMPLOYER_RATE,
	EPF_WAGE_CEILING,
	EPS_RATE,
	VPF_COMPONENT,
	_epfo_round,
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
			"label": _("Universal Account Number"),
			"fieldname": "uan_number",
			"fieldtype": "Data",
			"width": 170,
		},
		{
			"label": _("Name as per Universal Account Number"),
			"fieldname": "pf_name",
			"fieldtype": "Data",
			"width": 230,
		},
		{
			"label": _("Gross Wages"),
			"fieldname": "gross_wages",
			"fieldtype": "Currency",
			"options": "currency",
			"width": 130,
		},
		{
			"label": _("Provident Fund Wages"),
			"fieldname": "epf_wages",
			"fieldtype": "Currency",
			"options": "currency",
			"width": 160,
		},
		{
			"label": _("Pension Scheme Wages"),
			"fieldname": "eps_wages",
			"fieldtype": "Currency",
			"options": "currency",
			"width": 170,
		},
		{
			"label": _("Deposit Linked Insurance Wages"),
			"fieldname": "edli_wages",
			"fieldtype": "Currency",
			"options": "currency",
			"width": 200,
		},
		{
			"label": _("Employee Provident Fund Contribution"),
			"fieldname": "employee_epf",
			"fieldtype": "Currency",
			"options": "currency",
			"width": 230,
		},
		{
			"label": _("Voluntary Provident Fund Contribution"),
			"fieldname": "vpf",
			"fieldtype": "Currency",
			"options": "currency",
			"width": 230,
		},
		{
			"label": _("Employer Pension Scheme Contribution"),
			"fieldname": "eps_contribution",
			"fieldtype": "Currency",
			"options": "currency",
			"width": 240,
		},
		{
			"label": _("Employer Provident Fund Contribution"),
			"fieldname": "employer_epf_diff",
			"fieldtype": "Currency",
			"options": "currency",
			"width": 240,
		},
		{
			"label": _("Non-Contributing Period Days"),
			"fieldname": "ncp_days",
			"fieldtype": "Float",
			"width": 180,
		},
		{
			"label": _("Refund of Advances"),
			"fieldname": "refund_of_advances",
			"fieldtype": "Currency",
			"options": "currency",
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

	SS = DocType("Salary Slip")
	Emp = DocType("Employee")

	query = (
		frappe.qb.from_(SS)
		.join(Emp)
		.on(Emp.name == SS.employee)
		.select(
			SS.name.as_("slip"),
			SS.employee,
			SS.company,
			SS.salary_structure,
			SS.start_date,
			SS.gross_pay,
			SS.currency,
			SS.leave_without_pay,
			SS.absent_days,
			SS.payment_days,
			SS.total_working_days,
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

	for f in ("uan_number", "pf_name"):
		if frappe.db.has_column("Employee", f):
			query = query.select(getattr(Emp, f))

	slips = query.run(as_dict=True)
	if not slips:
		return []

	# EPF opt-in and the actual-wage rule live on the Salary Structure Assignment.
	# Resolve the assignment effective for each slip's period.
	for s in slips:
		ssa = get_effective_ssa_values(
			s.employee,
			s.company,
			s.salary_structure,
			s.start_date,
			["epf_applicable", "contribute_on_actual_pf_wage"],
		)
		s["epf_applicable"] = ssa.get("epf_applicable")
		s["contribute_on_actual_pf_wage"] = ssa.get("contribute_on_actual_pf_wage")

	# Only employees opted into EPF on their assignment appear in the register.
	slips = [s for s in slips if s.get("epf_applicable")]
	if not slips:
		return []

	totals_by_slip = _aggregate_salary_detail([s.slip for s in slips])

	data = []
	for s in slips:
		totals = totals_by_slip.get(s.slip, {})
		pf_wage = flt(totals.get("pf_wage"))
		employee_epf = flt(totals.get(EPF_EMPLOYEE_COMPONENT))
		vpf = flt(totals.get(VPF_COMPONENT))

		contribute_on_actual = bool(s.get("contribute_on_actual_pf_wage"))
		pf_wage_capped = min(pf_wage, EPF_WAGE_CEILING)
		epf_base = pf_wage if contribute_on_actual else pf_wage_capped

		# Post-1 Sept 2014 EPS rule: members whose PF wage > ceiling get no EPS.
		if pf_wage > EPF_WAGE_CEILING:
			eps_wages = 0.0
			eps_contribution = 0
		else:
			eps_wages = pf_wage_capped
			eps_contribution = _epfo_round(pf_wage_capped * EPS_RATE)

		employer_total = _epfo_round(epf_base * EPF_EMPLOYER_RATE)
		employer_epf_diff = max(0, employer_total - eps_contribution)

		ncp_days = flt(s.get("leave_without_pay")) + flt(s.get("absent_days"))
		# Fallback when LOP isn't recorded directly: derive from payment vs working days.
		if ncp_days == 0:
			ncp_days = max(0.0, flt(s.get("total_working_days")) - flt(s.get("payment_days")))

		data.append(
			{
				"employee": s.employee,
				"uan_number": s.get("uan_number") or "",
				"pf_name": s.get("pf_name") or "",
				"gross_wages": flt(s.gross_pay),
				"epf_wages": epf_base,
				"eps_wages": eps_wages,
				"edli_wages": pf_wage_capped,
				"employee_epf": employee_epf,
				"vpf": vpf,
				"eps_contribution": eps_contribution,
				"employer_epf_diff": employer_epf_diff,
				"ncp_days": ncp_days,
				"refund_of_advances": 0,
				"currency": s.currency or "INR",
			}
		)

	return data


def _aggregate_salary_detail(slip_names: list) -> dict:
	"""One Salary Detail sweep across all slips in the report.

	PF wage is the sum of every earning row — all earning components count.

	Returns: { slip_name: { "pf_wage": ..., "Provident Fund": ..., "Voluntary Provident Fund": ... } }
	"""
	if not slip_names:
		return {}

	rows = frappe.get_all(
		"Salary Detail",
		filters={
			"parenttype": "Salary Slip",
			"parent": ("in", slip_names),
		},
		fields=["parent", "parentfield", "salary_component", "amount"],
	)

	totals: dict[str, dict] = {}
	for r in rows:
		bucket = totals.setdefault(r.parent, {})
		if r.parentfield == "earnings":
			bucket["pf_wage"] = flt(bucket.get("pf_wage")) + flt(r.amount)
		elif r.parentfield == "deductions" and r.salary_component in (
			EPF_EMPLOYEE_COMPONENT,
			VPF_COMPONENT,
		):
			bucket[r.salary_component] = flt(bucket.get(r.salary_component)) + flt(r.amount)
	return totals


def _get_date_range(filters):
	"""
	Derive a start_date range from the report filters.

	Priority:
	  1. Contribution Period + Year  (half-year)
	  2. Month + Year                (single month)
	  3. No date filter              (None — all submitted slips for the company)
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


@frappe.whitelist()
def get_ecr_file(filters: dict | str) -> dict:
	"""Generate the EPFO Electronic Challan-cum-Return (ECR) text payload.

	Plain text, one line per member, no header, fields separated by ``#~#``.
	Eleven fields per the EPFO spec, in this order:

	    UAN, Member Name, Gross Wages, EPF Wages, EPS Wages, EDLI Wages,
	    EE EPF, EPS Contribution, EPF-EPS Diff (ER), NCP Days, Refund of Advances

	Hard-fails if any in-scope employee is missing a UAN: an incomplete ECR
	file would be rejected by the portal anyway, and silently dropping rows
	risks under-reporting remittances.  The error lists the affected
	employees so the user can fix the UAN mapping and retry.
	"""
	if isinstance(filters, str):
		filters = frappe.parse_json(filters)

	rows = get_data(filters or {})
	if not rows:
		return {"filename": _ecr_filename(filters or {}), "content": "", "row_count": 0}

	missing = [r["employee"] for r in rows if not r.get("uan_number")]
	if missing:
		_throw_missing_uan(missing)

	lines = []
	for r in rows:
		lines.append(
			"#~#".join(
				[
					str(r["uan_number"]),
					(r.get("pf_name") or "").upper(),
					_fmt_int(r["gross_wages"]),
					_fmt_int(r["epf_wages"]),
					_fmt_int(r["eps_wages"]),
					_fmt_int(r["edli_wages"]),
					_fmt_int(flt(r["employee_epf"]) + flt(r["vpf"])),
					_fmt_int(r["eps_contribution"]),
					_fmt_int(r["employer_epf_diff"]),
					_fmt_int(r["ncp_days"]),
					_fmt_int(r["refund_of_advances"]),
				]
			)
		)

	return {
		"filename": _ecr_filename(filters or {}),
		"content": "\n".join(lines),
		"row_count": len(lines),
	}


def _throw_missing_uan(employee_ids: list[str]) -> None:
	"""Surface UAN gaps as a clear, actionable error linking each affected
	employee so the user can update the master and rerun the export."""
	rows = frappe.get_all(
		"Employee",
		filters={"name": ("in", employee_ids)},
		fields=["name", "employee_name"],
		order_by="employee_name",
	)
	if not rows:  # Defensive — get_data already resolved them
		rows = [{"name": e, "employee_name": ""} for e in sorted(set(employee_ids))]

	items = "".join(
		"<li><a href='/app/employee/{name}'>{label}</a></li>".format(
			name=frappe.utils.escape_html(r["name"]),
			label=frappe.utils.escape_html(
				f"{r['employee_name']} ({r['name']})" if r.get("employee_name") else r["name"]
			),
		)
		for r in rows
	)
	frappe.throw(
		_(
			"Universal Account Number is missing for {count} employee(s). "
			"Update the UAN on each Employee record before generating the ECR file:"
			"<br><ul>{items}</ul>"
		).format(count=len(rows), items=items),
		title=_("Missing Universal Account Number"),
	)


def _fmt_int(value) -> str:
	"""ECR amounts are reported as whole rupees (EPFO rounds to nearest)."""
	return str(round(flt(value)))


def _ecr_filename(filters: dict) -> str:
	parts = ["ECR"]
	if filters.get("company"):
		parts.append(str(filters["company"]).replace(" ", "_"))
	if filters.get("contribution_period"):
		parts.append(str(filters["contribution_period"]).replace(" ", "_"))
	elif filters.get("month"):
		parts.append(str(filters["month"]))
	if filters.get("year"):
		parts.append(str(filters["year"]))
	return "_".join(parts) + ".txt"
