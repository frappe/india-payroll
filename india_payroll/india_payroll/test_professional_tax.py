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

from india_payroll.india_payroll.professional_tax import STATE_PT_CONFIG, _compute_pt_monthly
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
		→ gross_pay = ₹78,000 → Kerala slab (upto ₹99,999) = ₹1,440.
		Prior deductions = ₹0  →  PT charged this month = ₹1,440.
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
		self.assertEqual(pt_rows[0].amount, 1440)
