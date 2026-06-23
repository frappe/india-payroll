import datetime

import frappe
from frappe.query_builder.functions import Sum
from frappe.utils import flt, getdate

PT_SALARY_COMPONENT = "Professional Tax"

# ---------------------------------------------------------------------------
# State-wise Professional Tax configuration
#
# Each entry supports:
#   frequency   : "monthly" | "half-yearly"
#   slabs       : ordered list of {"upto": int_or_None, "amount": int}
#                 Slabs are evaluated in order; the first whose `upto` value
#                 is >= gross salary (or is None, meaning "above all") wins.
#   special_rules (optional):
#     february_amount      : amount charged in February (Maharashtra ₹300 rule)
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
			{"upto": 10000, "amount": 0},
			{"upto": 15000, "amount": 150},
			{"upto": 25000, "amount": 180},
			{"upto": None, "amount": 208},
		],
	},
	"Bihar": {
		"frequency": "monthly",
		"slabs": [
			{"upto": 25000, "amount": 0},
			{"upto": None, "amount": 200},
		],
	},
	"Gujarat": {
		"frequency": "monthly",
		"slabs": [
			{"upto": 5999, "amount": 0},
			{"upto": 8999, "amount": 80},
			{"upto": 11999, "amount": 150},
			{"upto": None, "amount": 200},
		],
	},
	"Jharkhand": {
		"frequency": "monthly",
		"slabs": [
			{"upto": 25000, "amount": 0},
			{"upto": None, "amount": 100},
		],
	},
	"Karnataka": {
		"frequency": "monthly",
		"slabs": [
			{"upto": 25000, "amount": 0},
			{"upto": 41666, "amount": 150},
			{"upto": None, "amount": 200},
		],
	},
	"Madhya Pradesh": {
		"frequency": "monthly",
		"slabs": [
			{"upto": 18750, "amount": 0},
			{"upto": 25000, "amount": 125},
			{"upto": 33333, "amount": 167},
			{"upto": None, "amount": 208},
		],
	},
	"Maharashtra": {
		"frequency": "monthly",
		# Slabs represent normal (non-February) amounts.
		# For salaries > ₹10,000 the February rule applies (see special_rules).
		# Women earning ≤ ₹10,000/month are fully exempt.
		"slabs": [
			{"upto": 7500, "amount": 0},
			{"upto": 10000, "amount": 175},
			{"upto": None, "amount": 200},
		],
		"special_rules": {
			# ₹300 replaces ₹200 for the highest slab in February so that
			# the annual total reaches the ₹2,500 constitutional cap:
			# 11 months * ₹200 + February * ₹300 = ₹2,500
			"february_amount": 300,
			# Women earning up to this monthly gross are fully exempt
			"women_exemption_upto": 10000,
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
			{"upto": None, "amount": 208},
		],
	},
	"Odisha": {
		"frequency": "monthly",
		"slabs": [
			{"upto": 13304, "amount": 0},
			{"upto": 25000, "amount": 125},
			{"upto": 41666, "amount": 167},
			{"upto": None, "amount": 208},
		],
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
			{"upto": 8500, "amount": 0},
			{"upto": 10000, "amount": 90},
			{"upto": 15000, "amount": 110},
			{"upto": 25000, "amount": 130},
			{"upto": 40000, "amount": 150},
			{"upto": None, "amount": 200},
		],
	},
	"Tamil Nadu": {
		"frequency": "half-yearly",
		"slabs": [
			{"upto": 21000, "amount": 0},
			{"upto": 30000, "amount": 135},
			{"upto": 45000, "amount": 315},
			{"upto": 60000, "amount": 690},
			{"upto": 75000, "amount": 1025},
			{"upto": None, "amount": 1250},
		],
	},
	"Kerala": {
		"frequency": "half-yearly",
		"slabs": [
			{"upto": 11999, "amount": 0},
			{"upto": 17999, "amount": 120},
			{"upto": 29999, "amount": 180},
			{"upto": 44999, "amount": 480},
			{"upto": 59999, "amount": 720},
			{"upto": 74999, "amount": 1080},
			{"upto": 99999, "amount": 1440},
			{"upto": None, "amount": 1560},
		],
	},
}


