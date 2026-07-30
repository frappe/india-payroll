"""Build the Sandbox "Sheet JSON" workbook uploaded to a job's presigned URL.

Sheet JSON is a *spreadsheet* representation, not a domain object: a workbook of
named sheets, each holding `list` blocks (key/value pairs) or `table` blocks
(header + rows). Sandbox publishes the schemas at
https://github.com/in-co-sandbox/in-co-sandbox-docs (data/tds/**/forms/); they are
vendored under `schemas/` and asserted against in the tests.

Two workbooks are in play, and they are not interchangeable:

    form24q_workbook   analytics / potential notices (Income-tax Act 1961 vocabulary,
                       includes the Annexure II salary_detail_sheet)
    form138_workbook   reports / TXT generation (Income-tax Act 2025 vocabulary)

Every date is epoch milliseconds, and enumerated columns take Sandbox's snake_case
tokens rather than the ITD numeric codes.
"""

import json
import os
from functools import cache

import frappe
from frappe import _
from frappe.utils import cint, flt, get_datetime

from india_payroll.india_payroll.tds.csi import get_challan_rows
from india_payroll.india_payroll.tds.validators import uses_new_act

SCHEMA_DIR = os.path.join(os.path.dirname(__file__), "schemas")

WORKBOOK_24Q = "form24q_workbook"
WORKBOOK_138 = "form138_workbook"

# Sandbox tokens, not the ITD numeric codes.
MINOR_HEAD_24Q = "tds_payable"
MINOR_HEAD_138 = "tds_payable_by_taxpayer"
NATURE_OF_PAYMENT_138 = "payments_made_to_employees_other_than_government_employees"
NATURE_OF_PAYMENT_24Q = "payout_to_employees"
DEFAULT_MODE_OF_PAYMENT = "C"
DEFAULT_COUNTRY = "INDIA"
DEFAULT_DIALLING_CODE = "91"


def workbook_name_for(doc) -> str:
	"""form138 for Income-tax Act 2025 periods, form24q otherwise."""
	return WORKBOOK_138 if uses_new_act(doc.financial_year) else WORKBOOK_24Q


def build_sheet_json(doc, workbook: str | None = None) -> dict:
	"""Return the Sheet JSON workbook for a TDS Return.

	`workbook` overrides the year-derived default, because the two endpoints that
	consume this disagree: analytics still takes form24q, reports takes form138.
	"""
	workbook = workbook or workbook_name_for(doc)
	builder = _BUILDERS.get(workbook)
	if not builder:
		frappe.throw(frappe._("Unsupported TDS workbook '{0}'.").format(workbook))
	return builder(doc)


# ------------------------------------------------------------------ schemas
@cache
def _schema(workbook: str) -> dict:
	"""The vendored Sandbox schema for a workbook (static data, safe to cache)."""
	with open(os.path.join(SCHEMA_DIR, f"{workbook}.schema.json")) as handle:
		return json.load(handle)


@cache
def _table_spec(workbook: str, sheet: str, block: str) -> tuple:
	"""(header, column type sets) for a table block, from the published schema."""
	for sheet_spec in _schema(workbook)["properties"]["sheets"]["items"]["oneOf"]:
		if sheet_spec["properties"]["name"]["enum"][0] != sheet:
			continue
		for block_spec in sheet_spec["properties"]["blocks"]["items"]["oneOf"]:
			if block_spec["properties"]["name"]["enum"][0] != block:
				continue
			header = tuple(col["enum"][0] for col in block_spec["properties"]["header"]["items"])
			columns = tuple(
				frozenset(col["type"] if isinstance(col.get("type"), list) else [col.get("type")])
				for col in block_spec["properties"]["rows"]["items"]["items"]
			)
			return header, columns
	frappe.throw(frappe._("No schema for {0}/{1}/{2}.").format(workbook, sheet, block))


def table_header(workbook: str, sheet: str, block: str) -> tuple:
	"""Column order for a table block.

	Sheets like Annexure II pin an exact column count, so the order is read from
	the contract rather than duplicated here where it could drift.
	"""
	return _table_spec(workbook, sheet, block)[0]


