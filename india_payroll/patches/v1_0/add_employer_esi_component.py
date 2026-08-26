import frappe


def execute():
	"""
	Create the Employer State Insurance salary component.

	The employer's 3.25% share had no component to sit on, so it was folded into
	the employee's deduction. Re-runnable: existing components are skipped.
	"""
	from india_payroll.install import create_esi_components

	create_esi_components()
	frappe.clear_cache(doctype="Salary Component")
