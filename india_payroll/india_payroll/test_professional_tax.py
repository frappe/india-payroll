# Copyright (c) 2026, Frappe Technologies Pvt. Ltd. and contributors
# License: GNU General Public License v3. See license.txt

import frappe
from erpnext.setup.doctype.employee.test_employee import make_employee
from hrms.payroll.doctype.salary_structure.salary_structure import make_salary_slip
from hrms.payroll.doctype.salary_structure.test_salary_structure import (
	create_salary_structure_assignment,
	make_salary_structure,
)
from hrms.tests.utils import HRMSTestSuite

from india_payroll.india_payroll.professional_tax import (
	STATE_PT_CONFIG,
	_compute_pt_monthly,
	_fiscal_year_period,
	_get_state_config,
	_slab_amount,
)
from india_payroll.install import create_professional_tax_component


class TestProfessionalTax(HRMSTestSuite):
	def setUp(self):
		create_professional_tax_component()

	def test_maharashtra_women_professional_tax_exemption(self):
		for month, taxable_amount in ((1, 200), (2, 300)):
			for gross_pay, expected in ((20000, 0), (25000, 0), (25000.01, taxable_amount)):
				with self.subTest(month=month, gross_pay=gross_pay):
					self.assertEqual(
						_compute_pt_monthly(gross_pay, STATE_PT_CONFIG["Maharashtra"], month, "Female"),
						expected,
					)

	def test_monthly_state_slabs(self):
		cases = (
			("Assam", "2026-04-01", 15000, 0),
			("Assam", "2026-04-01", 15001, 180),
			("Assam", "2026-04-01", 25001, 208),
			("Gujarat", "2026-04-01", 11999, 0),
			("Gujarat", "2026-04-01", 12000, 200),
			("Jharkhand", "2026-04-01", 25000, 0),
			("Jharkhand", "2026-04-01", 50000, 150),
			("Jharkhand", "2026-04-01", 90000, 208),
			("Jharkhand", "2027-03-01", 90000, 212),
			("Karnataka", "2026-04-01", 24999, 0),
			("Karnataka", "2026-04-01", 25000, 200),
			("Karnataka", "2027-02-01", 25000, 300),
			("Madhya Pradesh", "2026-04-01", 30000, 166),
			("Madhya Pradesh", "2027-03-01", 30000, 174),
			("Madhya Pradesh", "2027-03-01", 40000, 212),
			("Meghalaya", "2026-04-01", 40000, 200),
			("Odisha", "2026-02-01", 30000, 200),
			("Odisha", "2026-03-01", 30000, 300),
			("Odisha", "2026-04-01", 30000, 0),
			("West Bengal", "2026-09-01", 20000, 130),
			("West Bengal", "2026-09-01", 120000, 200),
			("West Bengal", "2026-10-01", 20000, 0),
			("West Bengal", "2026-10-01", 20001, 100),
			("West Bengal", "2026-10-01", 50000, 140),
			("West Bengal", "2026-10-01", 100000, 170),
			("West Bengal", "2026-10-01", 120000, 208),
		)
		for state, start_date, gross_pay, expected in cases:
			with self.subTest(state=state, start_date=start_date, gross_pay=gross_pay):
				date = frappe.utils.getdate(start_date)
				state_config = _get_state_config(state, date)
				self.assertEqual(_compute_pt_monthly(gross_pay, state_config, date.month, "Male"), expected)

	def test_periodic_state_slabs(self):
		cases = (
			("Tamil Nadu", 30000, 180),
			("Tamil Nadu", 45000, 425),
			("Tamil Nadu", 60000, 930),
			("Kerala", 17999, 320),
			("Kerala", 44999, 600),
			("Kerala", 125000, 1250),
			("Bihar", 300000, 0),
			("Bihar", 300001, 1000),
			("Bihar", 1000000, 2000),
			("Bihar", 1000001, 2500),
		)
		for state, cumulative_gross, expected in cases:
			with self.subTest(state=state, cumulative_gross=cumulative_gross):
				self.assertEqual(_slab_amount(cumulative_gross, STATE_PT_CONFIG[state]["slabs"]), expected)

	def test_fiscal_year_period(self):
		for date, start_year in (("2026-04-01", 2026), ("2026-12-15", 2026), ("2027-03-31", 2026)):
			with self.subTest(date=date):
				period_start, period_end = _fiscal_year_period(frappe.utils.getdate(date))
				self.assertEqual(str(period_start), f"{start_year}-04-01")
				self.assertEqual(str(period_end), f"{start_year + 1}-03-31")

	@HRMSTestSuite.change_settings("Payroll Settings", {"enable_professional_tax": 1})
	def test_maharashtra_professional_tax_applied_on_salary_slip(self):
		"""
		A male employee in Maharashtra with gross pay > ₹10,000 should have
		₹200 Professional Tax deducted in a standard (non-February) month.
		"""
		employee = make_employee(
			"test_maharashtra_pt@indiapayroll.com",
			company="_Test Company",
			gender="Male",
		)

		salary_structure = make_salary_structure(
			"Test PT Salary Structure",
			"Monthly",
			employee=employee,
			company="_Test Company",
			currency="INR",
		)

		ssa = create_salary_structure_assignment(
			employee,
			salary_structure.name,
			from_date="2026-04-01",
			company="_Test Company",
		)
		frappe.db.set_value("Salary Structure Assignment", ssa.name, "employment_state", "Maharashtra")

		salary_slip = make_salary_slip(
			salary_structure.name,
			employee=employee,
			posting_date="2026-04-01",
		)
		salary_slip.start_date = "2026-04-01"
		salary_slip.end_date = "2026-04-30"
		salary_slip.insert()

		pt_rows = [d for d in salary_slip.deductions if d.salary_component == "Professional Tax"]
		self.assertEqual(len(pt_rows), 1)
		self.assertEqual(pt_rows[0].amount, 200)

	@HRMSTestSuite.change_settings("Payroll Settings", {"enable_professional_tax": 1})
	def test_maharashtra_february_amount_for_female_employee(self):
		"""
		A female employee in Maharashtra with gross pay > ₹25,000 should have
		₹300 Professional Tax deducted in February (women exemption does not apply
		above ₹25,000; February special amount applies instead of the usual ₹200).
		"""
		employee = make_employee(
			"test_maharashtra_pt_female_feb@indiapayroll.com",
			company="_Test Company",
			gender="Female",
		)

		salary_structure = make_salary_structure(
			"Test PT Salary Structure Female Feb",
			"Monthly",
			employee=employee,
			company="_Test Company",
			currency="INR",
		)

		ssa = create_salary_structure_assignment(
			employee,
			salary_structure.name,
			from_date="2026-02-01",
			company="_Test Company",
		)

		frappe.db.set_value("Salary Structure Assignment", ssa.name, "employment_state", "Maharashtra")

		salary_slip = make_salary_slip(
			salary_structure.name,
			employee=employee,
			posting_date="2026-02-01",
		)
		salary_slip.start_date = "2026-02-01"
		salary_slip.end_date = "2026-02-28"
		salary_slip.insert()

		pt_rows = [d for d in salary_slip.deductions if d.salary_component == "Professional Tax"]
		self.assertEqual(len(pt_rows), 1)
		self.assertEqual(pt_rows[0].amount, 300)

	@HRMSTestSuite.change_settings("Payroll Settings", {"enable_professional_tax": 1})
	def test_kerala_half_yearly_first_month_of_period(self):
		"""
		Kerala is half-yearly. For the first month of a half-year period
		(April-September) with no prior submitted slips, the full half-yearly
		PT slab amount is charged in one go.

		Default salary structure: Basic ₹50,000 + HRA ₹3,000 + SA ₹25,000
		→ gross_pay = ₹78,000 → Kerala slab (upto ₹99,999) = ₹750.
		Prior deductions = ₹0  →  PT charged this month = ₹750.
		"""
		employee = make_employee(
			"test_kerala_pt@indiapayroll.com",
			company="_Test Company",
			gender="Male",
		)

		salary_structure = make_salary_structure(
			"Test PT Salary Structure Kerala",
			"Monthly",
			employee=employee,
			company="_Test Company",
			currency="INR",
		)

		ssa = create_salary_structure_assignment(
			employee,
			salary_structure.name,
			from_date="2026-04-01",
			company="_Test Company",
		)
		frappe.db.set_value("Salary Structure Assignment", ssa.name, "employment_state", "Kerala")

		salary_slip = make_salary_slip(
			salary_structure.name,
			employee=employee,
			posting_date="2026-04-01",
		)
		salary_slip.start_date = "2026-04-01"
		salary_slip.end_date = "2026-04-30"
		salary_slip.insert()

		pt_rows = [d for d in salary_slip.deductions if d.salary_component == "Professional Tax"]
		self.assertEqual(len(pt_rows), 1)
		self.assertEqual(pt_rows[0].amount, 750)
