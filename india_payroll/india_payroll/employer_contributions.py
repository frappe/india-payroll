"""Statutory employer contributions, for both the salary slip and CTC.

Each statute exposes ``get_employer_contributions()`` returning
``{salary_component: amount}``. Adding one means a function and a registry
entry; neither hook below changes.
"""

import frappe
from frappe.utils import flt

from india_payroll.india_payroll import esi
from india_payroll.india_payroll.utils import get_slip_ssa_values

CONTRIBUTION_SOURCES = (esi.get_employer_contributions,)

# assignment fields the sources read
CONFIG_FIELDS = ("is_person_with_disability",)


def compute_employer_contributions(earnings, config, *, paid_field="amount") -> dict:
	"""Every employer contribution for these earnings, keyed by component.

	Eligibility always reads ``default_amount``. The paid wage differs by
	context, so the caller names the field.
	"""
	amounts = {}
	for get_contributions in CONTRIBUTION_SOURCES:
		amounts.update(get_contributions(earnings, config, paid_field=paid_field))
	return amounts


def apply_regional_ctc_components(assignment, rows_by_type, data) -> None:
	"""Regional CTC hook -- without it, CTC understates the employer's cost."""
	earnings = rows_by_type.get("earnings") or []
	# an assignment evaluates a full cycle, so full-cycle wage is the paid wage
	contributions = compute_employer_contributions(earnings, assignment, paid_field="default_amount")

	for component, amount in contributions.items():
		assignment.upsert_employer_contribution(rows_by_type, data, component, amount)


def set_slip_employer_contributions(doc) -> None:
	"""Write the employer's statutory cost onto the salary slip.

	Informational only -- never affects gross, deduction or net pay.
	"""
	if not doc.salary_structure:
		return

	config = get_slip_ssa_values(doc, list(CONFIG_FIELDS))
	amounts = compute_employer_contributions(doc.earnings, config)

	for component, amount in amounts.items():
		doc.employer_contributions = [
			row for row in doc.employer_contributions if row.salary_component != component
		]
		if flt(amount) > 0 and frappe.db.exists("Salary Component", component):
			doc.append("employer_contributions", {"salary_component": component, "amount": amount})
