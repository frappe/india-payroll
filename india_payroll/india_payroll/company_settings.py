# Copyright (c) 2026, Frappe Technologies Pvt. Ltd. and contributors
# License: GNU General Public License v3. See license.txt

import frappe
from frappe import _

MULTI_COMPANY_FIELD = "enable_multi_company_payroll"
COMPANY_SETTINGS_FIELD = "company_payroll_settings"

STATUTE_TOGGLE_FIELDS = {
	"professional_tax": "enable_professional_tax",
	"esic": "enable_esic",
	"lwf": "enable_lwf",
	"epf": "enable_epf",
}

STATUTE_REGISTRATION_FIELDS = {
	"esic": "esic_registration_number",
	"epf": "epf_establishment_code",
	"professional_tax": "professional_tax_registration_number",
	"lwf": "lwf_registration_number",
}

GLOBAL_REGISTRATION_FIELDS = {
	"esic": "esic_registration_number",
	"epf": "epf_establishment_code",
}

REGISTRATION_LABELS = {
	"esic": "ESIC Registration Number",
	"epf": "EPF Establishment Code",
	"professional_tax": "Professional Tax Registration Number",
	"lwf": "LWF Registration Number",
}


def is_multi_company_enabled() -> bool:
	return bool(frappe.get_cached_value("Payroll Settings", "Payroll Settings", MULTI_COMPANY_FIELD))


def get_company_setting(company: str | None) -> "frappe._dict | None":
	if not company:
		return None

	settings = frappe.get_cached_doc("Payroll Settings")
	for row in settings.get(COMPANY_SETTINGS_FIELD) or []:
		if row.company == company:
			return frappe._dict(row.as_dict())

	return None


def is_statutory_enabled(statute: str, company: str | None = None) -> bool:
	"""Whether `statute` applies to `company`.

	The top-level Payroll Settings check is the master switch. When
	multi-company payroll is on, only companies listed in the company
	settings table participate; a company with no row is treated as
	not configured and gets no statutory deduction.
	"""
	settings = frappe.get_cached_doc("Payroll Settings")

	if not settings.get(STATUTE_TOGGLE_FIELDS[statute]):
		return False

	if not settings.get(MULTI_COMPANY_FIELD):
		return True

	return get_company_setting(company) is not None


def get_applicable_companies(statute: str) -> list[str] | None:
	"""Companies a statutory register may report `statute` liabilities for.

	`None` means no company restriction — multi-company payroll is off and
	every company follows the single global configuration, so reports keep
	their previous behaviour. A list means multi-company payroll is on and
	only the listed companies participate; an empty list means no company
	does, so the register must be empty.
	"""
	settings = frappe.get_cached_doc("Payroll Settings")

	if not settings.get(MULTI_COMPANY_FIELD):
		return None

	if not settings.get(STATUTE_TOGGLE_FIELDS[statute]):
		return []

	return [row.company for row in settings.get(COMPANY_SETTINGS_FIELD) or [] if row.company]


def get_registration_number(statute: str, company: str | None = None) -> str | None:
	"""Registration identifier for `statute`, per company when multi-company is on."""
	settings = frappe.get_cached_doc("Payroll Settings")

	if settings.get(MULTI_COMPANY_FIELD):
		row = get_company_setting(company)
		return row.get(STATUTE_REGISTRATION_FIELDS[statute]) if row else None

	global_field = GLOBAL_REGISTRATION_FIELDS.get(statute)
	return settings.get(global_field) if global_field else None


def validate_company_payroll_settings(doc, method=None) -> None:
	rows = doc.get(COMPANY_SETTINGS_FIELD) or []

	seen = {}
	for row in rows:
		for fieldname in STATUTE_REGISTRATION_FIELDS.values():
			value = row.get(fieldname)
			if value:
				row.set(fieldname, value.strip())

		if row.company in seen:
			frappe.throw(
				_("Row #{0}: Company {1} is already configured in row #{2}.").format(
					row.idx, frappe.bold(row.company), seen[row.company]
				),
				title=_("Duplicate Company"),
			)
		seen[row.company] = row.idx

	if doc.get(MULTI_COMPANY_FIELD) and not rows:
		frappe.throw(
			_(
				"Add at least one company to the Company Payroll Settings table. "
				"With multi-company payroll enabled, statutory deductions are applied "
				"only to companies listed there."
			),
			title=_("No Company Configured"),
		)
