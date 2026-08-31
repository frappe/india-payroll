# Copyright (c) 2026, Frappe Technologies Pvt. Ltd. and contributors
# License: GNU General Public License v3. See license.txt

import frappe


def get_effective_ssa_values(
	employee: str,
	company: str,
	salary_structure: str,
	on_date,
	fields: list[str],
) -> "frappe._dict":
	row = frappe.db.get_value(
		"Salary Structure Assignment",
		filters={
			"employee": employee,
			"company": company,
			"salary_structure": salary_structure,
			"from_date": ("<=", on_date),
			"docstatus": 1,
		},
		fieldname=fields,
		order_by="from_date desc",
		as_dict=True,
	)
	return row or frappe._dict()


def get_slip_ssa_values(doc, fields: list[str]) -> "frappe._dict":
	return get_effective_ssa_values(doc.employee, doc.company, doc.salary_structure, doc.end_date, fields)


def get_slips_with_deduction(slip_names: list[str], components: list[str]) -> set[str]:
	"""Names of the given slips that actually carry one of `components` as a deduction.

	A statutory deduction row is only ever written with a positive amount, so its
	presence is the slip's own record that the liability was incurred for that
	period — independent of how Payroll Settings is configured today.
	"""
	if not slip_names or not components:
		return set()

	rows = frappe.get_all(
		"Salary Detail",
		filters={
			"parenttype": "Salary Slip",
			"parent": ("in", slip_names),
			"parentfield": "deductions",
			"salary_component": ("in", components),
			"amount": (">", 0),
		},
		pluck="parent",
	)
	return set(rows)
