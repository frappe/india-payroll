"""TDS challan lookups on TRACES, through Sandbox.

Sandbox logs in to TRACES as the deductor, so it needs the TAN's TRACES password.
It keeps the password per TAN once it has been added; we send it on and never
store it ourselves.

    POST   /tds/traces/deductors/credential                 -> add the TRACES password
    DELETE /tds/traces/deductors/credential                 -> remove it
    POST   /tds/compliance/traces/deductors/challans        -> one challan by BSR, serial, date
    POST   /tds/compliance/traces/deductors/challans/search -> challans by deposit date range

A challan the bank has not reported to the department is missing here as well,
which is what leaves a CSI empty and fails the FVU. Checking before filing
catches that while there is still time to take it up with the bank.
"""

from datetime import datetime

import frappe
from frappe import _
from frappe.utils import add_months, flt, get_last_day, getdate, now_datetime

from india_payroll.india_payroll.tds.csi import IST, _epoch_ms, get_csi_date_range
from india_payroll.india_payroll.tds.sandbox_client import SandboxAPIError
from india_payroll.india_payroll.tds.validators import validate_tan

TRACES_CREDENTIAL_ENDPOINT = "tds/traces/deductors/credential"
CHALLAN_ENDPOINT = "tds/compliance/traces/deductors/challans"
CHALLAN_SEARCH_ENDPOINT = "tds/compliance/traces/deductors/challans/search"
TRACES_CREDENTIAL_ENTITY = "in.co.sandbox.tds.traces.deductor.credential.request"
TRACES_CREDENTIAL_DELETE_ENTITY = "in.co.sandbox.tds.traces.deductor.credential.delete.request"
CHALLAN_ENTITY = "in.co.sandbox.tds.compliance.challan.request"
CHALLAN_SEARCH_ENTITY = "in.co.sandbox.tds.compliance.challan.search"
SEARCH_PAGE_SIZE = 50
NOT_FOUND = "Not Found"

MINOR_HEADS = {
	"200": "TDS Payable by Taxpayer (200)",
	"400": "TDS Regular Assessment (400)",
}


class TracesCredentialsRequired(frappe.ValidationError):
	"""Sandbox has no working TRACES password for the TAN."""


def save_traces_credentials(client, tan: str, password: str) -> None:
	"""Replace whatever password Sandbox holds for the TAN, since adding over one fails with 409."""
	tan = validate_tan(tan, "TAN")
	if not password:
		frappe.throw(_("Enter the TRACES password for TAN {0}.").format(tan))

	client.request(
		"DELETE",
		TRACES_CREDENTIAL_ENDPOINT,
		json_body={"@entity": TRACES_CREDENTIAL_DELETE_ENTITY, "tan": tan},
	)
	client.request(
		"POST",
		TRACES_CREDENTIAL_ENDPOINT,
		json_body={"@entity": TRACES_CREDENTIAL_ENTITY, "tan": tan, "password": password},
	)


def fetch_challan(client, tan: str, bsr_code: str, challan_serial: str, paid_date, **log) -> dict | None:
	"""The challan as TRACES has it, or None when TRACES has no such challan."""
	try:
		response = client.request(
			"POST",
			CHALLAN_ENDPOINT,
			json_body={
				"@entity": CHALLAN_ENTITY,
				"tan": tan,
				"bsr_code": normalize_bsr(bsr_code),
				"challan_serial": normalize_serial(challan_serial),
				"paid_date_epoch": _epoch_ms(paid_date),
			},
			**log,
		)
	except SandboxAPIError as e:
		if "challan not found" in str(e).lower():
			return None
		_raise_for_credentials(e, tan)
		raise
	return _data(response)


def search_challans(client, tan: str, from_date, to_date, **log) -> list[dict]:
	"""Every challan TRACES has for the TAN with a deposit date in the range, across all pages."""
	challans = []
	page = 0
	while True:
		try:
			response = client.request(
				"POST",
				CHALLAN_SEARCH_ENDPOINT,
				json_body={
					"@entity": CHALLAN_SEARCH_ENTITY,
					"tan": tan,
					"from_date_epoch": _epoch_ms(from_date),
					"to_date_epoch": _epoch_ms(to_date),
					"page_number": page,
					"page_size": SEARCH_PAGE_SIZE,
				},
				**log,
			)
		except SandboxAPIError as e:
			_raise_for_credentials(e, tan)
			raise

		data = _data(response)
		challans.extend(data.get("data") or [])
		page += 1
		if page >= (data.get("totalPages") or 0):
			return challans


def get_search_range(company: str, financial_year: str, quarter: str) -> tuple:
	"""The quarter's last month is deposited in the month after it, even when no challan records that yet."""
	from india_payroll.india_payroll.tds.data_assembly import get_quarter_date_range

	start, end = get_csi_date_range(company, financial_year, quarter)
	return start, max(end, get_last_day(add_months(get_quarter_date_range(financial_year, quarter)[1], 1)))


