# Copyright (c) 2026, Frappe Technologies Pvt. Ltd. and contributors
# For license information, please see license.txt

import re
from datetime import date

import frappe
from frappe.utils import flt, getdate
from hrms.payroll.doctype.salary_slip.salary_slip import calculate_tax_by_tax_slab

from india_payroll.india_payroll.tax_exemption_setup import (
	EXEMPTION_CATEGORIES,
	setup_tax_exemption_categories,
)

# Payroll periods per year by Salary Structure payroll frequency. Kept local so the
# page does not depend on an HRMS internal constant (which has moved/been removed).
PERIODS_PER_YEAR = {
	"Monthly": 12,
	"Fortnightly": 26,
	"Bimonthly": 24,
	"Weekly": 52,
	"Daily": 365,
}

OLD_REGIME_SLAB = "Old Tax Regime: 2019"
NEW_REGIME_SLAB = "New Tax Regime: 2025-2026"

SECTION_CAPS = {
	"80CCD(1B)": 50000,
	"80D": 75000,
	"80DD": 125000,
	"80DDB": 100000,
	"80EE": 50000,
	"80EEA": 150000,
	"24": 200000,
	"80GG": 60000,
	"80TTA": 10000,
	"80TTB": 50000,
	"80U": 125000,
}


@frappe.whitelist()
def setup_if_missing() -> dict:
	"""Idempotently create income tax slabs and exemption categories."""
	from india_payroll.install import create_income_tax_slabs

	create_income_tax_slabs()

	setup_tax_exemption_categories()

	return {"ok": True}


@frappe.whitelist()
def get_employee_details(employee: str) -> dict:
	data = get_employee_salary_data(employee)

	categories = frappe.get_all(
		"Employee Tax Exemption Category",
		fields=["name", "max_amount", "description"],
	)
	if not categories:
		setup_tax_exemption_categories()
		categories = frappe.get_all(
			"Employee Tax Exemption Category",
			fields=["name", "max_amount", "description"],
		)

	categories_by_name = {c.name: c for c in categories}
	categories = [categories_by_name[name] for name in EXEMPTION_CATEGORIES if name in categories_by_name] + [
		c for c in categories if c.name not in EXEMPTION_CATEGORIES
	]

	category_list = []
	for category in categories:
		subs = frappe.get_all(
			"Employee Tax Exemption Sub Category",
			filters={"exemption_category": category.name},
			fields=["name", "max_amount"],
			order_by="name",
		)
		category_list.append(
			{
				"name": category.name,
				"max_amount": category.max_amount,
				"description": category.description,
				"sub_categories": [{"name": s.name, "max_amount": s.max_amount} for s in subs],
			}
		)

	data["exemption_categories"] = category_list
	data["prefill_declarations"] = build_prefill_declarations(data.pop("deductions", []), category_list)

	company = frappe.db.get_value("Employee", employee, "company")
	today = frappe.utils.today()
	payroll_period = frappe.db.get_value(
		"Payroll Period",
		{"company": company, "start_date": ("<=", today), "end_date": (">=", today)},
		"name",
	)
	data["payroll_period"] = payroll_period or None
	return data


def _normalize(name: str) -> str:
	return re.sub(r"[^a-z0-9]", "", (name or "").lower())


def build_prefill_declarations(deductions: list, category_list: list) -> dict:
	"""Fuzzy-match each payable salary deduction to an exemption sub-category by
	normalized name and return {category: {sub_category: annual_amount}}.

	A deduction matches a sub-category when one normalized name contains the other
	(e.g. 'Employee Provident Fund' -> 'Employee Provident Fund (EPF)'). Deductions
	with no matching sub-category (e.g. Professional Tax) are skipped.
	"""
	subs = [
		(category["name"], sub["name"], _normalize(sub["name"]))
		for category in category_list
		for sub in category.get("sub_categories", [])
	]

	prefill = {}
	for deduction in deductions:
		comp_norm = _normalize(deduction.get("component"))
		if len(comp_norm) < 4:
			continue
		for cat_name, sub_name, sub_norm in subs:
			if comp_norm in sub_norm or sub_norm in comp_norm:
				prefill.setdefault(cat_name, {})[sub_name] = flt(deduction.get("annual_amount"))
				break

	return prefill


@frappe.whitelist()
def get_current_payroll_period() -> dict:
	company = frappe.defaults.get_global_default("company")
	today = frappe.utils.today()
	period = frappe.db.get_value(
		"Payroll Period",
		{"company": company, "start_date": ("<=", today), "end_date": (">=", today)},
		"name",
	)
	return {"payroll_period": period or None}


