import frappe
from frappe.custom.doctype.custom_field.custom_field import create_custom_fields

from india_payroll.install import get_custom_fields


def execute():
	"""Create custom fields for Employer PF contributions (EPF, EPS, EDLI, Admin Charges)."""
	create_custom_fields(get_custom_fields())