def table_blanks(workbook: str, sheet: str, block: str) -> tuple:
	"""Per-column placeholder for values we cannot supply.

	Nullable columns take None; a non-nullable numeric column has to carry a
	figure, so it gets 0 — which is what Sandbox's own example workbook does.
	"""
	blanks = []
	for types in _table_spec(workbook, sheet, block)[1]:
		numeric = types & {"number", "integer", "long"}
		blanks.append(0 if numeric and "null" not in types else None)
	return tuple(blanks)


@cache
def mandatory_payer_fields(workbook: str, block: str) -> tuple:
	"""Payer-sheet keys the schema will not accept as null."""
	payer = next(
		sheet
		for sheet in _schema(workbook)["properties"]["sheets"]["items"]["oneOf"]
		if sheet["properties"]["name"]["enum"][0] == "payer_sheet"
	)
	spec = next(
		block_spec
		for block_spec in payer["properties"]["blocks"]["items"]["oneOf"]
		if block_spec["properties"]["name"]["enum"][0] == block
	)
	properties = spec["properties"]["items"]["items"]["properties"]
	return tuple(
		key
		for key, value in properties.items()
		if "null" not in (value["type"] if isinstance(value.get("type"), list) else [value.get("type")])
	)


def missing_payer_fields(doc, workbook: str | None = None) -> list[str]:
	"""Payer-sheet values the return cannot supply yet.

	Sandbox rejects an incomplete payer sheet with an opaque message (a blank TAN
	surfaces as "TAN mismatch"), so the gap is reported against the form's own
	labels before anything is uploaded.
	"""
	workbook = workbook or workbook_name_for(doc)
	book = _BUILDERS[workbook](doc)
	blocks = book["sheets"][0]["blocks"]

	missing = []
	for block in blocks:
		values = {key: value for item in block["items"] for key, value in item.items()}
		label = _("Deductor") if block["name"] == "payer_list" else _("Responsible Person")
		for key in mandatory_payer_fields(workbook, block["name"]):
			if values.get(key) in (None, ""):
				missing.append(f"{label}: {key}")
	return missing


# ------------------------------------------------------------------ helpers
def _epoch_ms(value) -> int | None:
	if not value:
		return None
	return int(get_datetime(value).timestamp() * 1000)


def _text(value) -> str | None:
	value = (value or "").strip() if isinstance(value, str) else value
	return value or None


def _sheet(name: str, blocks: list[dict]) -> dict:
	return {"name": name, "@entity": "sheet", "blocks": blocks}


def _list_block(name: str, items: list[tuple]) -> dict:
	return {"name": name, "@entity": "list", "items": [{key: value} for key, value in items]}


def _table_block(name: str, header: list[str], rows: list[list]) -> dict:
	return {"name": name, "@entity": "table", "header": header, "rows": rows}


def _payee_index(doc) -> dict:
	"""Map each distinct deductee to a 1-based payee serial number.

	The payee sheet lists a person once; the payment sheet references that serial
	per transaction, so both sheets must agree on the ordering.
	"""
	index = {}
	for row in doc.deductees:
		key = row.employee or row.pan
		if key not in index:
			index[key] = len(index) + 1
	return index


def _payee_key(row) -> str:
	return row.employee or row.pan


def _deposited(row) -> float:
	deposited = row.get("tax_deposited")
	return flt(deposited) if deposited else flt(row.tax_deducted)


def _challans(doc) -> list:
	return get_challan_rows(doc.company, doc.financial_year, doc.quarter)


def _challan_keys(row) -> tuple:
	"""(serial, bsr) for the deduction's challan — the join key into the challan sheet."""
	if not row.challan:
		return None, None
	return frappe.db.get_value("TDS Challan", row.challan, ["challan_serial_no", "bsr_code"])


