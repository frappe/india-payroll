import frappe


def execute():
	"""
	Drop the `include_in_pf_wage` custom field from Salary Component.

	PF wage is now computed from every earning component on the slip, so the
	per-component flag is no longer consulted anywhere. Remove the stale Custom
	Field so it doesn't linger on the Salary Component form.
	"""
	frappe.delete_doc_if_exists("Custom Field", "Salary Component-include_in_pf_wage")
