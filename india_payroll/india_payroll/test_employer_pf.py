import unittest
from decimal import Decimal
from unittest.mock import MagicMock, patch

import frappe
from india_payroll.india_payroll import employer_pf
from india_payroll.india_payroll.employer_pf import (
	CeilingShare,
	EmployerPFRates,
	calculate_employer_pf,
	cap_pf_wage,
	get_ceiling_shares,
	round_half_up,
)

RATES = EmployerPFRates(epf=Decimal("12"), eps=Decimal("8.33"), edli=Decimal("0.5"), admin=Decimal("0.5"))
CUTOFF = "2026-09-17"
OCTOBER = get_ceiling_shares(
	"2026-10-01", "2026-10-31", revision_date=CUTOFF, previous_ceiling=15000, current_ceiling=25000
)

SETTINGS = frappe._dict(
	employee_pf_component="Provident Fund",
	employer_epf_component="Employer Provident Fund",
	employer_eps_component="Employer Pension Scheme",
	employer_edli_component="Employees Deposit Linked Insurance",
	employer_pf_admin_component="EPF Admin Charges",
	employer_pf_wage_ceiling=25000,
	employer_pf_previous_wage_ceiling=15000,
	eps_eligibility_cutoff_date=CUTOFF,
	rates=RATES,
)
OUR_COMPONENTS = [
	"Employer Provident Fund",
	"Employer Pension Scheme",
	"Employees Deposit Linked Insurance",
	"EPF Admin Charges",
]


class MockDocument(frappe._dict):
	def append(self, field, value):
		row = frappe._dict(value)
		self.setdefault(field, []).append(row)
		return row

	def set(self, field, value):
		self[field] = value


class TestEmployerPFCalculation(unittest.TestCase):
	def test_specification_table(self):
		cases = [
			(10000, True, 10000, 367, 833, 50, 50),
			(18000, True, 18000, 661, 1499, 90, 90),
			(20000, True, 20000, 734, 1666, 100, 100),
			(22000, True, 22000, 807, 1833, 110, 110),
			(25000, True, 25000, 917, 2083, 125, 125),
			(30000, True, 25000, 917, 2083, 125, 125),
			(30000, False, 25000, 3000, 0, 125, 125),
			(50000, False, 25000, 3000, 0, 125, 125),
		]
		for wage, eligible, capped, epf, eps, edli, admin in cases:
			with self.subTest(wage=wage, eps_eligible=eligible):
				amounts = calculate_employer_pf(wage, eps_eligible=eligible, rates=RATES, ceilings=OCTOBER)
				self.assertEqual((amounts.capped_wage, amounts.epf_wage), (capped, capped))
				self.assertEqual(
					(amounts.employer_epf, amounts.employer_eps, amounts.edli, amounts.admin),
					(epf, eps, edli, admin),
				)
				self.assertEqual(amounts.employer_epf + amounts.employer_eps, round_half_up(capped * Decimal("0.12")))

	def test_contribution_on_actual_wage(self):
		cases = [
			(50000, False, True, 6000, 6000, 0, 125, 250),
			(50000, True, True, 6000, 3917, 2083, 125, 250),
			(50000, False, False, 3000, 3000, 0, 125, 125),
			(5000, True, True, 600, 183, 417, 25, 25),
		]
		for wage, eligible, actual, employee, epf, eps, edli, admin in cases:
			with self.subTest(wage=wage, eps_eligible=eligible, contribute_on_actual=actual):
				amounts = calculate_employer_pf(
					wage, eps_eligible=eligible, contribute_on_actual=actual, rates=RATES, ceilings=OCTOBER
				)
				self.assertEqual(
					(
						amounts.employee_epf,
						amounts.employer_epf,
						amounts.employer_eps,
						amounts.edli,
						amounts.admin,
					),
					(employee, epf, eps, edli, admin),
				)
				self.assertLessEqual(amounts.capped_wage, 25000)

	def test_totals(self):
		amounts = calculate_employer_pf(25000, eps_eligible=True, rates=RATES, ceilings=OCTOBER)
		self.assertEqual(amounts.employee_epf, 3000)
		self.assertEqual(amounts.total_employer_cost, 917 + 2083 + 125 + 125)
		self.assertEqual(amounts.credited_to_pf_account, 3000 + 917)

	def test_half_up_rounding(self):
		self.assertEqual(round_half_up(Decimal("2082.5")), 2083)
		self.assertEqual(round_half_up(Decimal("1249.5")), 1250)
		self.assertEqual(round_half_up(Decimal("1499.4")), 1499)
		self.assertEqual(round(2082.5), 2082)

	def test_ceiling_is_split_by_days_across_the_cutoff(self):
		ceilings = get_ceiling_shares(
			"2026-09-01", "2026-09-30", revision_date=CUTOFF, previous_ceiling=15000, current_ceiling=25000
		)
		self.assertEqual(
			[(c.ceiling, c.share) for c in ceilings],
			[(15000, Decimal(16) / 30), (25000, Decimal(14) / 30)],
		)
		self.assertEqual(cap_pf_wage(Decimal(20000), ceilings), Decimal("17333.33"))

	def test_service_days_never_increase_the_revised_share(self):
		joined = get_ceiling_shares(
			"2026-09-01",
			"2026-09-30",
			revision_date=CUTOFF,
			previous_ceiling=15000,
			current_ceiling=25000,
			joining_date="2026-09-07",
		)
		self.assertEqual(joined[1].share, Decimal(14) / 30)

		relieved = get_ceiling_shares(
			"2026-09-01",
			"2026-09-30",
			revision_date=CUTOFF,
			previous_ceiling=15000,
			current_ceiling=25000,
			relieving_date="2026-09-20",
		)
		self.assertEqual([c.share for c in relieved], [Decimal(16) / 20, Decimal(4) / 20])

	def test_before_the_cutoff_only_the_old_ceiling_applies(self):
		ceilings = get_ceiling_shares(
			"2026-08-01", "2026-08-31", revision_date=CUTOFF, previous_ceiling=15000, current_ceiling=25000
		)
		self.assertEqual(ceilings, [CeilingShare(Decimal(15000), Decimal(1))])
		amounts = calculate_employer_pf(20000, eps_eligible=True, rates=RATES, ceilings=ceilings)
		self.assertEqual((amounts.employer_eps, amounts.employer_epf), (1250, 550))


