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

from india_payroll.india_payroll.esi import (
	EMPLOYEE_ESI_RATE,
	EMPLOYER_ESI_RATE,
	ESI_EMPLOYEE_COMPONENT,
	ESI_EMPLOYER_COMPONENT,
	ESI_WAGE_CEILING,
	ESI_WAGE_CEILING_DISABILITY,
	get_esi_split,
)
from india_payroll.install import create_esi_components

# A single formula-based earning component used across all ESI tests.
# Using a dedicated component (not the shared "Basic Salary") prevents
# interference with other test suites that may set up "Basic Salary"
# with different settings.  The formula ``base`` ensures that when
# ``base=gross_pay`` on the SSA the slip's gross_pay equals gross_pay
# exactly — no extra HRA / Special Allowance rows that would inflate it.
_ESI_BASIC_COMPONENT = "ESI Test Basic"
_ESI_TEST_EARNINGS = [
	{
		"salary_component": _ESI_BASIC_COMPONENT,
		"abbr": "ESIB",
		"formula": "base",
		"type": "Earning",
		"amount_based_on_formula": 1,
		"depends_on_payment_days": 0,
	}
]

# A prorating earning (depends_on_payment_days=1) for LOP scenarios: its slip
# `amount` is reduced by payment days while `default_amount` stays at the full
# monthly value — exactly the case the ESI coverage test must handle.
_ESI_LOP_COMPONENT = "ESI LOP Basic"
_ESI_LOP_EARNINGS = [
	{
		"salary_component": _ESI_LOP_COMPONENT,
		"abbr": "ESILOP",
		"formula": "base",
		"type": "Earning",
		"amount_based_on_formula": 1,
		"depends_on_payment_days": 1,
	}
]


