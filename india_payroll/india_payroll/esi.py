import frappe
from frappe.utils import flt

ESI_EMPLOYEE_COMPONENT = "Employee State Insurance"

# Combined ESI contribution rate deducted on the salary slip:
# employee share (0.75%) + employer share (3.25%) = 4%
ESI_RATE = 0.04

ESI_WAGE_CEILING = 21_000
ESI_WAGE_CEILING_DISABILITY = 25_000


def apply_esi(doc, method=None) -> None:
	"""
	Salary Slip — before_save hook.

	Computes and injects ESI contributions when the employee's gross wages
	fall within the prescribed ceiling.  If the employee is not eligible,
	any previously injected ESI rows are removed.
	"""
	if not frappe.db.get_single_value("Payroll Settings", "enable_esic"):
		return

	if not doc.salary_structure:
		return

	if not frappe.db.exists("Salary Component", ESI_EMPLOYEE_COMPONENT):
		frappe.msgprint(
			frappe._(
				"Salary Component <b>{0}</b> not found. "
				"Please reinstall the India Payroll app or create it manually."
			).format(ESI_EMPLOYEE_COMPONENT),
			indicator="orange",
			alert=True,
		)
		return

	gross_pay = flt(doc.gross_pay)

	# Determine the applicable wage ceiling
	is_disabled = frappe.db.get_value("Employee", doc.employee, "is_person_with_disability")
	wage_ceiling = ESI_WAGE_CEILING_DISABILITY if is_disabled else ESI_WAGE_CEILING

	if gross_pay > wage_ceiling:
		# Employee not eligible — strip any stale ESI rows
		_remove_esi_components(doc)
		return

	esi = flt(gross_pay * ESI_RATE, 2)

	_update_esi_in_salary_slip(doc, esi)


def _remove_esi_components(doc) -> None:
	"""
	Remove the ESI employee component from the salary slip deductions.
	Recalculates totals only if rows were actually removed.
	"""
	before = len(doc.deductions)
	doc.deductions = [d for d in doc.deductions if d.salary_component != ESI_EMPLOYEE_COMPONENT]

	if len(doc.deductions) != before:
		_recalculate_totals(doc)


def _update_esi_in_salary_slip(doc, esi: float) -> None:
	"""
	Replace any existing Employee ESI row with the freshly computed amount.

	The single row covers the full 4% ESI contribution (employee 0.75% +
	employer 3.25%).  The employer's share is part of the CTC and is not
	shown as a separate component.
	"""
	# Remove stale row first
	doc.deductions = [d for d in doc.deductions if d.salary_component != ESI_EMPLOYEE_COMPONENT]

	if esi > 0:
		doc.append(
			"deductions",
			{
				"salary_component": ESI_EMPLOYEE_COMPONENT,
				"amount": esi,
			},
		)

	_recalculate_totals(doc)


def _recalculate_totals(doc) -> None:
	"""Recompute total_deduction and net_pay after modifying deduction rows."""
	doc.total_deduction = sum(flt(d.amount) for d in doc.deductions if not d.do_not_include_in_total)
	# Loan repayment is tracked outside `deductions` (see Salary Slip.set_net_pay),
	# so subtract it explicitly or net pay comes out too high.
	doc.net_pay = flt(doc.gross_pay) - (flt(doc.total_deduction) + flt(doc.get("total_loan_repayment")))
	if hasattr(doc, "rounded_total"):
		doc.rounded_total = round(doc.net_pay)