class TestSalarySlipHook(unittest.TestCase):
	def make_slip(self, basic=25000, pf=3000, docstatus=0, company_country="India", **values):
		slip = MockDocument(
			doctype="Salary Slip",
			employee="_T-EMP-PF",
			company="_Test Company",
			start_date="2026-10-01",
			end_date="2026-10-31",
			salary_structure="_Test Structure",
			docstatus=docstatus,
			employer_contributions=[],
			earnings=[],
			deductions=[],
			**values,
		)
		slip.append("earnings", {"salary_component": "Basic", "amount": basic})
		slip.append("earnings", {"salary_component": "House Rent Allowance", "amount": 10000})
		if pf:
			slip.append("deductions", {"salary_component": "Provident Fund", "amount": pf})
		self.country = company_country
		return slip

	def run_hook(self, slip, settings=SETTINGS, eps_eligible=1, contribute_on_actual=0):
		def cached_value(doctype, name, field):
			if doctype == "Company":
				return self.country
			if doctype == "Employee":
				return (None, None)
			raise AssertionError(doctype)

		with (
			patch.object(employer_pf, "get_settings", return_value=settings),
			patch.object(employer_pf.frappe, "get_cached_value", side_effect=cached_value),
			patch.object(
				employer_pf,
				"get_slip_ssa_values",
				return_value=frappe._dict(
					eps_eligible=eps_eligible, contribute_on_actual_pf_wage=contribute_on_actual
				),
			),
		):
			employer_pf.set_employer_pf_contributions(slip)

	def rows(self, slip):
		return {row.salary_component: row.amount for row in slip.employer_contributions}

	def test_rows_are_added(self):
		slip = self.make_slip()
		self.run_hook(slip)

		self.assertEqual(
			self.rows(slip),
			{
				"Employer Provident Fund": 917,
				"Employer Pension Scheme": 2083,
				"Employees Deposit Linked Insurance": 125,
				"EPF Admin Charges": 125,
			},
		)
		self.assertEqual(slip.pf_wage_considered, 25000)
		self.assertEqual(slip.total_employer_pf_cost, 3250)
		self.assertEqual(slip.pf_credited_to_employee_account, 3917)
		self.assertFalse(
			any(row.salary_component in OUR_COMPONENTS for row in slip.earnings + slip.deductions)
		)

	def test_not_eps_eligible(self):
		slip = self.make_slip(basic=50000)
		self.run_hook(slip, eps_eligible=0)
		self.assertEqual(self.rows(slip)["Employer Provident Fund"], 3000)
		self.assertEqual(self.rows(slip)["Employer Pension Scheme"], 0)

	def test_contribution_on_actual_wage_on_the_slip(self):
		slip = self.make_slip(basic=50000, pf=6000)
		self.run_hook(slip, contribute_on_actual=1)
		self.assertEqual(
			self.rows(slip),
			{
				"Employer Provident Fund": 3917,
				"Employer Pension Scheme": 2083,
				"Employees Deposit Linked Insurance": 125,
				"EPF Admin Charges": 250,
			},
		)
		self.assertEqual(slip.pf_wage_considered, 50000)
		self.assertEqual(slip.pf_credited_to_employee_account, 6000 + 3917)

	def test_disabled_setting_adds_nothing(self):
		slip = self.make_slip()
		self.run_hook(slip, settings=None)
		self.assertEqual(slip.employer_contributions, [])

	def test_saving_twice_keeps_four_rows(self):
		slip = self.make_slip()
		slip.append("employer_contributions", {"salary_component": "Employer State Insurance", "amount": 500})
		self.run_hook(slip)
		self.run_hook(slip)

		components = [row.salary_component for row in slip.employer_contributions]
		self.assertEqual(sorted(c for c in components if c in OUR_COMPONENTS), sorted(OUR_COMPONENTS))
		self.assertEqual(len(components), 5)
		self.assertIn("Employer State Insurance", components)

	def test_loss_of_pay_month_uses_the_prorated_wage(self):
		slip = self.make_slip(basic=10000, pf=1200, payment_days=15, total_working_days=30)
		self.run_hook(slip)
		self.assertEqual(slip.pf_wage_considered, 10000)
		self.assertEqual(self.rows(slip)["Employer Pension Scheme"], 833)
		self.assertEqual(self.rows(slip)["Employer Provident Fund"], 367)

	def test_additional_salary_is_not_pf_wage(self):
		slip = self.make_slip(basic=10000, pf=1200)
		slip.append(
			"earnings", {"salary_component": "Basic", "amount": 5000, "additional_salary": "HR-ADS-1"}
		)
		self.run_hook(slip)
		self.assertEqual(slip.pf_wage_considered, 10000)

	def test_employee_without_pf_is_skipped(self):
		slip = self.make_slip(pf=0)
		slip.append("employer_contributions", {"salary_component": "Employer Pension Scheme", "amount": 2083})
		self.run_hook(slip)
		self.assertEqual(slip.employer_contributions, [])
		self.assertEqual(slip.total_employer_pf_cost, 0)

	def test_company_outside_india_is_skipped(self):
		slip = self.make_slip(company_country="Kenya")
		self.run_hook(slip)
		self.assertEqual(slip.employer_contributions, [])

	def test_submitted_and_cancelled_slips_are_untouched(self):
		for docstatus in (1, 2):
			with self.subTest(docstatus=docstatus):
				slip = self.make_slip(docstatus=docstatus)
				slip.append("employer_contributions", {"salary_component": "EPF Admin Charges", "amount": 1})
				self.run_hook(slip)
				self.assertEqual(self.rows(slip), {"EPF Admin Charges": 1})

	def test_amended_slip_is_recalculated(self):
		slip = self.make_slip(amended_from="Sal Slip/_T-EMP-PF/00001")
		slip.append("employer_contributions", {"salary_component": "EPF Admin Charges", "amount": 1})
		self.run_hook(slip)
		self.assertEqual(self.rows(slip)["EPF Admin Charges"], 125)
		self.assertEqual(len(slip.employer_contributions), 4)

	def test_missing_settings_skip_with_a_message(self):
		slip = self.make_slip()
		with (
			patch.object(employer_pf.frappe, "msgprint") as msgprint,
			patch.object(employer_pf.frappe, "get_meta") as get_meta,
		):
			get_meta.return_value.get_label = lambda f: f
			self.run_hook(slip, settings=frappe._dict(SETTINGS, employer_eps_component=None))
		self.assertEqual(slip.employer_contributions, [])
		msgprint.assert_called_once()