class TestESI(HRMSTestSuite):
	def setUp(self):
		self.TEST_EMPLOYEE_EMAILS = [
			"test_esi_eligible@indiapayroll.com",
			"test_esi_exact_ceiling@indiapayroll.com",
			"test_esi_above_ceiling@indiapayroll.com",
			"test_esi_disability@indiapayroll.com",
			"test_esi_disability_above@indiapayroll.com",
			"test_esi_disabled_setting@indiapayroll.com",
			"test_esi_net_pay@indiapayroll.com",
			"test_esi_lop_eligibility@indiapayroll.com",
			"test_esi_lop_contribution@indiapayroll.com",
		]
		create_esi_components()
		self._ensure_esi_test_components()
		self._cleanup()

	def _ensure_esi_test_components(self):
		"""
		Ensure the ESI-specific salary components exist.
		Runs only when a component is absent so it is fast on subsequent runs.
		"""
		if not frappe.db.exists("Salary Component", _ESI_BASIC_COMPONENT):
			make_salary_component(_ESI_TEST_EARNINGS, False, ["_Test Company"])
		if not frappe.db.exists("Salary Component", _ESI_LOP_COMPONENT):
			make_salary_component(_ESI_LOP_EARNINGS, False, ["_Test Company"])

	def _cleanup(self):
		"""
		Delete salary slips and structure assignments for every test employee
		so that tests are fully re-runnable without leaving stale data.
		"""
		for email in self.TEST_EMPLOYEE_EMAILS:
			# Salary Slip stores employee_name which equals the email in test fixtures
			frappe.db.delete("Salary Slip", {"employee_name": email})
			# Salary Structure Assignment uses the employee docname
			emp = frappe.db.get_value("Employee", {"employee_name": email}, "name")
			if emp:
				frappe.db.delete("Salary Structure Assignment", {"employee": emp})

	def _make_salary_slip(
		self,
		email: str,
		structure_name: str,
		gross_pay: float,
		posting_date: str = "2026-04-01",
		start_date: str = "2026-04-01",
		end_date: str = "2026-04-30",
		gender: str = "Male",
	):
		"""
		Create a salary slip whose gross_pay is exactly `gross_pay`.

		The salary structure contains only the ESI-specific "ESI Test Basic"
		component (formula: ``base``) with no HRA, Special Allowance or other
		earnings that would inflate the gross beyond the intended value.  The
		SSA ``base`` is set to ``gross_pay``, so the slip's gross equals
		``gross_pay`` naturally without any manual row override.

		Deductions are also omitted from the structure so that Professional
		Tax, TDS, etc. cannot affect net_pay assertions in ESI tests.
		"""
		employee = make_employee(email, company="_Test Company", gender=gender)

		# Do NOT pass employee= here; we create the SSA ourselves below with
		# the correct base so make_salary_structure never auto-creates one at
		# the 50,000 default.
		salary_structure = make_salary_structure(
			structure_name,
			"Monthly",
			company="_Test Company",
			currency="INR",
			earnings=_ESI_TEST_EARNINGS,
			deductions=[],
		)

		create_salary_structure_assignment(
			employee,
			salary_structure.name,
			from_date=start_date,
			company="_Test Company",
			base=gross_pay,
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

	@HRMSTestSuite.change_settings("Payroll Settings", {"enable_esic": 1})
	def test_eligible_employee_esi_applied(self):
		"""
		An employee whose gross wages are below the ₹21,000 ceiling should
		have an employee ESI deduction row (0.75% of gross) and an employer
		contribution row (3.25% of gross) injected on the salary slip.
		"""
		gross = 15_000.0
		_, salary_slip = self._make_salary_slip(
			"test_esi_eligible@indiapayroll.com",
			"Test ESI Eligible Structure",
			gross,
		)
		salary_slip.insert()

		emp_rows = [d for d in salary_slip.deductions if d.salary_component == ESI_EMPLOYEE_COMPONENT]

		self.assertEqual(len(emp_rows), 1, "Employee ESI deduction row must be present")

		self.assertAlmostEqual(emp_rows[0].amount, flt(gross * EMPLOYEE_ESI_RATE, 2), places=2)  # 112.50

		employer_rows = [
			d for d in salary_slip.employer_contributions if d.salary_component == ESI_EMPLOYER_COMPONENT
		]
		self.assertEqual(len(employer_rows), 1, "Employer ESI contribution row must be present")
		self.assertAlmostEqual(employer_rows[0].amount, flt(gross * EMPLOYER_ESI_RATE, 2), places=2)  # 487.50

	@HRMSTestSuite.change_settings("Payroll Settings", {"enable_esic": 1})
	def test_employee_at_exact_ceiling_is_eligible(self):
		"""
		An employee earning exactly ₹21,000 (the wage ceiling) must be
		covered — the ceiling is inclusive (gross ≤ ceiling → eligible).
		"""
		gross = float(ESI_WAGE_CEILING)  # 21000
		_, salary_slip = self._make_salary_slip(
			"test_esi_exact_ceiling@indiapayroll.com",
			"Test ESI Exact Ceiling Structure",
			gross,
		)
		salary_slip.insert()

		emp_rows = [d for d in salary_slip.deductions if d.salary_component == ESI_EMPLOYEE_COMPONENT]
		self.assertEqual(len(emp_rows), 1, "Employee at exact ceiling must be eligible")

		self.assertAlmostEqual(emp_rows[0].amount, flt(gross * EMPLOYEE_ESI_RATE, 2), places=2)  # 157.50

	@HRMSTestSuite.change_settings("Payroll Settings", {"enable_esic": 1})
	def test_employee_above_ceiling_no_esi(self):
		"""
		An employee whose gross wages exceed ₹21,000 must not have any ESI
		deduction rows on the salary slip.
		"""
		gross = 25_000.0  # > 21,000 standard ceiling
		_, salary_slip = self._make_salary_slip(
			"test_esi_above_ceiling@indiapayroll.com",
			"Test ESI Above Ceiling Structure",
			gross,
		)
		salary_slip.insert()

		self.assertEqual(len(self._esi_rows(salary_slip)), 0, "Employee above ceiling must not have ESI rows")
		self.assertEqual(len(self._employer_esi_rows(salary_slip)), 0)

	@HRMSTestSuite.change_settings("Payroll Settings", {"enable_esic": 1})
	def test_person_with_disability_uses_higher_ceiling(self):
		"""
		A person with disability has an ESI wage ceiling of ₹25,000.
		Gross pay of ₹23,000 (above the standard ₹21,000 but ≤ ₹25,000)
		must still attract ESI contributions.
		"""
		gross = 23_000.0
		employee, salary_slip = self._make_salary_slip(
			"test_esi_disability@indiapayroll.com",
			"Test ESI Disability Ceiling Structure",
			gross,
		)
		self._set_ssa(employee, {"is_person_with_disability": 1})

		salary_slip.insert()

		emp_rows = [d for d in salary_slip.deductions if d.salary_component == ESI_EMPLOYEE_COMPONENT]
		self.assertEqual(
			len(emp_rows),
			1,
			"Person with disability earning 23,000 must be eligible (ceiling is 25,000)",
		)

		expected_emp = flt(gross * EMPLOYEE_ESI_RATE, 2)  # 172.50
		self.assertAlmostEqual(emp_rows[0].amount, expected_emp, places=2)

	@HRMSTestSuite.change_settings("Payroll Settings", {"enable_esic": 1})
	def test_person_with_disability_above_disability_ceiling_no_esi(self):
		"""
		A person with disability earning above ₹25,000 must NOT have ESI
		deductions — the disability ceiling is itself a hard limit.
		"""
		gross = float(ESI_WAGE_CEILING_DISABILITY) + 1  # 25,001
		employee, salary_slip = self._make_salary_slip(
			"test_esi_disability_above@indiapayroll.com",
			"Test ESI Disability Above Ceiling Structure",
			gross,
		)
		self._set_ssa(employee, {"is_person_with_disability": 1})

		salary_slip.insert()

		self.assertEqual(
			len(self._esi_rows(salary_slip)), 0, "Person with disability above 25,000 must not have ESI rows"
		)
		self.assertEqual(len(self._employer_esi_rows(salary_slip)), 0)

	@HRMSTestSuite.change_settings("Payroll Settings", {"enable_esic": 0})
	def test_esi_not_applied_when_disabled(self):
		"""
		When ESIC is disabled in Payroll Settings, no ESI deduction rows
		must appear on the salary slip — even for an otherwise eligible employee.
		"""
		gross = 15_000.0
		_, salary_slip = self._make_salary_slip(
			"test_esi_disabled_setting@indiapayroll.com",
			"Test ESI Disabled Setting Structure",
			gross,
		)
		salary_slip.insert()

		self.assertEqual(
			len(self._esi_rows(salary_slip)), 0, "ESI must not be applied when setting is disabled"
		)
		self.assertEqual(len(self._employer_esi_rows(salary_slip)), 0)

	@HRMSTestSuite.change_settings("Payroll Settings", {"enable_esic": 1})
	def test_net_pay_reduced_by_employee_esi(self):
		"""
		Net pay must be reduced by the employee's 0.75% share only. The employer's
		3.25% sits in employer_contributions and must not touch gross or net.
		"""
		gross = 20_000.0
		_, salary_slip = self._make_salary_slip(
			"test_esi_net_pay@indiapayroll.com",
			"Test ESI Net Pay Structure",
			gross,
		)
		salary_slip.insert()

		expected_employee = flt(gross * EMPLOYEE_ESI_RATE, 2)  # 150.0
		self.assertAlmostEqual(
			salary_slip.net_pay,
			gross - expected_employee,
			places=2,
			msg="net_pay must equal gross_pay minus the employee ESI share",
		)
		self.assertAlmostEqual(salary_slip.gross_pay, gross, places=2)

		employer_rows = [
			d for d in salary_slip.employer_contributions if d.salary_component == ESI_EMPLOYER_COMPONENT
		]
		self.assertAlmostEqual(employer_rows[0].amount, flt(gross * EMPLOYER_ESI_RATE, 2), places=2)  # 650.0

		emp_esi_in_deduction = sum(
			flt(d.amount) for d in salary_slip.deductions if d.salary_component == ESI_EMPLOYEE_COMPONENT
		)
		self.assertAlmostEqual(emp_esi_in_deduction, expected_employee, places=2)

	def _make_lop_salary_slip(self, email: str, structure_name: str, base: float):
		"""Build (uninserted) a salary slip whose single earning prorates with payment
		days, so LOP can be simulated by overriding payment_days/total_working_days."""
		employee = make_employee(email, company="_Test Company", gender="Male")
		salary_structure = make_salary_structure(
			structure_name,
			"Monthly",
			company="_Test Company",
			currency="INR",
			earnings=_ESI_LOP_EARNINGS,
			deductions=[],
		)
		create_salary_structure_assignment(
			employee,
			salary_structure.name,
			from_date="2026-04-01",
			company="_Test Company",
			base=base,
		)
		salary_slip = make_salary_slip(salary_structure.name, employee=employee, posting_date="2026-04-01")
		salary_slip.start_date = "2026-04-01"
		salary_slip.end_date = "2026-04-30"
		return salary_slip

	@staticmethod
	def _esi_rows(slip):
		return [d for d in slip.deductions if d.salary_component == ESI_EMPLOYEE_COMPONENT]

	@staticmethod
	def _employer_esi_rows(slip):
		return [d for d in slip.employer_contributions if d.salary_component == ESI_EMPLOYER_COMPONENT]

	@HRMSTestSuite.change_settings("Payroll Settings", {"enable_esic": 1})
	def test_eligibility_uses_full_gross_not_prorated(self):
		"""
		Coverage is judged on the full monthly gross, not the LOP-prorated slip
		gross. A ₹22,000 earner stays out of ESI even in a month where LOP drops
		the paid gross below the ₹21,000 ceiling.
		"""
		slip = self._make_lop_salary_slip(
			"test_esi_lop_eligibility@indiapayroll.com", "Test ESI LOP Eligibility Structure", 22_000
		)
		slip.insert()

		# Full month: gross 22,000 > 21,000 → not covered.
		self.assertEqual(len(self._esi_rows(slip)), 0)

		# Simulate LOP: half the period unpaid → prorated gross 11,000 (< ceiling),
		# but the full wage is still 22,000, so the employee remains out of ESI.
		slip.total_working_days = 30
		slip.payment_days = 15
		slip.calculate_net_pay()

		self.assertAlmostEqual(slip.gross_pay, 11_000, places=2)
		self.assertEqual(
			len(self._esi_rows(slip)),
			0,
			"A high earner must stay out of ESI even when LOP drops the paid gross below the ceiling",
		)

	@HRMSTestSuite.change_settings("Payroll Settings", {"enable_esic": 1})
	def test_contribution_levied_on_actual_paid_wages_under_lop(self):
		"""
		For a covered employee, the contribution is levied on the actual wages paid
		(the prorated gross), not the full gross: ₹20,000 earner with half-month LOP
		→ ESI on ₹10,000 = ₹400.
		"""
		slip = self._make_lop_salary_slip(
			"test_esi_lop_contribution@indiapayroll.com", "Test ESI LOP Contribution Structure", 20_000
		)
		slip.insert()

		# full month: employee ESI on 20,000 = 150
		self.assertAlmostEqual(self._esi_rows(slip)[0].amount, flt(20_000 * EMPLOYEE_ESI_RATE, 2), places=2)

		# Half-month LOP: still covered (full gross 20,000 ≤ ceiling), contribution on
		# the 10,000 actually paid = 75
		slip.total_working_days = 30
		slip.payment_days = 15
		slip.calculate_net_pay()

		self.assertAlmostEqual(slip.gross_pay, 10_000, places=2)
		self.assertAlmostEqual(self._esi_rows(slip)[0].amount, flt(10_000 * EMPLOYEE_ESI_RATE, 2), places=2)

	def test_get_esi_split_shares_sum_to_four_percent(self):
		split = get_esi_split(20_000)

		self.assertTrue(split.covered)
		self.assertAlmostEqual(split.employee, 150.0, places=2)
		self.assertAlmostEqual(split.employer, 650.0, places=2)
		self.assertAlmostEqual(split.total, 800.0, places=2)

	def test_get_esi_split_ceiling_is_inclusive(self):
		self.assertTrue(get_esi_split(ESI_WAGE_CEILING).covered)
		self.assertFalse(get_esi_split(ESI_WAGE_CEILING + 1).covered)

		self.assertTrue(get_esi_split(ESI_WAGE_CEILING_DISABILITY, is_person_with_disability=True).covered)
		self.assertFalse(
			get_esi_split(ESI_WAGE_CEILING_DISABILITY + 1, is_person_with_disability=True).covered
		)

	def test_get_esi_split_not_covered_is_all_zero(self):
		split = get_esi_split(ESI_WAGE_CEILING + 1)

		self.assertFalse(split.covered)
		self.assertEqual(split.employee, 0.0)
		self.assertEqual(split.employer, 0.0)
		self.assertEqual(split.total, 0.0)

	def test_get_esi_split_ceiling_gross_decides_coverage(self):
		"""Coverage follows ``ceiling_gross`` while the contribution follows ``gross``.

		This is what keeps an LOP month honest: a 22,000 earner paid 10,000 stays
		out of ESI, and a 20,000 earner paid 10,000 contributes on the 10,000.
		"""
		self.assertFalse(get_esi_split(10_000, ceiling_gross=22_000).covered)

		split = get_esi_split(10_000, ceiling_gross=20_000)
		self.assertTrue(split.covered)
		self.assertAlmostEqual(split.employee, 75.0, places=2)
		self.assertAlmostEqual(split.employer, 325.0, places=2)
