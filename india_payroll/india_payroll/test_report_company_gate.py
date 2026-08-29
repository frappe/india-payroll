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

from india_payroll.india_payroll.company_settings import (
	COMPANY_SETTINGS_FIELD,
	MULTI_COMPANY_FIELD,
)
from india_payroll.india_payroll.epf import EPF_EMPLOYEE_COMPONENT
from india_payroll.india_payroll.esi import ESI_EMPLOYEE_COMPONENT
from india_payroll.india_payroll.lwf import LWF_SALARY_COMPONENT
from india_payroll.india_payroll.report.employee_provident_fund_register import (
	employee_provident_fund_register as epf_register,
)
from india_payroll.india_payroll.report.esic_register import esic_register
from india_payroll.india_payroll.report.lwf_register import lwf_register
from india_payroll.india_payroll.test_company_settings import create_company
from india_payroll.install import create_epf_components, create_esi_components, create_lwf_component

_SLIP_COMPANY = "_Test Company"
_OTHER_COMPANY = "India Payroll MC Company A"

_HISTORICAL_EMAIL = "test_mc_gate_historical@indiapayroll.com"
_NEW_EMAIL = "test_mc_gate_new@indiapayroll.com"
_STRUCTURE = "Test MC Report Gate Structure"

_UAN = {
	_HISTORICAL_EMAIL: "100200300401",
	_NEW_EMAIL: "100200300402",
}

_BASIC_COMPONENT = "MC Gate Basic"
_EARNINGS = [
	{
		"salary_component": _BASIC_COMPONENT,
		"abbr": "MCGB",
		"formula": "base",
		"type": "Earning",
		"amount_based_on_formula": 1,
		"depends_on_payment_days": 0,
	}
]

_START = "2026-05-01"
_END = "2026-05-31"
_FILTERS = {"month": "May", "year": "2026"}