@frappe.whitelist()
def compute_tax_comparison(
	employee: str,
	declarations: dict | str,
	rent_monthly: float = 0,
	city_type: str = "non-metro",
	annual_gross: float = 0,
) -> dict:
	if isinstance(declarations, str):
		declarations = frappe.parse_json(declarations)

	data = get_employee_salary_data(employee)
	if flt(annual_gross):
		data["annual_gross"] = flt(annual_gross)

	old_taxable, old_breakdown = compute_old_regime(data, declarations, flt(rent_monthly), city_type)
	new_taxable, new_breakdown = compute_new_regime(data)

	old_slab = frappe.get_doc("Income Tax Slab", OLD_REGIME_SLAB)
	new_slab = frappe.get_doc("Income Tax Slab", NEW_REGIME_SLAB)

	# Pass empty eval dicts: the standard slabs carry no row conditions, but the HRMS
	# helper does eval_locals.update(...) for incomes above tax_relief_limit and would
	# fail on the default None.
	old_tax, _ = calculate_tax_by_tax_slab(old_taxable, old_slab, {}, {})
	new_tax, _ = calculate_tax_by_tax_slab(new_taxable, new_slab, {}, {})

	return {
		"old_regime": {
			"slab": OLD_REGIME_SLAB,
			"taxable_income": old_taxable,
			"tax": old_tax,
			"breakdown": old_breakdown,
		},
		"new_regime": {
			"slab": NEW_REGIME_SLAB,
			"taxable_income": new_taxable,
			"tax": new_tax,
			"breakdown": new_breakdown,
		},
		"recommended": "old" if old_tax < new_tax else "new",
		"savings": abs(old_tax - new_tax),
	}


def get_latest_assignment(employee):
	"""Latest Salary Structure Assignment for the employee, regardless of docstatus
	(draft or submitted). Returns the {name, docstatus} dict or None."""
	return frappe.db.get_value(
		"Salary Structure Assignment",
		{"employee": employee},
		["name", "docstatus"],
		order_by="from_date desc, creation desc",
		as_dict=True,
	)


@frappe.whitelist()
def set_tax_regime(employee: str, income_tax_slab: str) -> dict:
	assignment = get_latest_assignment(employee)
	if not assignment:
		frappe.throw(frappe._("No Salary Structure Assignment found for {0}").format(employee))
	if assignment.docstatus == 1:
		frappe.throw(frappe._("Salary Structure Assignment {0} is already submitted").format(assignment.name))
	frappe.db.set_value("Salary Structure Assignment", assignment.name, "income_tax_slab", income_tax_slab)
	return {"assignment": assignment.name}


@frappe.whitelist()
def notify_employee_to_select_tax_regime(assignment: str) -> dict:
	"""Email the employee on this Salary Structure Assignment a prompt to pick
	their tax regime in the Tax Regime Selector page."""
	ssa = frappe.db.get_value(
		"Salary Structure Assignment",
		assignment,
		["employee", "employee_name"],
		as_dict=True,
	)
	if not ssa:
		frappe.throw(frappe._("Salary Structure Assignment {0} not found").format(assignment))

	emails = frappe.db.get_value(
		"Employee",
		ssa.employee,
		["prefered_email", "company_email", "personal_email"],
		as_dict=True,
	)
	recipient = emails and (emails.prefered_email or emails.company_email or emails.personal_email)
	if not recipient:
		frappe.throw(frappe._("No email address found for {0}").format(ssa.employee_name or ssa.employee))

	link = frappe.utils.get_url("/app/tax-regime-selector")
	frappe.sendmail(
		recipients=[recipient],
		subject=frappe._("Action required: Select your tax regime"),
		message=frappe._(
			"Dear {0},<br><br>"
			"Please select your preferred income tax regime for the current payroll period "
			"using the Tax Regime Selector:"
			'<p style="margin: 15px 0px;">'
			'<a href="{1}" class="btn btn-primary" target="_blank">Open Tax Regime Selector</a>'
			"</p>"
			"Regards,<br>HR Team"
		).format(ssa.employee_name or "", link),
		reference_doctype="Salary Structure Assignment",
		reference_name=assignment,
	)
	return {"sent": True, "email": recipient}


@frappe.whitelist()
def notify_employees_to_select_tax_regime(names: str | list[str]) -> dict:
	"""Bulk version of notify_employee_to_select_tax_regime for the SSA list view.
	Notifies only draft assignments; skips submitted ones and those without an email."""
	if isinstance(names, str):
		names = frappe.parse_json(names)

	sent = 0
	skipped = 0
	for name in names:
		if frappe.db.get_value("Salary Structure Assignment", name, "docstatus") != 0:
			skipped += 1
			continue
		try:
			notify_employee_to_select_tax_regime(name)
			sent += 1
		except Exception:
			skipped += 1

	frappe.msgprint(
		frappe._("Notified {0} employee(s). Skipped {1} (submitted or no email).").format(sent, skipped)
	)
	return {"sent": sent, "skipped": skipped}


