import datetime

import frappe
from frappe.query_builder.functions import Sum
from frappe.utils import flt, getdate

from india_payroll.india_payroll.company_settings import is_statutory_enabled
from india_payroll.india_payroll.utils import get_slip_ssa_values

PT_SALARY_COMPONENT = "Professional Tax"

# ---------------------------------------------------------------------------
# State-wise Professional Tax configuration
#
# Each entry supports:
#   frequency   : "monthly" | "half-yearly" | "yearly"
#   slabs       : ordered list of {"upto": int_or_None, "amount": int}
#                 Slabs are evaluated in order; the first whose `upto` value
#                 is >= gross salary (or is None, meaning "above all") wins.
#                 A slab may carry `month_amounts` ({month: amount}) for months
#                 charged differently, e.g. {2: 300} for the February ₹300 rule.
#   revisions (optional):
#     list of {"effective_from": "YYYY-MM-DD", ...} overrides applied to
#     salary slips starting on or after that date
#   special_rules (optional):
#     women_exemption_upto : women earning ≤ this value are fully exempt
# ---------------------------------------------------------------------------
STATE_PT_CONFIG = {
	# ------------------------------------------------------------------
	# Monthly states
	# ------------------------------------------------------------------
	"Andhra Pradesh": {
		"frequency": "monthly",
		"slabs": [
			{"upto": 15000, "amount": 0},
			{"upto": 20000, "amount": 150},
			{"upto": None, "amount": 200},
		],
	},
	"Assam": {
		"frequency": "monthly",
		"slabs": [
			{"upto": 15000, "amount": 0},
			{"upto": 25000, "amount": 180},
			{"upto": None, "amount": 208},
		],
	},
	"Gujarat": {
		"frequency": "monthly",
		"slabs": [
			{"upto": 11999, "amount": 0},
			{"upto": None, "amount": 200},
		],
	},
	"Jharkhand": {
		"frequency": "monthly",
		"slabs": [
			{"upto": 25000, "amount": 0},
			{"upto": 41666, "amount": 100},
			{"upto": 66666, "amount": 150},
			{"upto": 83333, "amount": 175},
			{"upto": None, "amount": 208, "month_amounts": {3: 212}},
		],
	},
	"Karnataka": {
		"frequency": "monthly",
		"slabs": [
			{"upto": 24999, "amount": 0},
			{"upto": None, "amount": 200, "month_amounts": {2: 300}},
		],
	},
	"Madhya Pradesh": {
		"frequency": "monthly",
		"slabs": [
			{"upto": 18750, "amount": 0},
			{"upto": 25000, "amount": 125},
			{"upto": 33333, "amount": 166, "month_amounts": {3: 174}},
			{"upto": None, "amount": 208, "month_amounts": {3: 212}},
		],
	},
	"Maharashtra": {
		"frequency": "monthly",
		# From 1 April 2023, women earning ≤ ₹25,000/month are fully exempt.
		"slabs": [
			{"upto": 7500, "amount": 0},
			{"upto": 10000, "amount": 175},
			# ₹300 replaces ₹200 in February so that the annual total
			# reaches the ₹2,500 constitutional cap:
			# 11 months * ₹200 + February * ₹300 = ₹2,500
			{"upto": None, "amount": 200, "month_amounts": {2: 300}},
		],
		"special_rules": {
			# Women earning up to this monthly gross are fully exempt
			"women_exemption_upto": 25000,
		},
	},
	"Meghalaya": {
		"frequency": "monthly",
		"slabs": [
			{"upto": 4166, "amount": 0},
			{"upto": 6250, "amount": 16},
			{"upto": 8333, "amount": 25},
			{"upto": 12500, "amount": 41},
			{"upto": 16666, "amount": 62},
			{"upto": 20833, "amount": 83},
			{"upto": 25000, "amount": 104},
			{"upto": 29166, "amount": 125},
			{"upto": 33333, "amount": 150},
			{"upto": 37500, "amount": 175},
			{"upto": 41666, "amount": 200},
			{"upto": None, "amount": 208},
		],
	},
	"Odisha": {
		"frequency": "monthly",
		"slabs": [
			{"upto": 13333, "amount": 0},
			{"upto": 25000, "amount": 125},
			{"upto": None, "amount": 200, "month_amounts": {3: 300}},
		],
		# Professional Tax repealed in Odisha from 1 April 2026
		"revisions": [{"effective_from": "2026-04-01", "slabs": []}],
	},
	"Sikkim": {
		"frequency": "monthly",
		"slabs": [
			{"upto": 20000, "amount": 0},
			{"upto": 30000, "amount": 125},
			{"upto": 40000, "amount": 150},
			{"upto": None, "amount": 200},
		],
	},
	"Telangana": {
		"frequency": "monthly",
		"slabs": [
			{"upto": 15000, "amount": 0},
			{"upto": 20000, "amount": 150},
			{"upto": None, "amount": 200},
		],
	},
	"Tripura": {
		"frequency": "monthly",
		"slabs": [
			{"upto": 7500, "amount": 0},
			{"upto": 15000, "amount": 150},
			{"upto": None, "amount": 208},
		],
	},
	"West Bengal": {
		"frequency": "monthly",
		"slabs": [
			{"upto": 10000, "amount": 0},
			{"upto": 15000, "amount": 110},
			{"upto": 25000, "amount": 130},
			{"upto": 40000, "amount": 150},
			{"upto": None, "amount": 200},
		],
		"revisions": [
			{
				"effective_from": "2026-10-01",
				"slabs": [
					{"upto": 20000, "amount": 0},
					{"upto": 30000, "amount": 100},
					{"upto": 50000, "amount": 140},
					{"upto": 100000, "amount": 170},
					{"upto": None, "amount": 208},
				],
			}
		],
	},
	# ------------------------------------------------------------------
	# Half-yearly states (slabs on half-yearly income)
	# ------------------------------------------------------------------
	"Tamil Nadu": {
		"frequency": "half-yearly",
		# Rates vary by local body; these are Greater Chennai Corporation's
		"slabs": [
			{"upto": 21000, "amount": 0},
			{"upto": 30000, "amount": 180},
			{"upto": 45000, "amount": 425},
			{"upto": 60000, "amount": 930},
			{"upto": 75000, "amount": 1025},
			{"upto": None, "amount": 1250},
		],
	},
	"Kerala": {
		"frequency": "half-yearly",
		"slabs": [
			{"upto": 11999, "amount": 0},
			{"upto": 17999, "amount": 320},
			{"upto": 29999, "amount": 450},
			{"upto": 44999, "amount": 600},
			{"upto": 99999, "amount": 750},
			{"upto": 124999, "amount": 1000},
			{"upto": None, "amount": 1250},
		],
	},
	# ------------------------------------------------------------------
	# Yearly states (slabs on annual income)
	# ------------------------------------------------------------------
	"Bihar": {
		"frequency": "yearly",
		"slabs": [
			{"upto": 300000, "amount": 0},
			{"upto": 500000, "amount": 1000},
			{"upto": 1000000, "amount": 2000},
			{"upto": None, "amount": 2500},
		],
	},
}


