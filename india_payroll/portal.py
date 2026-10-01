"""Employee-portal screens contributed by India Payroll.

Registered through the portal's `employee_portal_screens` hook. Everything
India-specific — Form 16, tax regimes, exemption declarations and their proofs —
lives in this app; the portal only knows how to render the section vocabulary
returned below (stats / fields / table / files / choice / form).
"""

import frappe
from frappe import _
from frappe.utils import flt, fmt_money, getdate

from india_payroll.india_payroll.page.tax_regime_selector.tax_regime_selector import (
	NEW_REGIME_SLAB,
	OLD_REGIME_SLAB,
	compute_tax_comparison,
	get_employee_salary_data,
	get_latest_assignment,
)
from india_payroll.india_payroll.tax_exemption_setup import EXEMPTION_CATEGORIES

# Declaration form is capped to the categories employees actually fill in
# themselves; the rest are payroll-side and would only be noise here.
SELF_SERVICE_SECTIONS = ("80C", "80CCD(1B)", "80D", "80TTA", "24")

CITY_TYPES = ("metro", "non-metro")

# Breakdown lines shown in the regime comparison, in the order the desk page uses.
BREAKDOWN_LINES = (
	("gross", "Annual gross"),
	("standard_deduction", "Standard deduction"),
	("hra_exemption", "HRA exemption"),
	("lta_exemption", "LTA exemption"),
	("employer_nps", "Employer NPS"),
	("taxable_income", "Taxable income"),
)


def screens() -> list[dict]:
	"""The hook. Two screens, each carrying its own handlers."""
	return [
		{
			"slug": "form-16",
			"label": "Form 16",
			"icon": "lucide-file-badge",
			"group": "My Work",
			"order": 10,
			"get": "india_payroll.portal.form16_screen",
			"actions": {},
		},
		{
			"slug": "tax",
			"label": "Tax",
			"icon": "lucide-receipt-indian-rupee",
			"group": "My Work",
			"order": 11,
			"get": "india_payroll.portal.tax_screen",
			"actions": {
				"set_regime": "india_payroll.portal.set_regime",
				"save_declaration": "india_payroll.portal.save_declaration",
				"add_proof": "india_payroll.portal.add_proof",
				"recalculate": "india_payroll.portal.recalculate",
			},
		},
	]


# --------------------------------------------------------------------- Form 16


def _money(value) -> str:
	return fmt_money(flt(value), currency="INR")


def form16_screen(employee: str) -> dict:
	forms = frappe.get_all(
		"Form 16",
		filters={"employee": employee},
		fields=[
			"name",
			"financial_year",
			"pan",
			"tan",
			"gross_salary",
			"total_taxable_income",
			"total_tax_deducted",
			"part_a_status",
			"part_b_status",
			"part_a_file",
			"part_b_file",
		],
		order_by="financial_year desc",
		limit=5,
	)
	if not forms:
		return {
			"subtitle": "Your annual TDS certificate",
			"sections": [
				{
					"type": "files",
					"title": "Form 16",
					"files": [],
					"empty": "No Form 16 has been issued to you yet. It is generated after the Q4 TDS return is filed.",
				}
			],
		}

	latest = forms[0]
	sections = [
		{
			"type": "stats",
			"tiles": [
				{
					"label": "Gross Salary",
					"value": _money(latest.gross_salary),
					"hint": latest.financial_year,
				},
				{"label": "Taxable Income", "value": _money(latest.total_taxable_income)},
				{"label": "Tax Deducted", "value": _money(latest.total_tax_deducted), "hint": "TDS"},
			],
		},
		{
			"type": "files",
			"title": f"Form 16 for {latest.financial_year}",
			"files": _form16_files(latest),
			"empty": "Neither part has been generated yet.",
		},
		{
			"type": "fields",
			"title": "Certificate Details",
			"note": "HR-owned",
			"rows": [
				{"label": "Financial year", "value": latest.financial_year, "locked": True},
				{"label": "PAN", "value": latest.pan, "locked": True, "nums": True},
				{"label": "Employer TAN", "value": latest.tan, "locked": True, "nums": True},
			],
		},
	]

	if len(forms) > 1:
		sections.append(
			{
				"type": "table",
				"title": "Earlier Years",
				"idKey": "name",
				"columns": [
					{"key": "financial_year", "label": "Year", "primary": True},
					{"key": "gross", "label": "Gross", "align": "right", "nums": True, "muted": True},
					{"key": "tds", "label": "Tax Deducted", "align": "right", "nums": True},
					{"key": "state", "label": "Status", "align": "right", "badge": True},
				],
				"rows": [
					{
						"name": f.name,
						"financial_year": f.financial_year,
						"gross": _money(f.gross_salary),
						"tds": _money(f.total_tax_deducted),
						"state": "Available" if (f.part_a_file and f.part_b_file) else "Pending",
					}
					for f in forms[1:]
				],
			}
		)

	return {"subtitle": "Your annual TDS certificate", "sections": sections}