def get_employee_salary_data(employee):
	assignment = get_latest_assignment(employee)
	if not assignment:
		frappe.throw(frappe._("No Salary Structure Assignment found for {0}").format(employee))

	ssa = frappe.get_doc("Salary Structure Assignment", assignment.name)
	base = flt(ssa.base)
	periods = PERIODS_PER_YEAR.get(
		frappe.get_cached_value("Salary Structure", ssa.salary_structure, "payroll_frequency"), 12
	)

	# Component amounts come from the SSA's own evaluation (single shared pass).
	# Each row carries a full-cycle (per-period) default_amount.
	components = ssa.get_evaluated_components()

	def find_amount(rows, *, abbr=None, component_contains=None):
		for row in rows:
			if abbr and (row.abbr or "").upper() == abbr:
				return flt(row.default_amount)
			if component_contains and component_contains in (row.salary_component or "").upper():
				return flt(row.default_amount)
		return 0

	monthly_hra = find_amount(components.earnings, abbr="HRA")
	monthly_lta = find_amount(components.earnings, abbr="LTA")
	monthly_employer_nps = find_amount(components.employer_contributions, component_contains="NPS")

	# Payable deduction components (e.g. EPF, employee NPS) so the UI can fuzzy-match
	# them to exemption sub-categories and prefill the declaration table.
	deductions = [
		{"component": row.salary_component, "annual_amount": flt(row.default_amount) * periods}
		for row in components.deductions
		if not row.statistical_component and flt(row.default_amount) > 0
	]

	# Prefer the SSA's stored annual_gross_earning (computed on validate); fall back to
	# the latest salary slip only for legacy assignments saved before the field existed.
	annual_gross = flt(ssa.annual_gross_earning)
	if not annual_gross:
		slip_gross = frappe.db.get_value(
			"Salary Slip",
			{"employee": employee, "docstatus": 1},
			"gross_pay",
			order_by="posting_date desc",
		)
		annual_gross = flt(slip_gross) * periods

	emp_dob = frappe.db.get_value("Employee", employee, "date_of_birth")

	return {
		"annual_gross": annual_gross,
		"annual_basic": base * periods,
		"annual_hra": monthly_hra * periods,
		"annual_lta": monthly_lta * periods,
		"annual_employer_nps": monthly_employer_nps * periods,
		"has_hra": monthly_hra > 0,
		"is_senior_citizen": check_senior_citizen(emp_dob),
		"current_income_tax_slab": ssa.income_tax_slab,
		"ssa_docstatus": assignment.docstatus,
		"deductions": deductions,
	}


def check_senior_citizen(date_of_birth):
	if not date_of_birth:
		return False
	today = date.today()
	fy_start = date(today.year if today.month >= 4 else today.year - 1, 4, 1)
	dob = getdate(date_of_birth)
	age = fy_start.year - dob.year - ((dob.month, dob.day) > (fy_start.month, fy_start.day))
	return age >= 60


def compute_old_regime(data, declarations, rent_monthly, city_type):
	gross = data["annual_gross"]
	basic = data["annual_basic"]
	standard_deduction = 50000

	hra_exemption = 0
	if data["has_hra"] and rent_monthly > 0:
		hra_exemption = compute_hra_exemption(data["annual_hra"], basic, rent_monthly, city_type)

	lta_exemption = data["annual_lta"]
	employer_nps = min(data["annual_employer_nps"], basic * 0.10)
	via = compute_via_deductions(declarations, data)
	via_total = sum(via.values())

	taxable = max(0, gross - standard_deduction - hra_exemption - lta_exemption - employer_nps - via_total)

	return taxable, {
		"gross": gross,
		"standard_deduction": standard_deduction,
		"hra_exemption": hra_exemption,
		"lta_exemption": lta_exemption,
		"employer_nps": employer_nps,
		"via_deductions": via,
		"taxable_income": taxable,
	}


def compute_new_regime(data):
	gross = data["annual_gross"]
	basic = data["annual_basic"]
	standard_deduction = 75000
	employer_nps = min(data["annual_employer_nps"], basic * 0.10)

	taxable = max(0, gross - standard_deduction - employer_nps)

	return taxable, {
		"gross": gross,
		"standard_deduction": standard_deduction,
		"employer_nps": employer_nps,
		"taxable_income": taxable,
	}


def compute_hra_exemption(annual_hra, annual_basic, rent_monthly, city_type):
	annual_rent = rent_monthly * 12
	metro_percent = 0.50 if city_type == "metro" else 0.40
	return max(
		0,
		min(
			annual_hra,
			metro_percent * annual_basic,
			annual_rent - 0.10 * annual_basic,
		),
	)


def compute_via_deductions(declarations, data):
	deductions = {}

	# 80CCE combined cap: 80C + 80CCC + 80CCD(1)
	cce = min(
		flt(declarations.get("80C", 0))
		+ flt(declarations.get("80CCC", 0))
		+ flt(declarations.get("80CCD(1)", 0)),
		150000,
	)
	if cce:
		deductions["80CCE (80C + 80CCC + 80CCD(1))"] = cce

	# Sections with statutory caps
	for section, cap in SECTION_CAPS.items():
		if section == "80GG" and data["has_hra"]:
			continue
		amount = flt(declarations.get(section, 0))
		if amount:
			deductions[section] = min(amount, cap)

	# No-cap sections
	for section in ("80E", "80G", "80GGC"):
		amount = flt(declarations.get(section, 0))
		if amount:
			deductions[section] = amount

	return deductions
