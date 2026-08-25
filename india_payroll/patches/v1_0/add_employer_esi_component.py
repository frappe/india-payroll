import frappe


def execute():
	"""
	Create the Employer State Insurance salary component.

	The employer's 3.25% ESI share had no component to sit on, so it was folded
	into the single Employee State Insurance deduction and taken out of the
	employee's net pay. It is now an Employer Contribution shown on the salary
	slip instead.

	Re-runnable: create_esi_components skips components that already exist.
	"""
	from india_payroll.install import create_esi_components

	create_esi_components()
	frappe.clear_cache(doctype="Salary Component")
