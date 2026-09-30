# Copyright (c) 2026, Frappe Technologies Pvt. Ltd. and contributors
# License: GNU General Public License v3. See license.txt

import datetime
import re

import frappe
from frappe.utils import date_diff, flt, getdate

from india_payroll.india_payroll.company_settings import is_statutory_enabled
from india_payroll.india_payroll.utils import get_slip_ssa_values

# Employee deductions (reduce net pay).  Employer EPF/EPS/EDLI/Admin are
# components of type "Employer Contribution" and live on the Salary Structure's
# employer_contributions table — they're rolled into CTC by Salary Structure
# Assignment and are not injected onto the slip.
EPF_EMPLOYEE_COMPONENT = "Provident Fund"
VPF_COMPONENT = "Voluntary Provident Fund"

EPF_EMPLOYEE_COMPONENTS = (EPF_EMPLOYEE_COMPONENT, VPF_COMPONENT)

# --- Statutory constants --------------------------------------------------
# Employer-side rates remain here even though the slip hook no longer applies
# them — the EPF register / ECR report reads them when reconstructing the
# canonical employer split per EPFO statute.
EPF_WAGE_CEILING = 25_000  # PF / EPS / EDLI statutory ceiling (S.O. 5109(E))
EPF_WAGE_CEILING_REVISED_ON = datetime.date(2026, 9, 17)
EPF_PREVIOUS_WAGE_CEILING = 15_000  # in force until 16 Sept 2026
EPF_EMPLOYEE_RATE = 0.12  # employee EPF share
EPF_EMPLOYER_RATE = 0.12  # employer total share (split between EPF + EPS)
EPS_RATE = 0.0833  # employer's pension diversion
EDLI_RATE = 0.005  # employer's EDLI premium
EPF_ADMIN_RATE = 0.005  # employer's EPF admin charges

PF_WAGE_COMPONENT_PATTERNS = (
	r"\bbasic\b",  # Basic, Basic Salary, Basic Pay, Basic Wages, Basic + DA
	r"\bdearness\b",  # Dearness Allowance, Dearness Pay
	r"\bda\b",  # DA, Basic + DA
)

_PF_WAGE_COMPONENT_RE = re.compile("|".join(PF_WAGE_COMPONENT_PATTERNS))


def is_pf_wage_component(salary_component: str | None) -> bool:
	"""True when a Salary Component name reads as Basic or Dearness Allowance.

	Heuristic by design: there is no per-component PF flag, so a company that
	names its basic component something unrecognisable (e.g. "Fixed Pay") will
	not have it counted. ``apply_epf`` warns on the slip when EPF is applicable
	but nothing matched, so that miss is visible rather than silent.
	"""
	if not salary_component:
		return False
	normalised = re.sub(r"[^a-z0-9]+", " ", salary_component.lower().replace(".", ""))
	return bool(_PF_WAGE_COMPONENT_RE.search(normalised))


