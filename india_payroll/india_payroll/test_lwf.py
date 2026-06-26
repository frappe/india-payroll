# Copyright (c) 2026, Frappe Technologies Pvt. Ltd. and contributors
# License: GNU General Public License v3. See license.txt

import frappe
from erpnext.setup.doctype.employee.test_employee import make_employee
from frappe.utils import flt
from hrms.payroll.doctype.salary_slip.test_salary_slip import make_salary_component
from hrms.payroll.doctype.salary_structure.salary_structure import make_salary_slip
from hrms.payroll.doctype.salary_structure.test_salary_structure import (
	create_salary_structure_assignment,
	make_salary_structure,
)
from hrms.tests.utils import HRMSTestSuite

from india_payroll.india_payroll.lwf import LWF_SALARY_COMPONENT, LWF_STATE_CONFIG
from india_payroll.install import create_lwf_component

_LWF_BASIC_COMPONENT = "LWF Test Basic"
_LWF_TEST_EARNINGS = [
	{
		"salary_component": _LWF_BASIC_COMPONENT,
		"abbr": "LWFB",
		"formula": "base",
		"type": "Earning",
		"amount_based_on_formula": 1,
		"depends_on_payment_days": 0,
	}
]

# Test employee emails — one per test to avoid cross-test contamination
_TEST_EMAILS = [
	"test_lwf_monthly@indiapayroll.com",
	"test_lwf_halfyearly_june@indiapayroll.com",
	"test_lwf_halfyearly_non_month@indiapayroll.com",
	"test_lwf_annual_dec@indiapayroll.com",
	"test_lwf_annual_non_month@indiapayroll.com",
	"test_lwf_exempted@indiapayroll.com",
	"test_lwf_disabled_setting@indiapayroll.com",
	"test_lwf_no_lwf_state@indiapayroll.com",
	"test_lwf_net_pay@indiapayroll.com",
]


