# Copyright (c) 2026, Frappe Technologies Pvt. Ltd. and contributors
# License: GNU General Public License v3. See license.txt

import frappe
from erpnext.setup.doctype.employee.test_employee import make_employee
from hrms.payroll.doctype.salary_slip.test_salary_slip import make_salary_component
from hrms.payroll.doctype.salary_structure.salary_structure import make_salary_slip
from hrms.payroll.doctype.salary_structure.test_salary_structure import (
	create_salary_structure_assignment,
	make_salary_structure,
)
from hrms.tests.utils import HRMSTestSuite

from india_payroll.india_payroll.report.professional_tax_register import professional_tax_register
from india_payroll.install import create_professional_tax_component

_COMPANY = "_Test Company"
_STRUCTURE = "Test PT Register Structure"
_BASIC_COMPONENT = "PT Register Basic"
_EARNINGS = [
	{
		"salary_component": _BASIC_COMPONENT,
		"abbr": "PTRB",
		"formula": "base",
		"type": "Earning",
		"amount_based_on_formula": 1,
		"depends_on_payment_days": 0,
	}
]

_START = "2026-04-01"
_END = "2026-04-30"
_FILTERS = {"company": _COMPANY, "month": "April", "year": "2026"}


class TestProfessionalTaxRegister(HRMSTestSuite):
	def setUp(self):
		create_professional_tax_component()
		if not frappe.db.exists("Salary Component", _BASIC_COMPONENT):
			make_salary_component(_EARNINGS, False, [_COMPANY])

	def _make_submitted_slip(self, email, employment_state, base, gender="Male"):
		employee = make_employee(email, company=_COMPANY, gender=gender)
		structure = make_salary_structure(
			_STRUCTURE,
			"Monthly",
			company=_COMPANY,
			currency="INR",
			earnings=_EARNINGS,
			deductions=[],
		)
		assignment = create_salary_structure_assignment(
			employee, structure.name, from_date=_START, company=_COMPANY, base=base
		)
		frappe.db.set_value(
			"Salary Structure Assignment", assignment.name, "employment_state", employment_state
		)

		slip = make_salary_slip(structure.name, employee=employee, posting_date=_START)
		slip.start_date = _START
		slip.end_date = _END
		slip.insert()
		slip.submit()
		return slip

	def _row_for(self, employee, filters=None):
		rows = professional_tax_register.get_data({**_FILTERS, **(filters or {})})
		return next((row for row in rows if row["employee"] == employee), None)

	@HRMSTestSuite.change_settings("Payroll Settings", {"enable_professional_tax": 1})
	def test_deducted_professional_tax_is_reported(self):
		slip = self._make_submitted_slip("test_pt_register_deducted@indiapayroll.com", "Maharashtra", 30_000)

		row = self._row_for(slip.employee)
		self.assertEqual(row["employment_state"], "Maharashtra")
		self.assertEqual(row["frequency"], "Monthly")
		self.assertEqual(row["gross_wages"], 30_000)
		self.assertEqual(row["professional_tax"], 200)
		self.assertEqual(row["deduction_status"], "Deducted")

	@HRMSTestSuite.change_settings("Payroll Settings", {"enable_professional_tax": 1})
	def test_exempt_employee_in_pt_state_is_nil(self):
		# Maharashtra exempts women earning up to ₹25,000
		slip = self._make_submitted_slip(
			"test_pt_register_exempt@indiapayroll.com", "Maharashtra", 20_000, gender="Female"
		)

		row = self._row_for(slip.employee)
		self.assertEqual(row["professional_tax"], 0)
		self.assertEqual(row["deduction_status"], "Nil / Exempt")

	@HRMSTestSuite.change_settings("Payroll Settings", {"enable_professional_tax": 1})
	def test_employee_in_state_without_pt(self):
		slip = self._make_submitted_slip("test_pt_register_no_state@indiapayroll.com", "Haryana", 30_000)

		row = self._row_for(slip.employee)
		self.assertEqual(row["frequency"], "")
		self.assertEqual(row["professional_tax"], 0)
		self.assertEqual(row["deduction_status"], "No PT State")

	@HRMSTestSuite.change_settings("Payroll Settings", {"enable_professional_tax": 1})
	def test_state_and_status_filters(self):
		deducted = self._make_submitted_slip(
			"test_pt_register_filter_mh@indiapayroll.com", "Maharashtra", 30_000
		)
		no_state = self._make_submitted_slip("test_pt_register_filter_hr@indiapayroll.com", "Haryana", 30_000)

		self.assertIsNotNone(self._row_for(deducted.employee, {"employment_state": "Maharashtra"}))
		self.assertIsNone(self._row_for(no_state.employee, {"employment_state": "Maharashtra"}))

		self.assertIsNotNone(self._row_for(no_state.employee, {"deduction_status": "No PT State"}))
		self.assertIsNone(self._row_for(deducted.employee, {"deduction_status": "No PT State"}))

	def test_year_without_month_covers_the_whole_year(self):
		self.assertEqual(
			professional_tax_register._get_date_range({"year": "2026"}),
			{"from_date": "2026-01-01", "to_date": "2026-12-31"},
		)
		self.assertEqual(
			professional_tax_register._get_date_range({"year": "2026", "month": "February"}),
			{"from_date": "2026-02-01", "to_date": "2026-02-28"},
		)
		self.assertIsNone(professional_tax_register._get_date_range({}))