def _payment_tax(row) -> float:
	"""Tax a deduction contributes to its challan: TDS + surcharge + cess.

	Interest, fee and penalty sit in their own challan columns and are never part
	of the deductee-side total.
	"""
	return flt(row.tax_deducted) + flt(row.get("surcharge")) + flt(row.get("health_and_education_cess"))


def _challan_tax(challan) -> float:
	"""The challan's tax component, excluding interest / fee / penalty / others."""
	return flt(challan.tds_amount) + flt(challan.surcharge_amount) + flt(challan.education_cess)


def _allocations(doc) -> dict:
	"""(serial, bsr) -> tax allocated to that challan by the payment rows."""
	allocated = {}
	for row in doc.deductees:
		key = _challan_keys(row)
		entry = allocated.setdefault(key, {"deducted": 0.0, "deposited": 0.0, "rows": 0})
		entry["deducted"] += _payment_tax(row)
		entry["deposited"] += _deposited(row)
		entry["rows"] += 1
	return allocated


def challan_payment_mismatches(doc, workbook: str | None = None) -> list[str]:
	"""Explain any disagreement between the challan sheet and the payment sheet.

	Sandbox reports this as a bare "Challan-payment mismatch", so the comparison it
	is making — per challan, tax on the challan versus tax allocated by the
	deductions mapped to it — is spelled out here with the actual figures.
	"""
	workbook = workbook or workbook_name_for(doc)
	allocated = _allocations(doc)
	problems = []

	unmapped = allocated.get((None, None))
	if unmapped:
		problems.append(
			_("{0} deduction row(s) totalling {1} are not linked to any challan.").format(
				unmapped["rows"], unmapped["deducted"]
			)
		)

	known = set()
	for challan in _challans(doc):
		key = (challan.challan_serial_no, challan.bsr_code)
		known.add(key)
		entry = allocated.get(key)
		label = _("Challan {0} (BSR {1})").format(challan.challan_serial_no, challan.bsr_code)

		if not entry:
			problems.append(_("{0} has no deduction rows mapped to it.").format(label))
			continue

		expected = _challan_tax(challan)
		if abs(entry["deducted"] - expected) > 1:
			problems.append(
				_(
					"{0}: challan tax is {1} (TDS {2} + surcharge {3} + cess {4}), but the {5} "
					"deduction row(s) mapped to it total {6} — a difference of {7}."
				).format(
					label,
					expected,
					flt(challan.tds_amount),
					flt(challan.surcharge_amount),
					flt(challan.education_cess),
					entry["rows"],
					entry["deducted"],
					entry["deducted"] - expected,
				)
			)

	for key, entry in allocated.items():
		if key != (None, None) and key not in known:
			problems.append(
				_(
					"{0} deduction row(s) point at challan {1} (BSR {2}), which is not in this quarter."
				).format(entry["rows"], key[0], key[1])
			)

	return problems


# --------------------------------------------------------------- form 24Q
def _build_24q(doc) -> dict:
	payees = _payee_index(doc)
	return {
		"name": WORKBOOK_24Q,
		"@entity": "workbook",
		"sheets": [
			_sheet(
				"payer_sheet",
				[
					_list_block(
						"payer_list",
						[
							("name", _text(doc.deductor_name) or doc.company),
							("tan", doc.tan),
							("pan", doc.pan),
							("branch", _text(doc.deductor_branch)),
							("gstin", _text(doc.deductor_gstin)),
							("street", _text(doc.deductor_road_street)),
							("area", _text(doc.deductor_area_locality)),
							("city", _text(doc.deductor_district)),
							("state", _text(doc.deductor_state)),
							("postal_code", _text(doc.deductor_postal_code)),
							("email", _text(doc.deductor_email)),
							("mobile", _text(doc.deductor_contact_number)),
						],
					),
					_list_block(
						"responsible_person_list",
						[
							("designation", _text(doc.responsible_person_designation)),
							("name", _text(doc.responsible_person_name)),
							("pan", _text(doc.responsible_person_pan)),
							("street", _text(doc.rp_road_street or doc.deductor_road_street)),
							("area", _text(doc.rp_area_locality or doc.deductor_area_locality)),
							("city", _text(doc.rp_district or doc.deductor_district)),
							("state", _text(doc.rp_state or doc.deductor_state)),
							("postal_code", _text(doc.rp_postal_code or doc.deductor_postal_code)),
							("email", _text(doc.rp_email or doc.deductor_email)),
							("mobile", _text(doc.rp_contact_number or doc.deductor_contact_number)),
						],
					),
				],
			),
			_sheet("payee_sheet", [_payee_table_24q(doc, payees)]),
			_sheet("challan_sheet", [_challan_table_24q(doc)]),
			_sheet("payment_sheet", [_payment_table_24q(doc, payees)]),
			_sheet("salary_detail_sheet", [_salary_detail_table(doc, payees)]),
		],
	}


