"""Employer PF contributions (EPF, EPS, EDLI, Admin Charges) on the Salary Slip.

The calculation functions are pure (no database access), so every amount can be unit-tested; the
Salary Slip and Salary Structure Assignment hooks gather the inputs from Payroll Settings.

All arithmetic uses Decimal and rounds half-up to the rupee as EPFO does; Python's round() uses
banker's rounding (2082.5 -> 2082), which would understate EPS.
"""

import datetime
import re
from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import date_diff, flt, fmt_money, getdate

ONE_RUPEE = Decimal("1")
PAISE = Decimal("0.01")
HUNDRED = Decimal("100")

EMPLOYER_CONTRIBUTIONS = "employer_contributions"

# Payroll Settings field -> what it holds; all of them are required once the feature is on
COMPONENT_FIELDS = {
	"employer_epf_component": "employer_epf",
	"employer_eps_component": "employer_eps",
	"employer_edli_component": "edli",
	"employer_pf_admin_component": "admin",
}
REQUIRED_SETTINGS = (
	"employee_pf_component",
	*COMPONENT_FIELDS,
	"employer_pf_wage_ceiling",
	"employer_pf_previous_wage_ceiling",
	"eps_eligibility_cutoff_date",
)

PF_WAGE_COMPONENT_PATTERNS = (
	r"\bbasic\b",  # Basic, Basic Salary, Basic Pay, Basic Wages, Basic + DA
	r"\bdearness\b",  # Dearness Allowance, Dearness Pay
	r"\bda\b",  # DA, Basic + DA
)
_PF_WAGE_COMPONENT_RE = re.compile("|".join(PF_WAGE_COMPONENT_PATTERNS))


# ---------------------------------------------------------------------------
# Calculation Helpers
# ---------------------------------------------------------------------------


def round_half_up(value: Decimal | float | int | str) -> Decimal:
	"""Round to the nearest rupee, halves away from zero: 2082.5 -> 2083, 1499.4 -> 1499."""
	return Decimal(str(value)).quantize(ONE_RUPEE, rounding=ROUND_HALF_UP)


def to_decimal(value: Decimal | float | int | str | None) -> Decimal:
	return Decimal(str(value or 0))


@dataclass(frozen=True)
class EmployerPFRates:
	"""Rates in percent, as stored in Payroll Settings (12 means 12%)."""
	epf: Decimal  # employer EPF + EPS together
	eps: Decimal
	edli: Decimal
	admin: Decimal


@dataclass(frozen=True)
class CeilingShare:
	"""A PF wage ceiling and the share of the pay period it is in force for."""
	ceiling: Decimal
	share: Decimal


@dataclass(frozen=True)
class EmployerPFAmounts:
	pf_wage: Decimal
	capped_wage: Decimal  # EPS and EDLI wage: always capped at the ceiling
	epf_wage: Decimal  # EPF and Admin wage: actual when Contribute on Actual PF Wage, else capped
	employee_epf: Decimal  # reference only: the employee's 12% on the EPF wage
	employer_epf: Decimal
	employer_eps: Decimal
	edli: Decimal
	admin: Decimal

	@property
	def total_employer_cost(self) -> Decimal:
		return self.employer_epf + self.employer_eps + self.edli + self.admin

	@property
	def credited_to_pf_account(self) -> Decimal:
		"""What reaches the employee's PF account (A/c 1): employee 12% plus employer EPF."""
		return self.employee_epf + self.employer_epf


def get_ceiling_shares(
	start_date,
	end_date,
	*,
	revision_date,
	previous_ceiling,
	current_ceiling,
	joining_date=None,
	relieving_date=None,
) -> list[CeilingShare]:
	"""Ceilings in force over a pay period, weighted by calendar days."""
	start = getdate(start_date)
	end = getdate(end_date) if end_date else start
	revision = getdate(revision_date)
	previous, current = to_decimal(previous_ceiling), to_decimal(current_ceiling)

	period = _split(start, end, revision, previous, current)

	if joining_date and start < getdate(joining_date) <= end:
		start = getdate(joining_date)
	if relieving_date and start <= getdate(relieving_date) < end:
		end = getdate(relieving_date)
	service = _split(start, end, revision, previous, current)

	if _share_of(service, current) > _share_of(period, current):
		return period
	return service


