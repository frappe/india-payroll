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
from india_payroll.india_payroll.report.employee_provident_fund_register import (
	employee_provident_fund_register as epf_register,
)
from india_payroll.india_payroll.report.esic_register import esic_register
from india_payroll.india_payroll.report.lwf_register import lwf_register
from india_payroll.india_payroll.test_company_settings import create_company
from india_payroll.install import create_epf_components, create_esi_components, create_lwf_component

_SLIP_COMPANY = "_Test Company"
_OTHER_COMPANY = "India Payroll MC Company A"

_EMAIL = "test_mc_report_gate@indiapayroll.com"
_STRUCTURE = "Test MC Report Gate Structure"
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

_START = "2026-04-01"
_END = "2026-04-30"
_FILTERS = {"month": "April", "year": "2026"}


class TestReportCompanyGate(HRMSTestSuite):
	def setUp(self):
		create_esi_components()
		create_lwf_component()
		create_epf_components()
		create_company(_OTHER_COMPANY, "IPMCA")

		if not frappe.db.exists("Salary Component", _BASIC_COMPONENT):
			make_salary_component(_EARNINGS, False, [_SLIP_COMPANY])

		frappe.db.delete("Salary Slip", {"employee_name": _EMAIL})
		emp = frappe.db.get_value("Employee", {"employee_name": _EMAIL}, "name")
		if emp:
			frappe.db.delete("Salary Structure Assignment", {"employee": emp})

		self._disable_multi_company()
		self.slip = self._make_submitted_slip()

	def tearDown(self):
		frappe.db.rollback()
		frappe.clear_cache(doctype="Payroll Settings")

	def _save_settings(self, values: dict, rows: list[dict] | None = None):
		settings = frappe.get_doc("Payroll Settings")
		settings.email_salary_slip_to_employee = 0
		settings.update(values)
		settings.set(COMPANY_SETTINGS_FIELD, [])
		for row in rows or []:
			settings.append(COMPANY_SETTINGS_FIELD, row)
		settings.flags.ignore_permissions = True
		settings.save()
		frappe.clear_cache(doctype="Payroll Settings")

	def _disable_multi_company(self):
		self._save_settings(
			{
				MULTI_COMPANY_FIELD: 0,
				"enable_esic": 1,
				"enable_lwf": 1,
				"enable_epf": 1,
			}
		)

	def _exclude_slip_company(self):
		"""Turn on multi-company payroll listing only a company the slip does not belong to."""
		self._save_settings(
			{
				MULTI_COMPANY_FIELD: 1,
				"enable_esic": 1,
				"enable_lwf": 1,
				"enable_epf": 1,
			},
			[{"company": _OTHER_COMPANY, "esic_registration_number": "31000123450000"}],
		)

	def _make_submitted_slip(self):
		employee = make_employee(_EMAIL, company=_SLIP_COMPANY)
		frappe.db.set_value(
			"Employee",
			employee,
			{"uan_number": "100200300400", "pf_name": "MC Gate Test"},
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

	def _rows_for_slip(self, report):
		return [r for r in report.get_data(dict(_FILTERS)) if r["employee"] == self.slip.employee]

	def test_esic_register_includes_slip_before_exclusion(self):
		self.assertEqual(len(self._rows_for_slip(esic_register)), 1)

	def test_esic_register_excludes_unconfigured_company(self):
		self._exclude_slip_company()
		self.assertEqual(self._rows_for_slip(esic_register), [])

	def test_lwf_register_includes_slip_before_exclusion(self):
		self.assertEqual(len(self._rows_for_slip(lwf_register)), 1)

	def test_lwf_register_excludes_unconfigured_company(self):
		self._exclude_slip_company()
		self.assertEqual(self._rows_for_slip(lwf_register), [])

	def test_epf_register_includes_slip_before_exclusion(self):
		self.assertEqual(len(self._rows_for_slip(epf_register)), 1)

	def test_epf_register_excludes_unconfigured_company(self):
		self._exclude_slip_company()
		self.assertEqual(self._rows_for_slip(epf_register), [])

	def test_ecr_file_excludes_unconfigured_company(self):
		self._exclude_slip_company()
		result = epf_register.get_ecr_file({**_FILTERS, "company": _SLIP_COMPANY})
		self.assertEqual(result["row_count"], 0)
		self.assertEqual(result["content"], "")

	def test_ecr_file_requires_company_in_multi_company_mode(self):
		self._exclude_slip_company()
		self.assertRaises(frappe.ValidationError, epf_register.get_ecr_file, dict(_FILTERS))

	def test_ecr_file_includes_slip_in_single_company_mode(self):
		result = epf_register.get_ecr_file(dict(_FILTERS))
		self.assertEqual(result["row_count"], 1)
		self.assertTrue(result["content"].startswith("100200300400#~#"))
