"""Usage telemetry for India Payroll.

Data policy: an event describes how a feature is used, never the person or
company it is used for. Properties are limited to counts, booleans, durations
and values from a fixed vocabulary. Names, PANs, TANs, amounts and free text
never leave the site.
"""

import frappe
from frappe.database import savepoint
from frappe.query_builder.functions import Count
from frappe.utils import add_days, date_diff, getdate, today
from frappe.utils.telemetry import capture as _capture
from frappe.utils.telemetry import is_pulse_enabled as is_enabled
from frappe.utils.telemetry import site_age

from india_payroll.india_payroll.company_settings import MULTI_COMPANY_FIELD, STATUTE_TOGGLE_FIELDS
from india_payroll.india_payroll.epf import EPF_EMPLOYEE_COMPONENT, VPF_COMPONENT
from india_payroll.india_payroll.esi import ESI_EMPLOYEE_COMPONENT
from india_payroll.india_payroll.lwf import LWF_SALARY_COMPONENT
from india_payroll.india_payroll.professional_tax import PT_SALARY_COMPONENT

APP = "india_payroll"
MILESTONE_DOCTYPE = "India Payroll Telemetry Milestone"
ACTIVATION_WINDOW_DAYS = 30

INSTALL_MILESTONE = "app_installed"
FIRST_CAPTURE_MILESTONE = "first_capture"
CONVERSION_EVENT = "site_converted"
CONVERSION_WINDOW_DAYS = 14

FIXED_VOCABULARIES = {
	"step": {"validate", "generate_txt", "generate_fvu", "e_file"},
	"outcome": {"succeeded", "failed", "timed_out", "stalled"},
	"quarter": {"Q1", "Q2", "Q3", "Q4"},
	"return_type": {"Original", "Revised"},
	"payment_mode": {"Net Banking", "Debit Card", "Over the Counter", "NEFT/RTGS"},
	"minor_head": {"TDS Payable by Taxpayer (200)", "TDS Regular Assessment (400)"},
	"regime": {"old", "new"},
	"part": {"A", "B"},
}

STATUTORY_COMPONENTS = {
	"professional_tax": (PT_SALARY_COMPONENT,),
	"esic": (ESI_EMPLOYEE_COMPONENT,),
	"lwf": (LWF_SALARY_COMPONENT,),
	"epf": (EPF_EMPLOYEE_COMPONENT,),
	"vpf": (VPF_COMPONENT,),
}

SETTINGS_FIELDS = (*STATUTE_TOGGLE_FIELDS.values(), MULTI_COMPANY_FIELD, "enable_tds_filing")


def _skip_context() -> bool:
	return bool(
		frappe.flags.in_install
		or frappe.flags.in_migrate
		or frappe.flags.in_patch
		or frappe.flags.in_test
		or frappe.flags.in_import
	)


def _should_skip() -> bool:
	return _skip_context() or not is_enabled()


def sanitize_properties(properties: dict | None) -> dict:
	clean = {}
	for key, value in (properties or {}).items():
		if value is None or isinstance(value, bool | int | float):
			clean[key] = value
		elif isinstance(value, str) and key in FIXED_VOCABULARIES:
			clean[key] = value if value in FIXED_VOCABULARIES[key] else "other"
	return clean


def capture(event: str, properties: dict | None = None) -> None:
	"""Record a user action. Counts towards conversion."""
	if _should_skip():
		return

	_capture(event, APP, properties=sanitize_properties(properties))
	_track_conversion()


def capture_outcome(event: str, properties: dict | None = None) -> None:
	"""Record a background result. Never starts or satisfies the conversion clock."""
	if _should_skip():
		return

	_capture(event, APP, properties=sanitize_properties(properties))


def _claim_milestone(event: str) -> bool:
	claimed = False

	with savepoint(catch=Exception):
		frappe.get_doc({"doctype": MILESTONE_DOCTYPE, "event": event}).insert(ignore_permissions=True)
		claimed = True

	return claimed


def days_since_install() -> int | None:
	installed_on = frappe.db.get_value(MILESTONE_DOCTYPE, INSTALL_MILESTONE, "creation")
	if not installed_on:
		return None
	return date_diff(today(), installed_on) + 1


def capture_first(event: str, properties: dict | None = None) -> None:
	"""Record a first-time milestone, only for sites that installed the app recently."""
	if _should_skip():
		return

	age = days_since_install()
	if not age or age > ACTIVATION_WINDOW_DAYS:
		return

	if not _claim_milestone(event):
		return

	capture(event, {"day_since_install": age, **(properties or {})})


def _track_conversion() -> None:
	if frappe.db.exists(MILESTONE_DOCTYPE, CONVERSION_EVENT):
		return

	first_capture = frappe.db.get_value(MILESTONE_DOCTYPE, FIRST_CAPTURE_MILESTONE, "creation")
	if not first_capture:
		_claim_milestone(FIRST_CAPTURE_MILESTONE)
		return

	days_since_first_capture = date_diff(today(), first_capture)
	if days_since_first_capture <= CONVERSION_WINDOW_DAYS:
		return

	if _claim_milestone(CONVERSION_EVENT):
		_capture(
			CONVERSION_EVENT,
			APP,
			properties={
				"days_since_first_capture": days_since_first_capture,
				"day_since_install": days_since_install(),
				"site_age": site_age(),
			},
		)


def record_install() -> None:
	"""Start the activation window. Called from after_install."""
	if not _claim_milestone(INSTALL_MILESTONE) or frappe.flags.in_test or not is_enabled():
		return

	_capture(INSTALL_MILESTONE, APP, properties={"site_age": site_age()})


