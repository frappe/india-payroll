import frappe
from frappe.custom.doctype.custom_field.custom_field import create_custom_fields

# Layout-only Employee custom fields that previously split the India Payroll fields
# across three sections. They are replaced by a single `india_payroll_section`
# (with `india_payroll_cb`), so the old breaks are now orphaned.
OBSOLETE_LAYOUT_FIELDS = [
	"india_payroll_bank_cb",
	"india_payroll_esi_section",
	"india_payroll_epf_section",
]


def execute():
	"""Consolidate the India Payroll Employee custom fields under one section.

	1. Create/repoint the new layout (single `india_payroll_section` + column break).
	2. Drop the obsolete section/column-break fields. The data fields (ifsc_code,
	   esic_card_no, uan_number, pf_name, …) keep their names and values; only the
	   layout containers change.

	Re-runnable: the deletions are no-ops once the old fields are gone.
	"""
	from india_payroll.install import get_custom_fields

	create_custom_fields(get_custom_fields())

	for fieldname in OBSOLETE_LAYOUT_FIELDS:
		frappe.delete_doc_if_exists("Custom Field", f"Employee-{fieldname}")

	frappe.clear_cache(doctype="Employee")