def cap_pf_wage(pf_wage: Decimal, ceilings: list[CeilingShare]) -> Decimal:
	"""Cap the wage under each ceiling separately, weighted by days, to the paisa."""
	capped = sum((min(pf_wage, c.ceiling) * c.share for c in ceilings), Decimal(0))
	return capped.quantize(PAISE, rounding=ROUND_HALF_UP)


def calculate_employer_pf(
	pf_wage: Decimal | float | int,
	*,
	eps_eligible: bool,
	rates: EmployerPFRates,
	ceilings: list[CeilingShare],
	contribute_on_actual: bool = False,
) -> EmployerPFAmounts:
	"""Employer PF split for one salary slip.
	EPF and Admin Charges follow the EPF wage: actual wage when Contribute on Actual Wage is checked,
	otherwise capped at ceiling. EPS and EDLI are always capped.
	EPS is rounded first and Employer EPF is the rounded 12% minus EPS."""
	pf_wage = to_decimal(pf_wage)
	capped_wage = cap_pf_wage(pf_wage, ceilings)
	epf_wage = pf_wage.quantize(PAISE, rounding=ROUND_HALF_UP) if contribute_on_actual else capped_wage

	def percent_of(wage: Decimal, rate: Decimal) -> Decimal:
		return round_half_up(wage * rate / HUNDRED)

	employer_total = percent_of(epf_wage, rates.epf)
	employer_eps = percent_of(capped_wage, rates.eps) if eps_eligible else Decimal(0)

	return EmployerPFAmounts(
		pf_wage=pf_wage,
		capped_wage=capped_wage,
		epf_wage=epf_wage,
		employee_epf=percent_of(epf_wage, rates.epf),
		employer_epf=employer_total - employer_eps,
		employer_eps=employer_eps,
		edli=percent_of(capped_wage, rates.edli),
		admin=percent_of(epf_wage, rates.admin),
	)


def get_ceiling_on(date, *, revision_date, previous_ceiling, current_ceiling) -> Decimal:
	"""The single ceiling in force on a date."""
	if getdate(date) < getdate(revision_date):
		return to_decimal(previous_ceiling)
	return to_decimal(current_ceiling)


def _split(
	start: datetime.date, end: datetime.date, revision: datetime.date, previous: Decimal, current: Decimal
) -> list[CeilingShare]:
	if start >= revision:
		return [CeilingShare(current, Decimal(1))]
	if end < revision:
		return [CeilingShare(previous, Decimal(1))]

	total_days = Decimal(date_diff(end, start) + 1)
	revised_days = Decimal(date_diff(end, revision) + 1)
	return [
		CeilingShare(previous, (total_days - revised_days) / total_days),
		CeilingShare(current, revised_days / total_days),
	]


def _share_of(ceilings: list[CeilingShare], ceiling: Decimal) -> Decimal:
	return sum((c.share for c in ceilings if c.ceiling == ceiling), Decimal(0))


# ---------------------------------------------------------------------------
# Frappe Helpers for Wage Computation
# ---------------------------------------------------------------------------


def is_pf_wage_component(salary_component: str | None) -> bool:
	if not salary_component:
		return False
	try:
		from india_payroll.india_payroll.epf import is_pf_wage_component as core_is_pf
		return core_is_pf(salary_component)
	except Exception:
		normalised = re.sub(r"[^a-z0-9]+", " ", salary_component.lower().replace(".", ""))
		return bool(_PF_WAGE_COMPONENT_RE.search(normalised))


def _is_pf_wage_row(e) -> bool:
	return not e.get("additional_salary") and is_pf_wage_component(e.salary_component)


def compute_pf_wage(doc: Document) -> float:
	try:
		from india_payroll.india_payroll.epf import _compute_pf_wage as core_compute_pf
		return core_compute_pf(doc)
	except Exception:
		return sum(flt(e.amount) for e in (doc.get("earnings") or []) if _is_pf_wage_row(e))


