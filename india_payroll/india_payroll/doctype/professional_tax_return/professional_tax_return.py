import frappe
from frappe.model.document import Document
from frappe.query_builder import DocType
from frappe.utils import flt, get_first_day, get_last_day

from india_payroll.india_payroll.professional_tax import STATE_PT_CONFIG

MONTH_MAP = {
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


class ProfessionalTaxReturn(Document):
	# begin: auto-generated types
	# This code is auto-generated. Do not modify anything in this block.

	from typing import TYPE_CHECKING

	if TYPE_CHECKING:
		from frappe.types import DF

		company: DF.Link
		filing_frequency: DF.Literal["", "Monthly", "Quarterly"]
		month_or_quarter: DF.Literal[None]
		professional_tax_state: DF.Autocomplete
		year: DF.Autocomplete
	# end: auto-generated types

	pass


@frappe.whitelist()
def get_report_data(
	company: str,
	year: str,
	month_or_quarter: str,
	professional_tax_state: str,
	filing_frequency: str,
) -> dict:
	"""
	Return all data required to render for the given period.
	"""
	year = int(year)
	month_no = MONTH_MAP[month_or_quarter]
	from_date = get_first_day(f"{year}-{month_no:02d}-01")
	to_date = get_last_day(f"{year}-{month_no:02d}-01")

	employer = _get_employer_details(company, month_or_quarter, year, to_date)
	part_a, totals = _get_part_a(company, professional_tax_state, from_date, to_date, month_no)

	part_c, headcount = _get_part_c(company, professional_tax_state, from_date, to_date)

	part_iv = {
		"pt_deducted": totals["total_pt"],
		"employer_pt": "",
		"arrears": "",
		"interest": "",
		"penalty": "",
		"total_payable": totals["total_pt"],
		"challan_no": "",
		"payment_date": "",
		"bank_branch": "",
		"bsr_code": "",
		"amount_paid": "",
		"balance": "",
	}

	data = {
		"employer": employer,
		"state": professional_tax_state,
		"period": f"{month_or_quarter} {year}",
		"part_a": part_a,
		"totals": totals,
		"part_b": [],
		"part_c": part_c,
		"headcount": headcount,
		"part_iv": part_iv,
		"today": frappe.utils.formatdate(frappe.utils.today()),
	}
	return data


def _get_employer_details(company, month_or_quarter, year, to_date):
	ptan = frappe.db.get_single_value("Payroll Settings", "professional_tax_enrollment_number") or "\u2014"

	# Primary address linked to the company
	Address = DocType("Address")
	DynLink = DocType("Dynamic Link")
	addr_rows = (
		frappe.qb.from_(Address)
		.join(DynLink)
		.on(DynLink.parent == Address.name)
		.select(
			Address.address_line1,
			Address.address_line2,
			Address.city,
			Address.state,
			Address.pincode,
		)
		.where(DynLink.link_doctype == "Company")
		.where(DynLink.link_name == company)
		.where(Address.is_primary_address == 1)
		.limit(1)
		.run(as_dict=True)
	)

	address_str = "\u2014"
	if addr_rows:
		addr = addr_rows[0]
		address_str = (
			", ".join(
				filter(
					None,
					[addr.address_line1, addr.address_line2, addr.city, addr.state, addr.pincode],
				)
			)
			or "\u2014"
		)

	# Due date: 15th of the month following the return month
	if to_date.month == 12:
		due_date_raw = frappe.utils.getdate(f"{to_date.year + 1}-01-15")
	else:
		due_date_raw = frappe.utils.getdate(f"{to_date.year}-{to_date.month + 1:02d}-15")

	if frappe.db.has_column("Company", "pan"):
		pan = frappe.db.get_value("Company", company, "pan")
	else:
		pan = frappe.db.get_value("Company", company, "tax_id") or "\u2014"

	return {
		"company": company,
		"ptan": ptan,
		"pan": pan,
		"address": address_str,
		"period": f"{month_or_quarter} {year}",
		"due_date": frappe.utils.formatdate(due_date_raw),
	}


def _get_part_a(company, state, from_date, to_date, month_no):
	if state not in STATE_PT_CONFIG:
		return [], _zero_totals()

	state_config = STATE_PT_CONFIG[state]
	slabs = state_config["slabs"]
	special_rules = state_config.get("special_rules", {})

	# 1. Employees assigned to this state (via Salary Structure Assignment)
	SSA = DocType("Salary Structure Assignment")
	employee_rows = (
		frappe.qb.from_(SSA)
		.select(SSA.employee)
		.where(SSA.employment_state == state)
		.distinct()
		.run(as_list=True)
	)
	employee_list = [r[0] for r in employee_rows]

	empty_part_a = _build_empty_part_a(slabs, month_no, special_rules)

	if not employee_list:
		return empty_part_a, _zero_totals()

	# 2. Submitted salary slips for the period
	SS = DocType("Salary Slip")
	Emp = DocType("Employee")
	slips = (
		frappe.qb.from_(SS)
		.join(Emp)
		.on(Emp.name == SS.employee)
		.select(SS.name, SS.employee, SS.gross_pay, Emp.gender)
		.where(SS.company == company)
		.where(SS.docstatus == 1)
		.where(SS.start_date >= from_date)
		.where(SS.end_date <= to_date)
		.where(SS.employee.isin(employee_list))
		.run(as_dict=True)
	)

	if not slips:
		return empty_part_a, _zero_totals()

	# 3. Professional Tax deducted per slip (single query)
	slip_names = [s.name for s in slips]
	SalDetail = DocType("Salary Detail")
	pt_rows = (
		frappe.qb.from_(SalDetail)
		.select(SalDetail.parent, SalDetail.amount)
		.where(SalDetail.parent.isin(slip_names))
		.where(SalDetail.salary_component == "Professional Tax")
		.where(SalDetail.parentfield == "deductions")
		.run(as_dict=True)
	)
	pt_by_slip = {r.parent: flt(r.amount) for r in pt_rows}

	# 4. Accumulate into slab buckets
	slab_data = [
		{
			"slab_label": _slab_label(slabs, i),
			"male_count": 0,
			"female_count": 0,
			"total_count": 0,
			"pt_rate": _slab_rate(slabs[i], month_no, special_rules),
			"male_pt": 0.0,
			"female_pt": 0.0,
			"total_pt": 0.0,
		}
		for i in range(len(slabs))
	]

	for slip in slips:
		idx = _classify_gross(flt(slip.gross_pay), slabs)
		gender = (slip.gender or "").strip().lower()
		pt_amount = pt_by_slip.get(slip.name, 0.0)
		slab_data[idx]["total_count"] += 1
		slab_data[idx]["total_pt"] += pt_amount
		if gender == "female":
			slab_data[idx]["female_count"] += 1
			slab_data[idx]["female_pt"] += pt_amount
		else:
			slab_data[idx]["male_count"] += 1
			slab_data[idx]["male_pt"] += pt_amount

	# Round totals for clean display
	for row in slab_data:
		row["male_pt"] = _fmt(row["male_pt"])
		row["female_pt"] = _fmt(row["female_pt"])
		row["total_pt"] = _fmt(row["total_pt"])

	totals = {
		"male_count": sum(r["male_count"] for r in slab_data),
		"female_count": sum(r["female_count"] for r in slab_data),
		"total_count": sum(r["total_count"] for r in slab_data),
		"male_pt": _fmt(sum(flt(r["male_pt"]) for r in slab_data)),
		"female_pt": _fmt(sum(flt(r["female_pt"]) for r in slab_data)),
		"total_pt": _fmt(sum(flt(r["total_pt"]) for r in slab_data)),
	}

	return slab_data, totals


def _build_empty_part_a(slabs, month_no, special_rules):
	return [
		{
			"slab_label": _slab_label(slabs, i),
			"male_count": 0,
			"female_count": 0,
			"total_count": 0,
			"pt_rate": _slab_rate(slabs[i], month_no, special_rules),
			"male_pt": 0,
			"female_pt": 0,
			"total_pt": 0,
		}
		for i in range(len(slabs))
	]


def _zero_totals():
	return {"male_count": 0, "female_count": 0, "total_count": 0, "male_pt": 0, "female_pt": 0, "total_pt": 0}


def _slab_label(slabs, idx):
	"""Human-readable label for a slab, e.g. 'Upto \u20b97,500' or 'Above \u20b910,000'."""
	slab = slabs[idx]
	prev_upto = slabs[idx - 1]["upto"] if idx > 0 else 0
	if slab["upto"] is None:
		return f"Above \u20b9{prev_upto:,}"
	if idx == 0:
		return f"Upto \u20b9{slab['upto']:,}"
	return f"\u20b9{prev_upto + 1:,} to \u20b9{slab['upto']:,}"


def _slab_rate(slab, month_no, special_rules):
	"""Statutory PT rate for this slab, accounting for the February top-slab rule."""
	if slab["upto"] is None and month_no == 2:
		feb_amount = special_rules.get("february_amount")
		if feb_amount:
			return feb_amount
	return slab["amount"]


def _classify_gross(gross_pay, slabs):
	"""Return the zero-based index of the slab that applies to gross_pay."""
	for i, slab in enumerate(slabs):
		if slab["upto"] is None or gross_pay <= slab["upto"]:
			return i
	return len(slabs) - 1


def _fmt(amount):
	"""Format a monetary amount: integer if whole, else 2 decimal places."""
	amount = flt(amount, 2)
	return int(amount) if amount == int(amount) else amount


def _get_part_c(company, state, from_date, to_date):
	"""Return new joiners and separations during the month, plus headcount summary."""
	SSA = DocType("Salary Structure Assignment")
	Emp = DocType("Employee")

	emp_rows = (
		frappe.qb.from_(Emp)
		.join(SSA)
		.on(SSA.employee == Emp.name)
		.select(
			Emp.name,
			Emp.employee_name,
			Emp.designation,
			Emp.date_of_joining,
			Emp.relieving_date,
		)
		.where(SSA.employment_state == state)
		.where(SSA.company == company)
		.distinct()
		.run(as_dict=True)
	)

	# Gross pay from submitted salary slips in this period
	slip_gross = {}
	if emp_rows:
		emp_names = list({e.name for e in emp_rows})
		SS = DocType("Salary Slip")
		slips = (
			frappe.qb.from_(SS)
			.select(SS.employee, SS.gross_pay)
			.where(SS.company == company)
			.where(SS.docstatus == 1)
			.where(SS.start_date >= from_date)
			.where(SS.end_date <= to_date)
			.where(SS.employee.isin(emp_names))
			.run(as_dict=True)
		)
		for s in slips:
			slip_gross[s.employee] = flt(s.gross_pay)

	part_c = []
	joined_count = 0
	left_count = 0

	for emp in emp_rows:
		doj = frappe.utils.getdate(emp.date_of_joining) if emp.date_of_joining else None
		rd = frappe.utils.getdate(emp.relieving_date) if emp.relieving_date else None

		if doj and from_date <= doj <= to_date:
			joined_count += 1
			gross = slip_gross.get(emp.name, "")
			part_c.append(
				{
					"_index": len(part_c),
					"employee": emp.name,
					"employee_name": emp.employee_name or "",
					"designation": emp.designation or "",
					"event_date": frappe.utils.formatdate(doj),
					"salary": _fmt(gross) if gross else "",
					"slab_label": "",
					"event_type": "Joined",
					"badge_color": "#228b22",
				}
			)

		if rd and from_date <= rd <= to_date:
			left_count += 1
			gross = slip_gross.get(emp.name, "")
			part_c.append(
				{
					"_index": len(part_c),
					"employee": emp.name,
					"employee_name": emp.employee_name or "",
					"designation": emp.designation or "",
					"event_date": frappe.utils.formatdate(rd),
					"salary": _fmt(gross) if gross else "",
					"slab_label": "",
					"event_type": "Left",
					"badge_color": "#cc4400",
				}
			)

	# Re-index after sorting by event date
	part_c.sort(key=lambda x: x["event_date"])
	for i, row in enumerate(part_c):
		row["_index"] = i

	opening_count = sum(
		1
		for emp in emp_rows
		if emp.date_of_joining
		and frappe.utils.getdate(emp.date_of_joining) < from_date
		and (not emp.relieving_date or frappe.utils.getdate(emp.relieving_date) >= from_date)
	)
	closing_count = opening_count + joined_count - left_count

	headcount = {
		"opening": opening_count,
		"joined": joined_count,
		"left": left_count,
		"closing": closing_count,
		"opening_date": frappe.utils.formatdate(from_date),
		"closing_date": frappe.utils.formatdate(to_date),
	}

	return part_c, headcount
