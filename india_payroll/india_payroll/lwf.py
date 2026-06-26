# Copyright (c) 2026, Frappe Technologies Pvt. Ltd. and contributors
# License: GNU General Public License v3. See license.txt

import frappe
from frappe.utils import flt, getdate

from india_payroll.india_payroll.utils import get_slip_ssa_values

LWF_SALARY_COMPONENT = "Labour Welfare Fund"

# State-wise Labour Welfare Fund configuration (as of May 2026)
# Update this dict when the government notifies new rates and redeploy.

LWF_STATE_CONFIG = {
	# --- Monthly states ---
	"Haryana": {"employee": 34, "employer": 68, "frequency": "monthly"},
	"Kerala": {"employee": 50, "employer": 50, "frequency": "monthly"},
	"Punjab": {"employee": 5, "employer": 20, "frequency": "monthly"},
	"Chandigarh": {"employee": 5, "employer": 20, "frequency": "monthly"},
	# --- Half-yearly states (June + December) ---
	"Chhattisgarh": {"employee": 15, "employer": 45, "frequency": "half-yearly"},
	"Delhi": {"employee": 0.75, "employer": 2.25, "frequency": "half-yearly"},
	"Goa": {"employee": 60, "employer": 180, "frequency": "half-yearly"},
	"Gujarat": {"employee": 6, "employer": 12, "frequency": "half-yearly"},
	"Madhya Pradesh": {"employee": 10, "employer": 30, "frequency": "half-yearly"},
	"Maharashtra": {"employee": 25, "employer": 75, "frequency": "half-yearly"},
	"Odisha": {"employee": 20, "employer": 40, "frequency": "half-yearly"},
	"West Bengal": {"employee": 3, "employer": 30, "frequency": "half-yearly"},
	# --- Annual states (December only) ---
	"Andhra Pradesh": {"employee": 30, "employer": 70, "frequency": "annual"},
	"Karnataka": {"employee": 50, "employer": 100, "frequency": "annual"},
	"Tamil Nadu": {"employee": 20, "employer": 40, "frequency": "annual"},
	"Telangana": {"employee": 2, "employer": 5, "frequency": "annual"},
}

_HALF_YEARLY_MONTHS = frozenset({6, 12})  # June, December
_ANNUAL_MONTHS = frozenset({12})  # December only


def apply_lwf(doc, method=None) -> None:
	"""
	Salary Slip regional deduction hook (see apply_regional_deductions).

	Injects or removes the Labour Welfare Fund deduction based on:
	  • Whether LWF is enabled in Payroll Settings
	  • The employee's work state (from Salary Structure Assignment)
	  • The deduction month rule for that state's frequency
	  • Whether the employee is manually exempted (lwf_exempted field)
	"""
	if not frappe.db.get_single_value("Payroll Settings", "enable_lwf"):
		return

	if not doc.salary_structure:
		return

	if not frappe.db.exists("Salary Component", LWF_SALARY_COMPONENT):
		frappe.msgprint(
			frappe._(
				"Salary Component <b>{0}</b> not found. "
				"Please reinstall the India Payroll app or create it manually."
			).format(LWF_SALARY_COMPONENT),
			indicator="orange",
			alert=True,
		)
		return

	ssa = get_slip_ssa_values(doc, ["employment_state", "lwf_exempted"])
	employment_state = ssa.get("employment_state")

	if not employment_state or employment_state not in LWF_STATE_CONFIG:
		# State has no LWF — remove any stale row from previous state
		_remove_lwf_component(doc)
		return

	# Honour manual HR exemption set on the assignment
	if ssa.get("lwf_exempted"):
		_remove_lwf_component(doc)
		return

	state_config = LWF_STATE_CONFIG[employment_state]
	payroll_month = getdate(doc.start_date).month

	if not _is_deduction_month(state_config["frequency"], payroll_month):
		# Not a deduction month — remove any stale row
		_remove_lwf_component(doc)
		return

	employee_amount = flt(state_config["employee"], 2)
	_update_lwf_in_salary_slip(doc, employee_amount)


def _is_deduction_month(frequency: str, month: int) -> bool:
	"""Return True if `month` is a valid LWF deduction month for `frequency`."""
	if frequency == "monthly":
		return True
	if frequency == "half-yearly":
		return month in _HALF_YEARLY_MONTHS
	if frequency == "annual":
		return month in _ANNUAL_MONTHS
	return False


def _remove_lwf_component(doc) -> None:
	"""Remove the LWF salary component from deductions."""
	doc.deductions = [d for d in doc.deductions if d.salary_component != LWF_SALARY_COMPONENT]


def _update_lwf_in_salary_slip(doc, employee_amount: float) -> None:
	"""Replace any existing LWF deduction row with the flat amount for this period."""
	# Remove stale row first to avoid duplicates
	doc.deductions = [d for d in doc.deductions if d.salary_component != LWF_SALARY_COMPONENT]

	if employee_amount > 0:
		doc.append(
			"deductions",
			{
				"salary_component": LWF_SALARY_COMPONENT,
				"amount": employee_amount,
			},
		)