def _form16_files(form: dict) -> list[dict]:
	"""Part A and Part B, each served through the guarded endpoint below."""
	out = []
	for part, label, hint in (
		("a", "Part A", "TRACES certificate: employer, PAN/TAN and quarterly TDS"),
		("b", "Part B", "Salary breakup, deductions and tax computed"),
	):
		has_file = form.get(f"part_{part}_file")
		out.append(
			{
				"label": label,
				"hint": hint,
				"status": (form.get(f"part_{part}_status") or ("Available" if has_file else "Pending")),
				"filename": f"Form16-{part.upper()}-{form.get('financial_year')}.pdf",
				"url": (
					f"/api/method/india_payroll.portal.form16_file"
					f"?name={frappe.utils.quoted(form['name'])}&part={part}"
					if has_file
					else None
				),
			}
		)
	return out


@frappe.whitelist()
def form16_file(name: str, part: str):
	"""Stream one part of the employee's own Form 16.

	Guarded here rather than linking the private file directly: the file URL
	alone would be served to anyone with read access to File, and a Form 16 is
	the one document an employee must never see for a colleague.
	"""
	from hrms.api import get_current_employee

	if part not in ("a", "b"):
		raise frappe.DoesNotExistError(_("Unknown part."))

	form = frappe.db.get_value(
		"Form 16", name, ["employee", "part_a_file", "part_b_file", "financial_year"], as_dict=True
	)
	if not form:
		raise frappe.DoesNotExistError(_("This Form 16 does not exist."))
	if form.employee != get_current_employee():
		raise frappe.PermissionError(_("This Form 16 belongs to someone else."))

	file_url = form.get(f"part_{part}_file")
	if not file_url:
		frappe.throw(_("Part {0} has not been generated yet.").format(part.upper()))

	file_doc = frappe.get_doc("File", {"file_url": file_url})
	frappe.local.response.filename = f"Form16-{part.upper()}-{form.financial_year}.pdf"
	frappe.local.response.filecontent = file_doc.get_content()
	frappe.local.response.type = "pdf"


# ------------------------------------------------------------------ Tax screen


def _payroll_period(employee: str) -> dict | None:
	company = frappe.db.get_value("Employee", employee, "company")
	today = frappe.utils.nowdate()
	return frappe.db.get_value(
		"Payroll Period",
		{"company": company, "start_date": ["<=", today], "end_date": [">=", today]},
		["name", "start_date", "end_date"],
		as_dict=True,
	)


def _hra_inputs(employee: str) -> dict:
	"""Rent and city drive the HRA exemption, so they are kept between visits."""
	cached = frappe.cache().hget("india_payroll_portal_hra", employee) or {}
	return {
		"rent_monthly": flt(cached.get("rent_monthly")),
		"city_type": cached.get("city_type") or "non-metro",
	}


def _declared_rows(employee: str, period: dict | None) -> list[dict]:
	if not period:
		return []
	declaration = frappe.db.get_value(
		"Employee Tax Exemption Declaration",
		{"employee": employee, "payroll_period": period.name, "docstatus": ["<", 2]},
		"name",
	)
	if not declaration:
		return []
	return frappe.get_all(
		"Employee Tax Exemption Declaration Category",
		filters={"parent": declaration},
		fields=["exemption_category", "exemption_sub_category", "amount"],
	)