def apply_epf(doc, method=None) -> None:
	"""
	Salary Slip regional deduction hook (see apply_regional_deductions).

	Computes and injects employee EPF-scheme rows on the slip:
	  • Employee contribution (12 %)   → deductions
	  • VPF top-up (optional)          → deductions

	Employer contributions (EPF / EPS / EDLI / Admin) are configured as
	"Employer Contribution" components on the Salary Structure and handled
	by Salary Structure Assignment / CTC — not by this hook.

	Contributions are computed on PF wage — the Basic and Dearness Allowance
	earnings on the slip only (see ``_compute_pf_wage``) — never on gross pay.

	Gated by a single `epf_applicable` flag on the Salary Structure Assignment.
	All employees are assumed to be post-1 Sept 2014 EPF members.
	"""
	if not is_statutory_enabled("epf", doc.company):
		_remove_epf_components(doc)
		return

	if not doc.salary_structure:
		return

	# EPF config (opt-in flag, actual-wage rule, VPF election) lives on the
	# assignment so it can be set per SSA and revised mid-year.
	ssa = get_slip_ssa_values(
		doc,
		["epf_applicable", "contribute_on_actual_pf_wage", "vpf_mode", "vpf_percentage", "vpf_amount"],
	)

	if not ssa.get("epf_applicable"):
		_remove_epf_components(doc)
		return

	if not _required_components_exist():
		frappe.msgprint(
			frappe._(
				"One or more EPF Salary Components are missing. "
				"Please reinstall the India Payroll app or create them manually."
			),
			indicator="orange",
			alert=True,
		)
		return

	pf_wage = _compute_pf_wage(doc)
	if pf_wage <= 0:
		if not _has_pf_wage_component(doc):
			frappe.msgprint(
				frappe._(
					"No Basic or Dearness Allowance earning was found on this Salary Slip, "
					"so no EPF has been deducted. EPF applies only to Basic and Dearness "
					"Allowance; rename the component accordingly if it is PF-eligible."
				),
				indicator="orange",
				alert=True,
			)
		_remove_epf_components(doc)
		return

	contribute_on_actual = bool(ssa.get("contribute_on_actual_pf_wage"))
	joining_date, relieving_date = frappe.get_cached_value(
		"Employee", doc.employee, ["date_of_joining", "relieving_date"]
	)
	ceilings = get_epf_wage_ceilings(
		doc.start_date, doc.end_date, joining_date=joining_date, relieving_date=relieving_date
	)
	pf_wage_capped = cap_pf_wage(pf_wage, ceilings)
	epf_base = pf_wage if contribute_on_actual else pf_wage_capped

	employee_epf = _epfo_round(epf_base * EPF_EMPLOYEE_RATE)
	vpf = _compute_vpf(
		doc,
		epf_base,
		vpf_mode=ssa.get("vpf_mode"),
		vpf_percentage=ssa.get("vpf_percentage"),
		vpf_amount=ssa.get("vpf_amount"),
	)

	_apply_epf_components(doc, employee_epf=employee_epf, vpf=vpf)


def get_epf_wage_ceilings(
	start_date, end_date=None, *, joining_date=None, relieving_date=None
) -> list[tuple[float, float]]:
	"""Ceilings in force over a pay period, as (ceiling, share of days) pairs.

	S.O. 5109(E) raised the ceiling from ₹15,000 to ₹25,000 with effect from
	17 Sept 2026. A period that straddles that date is split by calendar days
	on each side, so September 2026 is [(15,000, 16/30), (25,000, 14/30)].

	An employee who joined or left within the period is weighted on the days in
	service only, so one relieved on 10 Sept 2026 stays at ₹15,000 and one
	relieved on 20 Sept 2026 gets [(15,000, 16/20), (25,000, 4/20)].
	"""
	start = getdate(start_date)
	end = getdate(end_date) if end_date else start

	if joining_date and start < getdate(joining_date) <= end:
		start = getdate(joining_date)
	if relieving_date and start <= getdate(relieving_date) < end:
		end = getdate(relieving_date)

	if start >= EPF_WAGE_CEILING_REVISED_ON:
		return [(EPF_WAGE_CEILING, 1.0)]
	if end < EPF_WAGE_CEILING_REVISED_ON:
		return [(EPF_PREVIOUS_WAGE_CEILING, 1.0)]

	total_days = date_diff(end, start) + 1
	revised_days = date_diff(end, EPF_WAGE_CEILING_REVISED_ON) + 1
	return [
		(EPF_PREVIOUS_WAGE_CEILING, (total_days - revised_days) / total_days),
		(EPF_WAGE_CEILING, revised_days / total_days),
	]


def get_epf_wage_ceiling(start_date, end_date=None, *, joining_date=None, relieving_date=None) -> float:
	"""Day-weighted ceiling for a pay period: ₹19,666.67 for September 2026.

	Only meaningful for a wage at or above every ceiling in the period; use
	``cap_pf_wage`` to cap an actual wage.
	"""
	ceilings = get_epf_wage_ceilings(
		start_date, end_date, joining_date=joining_date, relieving_date=relieving_date
	)
	return flt(sum(ceiling * share for ceiling, share in ceilings), 2)


def cap_pf_wage(pf_wage, ceilings) -> float:
	"""Cap the PF wage under each ceiling separately, weighted by days.

	A wage between the two ceilings is capped at ₹15,000 for the earlier days
	and taken in full for the later ones: ₹20,000 in September 2026 gives
	15,000 * 16/30 + 20,000 * 14/30 = ₹17,333.33, not min(20,000, 19,666.67).
	"""
	return flt(sum(min(flt(pf_wage), ceiling) * share for ceiling, share in ceilings), 2)