class TestReportCompanyGate(HRMSTestSuite):
	"""Removing a company from Company Payroll Settings must stop new statutory
	accrual without erasing what the company already deducted and remitted."""

	def setUp(self):
		create_esi_components()
		create_lwf_component()
		create_epf_components()
		create_company(_OTHER_COMPANY, "IPMCA")

		if not frappe.db.exists("Salary Component", _BASIC_COMPONENT):
			make_salary_component(_EARNINGS, False, [_SLIP_COMPANY])

		self._cleanup()

		self._configure(multi_company=False)
		self.historical_slip = self._make_submitted_slip(_HISTORICAL_EMAIL)

		self._configure(multi_company=True)
		self.new_slip = self._make_submitted_slip(_NEW_EMAIL)

	def tearDown(self):
		frappe.db.rollback()
		frappe.clear_cache(doctype="Payroll Settings")

	def _cleanup(self):
		for email in (_HISTORICAL_EMAIL, _NEW_EMAIL):
			frappe.db.delete("Salary Slip", {"employee_name": email})
			emp = frappe.db.get_value("Employee", {"employee_name": email}, "name")
			if emp:
				frappe.db.delete("Salary Structure Assignment", {"employee": emp})

	def _configure(self, multi_company: bool):
		"""Enable every statute. When `multi_company` is set, list only a company
		the slips do not belong to, so _Test Company is out of scope."""
		settings = frappe.get_doc("Payroll Settings")
		settings.email_salary_slip_to_employee = 0
		settings.update(
			{
				MULTI_COMPANY_FIELD: int(multi_company),
				"enable_esic": 1,
				"enable_lwf": 1,
				"enable_epf": 1,
			}
		)
		settings.set(COMPANY_SETTINGS_FIELD, [])
		if multi_company:
			settings.append(
				COMPANY_SETTINGS_FIELD,
				{"company": _OTHER_COMPANY, "esic_registration_number": "31000123450000"},
			)
		settings.flags.ignore_permissions = True
		settings.save()
		frappe.clear_cache(doctype="Payroll Settings")

	def _make_submitted_slip(self, email: str):
		employee = make_employee(email, company=_SLIP_COMPANY)
		frappe.db.set_value(
			"Employee",
			employee,
			{"uan_number": _UAN[email], "pf_name": "MC Gate Test"},
		)
		structure = make_salary_structure(
			_STRUCTURE,
			"Monthly",
			company=_SLIP_COMPANY,
			currency="INR",
			earnings=_EARNINGS,
			deductions=[],
		)
		create_salary_structure_assignment(
			employee,
			structure.name,
			from_date=_START,
			company=_SLIP_COMPANY,
			base=15_000,
		)
		ssa = frappe.db.get_value(
			"Salary Structure Assignment", {"employee": employee}, "name", order_by="from_date desc"
		)
		frappe.db.set_value(
			"Salary Structure Assignment",
			ssa,
			{"employment_state": "Haryana", "epf_applicable": 1},
		)

		slip = make_salary_slip(structure.name, employee=employee, posting_date=_START)
		slip.start_date = _START
		slip.end_date = _END
		slip.insert()
		slip.submit()
		return slip

	def _employees_in(self, report):
		return {r["employee"] for r in report.get_data(dict(_FILTERS))}

	def _deduction_components(self, slip_name):
		return set(
			frappe.get_all(
				"Salary Detail",
				filters={"parent": slip_name, "parentfield": "deductions", "amount": (">", 0)},
				pluck="salary_component",
			)
		)

	def test_fixture_records_deductions_only_before_exclusion(self):
		historical = self._deduction_components(self.historical_slip.name)
		self.assertIn(ESI_EMPLOYEE_COMPONENT, historical)
		self.assertIn(LWF_SALARY_COMPONENT, historical)
		self.assertIn(EPF_EMPLOYEE_COMPONENT, historical)

		new = self._deduction_components(self.new_slip.name)
		self.assertNotIn(ESI_EMPLOYEE_COMPONENT, new)
		self.assertNotIn(LWF_SALARY_COMPONENT, new)
		self.assertNotIn(EPF_EMPLOYEE_COMPONENT, new)

	def test_esic_register_keeps_already_deducted_liability(self):
		self.assertIn(self.historical_slip.employee, self._employees_in(esic_register))

	def test_esic_register_drops_post_exclusion_slip(self):
		self.assertNotIn(self.new_slip.employee, self._employees_in(esic_register))

	def test_lwf_register_keeps_already_deducted_liability(self):
		self.assertIn(self.historical_slip.employee, self._employees_in(lwf_register))

	def test_lwf_register_drops_post_exclusion_slip(self):
		self.assertNotIn(self.new_slip.employee, self._employees_in(lwf_register))

	def test_epf_register_keeps_already_deducted_liability(self):
		self.assertIn(self.historical_slip.employee, self._employees_in(epf_register))

	def test_epf_register_drops_post_exclusion_slip(self):
		self.assertNotIn(self.new_slip.employee, self._employees_in(epf_register))

	def test_ecr_file_remains_reproducible_after_exclusion(self):
		result = epf_register.get_ecr_file({**_FILTERS, "company": _SLIP_COMPANY})

		historical_uan = frappe.db.get_value("Employee", self.historical_slip.employee, "uan_number")
		new_uan = frappe.db.get_value("Employee", self.new_slip.employee, "uan_number")

		self.assertIn(f"{historical_uan}#~#", result["content"])
		self.assertNotIn(f"{new_uan}#~#", result["content"])

	def test_ecr_file_requires_company_in_multi_company_mode(self):
		self.assertRaises(frappe.ValidationError, epf_register.get_ecr_file, dict(_FILTERS))

	def test_both_slips_reported_when_multi_company_off(self):
		self._configure(multi_company=False)

		for report in (esic_register, lwf_register, epf_register):
			employees = self._employees_in(report)
			self.assertIn(self.historical_slip.employee, employees)
			self.assertIn(self.new_slip.employee, employees)
