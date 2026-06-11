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

from india_payroll.india_payroll.report.professional_tax_register.professional_tax_register import (
	execute,
)
from india_payroll.install import create_professional_tax_component


class TestProfessionalTaxRegister(HRMSTestSuite):
	def setUp(self):
		create_professional_tax_component()

	@HRMSTestSuite.change_settings("Payroll Settings", {"enable_professional_tax": 1})
	def test_maharashtra_employee_shown_as_deducted(self):
		"""A submitted Maharashtra slip appears with ₹200 PT, Monthly, Deducted."""
		row = self._register_row_for_maharashtra_employee()
		self.assertEqual(row["work_state"], "Maharashtra")
		self.assertEqual(row["frequency"], "Monthly")
		self.assertEqual(row["professional_tax"], 200)
		self.assertEqual(row["deduction_status"], "Deducted")

	def _register_row_for_maharashtra_employee(self):
		employee = make_employee(
			"test_pt_register@indiapayroll.com", company="_Test Company", gender="Male"
		)
		salary_structure = make_salary_structure(
			"Test PT Register Structure", "Monthly",
			employee=employee, company="_Test Company", currency="INR",
		)
		assignment = create_salary_structure_assignment(
			employee, salary_structure.name, from_date="2026-04-01", company="_Test Company"
		)
		frappe.db.set_value(
			"Salary Structure Assignment", assignment.name, "employment_state", "Maharashtra"
		)

		salary_slip = make_salary_slip(
			salary_structure.name, employee=employee, posting_date="2026-04-01"
		)
		salary_slip.start_date = "2026-04-01"
		salary_slip.end_date = "2026-04-30"
		salary_slip.insert()
		salary_slip.submit()

		_columns, data = execute({"company": "_Test Company", "year": "2026", "month": "April"})
		return next(row for row in data if row["employee"] == employee)