def apply_professional_tax(doc, method=None) -> None:
	"""
	Salary Slip regional deduction hook (see apply_regional_deductions).

	Computes and injects the correct Professional Tax deduction for the
	employee based on their employment state (set on Salary Structure
	Assignment).

	Only mutates ``doc.deductions``; the slip's ``set_net_pay`` (which runs
	immediately after this hook in ``calculate_net_pay``) recomputes totals.
	"""
	if not is_statutory_enabled("professional_tax", doc.company):
		_update_pt_in_salary_slip(doc, 0)
		return

	if not doc.salary_structure:
		return

	employment_state = get_slip_ssa_values(doc, ["employment_state"]).get("employment_state")

	if not employment_state or employment_state not in STATE_PT_CONFIG:
		return

	if not frappe.db.exists("Salary Component", PT_SALARY_COMPONENT):
		frappe.msgprint(
			frappe._(
				"Salary Component <b>{0}</b> not found. "
				"Please create it to enable professional tax deduction."
			).format(PT_SALARY_COMPONENT),
			indicator="orange",
			alert=True,
		)
		return

	state_config = _get_state_config(employment_state, getdate(doc.start_date))
	gender = frappe.db.get_value("Employee", doc.employee, "gender")
	payroll_month = getdate(doc.start_date).month

	if state_config["frequency"] in ("half-yearly", "yearly"):
		pt_amount = _compute_pt_periodic(doc, state_config)
	else:
		pt_amount = _compute_pt_monthly(flt(doc.gross_pay), state_config, payroll_month, gender)

	_update_pt_in_salary_slip(doc, pt_amount)