def apply_professional_tax(doc, method=None) -> None:
	"""
	Salary Slip — before_save hook.

	Computes and injects the correct Professional Tax deduction for the
	employee based on their employment state (set on Salary Structure
	Assignment).  Recalculates total_deduction and net_pay after injection.
	"""
	if not frappe.db.get_single_value("Payroll Settings", "enable_professional_tax"):
		return

	if not doc.salary_structure:
		return

	employment_state = _get_employment_state(doc)

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

	state_config = STATE_PT_CONFIG[employment_state]
	gender = frappe.db.get_value("Employee", doc.employee, "gender")
	payroll_month = getdate(doc.start_date).month

	if state_config["frequency"] == "half-yearly":
		pt_amount = _compute_pt_half_yearly(doc, state_config)
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
	if not frappe.db.get_single_value("Payroll Settings", "enable_professional_tax"):
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


def _get_employment_state(doc) -> str | None:
	"""
	Return the employment_state from the given Salary Structure Assignment.
	"""

	return frappe.db.get_value(
		"Salary Structure Assignment",
		filters={
			"employee": doc.employee,
			"company": doc.company,
			"from_date": ("<=", doc.start_date),
			"salary_structure": doc.salary_structure,
		},
		fieldname="employment_state",
	)


def _compute_pt_monthly(gross_pay: float, state_config: dict, month: int, gender: str) -> float:
	"""
	Return the Professional Tax amount for a monthly-frequency state.

	Handles:
	  • Maharashtra women exemption (≤ ₹10,000 gross → exempt)
	  • Maharashtra February rule (₹300 instead of ₹200 for highest slab)
	"""
	special_rules = state_config.get("special_rules", {})

	# Women earning up to the exemption threshold are fully exempt
	women_upto = special_rules.get("women_exemption_upto")
	if women_upto and gender == "Female" and gross_pay <= women_upto:
		return 0.0

	pt_amount = _slab_amount(gross_pay, state_config["slabs"])

	# Maharashtra February rule: employees in the highest slab pay ₹300
	# instead of ₹200 so that the annual total equals ₹2,500.
	feb_amount = special_rules.get("february_amount")
	if feb_amount and month == 2 and _is_highest_slab(gross_pay, state_config["slabs"]):
		pt_amount = feb_amount

	return flt(pt_amount)


def _compute_pt_half_yearly(doc, state_config: dict) -> float:
	"""
	Return the Professional Tax to deduct in this salary slip for a
	half-yearly-frequency state.

	Strategy (incremental deduction):
	  1. Determine the April-September or October-March period containing
		 the salary slip.
	  2. Sum gross_pay from all *submitted* slips in that period (excluding
		 the current slip, which may not be submitted yet).
	  3. Add the current slip's gross_pay to get the cumulative figure.
	  4. Apply the state's slab to that cumulative figure → total PT due.
	  5. Subtract PT already deducted in earlier slips of the same period.
	  6. The remainder is this month's PT (minimum 0).
	"""
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
	for `pt_amount`.  Then recalculate total_deduction and net_pay so that
	downstream processing sees correct figures.
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

	# Recalculate running totals
	doc.total_deduction = sum(flt(d.amount) for d in doc.deductions if not d.do_not_include_in_total)
	# Loan repayment is tracked outside `deductions` (see Salary Slip.set_net_pay),
	# so subtract it explicitly or net pay comes out too high.
	doc.net_pay = flt(doc.gross_pay) - (flt(doc.total_deduction) + flt(doc.get("total_loan_repayment")))
	if hasattr(doc, "rounded_total"):
		doc.rounded_total = round(doc.net_pay)


def _slab_amount(salary: float, slabs: list[dict]) -> float:
	"""
	Return the PT amount for `salary` by scanning slabs in order.
	The first slab whose `upto` is None (unbounded) or >= salary is selected.
	"""
	for slab in slabs:
		if slab["upto"] is None or flt(salary) <= slab["upto"]:
			return flt(slab["amount"])
	return 0.0


def _is_highest_slab(salary: float, slabs: list[dict]) -> bool:
	"""
	Return True if `salary` falls in the last (unbounded) slab.
	Used to apply the Maharashtra February rule only to the ₹200 slab,
	not to the ₹175 or ₹0 slabs.
	"""
	bounded = [s for s in slabs if s["upto"] is not None]
	return bool(bounded) and flt(salary) > bounded[-1]["upto"]