def get_eps_wage(pf_wage, ceilings) -> float:
	"""EPS wage for the period.

	Post-1 Sept 2014 rule: a member whose PF wage exceeds the ceiling gets no
	EPS, applied per ceiling in force, so ₹20,000 in September 2026 earns EPS
	on the 14 days under the ₹25,000 ceiling only.
	"""
	return flt(sum(flt(pf_wage) * share for ceiling, share in ceilings if flt(pf_wage) <= ceiling), 2)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _required_components_exist() -> bool:
	"""Both employee-side EPF salary components must exist before we inject rows."""
	for name in EPF_EMPLOYEE_COMPONENTS:
		if not frappe.db.exists("Salary Component", name):
			return False
	return True


def _is_pf_wage_row(e) -> bool:
	# Additional Salary earnings (bonuses/arrears) never count, even when the
	# component reads as Basic/DA.
	return not e.get("additional_salary") and is_pf_wage_component(e.salary_component)


def _has_pf_wage_component(doc) -> bool:
	return any(_is_pf_wage_row(e) for e in doc.earnings)


def _compute_pf_wage(doc) -> float:
	# `amount`, not `default_amount`: for components that depend on payment days
	# this is the LOP-prorated wage actually paid, which is what the EPF
	# register and the ECR report as EPF wages.
	return sum(flt(e.amount) for e in doc.earnings if _is_pf_wage_row(e))


def _compute_vpf(doc, epf_base: float, *, vpf_mode=None, vpf_percentage=None, vpf_amount=None) -> float:
	"""
	Voluntary Provident Fund — additional employee contribution above 12 %.

	The VPF election lives on the Salary Structure Assignment and is passed in
	by the caller. Two modes (default Amount):
	  • ``Amount`` — a fixed ``vpf_amount`` the employee elected per month,
	    prorated by payment_days / total_working_days so LOP months don't
	    over-deduct.
	  • ``Percentage`` — ``vpf_percentage`` of the EPF base (which already
	    follows the contribute-on-actual / capped rule and slip proration).

	The employer does not match VPF.  Falls back to Amount mode when
	``vpf_mode`` is unset (existing records before the field was added);
	combined with vpf_amount defaulting to 0, this means no surprise VPF
	deduction appears for employees who never opted in.
	"""
	if (vpf_mode or "Amount") == "Amount":
		amount = flt(vpf_amount)
		if amount <= 0:
			return 0.0
		total_days = flt(doc.total_working_days)
		if total_days > 0:
			amount = amount * flt(doc.payment_days) / total_days
		return _epfo_round(amount)

	vpf_pct = flt(vpf_percentage)
	if vpf_pct <= 0:
		return 0.0
	return _epfo_round(epf_base * vpf_pct / 100.0)


def _apply_epf_components(doc, *, employee_epf: float, vpf: float) -> None:
	"""Replace any existing employee EPF rows on the slip with fresh amounts."""
	doc.deductions = [d for d in doc.deductions if d.salary_component not in EPF_EMPLOYEE_COMPONENTS]

	if employee_epf > 0:
		doc.append(
			"deductions",
			{"salary_component": EPF_EMPLOYEE_COMPONENT, "amount": employee_epf},
		)

	if vpf > 0:
		doc.append(
			"deductions",
			{"salary_component": VPF_COMPONENT, "amount": vpf},
		)


def _remove_epf_components(doc) -> None:
	"""Strip employee EPF rows from deductions."""
	doc.deductions = [d for d in doc.deductions if d.salary_component not in EPF_EMPLOYEE_COMPONENTS]


def _epfo_round(amount: float) -> int:
	"""
	Round to the nearest rupee per EPFO conventions (half-up).

	Python's built-in `round()` uses banker's rounding, which can give
	surprising results at .5 boundaries (e.g. EPS = 8.33 % * 15,000 = 1249.5
	must become ₹1,250, not ₹1,249).  We use explicit half-up here.
	"""
	a = flt(amount)
	if a >= 0:
		return int(a + 0.5)
	return -int(-a + 0.5)
