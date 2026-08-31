import frappe
from frappe.utils.password import set_encrypted_password

CUSTOM_FIELD = "Payroll Settings-tds_api_key"


def execute():
	"""Move tds_api_key from Data to Password.

	It shipped as Data and was later redefined as Password. Custom Field refuses
	that transition, so `create_custom_fields` in after_migrate raises and blocks
	the whole migration on any site that installed the earlier version. Convert
	below the controller, then move the key into __Auth the way a Password field
	save would have.
	"""
	if frappe.db.get_value("Custom Field", CUSTOM_FIELD, "fieldtype") != "Data":
		return

	api_key = frappe.db.get_single_value("Payroll Settings", "tds_api_key")

	frappe.db.set_value("Custom Field", CUSTOM_FIELD, "fieldtype", "Password", update_modified=False)
	frappe.clear_cache(doctype="Payroll Settings")

	if not api_key:
		return

	set_encrypted_password("Payroll Settings", "Payroll Settings", api_key, "tds_api_key")
	frappe.db.set_single_value("Payroll Settings", "tds_api_key", "*" * len(api_key))
