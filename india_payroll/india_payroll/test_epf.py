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

from india_payroll.india_payroll.epf import (
	EPF_EMPLOYEE_COMPONENT,
	EPF_WAGE_CEILING,
	VPF_COMPONENT,
)
from india_payroll.install import create_epf_components

# The earning component used as Basic across all EPF tests. Every earning
# component now counts toward PF wage; formula `base` keeps gross_pay equal to
# the SSA base for predictable assertions.
_EPF_BASIC_COMPONENT = "EPF Test Basic"
_EPF_TEST_EARNINGS = [
	{
		"salary_component": _EPF_BASIC_COMPONENT,
		"abbr": "EPFTB",
		"formula": "base",
		"type": "Earning",
		"amount_based_on_formula": 1,
		"depends_on_payment_days": 0,
	}
]

_TEST_EMAILS = [
	"test_epf_below_ceiling@indiapayroll.com",
	"test_epf_all_earnings@indiapayroll.com",
	"test_epf_above_ceiling_capped@indiapayroll.com",
	"test_epf_above_ceiling_actual@indiapayroll.com",
	"test_epf_vpf@indiapayroll.com",
	"test_epf_vpf_amount@indiapayroll.com",
	"test_epf_vpf_amount_lop@indiapayroll.com",
	"test_epf_not_applicable@indiapayroll.com",
	"test_epf_disabled_setting@indiapayroll.com",
	"test_epf_net_pay@indiapayroll.com",
	"test_epf_preview@indiapayroll.com",
]