def get_slip_ssa_values(doc: Document, fields: list[str]) -> "frappe._dict":
	try:
		from india_payroll.india_payroll.utils import get_slip_ssa_values as core_get_ssa
		return core_get_ssa(doc, fields)
	except Exception:
		cache = getattr(frappe.local, "_india_payroll_epf_ssa_cache", None)
		if cache is None:
			cache = frappe.local._india_payroll_epf_ssa_cache = {}

		cache_key = (doc.employee, doc.salary_structure, str(doc.end_date))
		if cache_key not in cache:
			row = frappe.db.get_value(
				"Salary Structure Assignment",
				filters={
					"employee": doc.employee,
					"company": doc.company,
					"salary_structure": doc.salary_structure,
					"from_date": ("<=", doc.end_date),
					"docstatus": 1,
				},
				fieldname=fields,
				order_by="from_date desc",
				as_dict=True,
			)
			cache[cache_key] = row or frappe._dict()
		return cache[cache_key]


# ---------------------------------------------------------------------------
# Salary Slip Hook Handler
# ---------------------------------------------------------------------------


def get_settings() -> frappe._dict | None:
	"""Employer PF settings from Payroll Settings, or None while the feature is off."""
	settings = frappe.get_cached_doc("Payroll Settings")
	if not settings.get("enable_employer_pf_deduction"):
		return None

	return frappe._dict(
		{field: settings.get(field) for field in REQUIRED_SETTINGS},
		rates=EmployerPFRates(
			epf=to_decimal(settings.get("employer_pf_rate")),
			eps=to_decimal(settings.get("employer_eps_rate")),
			edli=to_decimal(settings.get("employer_edli_rate")),
			admin=to_decimal(settings.get("employer_pf_admin_rate")),
		),
	)


def set_employer_pf_contributions(doc: Document, method: str | None = None) -> None:
	"""Salary Slip validate: rebuild the four employer PF rows from the slip's earned PF wage."""
	if doc.docstatus != 0 or not doc.get("employee"):
		return

	settings = get_settings()
	if not settings:
		return

	our_components = {settings[field] for field in COMPONENT_FIELDS}
	doc.set(
		EMPLOYER_CONTRIBUTIONS,
		[row for row in (doc.get(EMPLOYER_CONTRIBUTIONS) or []) if row.salary_component not in our_components],
	)
	_set_totals(doc, None)

	if not is_applicable(doc, settings):
		return

	pf_wage = compute_pf_wage(doc)
	if pf_wage <= 0:
		return

	amounts = get_employer_pf_amounts(doc, settings, pf_wage=pf_wage)
	for field, key in COMPONENT_FIELDS.items():
		doc.append(
			EMPLOYER_CONTRIBUTIONS,
			{"salary_component": settings[field], "amount": flt(getattr(amounts, key))},
		)
	_set_totals(doc, amounts)


def is_applicable(doc: Document, settings: frappe._dict) -> bool:
	"""Indian company, and the employee's PF is deducted on this slip."""
	if frappe.get_cached_value("Company", doc.company, "country") != "India":
		return False

	missing = [field for field in REQUIRED_SETTINGS if not settings.get(field)]
	if missing:
		frappe.msgprint(
			_("Employer PF was not calculated: fill {0} in Payroll Settings.").format(
				", ".join(frappe.bold(_(frappe.get_meta("Payroll Settings").get_label(f))) for f in missing)
			),
			indicator="orange",
			alert=True,
		)
		return False

	return any(
		row.salary_component == settings.employee_pf_component and flt(row.amount) > 0
		for row in (doc.get("deductions") or [])
	)


def get_employer_pf_amounts(
	doc: Document, settings: frappe._dict, pf_wage: float | None = None
) -> EmployerPFAmounts:
	employee_info = (
		frappe.get_cached_value("Employee", doc.employee, ["date_of_joining", "relieving_date"])
		if doc.get("employee")
		else None
	)
	joining_date, relieving_date = employee_info or (None, None)
	ceilings = get_ceiling_shares(
		doc.start_date,
		doc.end_date,
		revision_date=settings.eps_eligibility_cutoff_date,
		previous_ceiling=settings.employer_pf_previous_wage_ceiling,
		current_ceiling=settings.employer_pf_wage_ceiling,
		joining_date=joining_date,
		relieving_date=relieving_date,
	)
	ssa = get_slip_ssa_values(doc, ["eps_eligible", "contribute_on_actual_pf_wage"])

	wage_to_use = compute_pf_wage(doc) if pf_wage is None else pf_wage

	return calculate_employer_pf(
		wage_to_use,
		eps_eligible=bool(ssa.get("eps_eligible")),
		contribute_on_actual=bool(ssa.get("contribute_on_actual_pf_wage")),
		rates=settings.rates,
		ceilings=ceilings,
	)