def _payee_table_24q(doc, payees: dict) -> dict:
	seen, rows = set(), []
	for row in doc.deductees:
		key = _payee_key(row)
		if key in seen:
			continue
		seen.add(key)
		rows.append(
			[
				payees[key],
				row.pan,
				row.employee_name,
				bool(cint(row.get("opting_new_regime"))),
				row.get("employee_category") or "general",
				bool(cint(row.get("is_pan_operative"))),
			]
		)
	return _table_block(
		"payee_table",
		["sr_no", "pan", "name", "opting_new_regime", "employee_category", "is_pan_operative"],
		rows,
	)


def _challan_table_24q(doc) -> dict:
	allocated = _allocations(doc)
	rows = [
		[
			challan.challan_serial_no,
			challan.bsr_code,
			_epoch_ms(challan.challan_date),
			MINOR_HEAD_24Q,
			int(flt(challan.tds_amount)),
			int(flt(challan.surcharge_amount)),
			int(flt(challan.education_cess)),
			int(flt(challan.interest)),
			int(flt(challan.fee)),
			int(flt(challan.others)),
			# What the deductions actually draw from this challan — not the gross
			# deposit, which also carries interest, fee and penalty.
			int(allocated.get((challan.challan_serial_no, challan.bsr_code), {}).get("deposited", 0)),
		]
		for challan in _challans(doc)
	]
	return _table_block(
		"challan_table",
		[
			"challan_serial",
			"bsr_code",
			"paid_date_epoch",
			"minor_head",
			"tds_amount",
			"surcharge",
			"health_and_education_cess",
			"interest",
			"late_filing_fees",
			"other_penalty",
			"utilized_amount",
		],
		rows,
	)


def _payment_table_24q(doc, payees: dict) -> dict:
	rows = []
	for row in doc.deductees:
		serial, bsr = _challan_keys(row)
		rows.append(
			[
				payees[_payee_key(row)],
				serial,
				bsr,
				NATURE_OF_PAYMENT_24Q,
				flt(row.amount_paid),
				_epoch_ms(row.date_of_payment),
				flt(row.tax_deducted),
				flt(row.get("surcharge")),
				flt(row.get("health_and_education_cess")),
				_epoch_ms(row.date_of_deduction),
				_text(row.get("reason_for_lower_deduction")),
				_text(row.get("certificate_number")),
			]
		)
	return _table_block(
		"payment_table",
		[
			"payee_sr_no",
			"challan_serial",
			"bsr_code",
			"nature_of_payment",
			"payment_amount",
			"payment_date_epoch",
			"tds_amount",
			"surcharge",
			# Sandbox's published *example* misspells this "heath_and_education_cess";
			# the schema — the enforced contract — spells it correctly.
			"health_and_education_cess",
			"deduction_date_epoch",
			"reason_for_lower_deduction",
			"certificate_number",
		],
		rows,
	)


