import click
import frappe

from india_payroll.install import get_custom_fields

WORKSPACE_NAME = "Tax & Benefits"
PAGE_ROUTE = "tax-regime-selector"


def before_uninstall():
	"""Reverse the customizations india_payroll.install.after_install() added:
	the Tax Regime Selector workspace/sidebar entries and the custom fields.

	The seeded data records (Salary Components, Income Tax Slabs / tax regimes,
	Employee Tax Exemption Categories) are left in place - they are generic
	HR/payroll convenience records, not india_payroll-specific, and may be in
	use. Mirrors india_compliance, which only removes customizations on uninstall."""
	try:
		delete_workspace_link()
		delete_sidebar_item()
		delete_custom_fields()
	except Exception:
		click.secho(
			"Removing customizations for India Payroll failed due to an error. "
			"Please try again or report the issue if it is not resolved.",
			fg="bright_red",
		)
		raise


def delete_workspace_link():
	"""Reverse add_tax_regime_selector_to_workspace(). Idempotent."""
	if not frappe.db.exists("Workspace", WORKSPACE_NAME):
		return

	workspace = frappe.get_doc("Workspace", WORKSPACE_NAME)
	link = next(
		(l for l in workspace.links if l.link_type == "Page" and l.link_to == PAGE_ROUTE),
		None,
	)
	if not link:
		return

	workspace.links.remove(link)

	# Decrement the 'Tax Setup' card's child count since we removed one of its links.
	for row in workspace.links:
		if row.type == "Card Break" and row.label == "Tax Setup":
			row.link_count = max((row.link_count or 0) - 1, 0)
			break

	for i, row in enumerate(workspace.links):
		row.idx = i + 1

	workspace.save(ignore_permissions=True)


def delete_sidebar_item():
	"""Reverse add_tax_regime_selector_to_sidebar(). Idempotent."""
	if not frappe.db.exists("Workspace Sidebar", WORKSPACE_NAME):
		return

	sidebar = frappe.get_doc("Workspace Sidebar", WORKSPACE_NAME)
	item = next(
		(i for i in sidebar.items if i.link_type == "Page" and i.link_to == PAGE_ROUTE),
		None,
	)
	if not item:
		return

	sidebar.items.remove(item)
	for i, row in enumerate(sidebar.items):
		row.idx = i + 1

	sidebar.save(ignore_permissions=True)


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
