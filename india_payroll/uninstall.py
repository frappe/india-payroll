import click
import frappe

from india_payroll.install import get_custom_fields


def before_uninstall():
	"""Reverse the customizations india_payroll.install.after_install() added.

	The India Payroll Workspace, Workspace Sidebar and Desktop Icon are module-owned
	standard records and are removed by the framework on app/module uninstall, so only
	the custom fields need explicit cleanup here.

	The seeded data records (Salary Components, Income Tax Slabs / tax regimes,
	Employee Tax Exemption Categories) are left in place - they are generic
	HR/payroll convenience records, not india_payroll-specific, and may be in
	use. Mirrors india_compliance, which only removes customizations on uninstall."""
	try:
		delete_custom_fields()
	except Exception:
		click.secho(
			"Removing customizations for India Payroll failed due to an error. "
			"Please try again or report the issue if it is not resolved.",
			fg="bright_red",
		)
		raise


def delete_custom_fields():
	"""Delete the custom fields created in install via a raw DB delete per
	doctype (custom fields are metadata, so link checks do not apply). Mirrors
	india_compliance.utils.custom_fields.delete_custom_fields."""
	for doctype, fields in get_custom_fields().items():
		frappe.db.delete(
			"Custom Field",
			{
				"fieldname": ("in", [field["fieldname"] for field in fields]),
				"dt": doctype,
			},
		)
		frappe.clear_cache(doctype=doctype)
