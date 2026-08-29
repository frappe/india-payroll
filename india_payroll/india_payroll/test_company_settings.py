# Copyright (c) 2026, Frappe Technologies Pvt. Ltd. and contributors
# License: GNU General Public License v3. See license.txt

import frappe
from hrms.tests.utils import HRMSTestSuite

from india_payroll.india_payroll.company_settings import (
	COMPANY_SETTINGS_FIELD,
	MULTI_COMPANY_FIELD,
	get_applicable_companies,
	get_registration_number,
	is_statutory_enabled,
	validate_company_payroll_settings,
)

_COMPANY_A = "India Payroll MC Company A"
_COMPANY_B = "India Payroll MC Company B"


def create_company(name: str, abbr: str) -> str:
	if not frappe.db.exists("Company", name):
		frappe.get_doc(
			{
				"doctype": "Company",
				"company_name": name,
				"abbr": abbr,
				"default_currency": "INR",
				"country": "India",
			}
		).insert()
	return name


class TestCompanyPayrollSettings(HRMSTestSuite):
	def setUp(self):
		create_company(_COMPANY_A, "IPMCA")
		create_company(_COMPANY_B, "IPMCB")
		self.settings = frappe.get_doc("Payroll Settings")

	def tearDown(self):
		frappe.db.rollback()
		frappe.clear_cache(doctype="Payroll Settings")

	def _apply(self, values: dict, rows: list[dict] | None = None):
		settings = frappe.get_doc("Payroll Settings")
		settings.update(values)
		settings.set(COMPANY_SETTINGS_FIELD, [])
		for row in rows or []:
			settings.append(COMPANY_SETTINGS_FIELD, row)
		settings.flags.ignore_permissions = True
		settings.save()
		frappe.clear_cache(doctype="Payroll Settings")
		return settings

	def test_single_company_mode_unaffected(self):
		self._apply(
			{
				MULTI_COMPANY_FIELD: 0,
				"enable_esic": 1,
				"enable_epf": 0,
				"esic_registration_number": "31000123450000",
			}
		)

		self.assertTrue(is_statutory_enabled("esic", _COMPANY_A))
		self.assertTrue(is_statutory_enabled("esic", _COMPANY_B))
		self.assertFalse(is_statutory_enabled("epf", _COMPANY_A))
		self.assertEqual(get_registration_number("esic", _COMPANY_A), "31000123450000")

	def test_configured_company_uses_its_own_registrations(self):
		self._apply(
			{MULTI_COMPANY_FIELD: 1, "enable_esic": 1, "enable_epf": 1},
			[
				{
					"company": _COMPANY_A,
					"esic_registration_number": "31000123450000",
					"epf_establishment_code": "MHBAN0012345",
				}
			],
		)

		self.assertTrue(is_statutory_enabled("esic", _COMPANY_A))
		self.assertEqual(get_registration_number("esic", _COMPANY_A), "31000123450000")
		self.assertEqual(get_registration_number("epf", _COMPANY_A), "MHBAN0012345")

	def test_unconfigured_company_is_not_applicable(self):
		self._apply(
			{MULTI_COMPANY_FIELD: 1, "enable_esic": 1, "enable_lwf": 1},
			[{"company": _COMPANY_A, "esic_registration_number": "31000123450000"}],
		)

		self.assertFalse(is_statutory_enabled("esic", _COMPANY_B))
		self.assertFalse(is_statutory_enabled("lwf", _COMPANY_B))
		self.assertIsNone(get_registration_number("esic", _COMPANY_B))

	def test_global_toggle_still_gates_configured_company(self):
		self._apply(
			{MULTI_COMPANY_FIELD: 1, "enable_esic": 0},
			[{"company": _COMPANY_A, "esic_registration_number": "31000123450000"}],
		)

		self.assertFalse(is_statutory_enabled("esic", _COMPANY_A))

	def test_global_registration_ignored_in_multi_company_mode(self):
		self._apply(
			{MULTI_COMPANY_FIELD: 1, "enable_esic": 1, "esic_registration_number": "99999999999999999"},
			[{"company": _COMPANY_A, "esic_registration_number": "31000123450000"}],
		)

		self.assertEqual(get_registration_number("esic", _COMPANY_A), "31000123450000")

	def test_duplicate_company_rejected(self):
		doc = frappe.get_doc("Payroll Settings")
		doc.set(MULTI_COMPANY_FIELD, 1)
		doc.set(COMPANY_SETTINGS_FIELD, [])
		doc.append(COMPANY_SETTINGS_FIELD, {"company": _COMPANY_A})
		doc.append(COMPANY_SETTINGS_FIELD, {"company": _COMPANY_A})

		self.assertRaises(frappe.ValidationError, validate_company_payroll_settings, doc)

	def test_multi_company_without_rows_rejected(self):
		doc = frappe.get_doc("Payroll Settings")
		doc.set(MULTI_COMPANY_FIELD, 1)
		doc.set(COMPANY_SETTINGS_FIELD, [])

		self.assertRaises(frappe.ValidationError, validate_company_payroll_settings, doc)

	def test_applicable_companies_unrestricted_in_single_company_mode(self):
		self._apply({MULTI_COMPANY_FIELD: 0, "enable_esic": 1, "enable_lwf": 1, "enable_epf": 1})

		for statute in ("esic", "lwf", "epf"):
			self.assertIsNone(get_applicable_companies(statute))

	def test_applicable_companies_limited_to_configured_rows(self):
		self._apply(
			{MULTI_COMPANY_FIELD: 1, "enable_esic": 1, "enable_lwf": 1, "enable_epf": 1},
			[{"company": _COMPANY_A, "esic_registration_number": "31000123450000"}],
		)

		for statute in ("esic", "lwf", "epf"):
			self.assertEqual(get_applicable_companies(statute), [_COMPANY_A])

	def test_applicable_companies_empty_when_statute_globally_off(self):
		self._apply(
			{MULTI_COMPANY_FIELD: 1, "enable_esic": 0, "enable_lwf": 1},
			[{"company": _COMPANY_A}],
		)

		self.assertEqual(get_applicable_companies("esic"), [])
		self.assertEqual(get_applicable_companies("lwf"), [_COMPANY_A])

	def test_registration_numbers_are_stripped(self):
		doc = frappe.get_doc("Payroll Settings")
		doc.set(MULTI_COMPANY_FIELD, 1)
		doc.set(COMPANY_SETTINGS_FIELD, [])
		doc.append(
			COMPANY_SETTINGS_FIELD,
			{"company": _COMPANY_A, "esic_registration_number": "  31000123450000  "},
		)

		validate_company_payroll_settings(doc)

		self.assertEqual(doc.get(COMPANY_SETTINGS_FIELD)[0].esic_registration_number, "31000123450000")
