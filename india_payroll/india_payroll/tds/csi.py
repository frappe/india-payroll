"""Challan helpers for TDS filing.

The CSI (Challan Status Inquiry) file is required alongside the TXT to generate
the FVU. It originates from OLTAS and is fetched through Sandbox's challan
endpoint. This module assembles the challan section used in the Sheet JSON and
retrieves the CSI bytes via the client.
"""

import base64

import frappe
from frappe import _
from frappe.utils import flt, formatdate

from india_payroll.india_payroll.doctype.tds_challan.tds_challan import get_challans_for_period
from india_payroll.india_payroll.tds.validators import normalize_financial_year

# Endpoint is configurable so the integration can track Sandbox API changes
# without a code edit. Override via site_config: "sandbox_tds_csi_endpoint".
DEFAULT_CSI_ENDPOINT = "tds/compliance/csi"


def build_challan_section(company: str, financial_year: str, quarter: str) -> list[dict]:
	"""Return the challan rows for a return, in the shape Sandbox's Sheet JSON expects."""
	challans = get_challans_for_period(company, financial_year, quarter)
	if not challans:
		frappe.throw(
			_("No submitted TDS Challan found for {0} / {1} / {2}. Record the challan(s) first.").format(
				company, financial_year, quarter
			)
		)

	section = []
	for ch in challans:
		section.append(
			{
				"challan_name": ch.name,
				"bsr_code": ch.bsr_code,
				"challan_serial_no": ch.challan_serial_no,
				"challan_date": formatdate(ch.challan_date, "dd-MM-yyyy"),
				"section": ch.section or "192",
				"minor_head": "200" if (ch.minor_head or "").endswith("(200)") else "400",
				"tds": flt(ch.tds_amount),
				"surcharge": flt(ch.surcharge_amount),
				"education_cess": flt(ch.education_cess),
				"interest": flt(ch.interest),
				"fee": flt(ch.fee),
				"others": flt(ch.others),
				"total": flt(ch.deposit_amount),
			}
		)
	return section


def total_deposited(company: str, financial_year: str, quarter: str) -> float:
	"""Sum of TDS deposited via challans for the period (for reconciliation)."""
	challans = get_challans_for_period(company, financial_year, quarter)
	return sum(flt(c.deposit_amount) for c in challans)


MANUAL_CSI_HINT = _(
	"Attach the CSI file for TAN {0} ({1} {2}) to the 'CSI File' field and run Generate FVU again. "
	"Download it from the TIN OLTAS 'Challan Status Inquiry' page for the deductor."
)


def fetch_csi(client, tan: str, financial_year: str, quarter: str) -> bytes:
	"""Fetch the CSI file bytes from Sandbox for the given TAN and period.

	Sandbox's documented CSI download is an interactive, OTP-verified TRACES flow
	(generate_otp / verify_otp), which cannot run unattended from a background job.
	This attempts the direct endpoint (overridable via `sandbox_tds_csi_endpoint`)
	and, when it is unavailable, asks the user to attach the CSI manually.
	"""
	endpoint = frappe.conf.get("sandbox_tds_csi_endpoint") or DEFAULT_CSI_ENDPOINT
	try:
		response = client.request(
			"POST",
			endpoint,
			json_body={
				"tan": tan,
				"financial_year": normalize_financial_year(financial_year),
				"quarter": quarter,
			},
		)
	except Exception as e:
		frappe.throw(
			_("Could not fetch the CSI file automatically ({0}).").format(str(e)[:200])
			+ " "
			+ MANUAL_CSI_HINT.format(tan, financial_year, quarter)
		)

	data = response.get("data", response) if isinstance(response, dict) else {}

	if data.get("csi_file_base64"):
		return base64.b64decode(data["csi_file_base64"])

	for key in ("csi_file_url", "csi_url"):
		if data.get(key):
			return client.fetch_file(data[key])

	frappe.throw(
		_("Sandbox did not return a CSI file for TAN {0} ({1} {2}).").format(tan, financial_year, quarter)
		+ " "
		+ MANUAL_CSI_HINT.format(tan, financial_year, quarter)
	)