class TestEPF(HRMSTestSuite):
	"""
	Covers the employee-side EPF deduction injected by ``apply_epf`` onto the
	salary slip.  Employer EPF / EPS / EDLI / Admin are now "Employer
	Contribution" components evaluated by Salary Structure Assignment into
	CTC — they don't appear on the slip and are not exercised here.
	"""

	def setUp(self):
		create_epf_components()
		self._ensure_epf_test_component()
		self._cleanup()

	def _ensure_epf_test_component(self):
		"""Create the EPF-specific basic earning component if absent."""
		if not frappe.db.exists("Salary Component", _EPF_BASIC_COMPONENT):
			make_salary_component(_EPF_TEST_EARNINGS, False, ["_Test Company"])

	def _cleanup(self):
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
		*,
		epf_applicable: bool = True,
		posting_date: str = "2026-04-01",
		start_date: str = "2026-04-01",
		end_date: str = "2026-04-30",
	):
		"""
		Create a salary slip whose Basic (PF-eligible) equals `gross_pay`.

		Sets epf_applicable on the Salary Structure Assignment so the hook treats
		the employee as opted into EPF deduction.
		"""
		employee = make_employee(email, company="_Test Company")

		salary_structure = make_salary_structure(
			structure_name,
			"Monthly",
			company="_Test Company",
			currency="INR",
			earnings=_EPF_TEST_EARNINGS,
			deductions=[],
		)

		ssa = create_salary_structure_assignment(
			employee,
			salary_structure.name,
			from_date=start_date,
			company="_Test Company",
			base=gross_pay,
		)
		# EPF opt-in now lives on the assignment, not the Employee master.
		frappe.db.set_value(
			"Salary Structure Assignment", ssa.name, "epf_applicable", 1 if epf_applicable else 0
		)

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

	@staticmethod
	def _amount(slip, table: str, component: str) -> float:
		row = next(
			(r for r in getattr(slip, table) if r.salary_component == component),
			None,
		)
		return flt(row.amount) if row else 0.0

	@HRMSTestSuite.change_settings(
		"Payroll Settings",
		{"enable_epf": 1, "enable_professional_tax": 0, "enable_esic": 0, "enable_lwf": 0},
	)
	def test_at_ceiling_standard_12_percent(self):
		"""PF wage ₹15,000 (= ceiling) should deduct ₹1,800 (12%)."""
		gross = float(EPF_WAGE_CEILING)
		_, slip = self._make_salary_slip(
			"test_epf_below_ceiling@indiapayroll.com",
			"Test EPF At Ceiling Structure",
			gross,
		)
		slip.insert()

		self.assertEqual(self._amount(slip, "deductions", EPF_EMPLOYEE_COMPONENT), 1_800)

	@HRMSTestSuite.change_settings(
		"Payroll Settings",
		{"enable_epf": 1, "enable_professional_tax": 0, "enable_esic": 0, "enable_lwf": 0},
	)
	def test_above_ceiling_default_caps_at_15000(self):
		"""
		PF wage ₹25,000 with `contribute_on_actual_pf_wage` unset (default).
		Employee EPF capped at ₹15,000 → ₹1,800.
		"""
		gross = 25_000.0
		_, slip = self._make_salary_slip(
			"test_epf_above_ceiling_capped@indiapayroll.com",
			"Test EPF Above Ceiling Capped Structure",
			gross,
		)
		slip.insert()

		self.assertEqual(self._amount(slip, "deductions", EPF_EMPLOYEE_COMPONENT), 1_800)

	@HRMSTestSuite.change_settings(
		"Payroll Settings",
		{"enable_epf": 1, "enable_professional_tax": 0, "enable_esic": 0, "enable_lwf": 0},
	)
	def test_above_ceiling_contribute_on_actual(self):
		"""
		PF wage ₹25,000 with `contribute_on_actual_pf_wage = 1`.
		Employee EPF: 12% * 25,000 = ₹3,000.
		"""
		gross = 25_000.0
		employee, slip = self._make_salary_slip(
			"test_epf_above_ceiling_actual@indiapayroll.com",
			"Test EPF Above Ceiling Actual Structure",
			gross,
		)
		self._set_ssa(employee, {"contribute_on_actual_pf_wage": 1})
		slip.insert()

		self.assertEqual(self._amount(slip, "deductions", EPF_EMPLOYEE_COMPONENT), 3_000)

	@HRMSTestSuite.change_settings(
		"Payroll Settings",
		{"enable_epf": 1, "enable_professional_tax": 0, "enable_esic": 0, "enable_lwf": 0},
	)
	def test_vpf_percentage_mode(self):
		"""
		vpf_mode = Percentage, vpf_percentage = 5: 5% extra on the EPF base.
		Employee EPF: 12% * 15,000 = ₹1,800
		VPF:          5% * 15,000 = ₹750
		"""
		gross = float(EPF_WAGE_CEILING)
		employee, slip = self._make_salary_slip(
			"test_epf_vpf@indiapayroll.com",
			"Test EPF VPF Structure",
			gross,
		)
		self._set_ssa(employee, {"vpf_mode": "Percentage", "vpf_percentage": 5})
		slip.insert()

		self.assertEqual(self._amount(slip, "deductions", EPF_EMPLOYEE_COMPONENT), 1_800)
		self.assertEqual(self._amount(slip, "deductions", VPF_COMPONENT), 750)

	@HRMSTestSuite.change_settings(
		"Payroll Settings",
		{"enable_epf": 1, "enable_professional_tax": 0, "enable_esic": 0, "enable_lwf": 0},
	)
	def test_vpf_amount_mode(self):
		"""
		vpf_mode = Amount, vpf_amount = ₹2,000: fixed lumpsum deduction
		regardless of PF wage. vpf_percentage is ignored.
		"""
		gross = float(EPF_WAGE_CEILING)
		employee, slip = self._make_salary_slip(
			"test_epf_vpf_amount@indiapayroll.com",
			"Test EPF VPF Amount Structure",
			gross,
		)
		self._set_ssa(employee, {"vpf_mode": "Amount", "vpf_amount": 2_000, "vpf_percentage": 5})
		slip.insert()

		self.assertEqual(self._amount(slip, "deductions", EPF_EMPLOYEE_COMPONENT), 1_800)
		self.assertEqual(self._amount(slip, "deductions", VPF_COMPONENT), 2_000)

	def test_vpf_amount_mode_prorates_on_lop(self):
		"""
		Amount mode prorates the elected monthly lumpsum by
		payment_days / total_working_days so LOP months don't over-deduct.

		Tested as a unit against ``_compute_vpf`` so we can dictate
		payment_days / total_working_days without staging an attendance
		fixture; the slip-side proration of earnings is exercised separately
		by Percentage mode tests.
		"""
		from india_payroll.india_payroll.epf import _compute_vpf

		vpf_args = {"vpf_mode": "Amount", "vpf_amount": 3_000}

		# Full month: no proration.
		full = frappe._dict(payment_days=30, total_working_days=30)
		self.assertEqual(_compute_vpf(full, 15_000, **vpf_args), 3_000)

		# 5 LOP days in a 30-day month → 25/30 * 3000 = 2500.
		lop = frappe._dict(payment_days=25, total_working_days=30)
		self.assertEqual(_compute_vpf(lop, 15_000, **vpf_args), 2_500)

		# Fractional payment_days (half-day LOP) — 24.5/30 * 3000 = 2450.
		half_day = frappe._dict(payment_days=24.5, total_working_days=30)
		self.assertEqual(_compute_vpf(half_day, 15_000, **vpf_args), 2_450)

	@HRMSTestSuite.change_settings(
		"Payroll Settings",
		{"enable_epf": 1, "enable_professional_tax": 0, "enable_esic": 0, "enable_lwf": 0},
	)
	def test_not_applicable_no_epf_rows(self):
		"""
		Employee.epf_applicable = 0 must suppress the employee EPF deduction,
		even when EPF is enabled in Payroll Settings.
		"""
		_, slip = self._make_salary_slip(
			"test_epf_not_applicable@indiapayroll.com",
			"Test EPF Not Applicable Structure",
			15_000.0,
			epf_applicable=False,
		)
		slip.insert()

		self.assertEqual(self._amount(slip, "deductions", EPF_EMPLOYEE_COMPONENT), 0)
		self.assertEqual(self._amount(slip, "deductions", VPF_COMPONENT), 0)

	@HRMSTestSuite.change_settings(
		"Payroll Settings",
		{"enable_epf": 0, "enable_professional_tax": 0, "enable_esic": 0, "enable_lwf": 0},
	)
	def test_disabled_setting_no_epf_rows(self):
		"""
		Master switch off → no employee EPF rows even if Employee.epf_applicable
		is on.
		"""
		_, slip = self._make_salary_slip(
			"test_epf_disabled_setting@indiapayroll.com",
			"Test EPF Disabled Setting Structure",
			15_000.0,
		)
		slip.insert()

		self.assertEqual(self._amount(slip, "deductions", EPF_EMPLOYEE_COMPONENT), 0)

	@HRMSTestSuite.change_settings(
		"Payroll Settings",
		{"enable_epf": 1, "enable_professional_tax": 0, "enable_esic": 0, "enable_lwf": 0},
	)
	def test_epf_injected_in_preview(self):
		"""
		Regression: statutory deductions must be injected during the salary slip
		preview (``process_salary_structure``), not only on save.

		The injectors run via the ``apply_regional_deductions`` regional override
		called inside ``calculate_net_pay``, so driving the preview path alone (no
		insert/save) must still produce the EPF deduction row.
		"""
		gross = float(EPF_WAGE_CEILING)
		_, slip = self._make_salary_slip(
			"test_epf_preview@indiapayroll.com",
			"Test EPF Preview Structure",
			gross,
		)

		# Drive only the preview path — never insert the slip.
		slip.process_salary_structure(for_preview=1)

		self.assertEqual(self._amount(slip, "deductions", EPF_EMPLOYEE_COMPONENT), 1_800)
		self.assertEqual(slip.docstatus, 0, "Preview must not persist the salary slip")

	@HRMSTestSuite.change_settings(
		"Payroll Settings",
		{"enable_epf": 1, "enable_professional_tax": 0, "enable_esic": 0, "enable_lwf": 0},
	)
	def test_net_pay_reduced_only_by_employee_share(self):
		"""
		Net pay must drop only by Employee PF + VPF.  Employer contributions
		are now off-slip (Salary Structure / CTC), so they cannot shift net
		pay or gross even structurally.
		"""
		gross = float(EPF_WAGE_CEILING)
		employee, slip = self._make_salary_slip(
			"test_epf_net_pay@indiapayroll.com",
			"Test EPF Net Pay Structure",
			gross,
		)
		self._set_ssa(employee, {"vpf_mode": "Percentage", "vpf_percentage": 5})
		slip.insert()

		expected_deduction = 1_800 + 750  # employee 12% + VPF 5%
		self.assertAlmostEqual(slip.total_deduction, expected_deduction, places=2)
		self.assertAlmostEqual(slip.gross_pay, gross, places=2)
		self.assertAlmostEqual(slip.net_pay, gross - expected_deduction, places=2)