def _set_totals(doc: Document, amounts: EmployerPFAmounts | None) -> None:
	doc.pf_wage_considered = flt(amounts.epf_wage) if amounts else 0
	doc.total_employer_pf_cost = flt(amounts.total_employer_cost) if amounts else 0
	doc.pf_credited_to_employee_account = flt(amounts.credited_to_pf_account) if amounts else 0


# ---------------------------------------------------------------------------
# Salary Structure Assignment Hooks
# ---------------------------------------------------------------------------


def warn_if_eps_wage_above_ceiling(doc: Document, method: str | None = None) -> None:
	"""Salary Structure Assignment validate: informational warning if PF wage exceeds ceiling."""
	if not doc.get("eps_eligible") or not doc.salary_structure:
		return

	settings = get_settings()
	if not settings or not settings.employer_pf_wage_ceiling or not settings.eps_eligibility_cutoff_date:
		return

	pf_wage = get_assignment_pf_wage(doc)
	joining_date = frappe.get_cached_value("Employee", doc.employee, "date_of_joining")
	ceiling_args = {
		"revision_date": settings.eps_eligibility_cutoff_date,
		"previous_ceiling": settings.employer_pf_previous_wage_ceiling,
		"current_ceiling": settings.employer_pf_wage_ceiling,
	}
	ceilings = {
		get_ceiling_on(settings.eps_eligibility_cutoff_date, **ceiling_args),
		get_ceiling_on(joining_date, **ceiling_args) if joining_date else to_decimal(0),
	}
	if to_decimal(pf_wage) <= max(ceilings):
		return

	frappe.msgprint(
		_(
			"EPS Eligible is ticked, but this assignment's PF wage of {0} is above the PF wage ceiling "
			"of {1}. EPS applies only if the PF wage was within the ceiling on the date of joining or on "
			"{2}, or the employee is already an EPS member. Check the employee's history; the flag has "
			"not been changed."
		).format(
			frappe.bold(fmt_money(pf_wage, currency=doc.currency)),
			frappe.bold(fmt_money(max(ceilings), currency=doc.currency)),
			frappe.bold(frappe.format(settings.eps_eligibility_cutoff_date, "Date")),
		),
		title=_("Check EPS Eligibility"),
		indicator="orange",
	)


def get_assignment_pf_wage(doc: Document) -> float:
	try:
		earnings = doc.get_evaluated_components().earnings
	except Exception:
		return 0
	return sum(
		flt(row.get("default_amount"))
		for row in earnings
		if is_pf_wage_component(row.get("salary_component"))
	)


def warn_if_joint_declaration_missing(doc: Document, method: str | None = None) -> None:
	if doc.get("contribute_on_actual_pf_wage") and not (
		doc.get("joint_declaration_on_file") or doc.get("joint_declaration_document")
	):
		frappe.msgprint(
			_(
				"Contribute on Actual PF Wage is ticked, but {0} is not. Contributions above the PF wage "
				"ceiling need a joint request from the employee and the employer under Para 26(6) of the "
				"EPF Scheme; attach the signed document or keep it on file and tick the box."
			).format(frappe.bold(_("Para 26(6) Joint Declaration on File"))),
			title=_("Joint Declaration Missing"),
			indicator="orange",
		)


def validate_joint_declaration_on_submit(doc: Document, method: str | None = None) -> None:
	"""Enforce Para 26(6) statutory compliance on submit of Salary Structure Assignment (Option B)."""
	if not doc.get("contribute_on_actual_pf_wage"):
		return

	if doc.get("joint_declaration_document"):
		doc.joint_declaration_on_file = 1

	if not (doc.get("joint_declaration_document") or doc.get("joint_declaration_on_file")):
		frappe.throw(
			_(
				"Cannot submit: Contribute on Actual PF Wage is enabled, but the signed Para 26(6) "
				"Joint Declaration document is not attached and not marked on file."
			),
			title=_("Para 26(6) Compliance Required"),
		)