def validate_employment_state(doc, method=None) -> None:
	"""
	Salary Structure Assignment — validate hook.

	Warns when Professional Tax is enabled but the assignment has no
	employment_state set. A warning (not a hard error) is shown so that
	users can still save assignments for states not yet covered by PT rules.
	"""
	if not is_statutory_enabled("professional_tax", doc.company):
		return

	if frappe.flags.in_test:
		return

	if not doc.employment_state:
		frappe.throw(
			frappe._(
				"Employment State is not set on this Salary Structure Assignment. "
				"Professional Tax will not be deducted for {0} until an Employment State is selected."
			).format(frappe.bold(doc.employee_name or doc.employee)),
			title=frappe._("Missing Employment State"),
		)


def _get_state_config(state: str, date: datetime.date) -> dict:
	"""Return the state's configuration with the revision in force on `date` applied."""
	state_config = STATE_PT_CONFIG[state]
	revisions = [r for r in state_config.get("revisions", []) if getdate(r["effective_from"]) <= date]
	if not revisions:
		return state_config

	latest = max(revisions, key=lambda r: getdate(r["effective_from"]))
	return {**state_config, **latest}


def _compute_pt_monthly(gross_pay: float, state_config: dict, month: int, gender: str) -> float:
	"""
	Return the Professional Tax amount for a monthly-frequency state.

	Handles:
	  • Maharashtra women exemption (≤ ₹25,000 gross → exempt)
	  • Month-specific amounts (e.g. ₹300 instead of ₹200 in February)
	"""
	special_rules = state_config.get("special_rules", {})

	# Women earning up to the exemption threshold are fully exempt
	women_upto = special_rules.get("women_exemption_upto")
	if women_upto and gender == "Female" and gross_pay <= women_upto:
		return 0.0

	return _slab_amount(gross_pay, state_config["slabs"], month)


def _compute_pt_periodic(doc, state_config: dict) -> float:
	"""
	Return the Professional Tax to deduct in this salary slip for a
	half-yearly or yearly frequency state.

	Strategy (incremental deduction):
	  1. Determine the period containing the salary slip: April-September
		 or October-March for half-yearly states, April-March for yearly.
	  2. Sum gross_pay from all *submitted* slips in that period (excluding
		 the current slip, which may not be submitted yet).
	  3. Add the current slip's gross_pay to get the cumulative figure.
	  4. Apply the state's slab to that cumulative figure → total PT due.
	  5. Subtract PT already deducted in earlier slips of the same period.
	  6. The remainder is this month's PT (minimum 0).
	"""
	if state_config["frequency"] == "yearly":
		period_start, period_end = _fiscal_year_period(getdate(doc.start_date))
	else:
		period_start, period_end = _half_year_period(getdate(doc.start_date))

	prior_gross = _cumulative_gross(doc.employee, period_start, period_end, exclude=doc.name)
	cumulative_gross = flt(prior_gross) + flt(doc.gross_pay)

	total_pt_due = _slab_amount(cumulative_gross, state_config["slabs"])
	already_deducted = _cumulative_pt_deducted(doc.employee, period_start, period_end, exclude=doc.name)

	return max(0.0, flt(total_pt_due) - flt(already_deducted))