def _salary_detail_table(doc, payees: dict) -> dict:
	"""Annexure II, Q4 only — one row per employee with the annual figures.

	The sheet has exactly 100 fixed columns, so the header is read from the
	published schema and only the payroll-derivable ones are filled; the rest are
	sent as None, matching Sandbox's own example workbook.
	"""
	header = table_header(WORKBOOK_24Q, "salary_detail_sheet", "salary_detail_table")
	blanks = table_blanks(WORKBOOK_24Q, "salary_detail_sheet", "salary_detail_table")

	rows = []
	if doc.quarter == "Q4":
		for entry in _salary_annexure(doc):
			key = entry["employee"]
			if key not in payees:
				continue
			values = {
				"payee_sr_no": payees[key],
				"income_tax_payable": flt(entry["total_tax"]),
				"surcharge": 0,
				"health_and_education_cess": 0,
				"tds_on_salary": flt(entry["total_tax"]),
				"salary_as_per_provisions_contained_in_section_17_1": flt(entry["gross_salary"]),
			}
			rows.append([values.get(column, blank) for column, blank in zip(header, blanks, strict=True)])
	return _table_block("salary_detail_table", list(header), rows)


# --------------------------------------------------------------- form 138
def _build_138(doc) -> dict:
	payees = _payee_index(doc)
	return {
		"name": WORKBOOK_138,
		"@entity": "workbook",
		"sheets": [
			_sheet(
				"payer_sheet",
				[
					_list_block(
						"payer_list",
						[
							("name", _text(doc.deductor_name) or doc.company),
							("tan", doc.tan),
							("pan", doc.pan),
							("country", _text(doc.deductor_country) or DEFAULT_COUNTRY),
							("flat_door_block_number", _text(doc.deductor_flat_door_block_number)),
							("post_office", _text(doc.deductor_post_office)),
							("road_street_block_sector", _text(doc.deductor_road_street)),
							("area_locality", _text(doc.deductor_area_locality)),
							("district", _text(doc.deductor_district)),
							("state", _text(doc.deductor_state)),
							("postal_code", _text(doc.deductor_postal_code)),
							("email", _text(doc.deductor_email)),
							(
								"contact_country_code",
								_text(doc.deductor_contact_country_code) or DEFAULT_DIALLING_CODE,
							),
							("contact_number", _text(doc.deductor_contact_number)),
							("deductor_type", _text(doc.deductor_type_code)),
							("government_state_code", _text(doc.government_state_code)),
							("ministry_code", _text(doc.ministry_code)),
							("ministry_name_other", _text(doc.ministry_name_other)),
							(
								"account_office_identification_number",
								_text(doc.account_office_identification_number),
							),
							("gstin", _text(doc.deductor_gstin)),
						],
					),
					_list_block(
						"responsible_person_list",
						[
							("name", _text(doc.responsible_person_name)),
							("designation", _text(doc.responsible_person_designation)),
							("pan", _text(doc.responsible_person_pan)),
							(
								"flat_door_block_number",
								_text(doc.rp_flat_door_block_number or doc.deductor_flat_door_block_number),
							),
							("post_office", _text(doc.rp_post_office or doc.deductor_post_office)),
							(
								"road_street_block_sector",
								_text(doc.rp_road_street or doc.deductor_road_street),
							),
							("area_locality", _text(doc.rp_area_locality or doc.deductor_area_locality)),
							("district", _text(doc.rp_district or doc.deductor_district)),
							("state", _text(doc.rp_state or doc.deductor_state)),
							("postal_code", _text(doc.rp_postal_code or doc.deductor_postal_code)),
							("email", _text(doc.rp_email or doc.deductor_email)),
							("country", _text(doc.rp_country or doc.deductor_country) or DEFAULT_COUNTRY),
							(
								"contact_country_code",
								_text(doc.rp_contact_country_code or doc.deductor_contact_country_code)
								or DEFAULT_DIALLING_CODE,
							),
							(
								"contact_number",
								_text(doc.rp_contact_number or doc.deductor_contact_number),
							),
						],
					),
				],
			),
			_sheet("payee_sheet", [_payee_table_138(doc, payees)]),
			_sheet("challan_sheet", [_challan_table_138(doc)]),
			_sheet("payment_sheet", [_payment_table_138(doc, payees)]),
		],
	}


