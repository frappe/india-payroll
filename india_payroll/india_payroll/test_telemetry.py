# Copyright (c) 2026, Frappe Technologies Pvt. Ltd. and contributors
# License: GNU General Public License v3. See license.txt

from unittest.mock import patch

import frappe
from frappe.utils import add_days
from hrms.tests.utils import HRMSTestSuite

from india_payroll import telemetry
from india_payroll.india_payroll.epf import EPF_EMPLOYEE_COMPONENT
from india_payroll.india_payroll.esi import ESI_EMPLOYEE_COMPONENT
from india_payroll.india_payroll.professional_tax import PT_SALARY_COMPONENT
from india_payroll.telemetry import (
	CONVERSION_EVENT,
	FIRST_CAPTURE_MILESTONE,
	INSTALL_MILESTONE,
	MILESTONE_DOCTYPE,
)

FROZEN_TODAY = "2026-03-12"


class TestIndiaPayrollTelemetry(HRMSTestSuite):
	def setUp(self):
		self.captured = []

		def collect(event, app, properties=None):
			self.captured.append((event, app, properties or {}))

		patches = [
			patch("india_payroll.telemetry._skip_context", return_value=False),
			patch("india_payroll.telemetry.is_enabled", return_value=True),
			patch("india_payroll.telemetry._capture", side_effect=collect),
			patch("india_payroll.telemetry.site_age", return_value=400),
			patch("india_payroll.telemetry.today", return_value=FROZEN_TODAY),
		]
		for p in patches:
			p.start()
			self.addCleanup(p.stop)

		frappe.db.delete(MILESTONE_DOCTYPE)
		telemetry._discard_submitted_slips()
		self.addCleanup(telemetry._discard_submitted_slips)

	def events(self, name: str) -> list[dict]:
		return [props for event, _app, props in self.captured if event == name]

	def set_milestone(self, event: str, days_ago: int):
		frappe.db.delete(MILESTONE_DOCTYPE, {"event": event})
		frappe.get_doc({"doctype": MILESTONE_DOCTYPE, "event": event}).insert()
		frappe.db.set_value(
			MILESTONE_DOCTYPE, event, "creation", f"{add_days(FROZEN_TODAY, -days_ago)} 10:00:00"
		)

	def test_events_are_sent_under_the_india_payroll_app(self):
		telemetry.capture("tds_return_submitted", {"quarter": "Q2"})
		self.assertEqual({app for _event, app, _props in self.captured}, {"india_payroll"})

	def test_sanitize_drops_free_text_and_coarsens_unknown_vocabulary(self):
		clean = telemetry.sanitize_properties(
			{"employee": "HR-EMP-0001", "quarter": "Q5", "regime": "new", "count": 3, "flag": True}
		)
		self.assertEqual(clean, {"quarter": "other", "regime": "new", "count": 3, "flag": True})

	def test_nothing_is_sent_when_telemetry_is_disabled(self):
		self.set_milestone(INSTALL_MILESTONE, 1)
		with patch("india_payroll.telemetry.is_enabled", return_value=False):
			telemetry.capture("tds_return_submitted")
			telemetry.capture_first("first_tds_return_created")

		self.assertEqual(self.captured, [])
		self.assertFalse(frappe.db.exists(MILESTONE_DOCTYPE, "first_tds_return_created"))

	def test_first_time_milestone_fires_once_within_the_activation_window(self):
		self.set_milestone(INSTALL_MILESTONE, 4)

		telemetry.capture_first("first_tds_return_created")
		telemetry.capture_first("first_tds_return_created")

		self.assertEqual(self.events("first_tds_return_created"), [{"day_since_install": 5}])

	def test_first_time_milestone_is_skipped_after_the_window_or_without_an_install_date(self):
		telemetry.capture_first("first_epf_deducted")
		self.set_milestone(INSTALL_MILESTONE, telemetry.ACTIVATION_WINDOW_DAYS + 1)
		telemetry.capture_first("first_epf_deducted")

		self.assertEqual(self.events("first_epf_deducted"), [])

	def test_record_install_starts_the_window_and_reports_site_age(self):
		with patch.dict(frappe.flags, {"in_test": False}):
			telemetry.record_install()
			telemetry.record_install()

		self.assertTrue(frappe.db.exists(MILESTONE_DOCTYPE, INSTALL_MILESTONE))
		self.assertEqual(self.events(INSTALL_MILESTONE), [{"site_age": 400}])

	def test_conversion_fires_once_after_the_window(self):
		telemetry.capture("tds_return_submitted")
		self.assertTrue(frappe.db.exists(MILESTONE_DOCTYPE, FIRST_CAPTURE_MILESTONE))
		self.assertEqual(self.events(CONVERSION_EVENT), [])

		self.set_milestone(FIRST_CAPTURE_MILESTONE, telemetry.CONVERSION_WINDOW_DAYS + 1)
		telemetry.capture("tds_return_submitted")
		telemetry.capture("tds_return_submitted")

		self.assertEqual(
			self.events(CONVERSION_EVENT),
			[{"days_since_first_capture": 15, "day_since_install": None, "site_age": 400}],
		)

	def test_background_outcomes_do_not_start_the_conversion_clock(self):
		telemetry.capture_outcome("tds_filing_step_finished", {"step": "validate"})
		self.assertFalse(frappe.db.exists(MILESTONE_DOCTYPE, FIRST_CAPTURE_MILESTONE))

	def test_payroll_settings_update_sends_statutory_flags_only_on_change(self):
		self.set_milestone(INSTALL_MILESTONE, 2)
		settings = frappe.get_doc("Payroll Settings")
		settings.enable_epf = 0
		settings.enable_esic = 0
		settings.save()
		self.captured.clear()

		settings.reload()
		settings.enable_epf = 1
		settings.save()

		[props] = self.events("statutory_settings_updated")
		self.assertTrue(props["epf"])
		self.assertFalse(props["esic"])
		self.assertIn("company_count", props)
		self.assertEqual(self.events("epf_enabled"), [{"day_since_install": 3}])

		self.captured.clear()
		settings.reload()
		settings.save()
		self.assertEqual(self.events("statutory_settings_updated"), [])

	def test_changing_one_toggle_does_not_mark_other_enabled_statutes(self):
		self.set_milestone(INSTALL_MILESTONE, 2)
		settings = frappe.get_doc("Payroll Settings")
		settings.enable_epf = 1
		settings.enable_esic = 0
		settings.save()
		frappe.db.delete(MILESTONE_DOCTYPE, {"event": ["like", "%_enabled"]})
		self.captured.clear()

		settings.reload()
		settings.enable_esic = 1
		settings.save()

		self.assertEqual(len(self.events("statutory_settings_updated")), 1)
		self.assertEqual(self.events("esic_enabled"), [{"day_since_install": 3}])
		self.assertEqual(self.events("epf_enabled"), [])
		self.assertFalse(frappe.db.exists(MILESTONE_DOCTYPE, "epf_enabled"))

		settings.reload()
		settings.enable_esic = 0
		settings.save()
		self.assertEqual(len(self.events("statutory_settings_updated")), 2)
		self.assertEqual(len(self.events("esic_enabled")), 1)

	def test_salary_slip_submit_marks_each_statutory_deduction_once(self):
		self.set_milestone(INSTALL_MILESTONE, 1)
		slip = frappe._dict(
			deductions=[
				frappe._dict(salary_component=EPF_EMPLOYEE_COMPONENT),
				frappe._dict(salary_component=PT_SALARY_COMPONENT),
			]
		)

		telemetry.on_salary_slip_submit(slip)
		telemetry.on_salary_slip_submit(slip)

		self.assertEqual(len(self.events("first_epf_deducted")), 1)
		self.assertEqual(len(self.events("first_professional_tax_deducted")), 1)
		self.assertEqual(self.events("first_esic_deducted"), [])

	def test_filing_step_events_carry_no_identifiers(self):
		doc = frappe._dict(
			name="TDS-RET-0001",
			tan="ABCD12345E",
			quarter="Q1",
			return_type="Original",
			deductees=[{}, {}],
		)
		telemetry.on_filing_step_started(doc, "generate_fvu")
		telemetry.on_filing_step_finished(doc, "generate_fvu", "failed")

		expected = {"step": "generate_fvu", "quarter": "Q1", "return_type": "Original", "deductee_count": 2}
		self.assertEqual(self.events("tds_filing_step_started"), [expected])
		self.assertEqual(self.events("tds_filing_step_finished"), [{**expected, "outcome": "failed"}])

	def test_first_filed_milestone_waits_for_a_successful_e_file(self):
		self.set_milestone(INSTALL_MILESTONE, 1)
		doc = frappe._dict(quarter="Q1", return_type="Original", deductees=[])

		telemetry.on_filing_step_started(doc, "e_file")
		telemetry.on_filing_step_finished(doc, "e_file", "failed")
		telemetry.on_filing_step_finished(doc, "generate_fvu", "succeeded")
		self.assertEqual(self.events("first_tds_return_filed"), [])
		self.assertFalse(frappe.db.exists(MILESTONE_DOCTYPE, "first_tds_return_filed"))

		frappe.db.delete(MILESTONE_DOCTYPE, {"event": FIRST_CAPTURE_MILESTONE})
		telemetry.on_filing_step_finished(doc, "e_file", "succeeded")
		telemetry.on_filing_step_finished(doc, "e_file", "succeeded")

		self.assertEqual(self.events("first_tds_return_filed"), [{"day_since_install": 2, "quarter": "Q1"}])
		self.assertFalse(frappe.db.exists(MILESTONE_DOCTYPE, FIRST_CAPTURE_MILESTONE))

	def submit_batch(self, *slips: str):
		for name in slips:
			telemetry._queue_submitted_slip(name)
		telemetry.flush_submitted_slips()

	def test_slip_batch_counts_each_statutory_component(self):
		self.submit_batch(
			insert_slip([EPF_EMPLOYEE_COMPONENT, ESI_EMPLOYEE_COMPONENT]),
			insert_slip([EPF_EMPLOYEE_COMPONENT], income_tax=True),
		)

		[props] = self.events("salary_slips_submitted")
		self.assertEqual(props["slips_submitted"], 2)
		self.assertEqual(props["slips_with_epf"], 2)
		self.assertEqual(props["slips_with_esic"], 1)
		self.assertEqual(props["slips_with_lwf"], 0)
		self.assertEqual(props["slips_with_income_tax"], 1)
		self.assertFalse(props["via_payroll_entry"])
		self.assertFalse(frappe.db.exists(MILESTONE_DOCTYPE, FIRST_CAPTURE_MILESTONE))

	def test_slip_batch_ignores_earlier_slips_modified_in_the_same_window(self):
		insert_slip([EPF_EMPLOYEE_COMPONENT], modified=frappe.utils.now())
		self.submit_batch(insert_slip([PT_SALARY_COMPONENT]))

		[props] = self.events("salary_slips_submitted")
		self.assertEqual(props["slips_submitted"], 1)
		self.assertEqual(props["slips_with_epf"], 0)
		self.assertEqual(props["slips_with_professional_tax"], 1)

	def test_slip_batch_counts_components_flagged_only_as_income_tax(self):
		make_flagged_income_tax_component()
		self.submit_batch(
			insert_slip([TDS_ONLY_COMPONENT]),
			insert_slip([EPF_EMPLOYEE_COMPONENT], income_tax=True),
			insert_slip([EPF_EMPLOYEE_COMPONENT]),
		)

		[props] = self.events("salary_slips_submitted")
		self.assertEqual(props["slips_submitted"], 3)
		self.assertEqual(props["slips_with_income_tax"], 2)

	def test_slip_batch_skips_slips_that_did_not_stay_submitted(self):
		self.submit_batch(insert_slip([EPF_EMPLOYEE_COMPONENT], docstatus=0))
		self.assertEqual(self.events("salary_slips_submitted"), [])

	def test_queued_slips_are_sent_after_commit_and_dropped_on_rollback(self):
		self.addCleanup(telemetry._discard_submitted_slips)
		telemetry._queue_submitted_slip(insert_slip([EPF_EMPLOYEE_COMPONENT]))
		telemetry._queue_submitted_slip(insert_slip([EPF_EMPLOYEE_COMPONENT]))

		self.assertEqual(list(frappe.db.after_commit._functions).count(telemetry.flush_submitted_slips), 1)
		self.assertIn(telemetry._discard_submitted_slips, frappe.db.after_rollback._functions)
		self.assertEqual(len(frappe.flags.india_payroll_submitted_slips["slips"]), 2)

		telemetry._discard_submitted_slips()
		telemetry.flush_submitted_slips()
		self.assertEqual(self.events("salary_slips_submitted"), [])