class TestEPSEligibleWarning(unittest.TestCase):
	def run_validate(self, pf_wage, joining_date, eps_eligible=1):
		ssa = MockDocument(
			doctype="Salary Structure Assignment",
			employee="_T-EMP-PF",
			salary_structure="_Test Structure",
			eps_eligible=eps_eligible,
			currency="INR",
		)
		with (
			patch.object(employer_pf, "get_settings", return_value=SETTINGS),
			patch.object(employer_pf, "get_assignment_pf_wage", return_value=pf_wage),
			patch.object(employer_pf.frappe, "get_cached_value", return_value=joining_date),
			patch.object(employer_pf.frappe, "msgprint") as msgprint,
			patch.object(employer_pf, "fmt_money", side_effect=lambda v, **kw: str(v)),
			patch.object(employer_pf.frappe, "format", side_effect=lambda v, t: str(v)),
			patch.object(employer_pf, "_", side_effect=lambda s: s),
		):
			employer_pf.warn_if_eps_wage_above_ceiling(ssa)
		return msgprint

	def test_warns_when_wage_is_above_the_ceiling(self):
		self.run_validate(30000, "2020-01-01").assert_called_once()

	def test_no_warning_within_the_ceiling(self):
		self.run_validate(20000, "2020-01-01").assert_not_called()

	def test_no_warning_when_not_ticked(self):
		self.run_validate(30000, "2020-01-01", eps_eligible=0).assert_not_called()


