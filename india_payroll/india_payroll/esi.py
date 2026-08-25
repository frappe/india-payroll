import frappe
from frappe.utils import flt

from india_payroll.india_payroll.company_settings import is_statutory_enabled
from india_payroll.india_payroll.utils import get_slip_ssa_values

ESI_EMPLOYEE_COMPONENT = "Employee State Insurance"
ESI_EMPLOYER_COMPONENT = "Employer State Insurance"

EMPLOYEE_ESI_RATE = 0.0075
EMPLOYER_ESI_RATE = 0.0325

ESI_WAGE_CEILING = 21_000
ESI_WAGE_CEILING_DISABILITY = 25_000


def get_esi_split(gross, *, is_person_with_disability=False, ceiling_gross=None) -> frappe._dict:
	"""
	Split the ESI contribution on ``gross`` into the employee and employer shares.

	``ceiling_gross`` decides coverage when it differs from the wage the
	contribution is levied on — the salary slip judges coverage on the full
	monthly gross but contributes on the LOP-prorated wage. Callers with a
	single wage (the CTC hook, the register) omit it.
	"""
	gross = flt(gross)
	ceiling = ESI_WAGE_CEILING_DISABILITY if is_person_with_disability else ESI_WAGE_CEILING
	covered = flt(ceiling_gross if ceiling_gross is not None else gross) <= ceiling

	if not covered or gross <= 0:
		return frappe._dict(covered=False, ceiling=ceiling, employee=0.0, employer=0.0, total=0.0)

	employee = flt(gross * EMPLOYEE_ESI_RATE, 2)
	employer = flt(gross * EMPLOYER_ESI_RATE, 2)

	return frappe._dict(
		covered=True,
		ceiling=ceiling,
		employee=employee,
		employer=employer,
		total=flt(employee + employer, 2),
	)


def apply_esi(doc, method=None) -> None:
	"""
	Salary Slip regional hook (see apply_regional_deductions).

	Injects the employee's ESI share (0.75%) as a deduction and the employer's
	share (3.25%) as an employer contribution, when the employee's wage is
	within the prescribed ceiling; otherwise removes any previously injected
	ESI rows.

	Coverage (the wage-ceiling test) is decided on the *full* monthly gross from
	the structure assignment — not the payment-days-prorated ``doc.gross_pay`` —
	so a high earner is not wrongly pulled into ESI in an LOP month. The
	contribution itself is still levied on the actual wages paid (``gross_pay``).
	"""
	if not is_statutory_enabled("esic", doc.company):
		_remove_esi_components(doc)
		return

	if not doc.salary_structure:
		return

	if not _required_components_exist():
		return

	# Determine the applicable wage ceiling (PwD flag lives on the assignment)
	is_disabled = get_slip_ssa_values(doc, ["is_person_with_disability"]).get("is_person_with_disability")

	split = get_esi_split(
		doc.gross_pay,
		is_person_with_disability=bool(is_disabled),
		ceiling_gross=_full_gross(doc),
	)

	if not split.covered:
		# Wage above the ceiling — not covered. Strip any stale ESI rows.
		_remove_esi_components(doc)
		return

	_update_esi_in_salary_slip(doc, split)


def _required_components_exist() -> bool:
	missing = [
		component
		for component in (ESI_EMPLOYEE_COMPONENT, ESI_EMPLOYER_COMPONENT)
		if not frappe.db.exists("Salary Component", component)
	]
	if not missing:
		return True

	frappe.msgprint(
		frappe._(
			"Salary Component <b>{0}</b> not found. "
			"Please reinstall the India Payroll app or create it manually."
		).format(", ".join(missing)),
		indicator="orange",
		alert=True,
	)
	return False


def _full_gross(doc) -> float:
	"""Full, unprorated gross for the period — the gross the structure assignment
	yields with no LOP.
	"""
	return sum(flt(e.default_amount) for e in doc.earnings if not e.do_not_include_in_total)


def _remove_esi_components(doc) -> None:
	"""Remove both ESI rows from the salary slip."""
	doc.deductions = [d for d in doc.deductions if d.salary_component != ESI_EMPLOYEE_COMPONENT]
	doc.employer_contributions = [
		d for d in doc.employer_contributions if d.salary_component != ESI_EMPLOYER_COMPONENT
	]


def _update_esi_in_salary_slip(doc, split) -> None:
	"""
	Replace any existing ESI rows with the freshly computed shares.

	The employee's 0.75% reduces net pay; the employer's 3.25% is a cost the
	employer bears on top of gross and is shown on the slip for transparency
	without affecting gross, total deduction or net pay.
	"""
	_remove_esi_components(doc)

	if split.employee > 0:
		doc.append("deductions", {"salary_component": ESI_EMPLOYEE_COMPONENT, "amount": split.employee})

	if split.employer > 0:
		doc.append(
			"employer_contributions",
			{"salary_component": ESI_EMPLOYER_COMPONENT, "amount": split.employer},
		)
