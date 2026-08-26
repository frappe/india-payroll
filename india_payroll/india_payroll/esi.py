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
	"""Split ESI on ``gross`` into the employee and employer shares.

	``ceiling_gross`` decides coverage when it differs from the wage levied on -
	the slip judges coverage on full gross but contributes on the paid wage.
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
	"""Deduct the employee's 0.75% ESI share.

	Coverage is judged on full gross so LOP cannot pull a high earner into ESI,
	while the contribution follows the wage actually paid. The employer's 3.25%
	is written by ``employer_contributions``.
	"""
	if not is_statutory_enabled("esic", doc.company):
		_remove_esi_components(doc)
		return

	if not doc.salary_structure:
		return

	if not _required_components_exist():
		return

	# the PwD flag decides which ceiling applies
	is_disabled = get_slip_ssa_values(doc, ["is_person_with_disability"]).get("is_person_with_disability")

	split = get_esi_split(
		doc.gross_pay,
		is_person_with_disability=bool(is_disabled),
		ceiling_gross=esi_gross(doc.earnings, "default_amount"),
	)

	if not split.covered:
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


def esi_gross(earnings, field="amount") -> float:
	"""ESI is levied on gross wages: every payable earning.

	``amount`` is the wage paid, ``default_amount`` the full cycle with no LOP.
	"""
	return sum(
		flt(row.get(field))
		for row in earnings
		if not row.get("statistical_component") and not row.get("do_not_include_in_total")
	)


def _remove_esi_components(doc) -> None:
	"""Remove the employee ESI row from the salary slip."""
	doc.deductions = [d for d in doc.deductions if d.salary_component != ESI_EMPLOYEE_COMPONENT]


def _update_esi_in_salary_slip(doc, split) -> None:
	"""Replace any existing employee ESI row with the freshly computed share."""
	_remove_esi_components(doc)

	if split.employee > 0:
		doc.append("deductions", {"salary_component": ESI_EMPLOYEE_COMPONENT, "amount": split.employee})


def get_employer_contributions(earnings, config, *, paid_field="amount") -> dict:
	"""Employer ESI, keyed by component.

	Returns zero rather than omitting it, so a stale row gets cleared.
	"""
	if not frappe.db.get_single_value("Payroll Settings", "enable_esic"):
		return {ESI_EMPLOYER_COMPONENT: 0.0}

	split = get_esi_split(
		esi_gross(earnings, paid_field),
		is_person_with_disability=bool(config.get("is_person_with_disability")),
		ceiling_gross=esi_gross(earnings, "default_amount"),
	)
	return {ESI_EMPLOYER_COMPONENT: split.employer}
