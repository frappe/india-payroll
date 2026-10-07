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

from india_payroll.india_payroll.epf import (
	EDLI_COMPONENT,
	EPF_ADMIN_COMPONENT,
	EPF_EMPLOYER_COMPONENT,
	EPF_EMPLOYER_COMPONENTS,
	EPS_COMPONENT,
)
from india_payroll.india_payroll.esi import (
	EMPLOYER_ESI_RATE,
	ESI_EMPLOYER_COMPONENT,
	ESI_WAGE_CEILING,
)
from india_payroll.install import create_epf_components, create_esi_components

_BEFORE_REVISION = "2026-04-01"
_AFTER_REVISION = "2026-10-01"

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
		create_epf_components()
		make_salary_component(_EARNINGS, False, ["_Test Company"])
		frappe.db.set_single_value("Payroll Settings", "enable_esic", 1)
		frappe.db.set_single_value("Payroll Settings", "enable_epf", 1)

	def _make_assignment(self, email, structure_name, base, other_details=None, config=None, from_date=None):
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
		ssa = create_salary_structure_assignment(
			employee,
			structure.name,
			base=base,
			company="_Test Company",
			currency="INR",
			from_date=from_date,
		)

		if config:
			ssa.update(config)
			ssa.calculate_ctc_and_gross()

		return ssa

	def _employer_rows(self, ssa):
		return ssa.get_evaluated_components()["employer_contributions"]

	def _employer_amounts(self, ssa) -> dict:
		return {r.salary_component: r.default_amount for r in self._employer_rows(ssa)}

	def _make_epf_assignment(self, email, base, from_date, **config):
		return self._make_assignment(
			email,
			f"CTC EPF {email}",
			base,
			config={"epf_applicable": 1, **config},
			from_date=from_date,
		)

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

	def test_employer_epf_at_the_previous_ceiling(self):
		ssa = self._make_epf_assignment("ctc_epf_old@indiapayroll.com", 15_000.0, _BEFORE_REVISION)

		amounts = self._employer_amounts(ssa)

		# 12% of 15,000 is 1,800, of which EPS takes 8.33% (1,250, half-up)
		self.assertEqual(amounts[EPS_COMPONENT], 1_250)
		self.assertEqual(amounts[EPF_EMPLOYER_COMPONENT], 550)
		self.assertEqual(amounts[EDLI_COMPONENT], 75)
		self.assertEqual(amounts[EPF_ADMIN_COMPONENT], 75)

	def test_employer_epf_at_the_revised_ceiling(self):
		base = 25_000.0
		ssa = self._make_epf_assignment("ctc_epf_new@indiapayroll.com", base, _AFTER_REVISION)

		amounts = self._employer_amounts(ssa)

		# 12% of 25,000 is 3,000, of which EPS takes 8.33% (2,083, half-up)
		self.assertEqual(amounts[EPS_COMPONENT], 2_083)
		self.assertEqual(amounts[EPF_EMPLOYER_COMPONENT], 917)
		self.assertEqual(amounts[EDLI_COMPONENT], 125)
		self.assertEqual(amounts[EPF_ADMIN_COMPONENT], 125)
		self.assertNotIn(ESI_EMPLOYER_COMPONENT, amounts)

		self.assertAlmostEqual(ssa.ctc - ssa.annual_gross_earning, (3_000 + 125 + 125) * 12, places=2)

	def test_no_eps_above_the_revised_ceiling(self):
		ssa = self._make_epf_assignment("ctc_epf_above@indiapayroll.com", 30_000.0, _AFTER_REVISION)

		amounts = self._employer_amounts(ssa)

		self.assertNotIn(EPS_COMPONENT, amounts)
		self.assertEqual(amounts[EPF_EMPLOYER_COMPONENT], 3_000)
		self.assertEqual(amounts[EDLI_COMPONENT], 125)
		self.assertEqual(amounts[EPF_ADMIN_COMPONENT], 125)

	def test_no_eps_above_the_previous_ceiling(self):
		ssa = self._make_epf_assignment("ctc_epf_above_old@indiapayroll.com", 20_000.0, _BEFORE_REVISION)

		amounts = self._employer_amounts(ssa)

		self.assertNotIn(EPS_COMPONENT, amounts)
		self.assertEqual(amounts[EPF_EMPLOYER_COMPONENT], 1_800)

	def test_contribute_on_actual_raises_only_employer_epf(self):
		ssa = self._make_epf_assignment(
			"ctc_epf_actual@indiapayroll.com",
			30_000.0,
			_AFTER_REVISION,
			contribute_on_actual_pf_wage=1,
		)

		amounts = self._employer_amounts(ssa)

		self.assertEqual(amounts[EPF_EMPLOYER_COMPONENT], 3_600)
		self.assertEqual(amounts[EDLI_COMPONENT], 125)
		self.assertEqual(amounts[EPF_ADMIN_COMPONENT], 125)

	def test_computed_epf_overrides_a_flat_structure_row(self):
		ssa = self._make_assignment(
			"ctc_epf_flat@indiapayroll.com",
			"CTC EPF Flat Structure",
			30_000.0,
			other_details={
				"employer_contributions": [
					{"salary_component": EPF_EMPLOYER_COMPONENT, "abbr": "EPF", "amount": 1_800}
				]
			},
			config={"epf_applicable": 1},
			from_date=_AFTER_REVISION,
		)

		rows = [r for r in self._employer_rows(ssa) if r.salary_component == EPF_EMPLOYER_COMPONENT]
		self.assertEqual(len(rows), 1)
		self.assertEqual(rows[0].default_amount, 3_000)

	def test_no_employer_epf_when_not_applicable(self):
		ssa = self._make_epf_assignment(
			"ctc_epf_off@indiapayroll.com", 30_000.0, _AFTER_REVISION, epf_applicable=0
		)

		amounts = self._employer_amounts(ssa)

		for component in EPF_EMPLOYER_COMPONENTS:
			self.assertNotIn(component, amounts)
		self.assertAlmostEqual(ssa.ctc, ssa.annual_gross_earning, places=2)

	def test_no_employer_epf_when_epf_disabled(self):
		frappe.db.set_single_value("Payroll Settings", "enable_epf", 0)
		ssa = self._make_epf_assignment("ctc_epf_disabled@indiapayroll.com", 30_000.0, _AFTER_REVISION)

		self.assertNotIn(EPF_EMPLOYER_COMPONENT, self._employer_amounts(ssa))
		self.assertAlmostEqual(ssa.ctc, ssa.annual_gross_earning, places=2)
