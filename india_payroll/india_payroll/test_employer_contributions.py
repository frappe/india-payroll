# Copyright (c) 2026, Frappe Technologies Pvt. Ltd. and contributors
# License: GNU General Public License v3. See license.txt

import frappe
from erpnext.setup.doctype.employee.test_employee import make_employee
from frappe.utils import flt
from hrms.payroll.doctype.salary_slip.test_salary_slip import make_salary_component
from hrms.payroll.doctype.salary_structure.test_salary_structure import (
	create_salary_structure_assignment,
	make_salary_structure,
)
from hrms.tests.utils import HRMSTestSuite

from india_payroll.india_payroll.esi import (
	EMPLOYER_ESI_RATE,
	ESI_EMPLOYER_COMPONENT,
	ESI_WAGE_CEILING,
)
from india_payroll.install import create_esi_components

_BASIC = "CTC Test Basic"
_EARNINGS = [
	{
		"salary_component": _BASIC,
		"abbr": "CTCB",
		"formula": "base",
		"type": "Earning",
		"amount_based_on_formula": 1,
		"depends_on_payment_days": 0,
	}
]


class TestEmployerContributions(HRMSTestSuite):
	def setUp(self):
		create_esi_components()
		make_salary_component(_EARNINGS, False, ["_Test Company"])
		frappe.db.set_single_value("Payroll Settings", "enable_esic", 1)

	def _make_assignment(self, email, structure_name, base, other_details=None):
		employee = make_employee(email, company="_Test Company")
		structure = make_salary_structure(
			structure_name,
			"Monthly",
			company="_Test Company",
			currency="INR",
			earnings=_EARNINGS,
			deductions=[],
			other_details=other_details,
		)
		return create_salary_structure_assignment(
			employee, structure.name, base=base, company="_Test Company", currency="INR"
		)

	def _employer_rows(self, ssa):
		return ssa.get_evaluated_components()["employer_contributions"]

	def test_employer_esi_reaches_ctc(self):
		base = 15_000.0
		ssa = self._make_assignment("ctc_esi@indiapayroll.com", "CTC ESI Structure", base)

		rows = [r for r in self._employer_rows(ssa) if r.salary_component == ESI_EMPLOYER_COMPONENT]
		self.assertEqual(len(rows), 1, "Employer ESI must be injected into the CTC components")

		expected = flt(base * EMPLOYER_ESI_RATE, 2)  # 487.50
		self.assertAlmostEqual(rows[0].default_amount, expected, places=2)

		# CTC exceeds annual gross by exactly the employer's yearly cost
		self.assertAlmostEqual(ssa.annual_gross_earning, base * 12, places=2)
		self.assertAlmostEqual(ssa.ctc - ssa.annual_gross_earning, expected * 12, places=2)

	def test_no_employer_esi_above_ceiling(self):
		ssa = self._make_assignment(
			"ctc_esi_above@indiapayroll.com", "CTC ESI Above Structure", ESI_WAGE_CEILING + 1_000
		)

		rows = [r for r in self._employer_rows(ssa) if r.salary_component == ESI_EMPLOYER_COMPONENT]
		self.assertEqual(len(rows), 0)
		self.assertAlmostEqual(ssa.ctc, ssa.annual_gross_earning, places=2)

	def test_no_employer_esi_when_esic_disabled(self):
		frappe.db.set_single_value("Payroll Settings", "enable_esic", 0)
		ssa = self._make_assignment("ctc_esi_off@indiapayroll.com", "CTC ESI Off Structure", 15_000)

		rows = [r for r in self._employer_rows(ssa) if r.salary_component == ESI_EMPLOYER_COMPONENT]
		self.assertEqual(len(rows), 0)
		self.assertAlmostEqual(ssa.ctc, ssa.annual_gross_earning, places=2)

	def test_structure_row_is_upserted_not_duplicated(self):
		"""A company that also lists the component on the structure gets the
		statutory value, not a second row."""
		base = 15_000.0
		ssa = self._make_assignment(
			"ctc_esi_dupe@indiapayroll.com",
			"CTC ESI Duplicate Structure",
			base,
			other_details={
				"employer_contributions": [
					{"salary_component": ESI_EMPLOYER_COMPONENT, "abbr": "ERESI", "amount": 9_999}
				]
			},
		)

		rows = [r for r in self._employer_rows(ssa) if r.salary_component == ESI_EMPLOYER_COMPONENT]
		self.assertEqual(len(rows), 1, "must upsert, not duplicate")
		self.assertAlmostEqual(rows[0].default_amount, flt(base * EMPLOYER_ESI_RATE, 2), places=2)
		self.assertAlmostEqual(
			ssa.ctc - ssa.annual_gross_earning, flt(base * EMPLOYER_ESI_RATE, 2) * 12, places=2
		)

	def test_hook_does_not_mutate_cached_structure(self):
		ssa = self._make_assignment("ctc_esi_cache@indiapayroll.com", "CTC ESI Cache Structure", 15_000)
		ssa.get_evaluated_components()

		cached = frappe.get_cached_doc("Salary Structure", ssa.salary_structure)
		self.assertEqual(len(cached.employer_contributions), 0)