class TestLWF(HRMSTestSuite):
	def setUp(self):
		create_lwf_component()
		self._ensure_lwf_test_component()
		self._cleanup()

	def _ensure_lwf_test_component(self):
		"""Create the LWF-specific test salary component if absent."""
		if not frappe.db.exists("Salary Component", _LWF_BASIC_COMPONENT):
			make_salary_component(_LWF_TEST_EARNINGS, False, ["_Test Company"])

	def _cleanup(self):
		"""Delete salary slips and SSAs for all test employees so tests are re-runnable."""
		for email in _TEST_EMAILS:
			frappe.db.delete("Salary Slip", {"employee_name": email})
			emp = frappe.db.get_value("Employee", {"employee_name": email}, "name")
			if emp:
				frappe.db.delete("Salary Structure Assignment", {"employee": emp})

	def _make_salary_slip(
		self,
		email: str,
		structure_name: str,
		gross_pay: float,
		employment_state: str,
		posting_date: str = "2026-04-01",
		start_date: str = "2026-04-01",
		end_date: str = "2026-04-30",
	):
		"""
		Create a salary slip with gross_pay == `gross_pay` in `employment_state`.

		The SSA employment_state is set after creation (same pattern as PT tests)
		so that the LWF hook can resolve the correct state config.

		Deductions are omitted from the salary structure so that PT and ESI
		hooks cannot interfere with net_pay assertions in LWF tests.
		"""
		employee = make_employee(email, company="_Test Company")

		salary_structure = make_salary_structure(
			structure_name,
			"Monthly",
			company="_Test Company",
			currency="INR",
			earnings=_LWF_TEST_EARNINGS,
			deductions=[],
		)

		ssa = create_salary_structure_assignment(
			employee,
			salary_structure.name,
			from_date=start_date,
			company="_Test Company",
			base=gross_pay,
		)
		frappe.db.set_value("Salary Structure Assignment", ssa.name, "employment_state", employment_state)

		salary_slip = make_salary_slip(
			salary_structure.name,
			employee=employee,
			posting_date=posting_date,
		)
		salary_slip.start_date = start_date
		salary_slip.end_date = end_date

		return employee, salary_slip

	def _set_ssa(self, employee: str, values: dict) -> str:
		"""Set India Payroll statutory config on the employee's salary structure assignment."""
		ssa = frappe.db.get_value(
			"Salary Structure Assignment", {"employee": employee}, "name", order_by="from_date desc"
		)
		frappe.db.set_value("Salary Structure Assignment", ssa, values)
		return ssa

	@HRMSTestSuite.change_settings(
		"Payroll Settings",
		{"enable_lwf": 1, "enable_professional_tax": 0, "enable_esic": 0},
	)
	def test_monthly_state_deducts_every_month(self):
		"""
		Haryana is a monthly-frequency state (₹34/employee/month).
		LWF must be deducted in any calendar month — here April.
		"""
		state = "Haryana"
		expected = flt(LWF_STATE_CONFIG[state]["employee"], 2)  # 34.0

		_, salary_slip = self._make_salary_slip(
			"test_lwf_monthly@indiapayroll.com",
			"Test LWF Monthly Structure",
			gross_pay=30_000.0,
			employment_state=state,
			posting_date="2026-04-01",
			start_date="2026-04-01",
			end_date="2026-04-30",
		)
		salary_slip.insert()

		lwf_rows = [d for d in salary_slip.deductions if d.salary_component == LWF_SALARY_COMPONENT]
		self.assertEqual(len(lwf_rows), 1, "Haryana (monthly) must have one LWF row in April")
		self.assertAlmostEqual(lwf_rows[0].amount, expected, places=2)

	@HRMSTestSuite.change_settings(
		"Payroll Settings",
		{"enable_lwf": 1, "enable_professional_tax": 0, "enable_esic": 0},
	)
	def test_half_yearly_state_deducts_in_june(self):
		"""
		Maharashtra is a half-yearly state (₹25/employee per period).
		LWF must be deducted in June.
		"""
		state = "Maharashtra"
		expected = flt(LWF_STATE_CONFIG[state]["employee"], 2)  # 25.0

		_, salary_slip = self._make_salary_slip(
			"test_lwf_halfyearly_june@indiapayroll.com",
			"Test LWF Half Yearly June Structure",
			gross_pay=30_000.0,
			employment_state=state,
			posting_date="2026-06-01",
			start_date="2026-06-01",
			end_date="2026-06-30",
		)
		salary_slip.insert()

		lwf_rows = [d for d in salary_slip.deductions if d.salary_component == LWF_SALARY_COMPONENT]
		self.assertEqual(len(lwf_rows), 1, "Maharashtra (half-yearly) must have one LWF row in June")
		self.assertAlmostEqual(lwf_rows[0].amount, expected, places=2)

	@HRMSTestSuite.change_settings(
		"Payroll Settings",
		{"enable_lwf": 1, "enable_professional_tax": 0, "enable_esic": 0},
	)
	def test_half_yearly_state_skips_non_deduction_month(self):
		"""
		Maharashtra is half-yearly (June + December only).
		No LWF must be deducted in April (a non-deduction month).
		"""
		_, salary_slip = self._make_salary_slip(
			"test_lwf_halfyearly_non_month@indiapayroll.com",
			"Test LWF Half Yearly Non Month Structure",
			gross_pay=30_000.0,
			employment_state="Maharashtra",
			posting_date="2026-04-01",
			start_date="2026-04-01",
			end_date="2026-04-30",
		)
		salary_slip.insert()

		lwf_rows = [d for d in salary_slip.deductions if d.salary_component == LWF_SALARY_COMPONENT]
		self.assertEqual(len(lwf_rows), 0, "Maharashtra (half-yearly) must NOT have LWF in April")

	@HRMSTestSuite.change_settings(
		"Payroll Settings",
		{"enable_lwf": 1, "enable_professional_tax": 0, "enable_esic": 0},
	)
	def test_annual_state_deducts_in_december(self):
		"""
		Karnataka is an annual state (₹50/employee per year, deducted in December).
		LWF must be deducted in December.
		"""
		state = "Karnataka"
		expected = flt(LWF_STATE_CONFIG[state]["employee"], 2)  # 50.0

		_, salary_slip = self._make_salary_slip(
			"test_lwf_annual_dec@indiapayroll.com",
			"Test LWF Annual Dec Structure",
			gross_pay=30_000.0,
			employment_state=state,
			posting_date="2026-12-01",
			start_date="2026-12-01",
			end_date="2026-12-31",
		)
		salary_slip.insert()

		lwf_rows = [d for d in salary_slip.deductions if d.salary_component == LWF_SALARY_COMPONENT]
		self.assertEqual(len(lwf_rows), 1, "Karnataka (annual) must have one LWF row in December")
		self.assertAlmostEqual(lwf_rows[0].amount, expected, places=2)

	@HRMSTestSuite.change_settings(
		"Payroll Settings",
		{"enable_lwf": 1, "enable_professional_tax": 0, "enable_esic": 0},
	)
	def test_annual_state_skips_non_deduction_month(self):
		"""
		Karnataka is an annual state (December only).
		No LWF must be deducted in November.
		"""
		_, salary_slip = self._make_salary_slip(
			"test_lwf_annual_non_month@indiapayroll.com",
			"Test LWF Annual Non Month Structure",
			gross_pay=30_000.0,
			employment_state="Karnataka",
			posting_date="2026-11-01",
			start_date="2026-11-01",
			end_date="2026-11-30",
		)
		salary_slip.insert()

		lwf_rows = [d for d in salary_slip.deductions if d.salary_component == LWF_SALARY_COMPONENT]
		self.assertEqual(len(lwf_rows), 0, "Karnataka (annual) must NOT have LWF in November")

	@HRMSTestSuite.change_settings(
		"Payroll Settings",
		{"enable_lwf": 1, "enable_professional_tax": 0, "enable_esic": 0},
	)
	def test_exempted_employee_no_lwf(self):
		"""
		An employee with lwf_exempted=1 must not have LWF deducted even in a
		valid deduction month for a monthly state.
		"""
		employee, salary_slip = self._make_salary_slip(
			"test_lwf_exempted@indiapayroll.com",
			"Test LWF Exempted Structure",
			gross_pay=30_000.0,
			employment_state="Haryana",
			posting_date="2026-04-01",
			start_date="2026-04-01",
			end_date="2026-04-30",
		)
		self._set_ssa(employee, {"lwf_exempted": 1})

		salary_slip.insert()

		lwf_rows = [d for d in salary_slip.deductions if d.salary_component == LWF_SALARY_COMPONENT]
		self.assertEqual(len(lwf_rows), 0, "Manually exempted employee must not have LWF rows")

	@HRMSTestSuite.change_settings(
		"Payroll Settings",
		{"enable_lwf": 0, "enable_professional_tax": 0, "enable_esic": 0},
	)
	def test_lwf_disabled_setting_no_deduction(self):
		"""
		When LWF is disabled in Payroll Settings, no LWF deduction must appear
		on the salary slip even for an eligible state and valid deduction month.
		"""
		_, salary_slip = self._make_salary_slip(
			"test_lwf_disabled_setting@indiapayroll.com",
			"Test LWF Disabled Setting Structure",
			gross_pay=30_000.0,
			employment_state="Haryana",
			posting_date="2026-04-01",
			start_date="2026-04-01",
			end_date="2026-04-30",
		)
		salary_slip.insert()

		lwf_rows = [d for d in salary_slip.deductions if d.salary_component == LWF_SALARY_COMPONENT]
		self.assertEqual(len(lwf_rows), 0, "LWF must not be deducted when enable_lwf is disabled")

	@HRMSTestSuite.change_settings(
		"Payroll Settings",
		{"enable_lwf": 1, "enable_professional_tax": 0, "enable_esic": 0},
	)
	def test_no_lwf_state_no_deduction(self):
		"""
		An employee working in Uttar Pradesh (no LWF Act) must not have any
		LWF deduction regardless of the month.
		"""
		_, salary_slip = self._make_salary_slip(
			"test_lwf_no_lwf_state@indiapayroll.com",
			"Test LWF No State Structure",
			gross_pay=30_000.0,
			employment_state="Uttar Pradesh",
			posting_date="2026-04-01",
			start_date="2026-04-01",
			end_date="2026-04-30",
		)
		salary_slip.insert()

		lwf_rows = [d for d in salary_slip.deductions if d.salary_component == LWF_SALARY_COMPONENT]
		self.assertEqual(len(lwf_rows), 0, "States without LWF must not have LWF rows")

	@HRMSTestSuite.change_settings(
		"Payroll Settings",
		{"enable_lwf": 1, "enable_professional_tax": 0, "enable_esic": 0},
	)
	def test_net_pay_reduced_by_lwf(self):
		"""
		For a Kerala employee (monthly, ₹50), net_pay must be reduced by exactly
		₹50 from gross_pay (with no other deductions in the test structure).
		"""
		state = "Kerala"
		gross = 40_000.0
		lwf_amount = flt(LWF_STATE_CONFIG[state]["employee"], 2)  # 50.0

		_, salary_slip = self._make_salary_slip(
			"test_lwf_net_pay@indiapayroll.com",
			"Test LWF Net Pay Structure",
			gross_pay=gross,
			employment_state=state,
			posting_date="2026-04-01",
			start_date="2026-04-01",
			end_date="2026-04-30",
		)
		salary_slip.insert()

		expected_net = gross - lwf_amount  # 39,950.0
		self.assertAlmostEqual(
			flt(salary_slip.net_pay),
			expected_net,
			places=2,
			msg="net_pay must be gross_pay minus the LWF deduction",
		)
