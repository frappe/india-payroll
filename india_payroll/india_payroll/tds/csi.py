"""Challan helpers for TDS filing.

The CSI (Challan Status Inquiry) file is required alongside the TXT to generate
the FVU. It originates from OLTAS, and Sandbox exposes it as a two-step,
OTP-verified flow against the deductor's TRACES-registered mobile number:

    POST /tds/compliance/csi/otp         -> reference_id, OTP sent to the deductor
    POST /tds/compliance/csi/otp/verify  -> csi_url

Because a human has to read the OTP, this cannot run inside the background filing
job; it is driven from the return instead, and the CSI can always be attached by
hand as well.
"""

import base64
import re
from datetime import datetime, timedelta, timezone

import frappe
from frappe import _
from frappe.utils import flt, getdate

from india_payroll.india_payroll.doctype.tds_challan.tds_challan import get_challans_for_period

CSI_OTP_ENDPOINT = "tds/compliance/csi/otp"
CSI_VERIFY_ENDPOINT = "tds/compliance/csi/otp/verify"
CSI_OTP_ENTITY = "in.co.sandbox.tds.compliance.deductors.otp.request"
CSI_VERIFY_ENTITY = "in.co.sandbox.tds.compliance.deductors.csi.request"
CSI_REASON_MIN_LENGTH = 20
IST = timezone(timedelta(hours=5, minutes=30))


def _epoch_ms(value) -> int:
	"""Midnight IST of the given date, matching the epoch values in Sandbox's examples."""
	day = getdate(value)
	return int(datetime(day.year, day.month, day.day, tzinfo=IST).timestamp() * 1000)


def get_csi_date_range(company: str, financial_year: str, quarter: str) -> tuple:
	"""The CSI filters on deposit date, and a quarter's challans are deposited after it ends."""
	from india_payroll.india_payroll.tds.data_assembly import get_quarter_date_range

	start, end = get_quarter_date_range(financial_year, quarter)
	deposit_dates = [
		getdate(c.challan_date)
		for c in get_challans_for_period(company, financial_year, quarter)
		if c.challan_date
	]
	if deposit_dates:
		start = min(start, *deposit_dates)
		end = max(end, *deposit_dates)
	return start, end


def validate_csi_content(content: bytes, tan: str) -> None:
	"""OLTAS answers an empty range with a plain-text message instead of a CSI file."""
	text = (content or b"").decode("utf-8", errors="replace").strip()
	header = text.splitlines()[0].split("^") if text else []
	if len(header) < 2 or header[1].strip().upper() != tan.upper():
		frappe.throw(
			_(
				"Sandbox returned no usable CSI file for TAN {0} (OLTAS replied: {1}). Check that the "
				"challans for this period appear in the TIN Challan Status Inquiry."
			).format(tan, text[:100] or _("empty response"))
		)


def get_challan_rows(company: str, financial_year: str, quarter: str) -> list:
	"""Submitted challans for the period. Shaping into sheet columns is the caller's job."""
	challans = get_challans_for_period(company, financial_year, quarter)
	if not challans:
		frappe.throw(
			_("No submitted TDS Challan found for {0} / {1} / {2}. Record the challan(s) first.").format(
				company, financial_year, quarter
			)
		)
	return challans


def total_deposited(company: str, financial_year: str, quarter: str) -> float:
	"""Sum of TDS deposited via challans for the period (for reconciliation)."""
	challans = get_challans_for_period(company, financial_year, quarter)
	return sum(flt(c.deposit_amount) for c in challans)


def request_csi_otp(client, tan: str, mobile_number: str, from_date, to_date, reason: str) -> str:
	"""Start the CSI download: Sandbox sends an OTP to the deductor. Returns a reference id."""
	mobile = re.sub(r"\D", "", mobile_number or "")[-10:]
	if not re.fullmatch(r"[1-9][0-9]{9}", mobile):
		frappe.throw(_("A valid 10-digit mobile number registered with TRACES is required for the CSI OTP."))

	reason = (reason or "").strip()
	if len(reason) < CSI_REASON_MIN_LENGTH:
		frappe.throw(
			_("Give a reason of at least {0} characters for the CSI download.").format(CSI_REASON_MIN_LENGTH)
		)

	response = client.request(
		"POST",
		frappe.conf.get("sandbox_tds_csi_otp_endpoint") or CSI_OTP_ENDPOINT,
		json_body={
			"@entity": CSI_OTP_ENTITY,
			"tan": tan,
			"mobile_number": mobile,
			"from": _epoch_ms(from_date),
			"to": _epoch_ms(to_date),
			"consent": "Y",
			"reason": reason,
		},
	)
	data = response.get("data", response) if isinstance(response, dict) else {}
	reference_id = data.get("reference_id")
	if not reference_id:
		frappe.throw(
			_("Sandbox could not send the CSI OTP: {0}").format(
				data.get("message") or _("no reference id returned")
			)
		)
	return reference_id


def verify_csi_otp(client, reference_id: str, otp: str) -> bytes:
	"""Complete the CSI download with the OTP and return the file bytes."""
	response = client.request(
		"POST",
		frappe.conf.get("sandbox_tds_csi_verify_endpoint") or CSI_VERIFY_ENDPOINT,
		json_body={
			"@entity": CSI_VERIFY_ENTITY,
			"reference_id": reference_id,
			"otp": (otp or "").strip(),
		},
	)
	data = response.get("data", response) if isinstance(response, dict) else {}

	if data.get("csi_file_base64"):
		return base64.b64decode(data["csi_file_base64"])

	for key in ("csi_url", "csi_file_url"):
		if data.get(key):
			return client.fetch_file(data[key])

	frappe.throw(
		_("Sandbox did not return a CSI file: {0}").format(data.get("message") or _("no file URL returned"))
	)