def _half_year_period(date: datetime.date) -> tuple[datetime.date, datetime.date]:
	year, month = date.year, date.month

	if 4 <= month <= 9:
		return datetime.date(year, 4, 1), datetime.date(year, 9, 30)
	elif month >= 10:
		return datetime.date(year, 10, 1), datetime.date(year + 1, 3, 31)
	else:
		return datetime.date(year - 1, 10, 1), datetime.date(year, 3, 31)


def _fiscal_year_period(date: datetime.date) -> tuple[datetime.date, datetime.date]:
	start_year = date.year if date.month >= 4 else date.year - 1
	return datetime.date(start_year, 4, 1), datetime.date(start_year + 1, 3, 31)


def _cumulative_gross(
	employee: str,
	period_start: datetime.date,
	period_end: datetime.date,
	exclude: str | None = None,
) -> float:
	"""Sum of gross_pay from submitted salary slips in the half-year period."""
	SalarySlip = frappe.qb.DocType("Salary Slip")
	query = (
		frappe.qb.from_(SalarySlip)
		.select(Sum(SalarySlip.gross_pay))
		.where(
			(SalarySlip.employee == employee)
			& (SalarySlip.docstatus == 1)
			& (SalarySlip.start_date >= period_start)
			& (SalarySlip.end_date <= period_end)
		)
	)
	if exclude:
		query = query.where(SalarySlip.name != exclude)

	result = query.run()
	return flt(result[0][0]) if result and result[0][0] else 0.0


def _cumulative_pt_deducted(
	employee: str,
	period_start: datetime.date,
	period_end: datetime.date,
	exclude: str | None = None,
) -> float:
	"""Sum of Professional Tax already deducted in submitted slips this period."""
	SalarySlip = frappe.qb.DocType("Salary Slip")
	SalaryDetail = frappe.qb.DocType("Salary Detail")
	query = (
		frappe.qb.from_(SalarySlip)
		.join(SalaryDetail)
		.on(SalarySlip.name == SalaryDetail.parent)
		.select(Sum(SalaryDetail.amount))
		.where(
			(SalarySlip.employee == employee)
			& (SalarySlip.docstatus == 1)
			& (SalarySlip.start_date >= period_start)
			& (SalarySlip.end_date <= period_end)
			& (SalaryDetail.salary_component == PT_SALARY_COMPONENT)
			& (SalaryDetail.parentfield == "deductions")
		)
	)
	if exclude:
		query = query.where(SalarySlip.name != exclude)

	result = query.run()
	return flt(result[0][0]) if result and result[0][0] else 0.0


def _update_pt_in_salary_slip(doc, pt_amount: float) -> None:
	"""
	Remove any existing Professional Tax deduction row and add a fresh one
	for `pt_amount`.

	Only mutates ``doc.deductions``; the slip's ``set_net_pay`` (which runs
	immediately after this hook in ``calculate_net_pay``) recomputes totals.
	"""
	# Remove any existing PT row (e.g. from a previous save or manual entry)
	doc.deductions = [d for d in doc.deductions if d.salary_component != PT_SALARY_COMPONENT]

	if pt_amount > 0:
		doc.append(
			"deductions",
			{
				"salary_component": PT_SALARY_COMPONENT,
				"amount": flt(pt_amount),
			},
		)


def _slab_amount(salary: float, slabs: list[dict], month: int | None = None) -> float:
	"""
	Return the PT amount for `salary` by scanning slabs in order.
	The first slab whose `upto` is None (unbounded) or >= salary is selected.
	If the slab defines a different amount for `month`, that amount is returned.
	"""
	for slab in slabs:
		if slab["upto"] is None or flt(salary) <= slab["upto"]:
			return flt(slab.get("month_amounts", {}).get(month, slab["amount"]))
	return 0.0