TDS_ONLY_COMPONENT = "_Test Telemetry TDS"


def make_flagged_income_tax_component():
	if frappe.db.exists("Salary Component", TDS_ONLY_COMPONENT):
		return
	frappe.get_doc(
		{
			"doctype": "Salary Component",
			"salary_component": TDS_ONLY_COMPONENT,
			"salary_component_abbr": "_TTDS",
			"type": "Deduction",
			"is_income_tax_component": 1,
		}
	).insert()


def insert_slip(
	components: list[str], income_tax: bool = False, docstatus: int = 1, modified: str = "2026-01-15 12:00:00"
) -> str:
	name = f"_Test Telemetry Slip {frappe.generate_hash(length=8)}"
	frappe.db.sql(
		"""insert into `tabSalary Slip` (name, docstatus, company, creation, modified)
		values (%s, %s, '_Test Company', %s, %s)""",
		(name, docstatus, modified, modified),
	)
	rows = [(component, 0) for component in components]
	if income_tax:
		rows.append(("Income Tax", 1))
	for idx, (component, variable) in enumerate(rows, start=1):
		frappe.db.sql(
			"""insert into `tabSalary Detail` (name, parent, parenttype, parentfield, idx,
				salary_component, variable_based_on_taxable_salary)
			values (%s, %s, 'Salary Slip', 'deductions', %s, %s, %s)""",
			(frappe.generate_hash(length=10), name, idx, component, variable),
		)
	return name
