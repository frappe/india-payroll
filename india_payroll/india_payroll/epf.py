# Copyright (c) 2026, Frappe Technologies Pvt. Ltd. and contributors
# License: GNU General Public License v3. See license.txt

import frappe
from frappe.utils import flt

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
	Salary Slip — before_save hook.

	Computes and injects employee EPF-scheme rows on the slip:
	  • Employee contribution (12 %)   → deductions
	  • VPF top-up (optional)          → deductions

	Employer contributions (EPF / EPS / EDLI / Admin) are configured as
	"Employer Contribution" components on the Salary Structure and handled
	by Salary Structure Assignment / CTC — not by this hook.

	Gated by a single `epf_applicable` flag on the Employee master.  All
	employees are assumed to be post-1 Sept 2014 EPF members.
	"""
	if not frappe.db.get_single_value("Payroll Settings", "enable_epf"):
		_remove_epf_components(doc)
		return

	if not doc.salary_structure:
		return

	if not _is_epf_applicable(doc.employee):
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
		# Nothing to contribute on (e.g. no components flagged as PF wage)
		_remove_epf_components(doc)
		return

	contribute_on_actual = bool(frappe.db.get_value("Employee", doc.employee, "contribute_on_actual_pf_wage"))
	pf_wage_capped = min(pf_wage, EPF_WAGE_CEILING)
	epf_base = pf_wage if contribute_on_actual else pf_wage_capped

	employee_epf = _epfo_round(epf_base * EPF_EMPLOYEE_RATE)
	vpf = _compute_vpf(doc, epf_base)

	_apply_epf_components(doc, employee_epf=employee_epf, vpf=vpf)
	_recalculate_totals(doc)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _is_epf_applicable(employee: str) -> bool:
	"""Return True if EPF is opted-in on the Employee master."""
	return bool(frappe.db.get_value("Employee", employee, "epf_applicable"))


def _required_components_exist() -> bool:
	"""Both employee-side EPF salary components must exist before we inject rows."""
	for name in EPF_EMPLOYEE_COMPONENTS:
		if not frappe.db.exists("Salary Component", name):
			return False
	return True


def _compute_pf_wage(doc) -> float:
	"""
	Sum every earning amount on the slip — all earning components count
	toward PF wage.

	Amounts on `doc.earnings` are already prorated for LOP / payment_days by
	the Salary Slip controller, so the returned PF wage reflects NCP days.
	"""
	return sum(flt(e.amount) for e in doc.earnings)


def _compute_vpf(doc, epf_base: float) -> float:
	"""
	Voluntary Provident Fund — additional employee contribution above 12 %.

	Two modes (Employee master, default Amount):
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
	emp = (
		frappe.db.get_value(
			"Employee",
			doc.employee,
			["vpf_mode", "vpf_percentage", "vpf_amount"],
			as_dict=True,
		)
		or frappe._dict()
	)

	if (emp.vpf_mode or "Amount") == "Amount":
		amount = flt(emp.vpf_amount)
		if amount <= 0:
			return 0.0
		total_days = flt(doc.total_working_days)
		if total_days > 0:
			amount = amount * flt(doc.payment_days) / total_days
		return _epfo_round(amount)

	vpf_pct = flt(emp.vpf_percentage)
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
	"""Strip employee EPF rows from deductions; recalculate."""
	before = len(doc.deductions)
	doc.deductions = [d for d in doc.deductions if d.salary_component not in EPF_EMPLOYEE_COMPONENTS]
	if len(doc.deductions) != before:
		_recalculate_totals(doc)


def _recalculate_totals(doc) -> None:
	"""Recompute total_deduction and net_pay after modifying deduction rows."""
	doc.total_deduction = sum(flt(d.amount) for d in doc.deductions if not d.do_not_include_in_total)
	doc.net_pay = flt(doc.gross_pay) - flt(doc.total_deduction)
	if hasattr(doc, "rounded_total"):
		doc.rounded_total = round(doc.net_pay)


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