def _section_totals(rows: list[dict]) -> dict:
	"""{section: total} — what compute_via_deductions reads. It does
	flt(declarations.get("80C")), so a nested {section: {sub: amount}} map
	silently evaluates to zero and every deduction is ignored."""
	totals = {}
	for r in rows:
		totals[r.exemption_category] = flt(totals.get(r.exemption_category)) + flt(r.amount)
	return totals


def _form_values(rows: list[dict]) -> dict:
	"""{sub_category: amount}, for prefilling the declaration form."""
	return {r.exemption_sub_category: flt(r.amount) for r in rows}


def tax_screen(employee: str) -> dict:
	period = _payroll_period(employee)
	assignment = get_latest_assignment(employee)
	# the shared helper returns only {name, docstatus}; the slab is read here
	current_slab = (
		frappe.db.get_value("Salary Structure Assignment", assignment["name"], "income_tax_slab")
		if assignment
		else None
	)

	rows = _declared_rows(employee, period)
	flat = _form_values(rows)
	hra = _hra_inputs(employee)

	# Same engine the desk selector uses, so the two can never disagree.
	comparison = compute_tax_comparison(
		employee, _section_totals(rows), hra["rent_monthly"], hra["city_type"]
	)
	old, new = comparison["old_regime"], comparison["new_regime"]
	better = old if comparison["recommended"] == "old" else new
	better_label = "Old Regime" if comparison["recommended"] == "old" else "New Regime"
	salary = get_employee_salary_data(employee)

	# The decision this page exists for, so it leads. Everything under it is the
	# evidence for that decision and the paperwork that follows from it.
	sections = [
		{
			"type": "choice",
			"title": "Tax Regime",
			"hint": "This is what payroll will use for the rest of the year.",
			"field": "income_tax_slab",
			"current": current_slab,
			"options": [
				{
					"value": OLD_REGIME_SLAB,
					"label": "Old Regime",
					"hint": f"Tax {_money(old['tax'])} · claim 80C, 80D, HRA",
				},
				{
					"value": NEW_REGIME_SLAB,
					"label": "New Regime",
					"hint": f"Tax {_money(new['tax'])} · lower rates, few deductions",
				},
			],
		},
		{
			"type": "stats",
			"tiles": [
				{"label": "Annual Gross", "value": _money(salary.get("annual_gross"))},
				{"label": "Recommended", "value": better_label, "hint": "on what you have declared"},
				{
					"label": "You Save",
					"value": _money(comparison["savings"]),
					"hint": "versus the other regime",
				},
			],
		},
		{
			"type": "note",
			"tone": "success" if comparison["savings"] else "muted",
			"text": (
				f"On these numbers the {better_label.lower()} costs you "
				f"{_money(better['tax'])} in tax, {_money(comparison['savings'])} less than the alternative. "
				"Declaring more investments can change this — recalculate after editing below."
			),
		},
		{
			"type": "table",
			"title": "Regime Comparison",
			"idKey": "line",
			"columns": [
				{"key": "line", "label": "", "primary": True},
				{"key": "old", "label": "Old Regime", "align": "right", "nums": True},
				{"key": "new", "label": "New Regime", "align": "right", "nums": True},
			],
			"rows": _comparison_rows(old, new),
			"total": {"label": "Tax payable", "value": f"{_money(old['tax'])}   ·   {_money(new['tax'])}"},
		},
		{
			"type": "form",
			"title": "Rent and HRA",
			"hint": "HRA exemption is only available under the old regime.",
			"fields": [
				{
					"fieldname": "rent_monthly",
					"label": "Monthly rent paid",
					"type": "number",
					"value": hra["rent_monthly"],
				},
				{
					"fieldname": "city_type",
					"label": "City",
					"type": "select",
					"options": list(CITY_TYPES),
					"value": hra["city_type"],
				},
			],
			"action": {"label": "Recalculate", "action": "recalculate"},
		},
	]

	if not period:
		sections.append(
			{
				"type": "note",
				"text": "There is no active payroll period, so declarations cannot be recorded yet.",
			}
		)
	else:
		submitted = frappe.db.get_value(
			"Employee Tax Exemption Declaration",
			{"employee": employee, "payroll_period": period.name, "docstatus": 1},
			"name",
		)
		sections.append(
			{
				"type": "form",
				"title": "Declarations",
				"hint": (
					f"Submitted for {period.name}. Ask HR to reopen it to make changes."
					if submitted
					else f"What you intend to invest this year ({period.name}). Proofs come later."
				),
				"fields": [
					{
						"fieldname": _slug(cat),
						"label": f"{cat} — {EXEMPTION_CATEGORIES[cat]['sub_categories'][0]['name']}",
						"type": "number",
						"value": flat.get(EXEMPTION_CATEGORIES[cat]["sub_categories"][0]["name"], 0),
						"placeholder": f"Up to {_money(EXEMPTION_CATEGORIES[cat]['max_amount'])}",
					}
					for cat in SELF_SERVICE_SECTIONS
					if cat in EXEMPTION_CATEGORIES and EXEMPTION_CATEGORIES[cat].get("sub_categories")
				],
				"action": {"label": "Save Declaration", "action": "save_declaration"},
			}
		)

	if period:
		sections.append(_proof_section(employee, period))
	return {
		"subtitle": "Compare regimes, declare investments, submit proofs",
		# the page's one committing action, so it sits in the header rather than
		# inside the card; field/current keep it disabled until the pick changes
		"actions": [
			{
				"label": "Save Regime",
				"action": "set_regime",
				"variant": "solid",
				"icon": "lucide-check",
				"field": "income_tax_slab",
				"current": current_slab,
			}
		],
		"sections": sections,
	}