# ---- Configuration -----------------------------------------------------------


def _statutory_flags(doc) -> dict:
	return {statute: bool(doc.get(field)) for statute, field in STATUTE_TOGGLE_FIELDS.items()}


def on_payroll_settings_update(doc, method=None):
	if not any(doc.has_value_changed(field) for field in SETTINGS_FIELDS):
		return

	flags = _statutory_flags(doc)
	capture(
		"statutory_settings_updated",
		{
			**flags,
			"multi_company": bool(doc.get(MULTI_COMPANY_FIELD)),
			"company_count": len(doc.get("company_payroll_settings") or []),
			"tds_filing": bool(doc.get("enable_tds_filing")),
		},
	)

	for statute, enabled in flags.items():
		if enabled:
			capture_first(f"{statute}_enabled")
	if doc.get("enable_tds_filing"):
		capture_first("tds_filing_enabled")


# ---- Payroll -----------------------------------------------------------------


def on_salary_slip_submit(doc, method=None):
	components = {row.salary_component for row in doc.get("deductions") or []}
	for statute, names in STATUTORY_COMPONENTS.items():
		if components.intersection(names):
			capture_first(f"first_{statute}_deducted")


def capture_daily_payroll_summary():
	"""Statutory coverage of the salary slips submitted on the day that just ended."""
	if _should_skip():
		return

	day = add_days(today(), -1)
	SalarySlip = frappe.qb.DocType("Salary Slip")
	SalaryDetail = frappe.qb.DocType("Salary Detail")

	day_filter = (
		(SalarySlip.docstatus == 1)
		& (SalarySlip.modified >= f"{day} 00:00:00")
		& (SalarySlip.modified <= f"{day} 23:59:59")
	)

	slips, companies = (
		frappe.qb.from_(SalarySlip)
		.select(Count(SalarySlip.name), Count(SalarySlip.company).distinct())
		.where(day_filter)
	).run()[0]

	if not slips:
		return

	rows = (
		frappe.qb.from_(SalaryDetail)
		.join(SalarySlip)
		.on(SalaryDetail.parent == SalarySlip.name)
		.select(SalaryDetail.salary_component, Count(SalaryDetail.parent).distinct())
		.where(day_filter & (SalaryDetail.parenttype == "Salary Slip"))
		.groupby(SalaryDetail.salary_component)
	).run()
	slips_by_component = dict(rows)

	with_income_tax = (
		frappe.qb.from_(SalaryDetail)
		.join(SalarySlip)
		.on(SalaryDetail.parent == SalarySlip.name)
		.select(Count(SalaryDetail.parent).distinct())
		.where(
			day_filter
			& (SalaryDetail.parenttype == "Salary Slip")
			& (SalaryDetail.variable_based_on_taxable_salary == 1)
		)
	).run()[0][0]

	settings = frappe.get_cached_doc("Payroll Settings")

	capture_outcome(
		"payroll_daily_summary",
		{
			"slips_submitted": slips,
			"companies": companies,
			**{
				f"slips_with_{statute}": sum(slips_by_component.get(name, 0) for name in names)
				for statute, names in STATUTORY_COMPONENTS.items()
			},
			"slips_with_income_tax": with_income_tax or 0,
			**{f"{statute}_enabled": enabled for statute, enabled in _statutory_flags(settings).items()},
			"multi_company": bool(settings.get(MULTI_COMPANY_FIELD)),
			"weekday": getdate(day).weekday(),
		},
	)


# ---- Income tax --------------------------------------------------------------


def on_tax_regime_set(employee: str, regime: str) -> None:
	employee_user = frappe.db.get_value("Employee", employee, "user_id")
	capture("tax_regime_selected", {"regime": regime, "by_employee": employee_user == frappe.session.user})


def on_tax_regime_reminder(sent: int, skipped: int = 0, bulk: bool = False) -> None:
	capture("tax_regime_reminder_sent", {"sent": sent, "skipped": skipped, "bulk": bulk})


# ---- TDS filing --------------------------------------------------------------


def _return_properties(doc) -> dict:
	return {
		"quarter": doc.quarter,
		"return_type": doc.return_type,
		"deductee_count": len(doc.get("deductees") or []),
	}


def on_tds_return_insert(doc, method=None):
	capture_first("first_tds_return_created", {"quarter": doc.quarter})


def on_tds_return_submit(doc, method=None):
	capture("tds_return_submitted", _return_properties(doc))


def on_tds_challan_submit(doc, method=None):
	capture(
		"tds_challan_submitted",
		{
			"quarter": doc.quarter,
			"payment_mode": doc.payment_mode,
			"minor_head": doc.minor_head,
			"has_interest": bool(doc.get("interest")),
			"has_fee": bool(doc.get("fee")),
		},
	)


def on_filing_step_started(doc, step: str) -> None:
	capture("tds_filing_step_started", {"step": step, **_return_properties(doc)})
	if step == "e_file":
		capture_first("first_tds_return_filed", {"quarter": doc.quarter})


def on_filing_step_finished(doc, step: str, outcome: str) -> None:
	capture_outcome("tds_filing_step_finished", {"step": step, "outcome": outcome, **_return_properties(doc)})


def on_validation_skipped(doc) -> None:
	capture("tds_validation_skipped", _return_properties(doc))


def on_form16_created(count: int) -> None:
	capture("form16_created", {"count": count})


def on_form16_part_requested(part: str) -> None:
	capture("form16_part_requested", {"part": part})
