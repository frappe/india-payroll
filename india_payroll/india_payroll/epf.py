# Copyright (c) 2026, Frappe Technologies Pvt. Ltd. and contributors
# License: GNU General Public License v3. See license.txt

import frappe
from frappe.utils import flt

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
EPF_WAGE_CEILING = 15_000  # PF / EPS / EDLI statutory ceiling
EPF_EMPLOYEE_RATE = 0.12  # employee EPF share
EPF_EMPLOYER_RATE = 0.12  # employer total share (split between EPF + EPS)
EPS_RATE = 0.0833  # employer's pension diversion
EDLI_RATE = 0.005  # employer's EDLI premium
EPF_ADMIN_RATE = 0.005  # employer's EPF admin charges


def apply_epf(doc, method=None) -> None:
	"""
	Salary Slip regional deduction hook (see apply_regional_deductions).

	Computes and injects employee EPF-scheme rows on the slip:
	  • Employee contribution (12 %)   → deductions
	  • VPF top-up (optional)          → deductions

	Employer contributions (EPF / EPS / EDLI / Admin) are configured as
	"Employer Contribution" components on the Salary Structure and handled
	by Salary Structure Assignment / CTC — not by this hook.

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

	monthly_pf_wage = _compute_pf_wage(doc)
	if monthly_pf_wage <= 0:
		# Nothing to contribute on (e.g. no components flagged as PF wage)
		_remove_epf_components(doc)
		return

	contribute_on_actual = bool(ssa.get("contribute_on_actual_pf_wage"))
	epf_base = _compute_monthly_epf_base(monthly_pf_wage, contribute_on_actual=contribute_on_actual)

	# The ceiling test above uses the full-month structure base; the actual
	# contribution is prorated for LOP (payment vs working days).
	employee_epf = _epfo_round(epf_base * EPF_EMPLOYEE_RATE * _lop_factor(doc))
	vpf = _compute_vpf(
		doc,
		epf_base,
		vpf_mode=ssa.get("vpf_mode"),
		vpf_percentage=ssa.get("vpf_percentage"),
		vpf_amount=ssa.get("vpf_amount"),
	)

	_apply_epf_components(doc, employee_epf=employee_epf, vpf=vpf)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _required_components_exist() -> bool:
	"""Both employee-side EPF salary components must exist before we inject rows."""
	for name in EPF_EMPLOYEE_COMPONENTS:
		if not frappe.db.exists("Salary Component", name):
			return False
	return True


def _compute_pf_wage(doc) -> float:
	"""
	Sum the PF-eligible *structure* earnings on the slip (one month).

	PF wage is *not* gross pay. Two categories of earning are excluded per
	EPF statute (Sec. 2(b) excludes HRA; the EPFO circular excludes ad-hoc /
	incentive-type pay):

	  • HRA — identified from the Company master's ``hra_component`` field.
	  • Any earning sourced from an Additional Salary (bonuses, incentives,
	    arrears and other one-off components), flagged by ``additional_salary``
	    on the slip row.

	Uses ``default_amount`` (the unprorated Salary Structure amount), not the
	LOP-prorated ``amount``: the ceiling test is decided on the full structure
	wage annualised over the year (see ``_compute_monthly_epf_base``), so a
	high earner in an LOP month is not wrongly pulled below the ceiling.
	"""
	hra_component = frappe.db.get_value("Company", doc.company, "hra_component") if doc.company else None

	total = 0.0
	for e in doc.earnings:
		# Skip HRA — excluded from PF wage by statute.
		if hra_component and e.salary_component == hra_component:
			continue
		# Skip anything injected from an Additional Salary (bonuses/incentives).
		if e.get("additional_salary"):
			continue
		total += flt(e.default_amount)

	return total


def _compute_monthly_epf_base(monthly_pf_wage: float, *, contribute_on_actual: bool) -> float:
	"""
	Resolve the monthly EPF base from the full-year PF wage.

	The ₹15,000 ceiling is a statutory wage limit; applying it month-by-month
	against the actual paid wage lets LOP or mid-month variation distort the
	base. Instead we annualise the structure PF wage, apply the annual ceiling
	(₹15,000 × 12), then bring it back to a flat per-month base:

	  • ``contribute_on_actual`` → base on the full annual PF wage.
	  • otherwise                → base capped at the annual ceiling.

	The result is the full-month base; LOP proration is applied to the
	contribution by the caller (see ``_lop_factor``).
	"""
	annual_pf_wage = flt(monthly_pf_wage) * 12
	annual_ceiling = EPF_WAGE_CEILING * 12
	annual_base = annual_pf_wage if contribute_on_actual else min(annual_pf_wage, annual_ceiling)
	return annual_base / 12


def _lop_factor(doc) -> float:
	"""Proration factor for LOP: paid days over total working days.

	Falls back to 1.0 (no proration) when total working days is unavailable, so
	the contribution is never divided by zero for a preview or a manual slip.
	"""
	total_days = flt(doc.total_working_days)
	if total_days <= 0:
		return 1.0
	return flt(doc.payment_days) / total_days


def _compute_vpf(doc, epf_base: float, *, vpf_mode=None, vpf_percentage=None, vpf_amount=None) -> float:
	"""
	Voluntary Provident Fund — additional employee contribution above 12 %.

	The VPF election lives on the Salary Structure Assignment and is passed in
	by the caller. Both modes prorate by payment_days / total_working_days so
	LOP months don't over-deduct (``epf_base`` is the full-month base). Default
	Amount mode:
	  • ``Amount`` — a fixed ``vpf_amount`` the employee elected per month.
	  • ``Percentage`` — ``vpf_percentage`` of the EPF base (which follows the
	    contribute-on-actual / capped rule).

	The employer does not match VPF.  Falls back to Amount mode when
	``vpf_mode`` is unset (existing records before the field was added);
	combined with vpf_amount defaulting to 0, this means no surprise VPF
	deduction appears for employees who never opted in.
	"""
	factor = _lop_factor(doc)

	if (vpf_mode or "Amount") == "Amount":
		amount = flt(vpf_amount)
		if amount <= 0:
			return 0.0
		return _epfo_round(amount * factor)

	vpf_pct = flt(vpf_percentage)
	if vpf_pct <= 0:
		return 0.0
	return _epfo_round(epf_base * vpf_pct / 100.0 * factor)


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