def _comparison_rows(old: dict, new: dict) -> list[dict]:
	"""One row per breakdown line, old beside new — the desk page's table."""
	ob, nb = old.get("breakdown") or {}, new.get("breakdown") or {}
	rows = []
	for key, label in BREAKDOWN_LINES:
		if key not in ob and key not in nb:
			continue
		rows.append(
			{
				"line": label,
				"old": _money(ob.get(key)) if key in ob else "—",
				"new": _money(nb.get(key)) if key in nb else "—",
			}
		)
	# deductions claimed under the old regime only
	for section, amount in (ob.get("via_deductions") or {}).items():
		rows.append({"line": f"Deduction {section}", "old": _money(amount), "new": "—"})
	return rows


def _slug(category: str) -> str:
	return "sec_" + category.lower().replace("(", "_").replace(")", "").replace(".", "_")


def _proof_section(employee: str, period: dict) -> dict:
	proof = frappe.db.get_value(
		"Employee Tax Exemption Proof Submission",
		{"employee": employee, "payroll_period": period.name, "docstatus": ["<", 2]},
		["name", "total_actual_amount", "docstatus"],
		as_dict=True,
	)
	rows = []
	if proof:
		rows = [
			{
				"name": r.name,
				"category": r.exemption_sub_category,
				"amount": _money(r.amount),
				"state": "Submitted" if proof.docstatus == 1 else "Draft",
			}
			for r in frappe.get_all(
				"Employee Tax Exemption Proof Submission Detail",
				filters={"parent": proof.name},
				fields=["name", "exemption_sub_category", "amount"],
			)
		]

	return {
		"type": "table",
		"title": "Investment Proofs",
		"idKey": "name",
		"columns": [
			{"key": "category", "label": "Investment", "primary": True},
			{"key": "amount", "label": "Actual Amount", "align": "right", "nums": True},
			{"key": "state", "label": "Status", "align": "right", "badge": True},
		],
		"rows": rows,
		"empty": "No proofs submitted yet. Declare first, then upload proof of what you actually invested.",
		"total": {"label": "Total proved", "value": _money(proof.total_actual_amount)} if proof else None,
	}


# --------------------------------------------------------------------- actions


def recalculate(employee: str, values: dict) -> dict:
	"""Re-run the comparison with new rent/city. Nothing is persisted to payroll."""
	city = values.get("city_type")
	if city and city not in CITY_TYPES:
		frappe.throw(_("Pick a city type."))

	frappe.cache().hset(
		"india_payroll_portal_hra",
		employee,
		{"rent_monthly": flt(values.get("rent_monthly")), "city_type": city or "non-metro"},
	)
	return {"message": _("Recalculated")}


