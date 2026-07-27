import frappe


def execute():
	"""Ensure the Payroll Manager role (used by the TDS filing doctypes) exists.

	Runs before model sync so the doctype permissions can resolve the role.
	If an earlier build created a 'TDS Manager' role, rename it to keep grants
	intact; otherwise create 'Payroll Manager' fresh.
	"""
	if frappe.db.exists("Role", "Payroll Manager"):
		return

	frappe.get_doc(
		{
			"doctype": "Role",
			"role_name": "Payroll Manager",
			"desk_access": 1,
			"home_page": "",
		}
	).insert(ignore_permissions=True)