def recorded_challan_keys(company: str) -> set:
	"""Challans already entered for the company, in any period, so they are not offered again."""
	return {
		challan_key(c.bsr_code, c.challan_serial_no, c.challan_date)
		for c in frappe.get_all(
			"TDS Challan",
			filters={"company": company, "docstatus": ("<", 2)},
			fields=["bsr_code", "challan_serial_no", "challan_date"],
		)
	}


def _raise_for_credentials(error: SandboxAPIError, tan: str) -> None:
	message = str(error).lower()
	if "credentials not found" in message:
		raise TracesCredentialsRequired(
			_("Add the TRACES password for TAN {0} to check challans.").format(tan)
		)
	if "invalid credentials" in message:
		raise TracesCredentialsRequired(
			_("TRACES rejected the saved password for TAN {0}. Enter the current one.").format(tan)
		)


def _data(response) -> dict:
	if not isinstance(response, dict):
		return {}
	return response.get("data") or {}


def normalize_bsr(value) -> str:
	return str(value or "").strip().zfill(7)


def normalize_serial(value) -> str:
	return str(value or "").strip().zfill(5)


def paid_date(challan: dict):
	"""The deposit date of a TRACES challan, on the IST calendar."""
	epoch = challan.get("paid_date_epoch")
	if epoch is None:
		return None
	return datetime.fromtimestamp(epoch / 1000, tz=IST).date()


def challan_key(bsr_code, challan_serial, deposit_date) -> tuple:
	return normalize_bsr(bsr_code), normalize_serial(challan_serial), getdate(deposit_date)


def traces_key(challan: dict) -> tuple:
	return challan_key(challan.get("bsr_code"), challan.get("challan_serial"), paid_date(challan))


def traces_values(challan: dict | None) -> dict:
	"""The TRACES fields a TDS Challan keeps, from a TRACES challan or its absence."""
	values = {"traces_checked_on": now_datetime()}
	if not challan:
		values.update(
			traces_status=NOT_FOUND,
			traces_cin=None,
			traces_amount=0,
			traces_claimed_amount=0,
			traces_available_amount=0,
		)
		return values

	consumption = challan.get("consumption_details") or {}
	values.update(
		traces_status=challan.get("status") or "",
		traces_cin=challan.get("challan_identification_number"),
		traces_amount=flt(challan.get("total_amount")),
		traces_claimed_amount=flt(consumption.get("claimed_amount")),
		traces_available_amount=flt(consumption.get("available_amount")),
	)
	return values


def new_challan_values(challan: dict) -> dict:
	"""TDS Challan fields for a challan found on TRACES but not yet recorded."""
	breakdown = challan.get("tax_breakdown") or []
	values = {
		"challan_date": paid_date(challan),
		"bsr_code": normalize_bsr(challan.get("bsr_code")),
		"challan_serial_no": normalize_serial(challan.get("challan_serial")),
		"deposit_amount": flt(challan.get("total_amount")),
		"tds_amount": sum(flt(row.get("tax_amount")) for row in breakdown),
		"surcharge_amount": sum(flt(row.get("surcharge")) for row in breakdown),
		"education_cess": sum(flt(row.get("cess")) for row in breakdown),
	}
	if challan.get("minor_head") in MINOR_HEADS:
		values["minor_head"] = MINOR_HEADS[challan["minor_head"]]
	values.update(traces_values(challan))
	return values


def summarize(challan: dict) -> dict:
	"""What the return's check dialog shows for a TRACES challan."""
	breakdown = challan.get("tax_breakdown") or []
	return {
		"bsr_code": normalize_bsr(challan.get("bsr_code")),
		"challan_serial": normalize_serial(challan.get("challan_serial")),
		"deposit_date": paid_date(challan),
		"amount": flt(challan.get("total_amount")),
		"cin": challan.get("challan_identification_number"),
		"status": challan.get("status"),
		"minor_head": challan.get("minor_head"),
		"section": ", ".join(filter(None, (row.get("section") for row in breakdown)))
		or challan.get("section_code"),
	}


@frappe.whitelist(methods=["POST"])
def set_traces_password(company: str, password: str) -> None:
	from india_payroll.india_payroll.tds.sandbox_client import SandboxTDSClient

	frappe.has_permission("TDS Challan", "write", throw=True)
	tan = frappe.db.get_value("Company", company, "tan")
	if not tan:
		frappe.throw(
			_("Company {0} has no TAN. Set it in the company's TDS Deductor Details.").format(company)
		)
	save_traces_credentials(SandboxTDSClient(), tan, password)