def set_regime(employee: str, values: dict) -> dict:
	slab = values.get("income_tax_slab")
	if slab not in (OLD_REGIME_SLAB, NEW_REGIME_SLAB):
		frappe.throw(_("Pick one of the two regimes."))

	assignment = get_latest_assignment(employee)
	if not assignment:
		frappe.throw(_("You do not have a salary structure assignment yet."))

	frappe.db.set_value("Salary Structure Assignment", assignment["name"], "income_tax_slab", slab)
	return {"message": _("Tax regime saved")}


def _get_or_make_declaration(employee: str, period: dict):
	existing = frappe.db.get_value(
		"Employee Tax Exemption Declaration",
		{"employee": employee, "payroll_period": period.name, "docstatus": ["<", 2]},
		["name", "docstatus"],
		as_dict=True,
	)
	if existing and existing.docstatus == 1:
		frappe.throw(_("Your declaration for this period has been submitted and can no longer be edited."))
	if existing:
		return frappe.get_doc("Employee Tax Exemption Declaration", existing.name)

	doc = frappe.new_doc("Employee Tax Exemption Declaration")
	doc.employee = employee
	doc.payroll_period = period.name
	doc.company = frappe.db.get_value("Employee", employee, "company")
	return doc


def save_declaration(employee: str, values: dict) -> dict:
	period = _payroll_period(employee)
	if not period:
		frappe.throw(_("There is no active payroll period."))

	doc = _get_or_make_declaration(employee, period)
	doc.declarations = []
	for category in SELF_SERVICE_SECTIONS:
		meta = EXEMPTION_CATEGORIES.get(category)
		if not meta or not meta.get("sub_categories"):
			continue
		amount = flt(values.get(_slug(category)))
		if amount <= 0:
			continue
		if amount > flt(meta["max_amount"]):
			frappe.throw(_("{0} is capped at {1}").format(category, _money(meta["max_amount"])))
		doc.append(
			"declarations",
			{
				"exemption_sub_category": meta["sub_categories"][0]["name"],
				"exemption_category": category,
				"amount": amount,
			},
		)

	if not doc.declarations:
		frappe.throw(_("Enter at least one amount before saving."))

	doc.save(ignore_permissions=True)
	return {"message": _("Declaration saved as a draft for HR to review")}


def add_proof(employee: str, values: dict) -> dict:
	"""Record what was actually invested, against the declaration."""
	period = _payroll_period(employee)
	if not period:
		frappe.throw(_("There is no active payroll period."))

	existing = frappe.db.get_value(
		"Employee Tax Exemption Proof Submission",
		{"employee": employee, "payroll_period": period.name, "docstatus": ["<", 2]},
		["name", "docstatus"],
		as_dict=True,
	)
	if existing and existing.docstatus == 1:
		frappe.throw(_("Your proofs for this period have already been submitted."))

	doc = (
		frappe.get_doc("Employee Tax Exemption Proof Submission", existing.name)
		if existing
		else frappe.new_doc("Employee Tax Exemption Proof Submission")
	)
	if not existing:
		doc.employee = employee
		doc.payroll_period = period.name
		doc.company = frappe.db.get_value("Employee", employee, "company")
		doc.submission_date = getdate()

	category = values.get("category")
	amount = flt(values.get("amount"))
	if not category or amount <= 0:
		frappe.throw(_("Pick an investment and enter the amount you actually invested."))

	# type_of_proof and max_amount are mandatory on the child row
	sub = frappe.db.get_value(
		"Employee Tax Exemption Sub Category",
		category,
		["exemption_category", "max_amount"],
		as_dict=True,
	)
	doc.append(
		"tax_exemption_proofs",
		{
			"exemption_sub_category": category,
			"exemption_category": values.get("section") or (sub or {}).get("exemption_category"),
			"max_amount": (sub or {}).get("max_amount") or amount,
			"type_of_proof": values.get("type_of_proof") or _("Investment proof"),
			"amount": amount,
		},
	)
	doc.save(ignore_permissions=True)
	return {"message": _("Proof recorded")}