class TestJointDeclarationWarning(unittest.TestCase):
	def run_validate(self, contribute_on_actual, declaration, doc_attached=None):
		ssa = MockDocument(
			doctype="Salary Structure Assignment",
			contribute_on_actual_pf_wage=contribute_on_actual,
			joint_declaration_on_file=declaration,
			joint_declaration_document=doc_attached,
		)
		with (
			patch.object(employer_pf.frappe, "msgprint") as msgprint,
			patch.object(employer_pf, "_", side_effect=lambda s: s),
		):
			employer_pf.warn_if_joint_declaration_missing(ssa)
		return msgprint

	def test_warns_without_the_declaration(self):
		self.run_validate(1, 0).assert_called_once()

	def test_no_warning_with_the_declaration(self):
		self.run_validate(1, 1).assert_not_called()

	def test_no_warning_with_the_document(self):
		self.run_validate(1, 0, doc_attached="/files/joint_decl.pdf").assert_not_called()

	def test_no_warning_on_capped_wage(self):
		self.run_validate(0, 0).assert_not_called()


class TestJointDeclarationOnSubmit(unittest.TestCase):
	def test_submit_allowed_with_document(self):
		doc = MockDocument(
			contribute_on_actual_pf_wage=1,
			joint_declaration_document="/files/signed_para26_6.pdf",
			joint_declaration_on_file=0,
		)
		employer_pf.validate_joint_declaration_on_submit(doc)
		self.assertEqual(doc.joint_declaration_on_file, 1)

	def test_submit_allowed_with_on_file_checkbox(self):
		doc = MockDocument(
			contribute_on_actual_pf_wage=1,
			joint_declaration_document=None,
			joint_declaration_on_file=1,
		)
		employer_pf.validate_joint_declaration_on_submit(doc)

	def test_submit_blocked_without_declaration(self):
		doc = MockDocument(
			contribute_on_actual_pf_wage=1,
			joint_declaration_document=None,
			joint_declaration_on_file=0,
		)
		with self.assertRaises(Exception):
			employer_pf.validate_joint_declaration_on_submit(doc)

	def test_submit_allowed_when_contribute_on_actual_disabled(self):
		doc = MockDocument(
			contribute_on_actual_pf_wage=0,
			joint_declaration_document=None,
			joint_declaration_on_file=0,
		)
		employer_pf.validate_joint_declaration_on_submit(doc)


if __name__ == "__main__":
	unittest.main()