def _payee_table_138(doc, payees: dict) -> dict:
	seen, rows = set(), []
	for row in doc.deductees:
		key = _payee_key(row)
		if key in seen:
			continue
		seen.add(key)
		rows.append([payees[key], row.pan, row.employee_name])
	return _table_block("payee_table", ["sr_no", "pan", "name"], rows)


def _challan_table_138(doc) -> dict:
	rows = [
		[
			challan.challan_serial_no,
			challan.bsr_code,
			_epoch_ms(challan.challan_date),
			MINOR_HEAD_138,
			DEFAULT_MODE_OF_PAYMENT,
			"Y" if not flt(challan.deposit_amount) else "N",
			# Tax only. Interest, fee and penalty have their own columns, so folding
			# the gross deposit in here double-counts them and the payment rows —
			# which carry tax alone — can never reconcile against it.
			int(_challan_tax(challan)),
			int(flt(challan.interest)),
			int(flt(challan.fee)),
			int(flt(challan.others)),
			int(_challan_tax(challan)),
		]
		for challan in _challans(doc)
	]
	return _table_block(
		"challan_table",
		[
			"bank_challan_number_or_ddo_serial_number",
			"bsr_code_or_form_24g_receipt_number",
			"bank_challan_or_transfer_voucher_date_epoch",
			"minor_head",
			"mode_of_payment",
			"nil_challan_indicator",
			"total_tax_deducted",
			"total_interest",
			"total_fee",
			"total_penalty",
			"total_tax_deposited",
		],
		rows,
	)


def _payment_table_138(doc, payees: dict) -> dict:
	rows = []
	for row in doc.deductees:
		serial, bsr = _challan_keys(row)
		rows.append(
			[
				payees[_payee_key(row)],
				serial,
				bsr,
				NATURE_OF_PAYMENT_138,
				flt(row.amount_paid),
				_epoch_ms(row.date_of_payment),
				flt(row.tax_deducted),
				_epoch_ms(row.date_of_deduction),
				_deposited(row),
				_epoch_ms(row.date_of_deduction),
				_text(row.get("reason_for_lower_deduction")),
				_text(row.get("certificate_number")),
			]
		)
	return _table_block(
		"payment_table",
		[
			"payee_sr_no",
			"bank_challan_number_or_ddo_serial_number",
			"bsr_code_or_form_24g_receipt_number",
			"nature_of_payment",
			"payment_amount",
			"payment_date_epoch",
			"total_tax_deducted",
			"deduction_date_epoch",
			"total_tax_deposited",
			"deposit_date_epoch",
			"reason_for_lower_deduction",
			"certificate_number",
		],
		rows,
	)


_BUILDERS = {WORKBOOK_24Q: _build_24q, WORKBOOK_138: _build_138}


def _salary_annexure(doc) -> list[dict]:
	"""Annual taxable salary + total tax per employee, from the latest Q4 slip."""
	annexure = []
	employees = list({d.employee for d in doc.deductees if d.employee})
	if not employees:
		return annexure

	fy_start, fy_end = frappe.db.get_value(
		"Fiscal Year", doc.financial_year, ["year_start_date", "year_end_date"]
	)

	for employee in employees:
		latest = frappe.get_all(
			"Salary Slip",
			filters={
				"employee": employee,
				"company": doc.company,
				"docstatus": 1,
				"start_date": [">=", fy_start],
				"end_date": ["<=", fy_end],
			},
			fields=["employee_name", "annual_taxable_amount", "total_income_tax", "gross_year_to_date"],
			order_by="end_date desc",
			limit=1,
		)
		if not latest:
			continue
		slip = latest[0]
		pan = frappe.db.get_value("Employee", employee, "pan_number")
		annexure.append(
			{
				"employee": employee,
				"name": slip.employee_name,
				"pan": (pan or "").strip().upper(),
				"gross_salary": flt(slip.gross_year_to_date),
				"taxable_income": flt(slip.annual_taxable_amount),
				"total_tax": flt(slip.total_income_tax),
			}
		)
	return annexure
