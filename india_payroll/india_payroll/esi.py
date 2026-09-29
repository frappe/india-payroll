import re

import frappe
from frappe.utils import flt

from india_payroll.india_payroll.company_settings import is_statutory_enabled
from india_payroll.india_payroll.epf import PF_WAGE_COMPONENT_PATTERNS
from india_payroll.india_payroll.utils import get_slip_ssa_values

ESI_EMPLOYEE_COMPONENT = "Employee State Insurance"
ESI_EMPLOYER_COMPONENT = "Employer State Insurance"

EMPLOYEE_ESI_RATE = 0.0075
EMPLOYER_ESI_RATE = 0.0325

ESI_WAGE_CEILING = 21_000
ESI_WAGE_CEILING_DISABILITY = 25_000

ESI_WAGE_COMPONENT_PATTERNS = (
	*PF_WAGE_COMPONENT_PATTERNS,
	r"\bretaining\b",  # Retaining Allowance
	r"\bra\b",  # RA
)

_ESI_WAGE_COMPONENT_RE = re.compile("|".join(ESI_WAGE_COMPONENT_PATTERNS))


def is_esi_wage_component(salary_component: str | None) -> bool:
	"""True when a Salary Component name reads as Basic, Dearness Allowance or
	Retaining Allowance.

	Heuristic by design, like ``epf.is_pf_wage_component``: a component named
	something unrecognisable will not be counted. ``apply_esi`` warns on the
	slip when nothing matched, so that miss is visible rather than silent.
	"""
	if not salary_component:
		return False
	normalised = re.sub(r"[^a-z0-9]+", " ", salary_component.lower().replace(".", ""))
	return bool(_ESI_WAGE_COMPONENT_RE.search(normalised))


def get_esi_split(gross, *, is_person_with_disability=False, ceiling_gross=None) -> frappe._dict:
	"""Split ESI on ``gross`` (the ESI wage) into the employee and employer shares.

	``ceiling_gross`` decides coverage when it differs from the wage levied on -
	the slip judges coverage on the full-cycle wage but contributes on the paid wage.
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

	ESI wage is the Basic, Dearness Allowance and Retaining Allowance earnings
	on the slip (see ``esi_wage``). Coverage is judged on the full-cycle wage so
	LOP cannot pull a high earner into ESI, while the contribution follows the
	wage actually paid. The employer's 3.25% is written by
	``employer_contributions``.
	"""
	if not is_statutory_enabled("esic", doc.company):
		_remove_esi_components(doc)
		return

	if not doc.salary_structure:
		return

	if not _employee_component_exists():
		return

	# the PwD flag decides which ceiling applies
	is_disabled = get_slip_ssa_values(doc, ["is_person_with_disability"]).get("is_person_with_disability")

	split = get_esi_split(
		esi_wage(doc.earnings, "amount"),
		is_person_with_disability=bool(is_disabled),
		ceiling_gross=esi_wage(doc.earnings, "default_amount"),
	)

	if not split.covered:
		if not _has_esi_wage_component(doc.earnings):
			frappe.msgprint(
				frappe._(
					"No Basic, Dearness Allowance or Retaining Allowance earning was found on "
					"this Salary Slip, so no ESI has been deducted. ESI applies only to these "
					"components; rename the component accordingly if it is ESI-eligible."
				),
				indicator="orange",
				alert=True,
			)
		_remove_esi_components(doc)
		return

	_update_esi_in_salary_slip(doc, split)


def _employee_component_exists() -> bool:
	"""Only the employee component gates the deduction. The employer component is
	written elsewhere, so a missing one must not stop the employee's share."""
	if frappe.db.exists("Salary Component", ESI_EMPLOYEE_COMPONENT):
		return True

	frappe.msgprint(
		frappe._(
			"Salary Component <b>{0}</b> not found. "
			"Please reinstall the India Payroll app or create it manually."
		).format(ESI_EMPLOYEE_COMPONENT),
		indicator="orange",
		alert=True,
	)
	return False


def esi_wage(earnings, field="amount") -> float:
	"""ESI is levied on Basic, Dearness Allowance and Retaining Allowance earnings.

	``amount`` is the wage paid, ``default_amount`` the full cycle with no LOP.
	"""
	return sum(flt(row.get(field)) for row in earnings if _is_payable(row) and _is_esi_wage_row(row))


def _is_payable(row) -> bool:
	return not row.get("statistical_component") and not row.get("do_not_include_in_total")


def _is_esi_wage_row(row) -> bool:
	# Additional Salary earnings (bonuses/arrears) never count, even when the
	# component reads as Basic/DA/RA.
	return not row.get("additional_salary") and is_esi_wage_component(row.get("salary_component"))


def _has_esi_wage_component(earnings) -> bool:
	return any(_is_payable(row) and _is_esi_wage_row(row) for row in earnings)


def _remove_esi_components(doc) -> None:
	"""Remove the employee ESI row from the salary slip."""
	doc.deductions = [d for d in doc.deductions if d.salary_component != ESI_EMPLOYEE_COMPONENT]


def _update_esi_in_salary_slip(doc, split) -> None:
	"""Replace any existing employee ESI row with the freshly computed share."""
	_remove_esi_components(doc)

	if split.employee > 0:
		doc.append("deductions", {"salary_component": ESI_EMPLOYEE_COMPONENT, "amount": split.employee})


def get_employer_contributions(earnings, config, *, paid_field="amount", company=None) -> dict:
	"""Employer ESI, keyed by component.

	Returns zero rather than omitting it, so a stale row gets cleared.
	"""
	if not is_statutory_enabled("esic", company):
		return {ESI_EMPLOYER_COMPONENT: 0.0}

	split = get_esi_split(
		esi_wage(earnings, paid_field),
		is_person_with_disability=bool(config.get("is_person_with_disability")),
		ceiling_gross=esi_wage(earnings, "default_amount"),
	)
	return {ESI_EMPLOYER_COMPONENT: split.employer}
