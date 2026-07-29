"""Form 16 generation: Part B from salary data, Part A from TRACES.

Part B (the salary/tax computation) is built from the employee's annual salary
figures and rendered by Sandbox. Part A (the TDS certificate from TRACES) can
only be requested after the Q4 return has been filed; it is an async TRACES
request that is polled to completion.
"""

import frappe
from frappe import _
from frappe.utils import flt

from india_payroll.india_payroll.tds.filing import (
	FAILURE_STATUSES,
	SUCCESS_STATUSES,
	_attach,
	_data,
	_download_result,
	_extract_from_zip,
)
from india_payroll.india_payroll.tds.sandbox_client import SandboxTDSClient
from india_payroll.india_payroll.tds.validators import normalize_financial_year

PART_B_ENDPOINT = "tds/reports/form-16-part-b"
# Part A comes from TRACES, whose documented contract is
# POST /tds/compliance/traces/deductors/forms/form16 with the deductor's TRACES
# credentials plus a challan-based security challenge. Those inputs are not
# modelled on Form 16 yet, so the endpoint stays overridable via site_config.
PART_A_ENDPOINT = "tds/compliance/traces/deductors/forms/form16"


def endpoint(part: str) -> str:
	default = PART_B_ENDPOINT if part == "b" else PART_A_ENDPOINT
	return frappe.conf.get(f"sandbox_tds_form16_part_{part}_endpoint") or default


# ----------------------------------------------------------- bulk creation
@frappe.whitelist()
def create_forms_for_return(return_name: str) -> int:
	"""Create a Form 16 record for each employee in a filed Q4 TDS Return."""
	ret = frappe.get_doc("TDS Return", return_name)
	ret.check_permission("write")
	if ret.quarter != "Q4":
		frappe.throw(_("Form 16 is generated from the Q4 return (which carries the annual annexure)."))

	from india_payroll.india_payroll.tds.sheet_json import _salary_annexure

	created = 0
	for row in _salary_annexure(ret):
		if frappe.db.exists("Form 16", {"employee": row["employee"], "financial_year": ret.financial_year}):
			continue
		doc = frappe.get_doc(
			{
				"doctype": "Form 16",
				"employee": row["employee"],
				"pan": row["pan"],
				"company": ret.company,
				"tan": ret.tan,
				"financial_year": ret.financial_year,
				"tds_return": ret.name,
				"gross_salary": flt(row["gross_salary"]),
				"total_taxable_income": flt(row["taxable_income"]),
				"total_tax_deducted": flt(row["total_tax"]),
			}
		)
		doc.insert()
		created += 1
	return created


# ------------------------------------------------------------------ enqueue
def enqueue_part_b(docname: str) -> str | None:
	job = frappe.enqueue(run_part_b, queue="long", timeout=600, enqueue_after_commit=True, docname=docname)
	frappe.msgprint(_("Form 16 Part B generation started."), alert=True)
	return job.id if job else None


def enqueue_part_a(docname: str) -> str | None:
	doc = frappe.get_doc("Form 16", docname)
	if not (
		doc.tds_return
		and frappe.db.get_value("TDS Return", doc.tds_return, "filing_status") in ("Filed", "Accepted")
	):
		frappe.throw(_("Part A can only be requested after the linked Q4 return is filed."))
	job = frappe.enqueue(run_part_a, queue="long", timeout=600, enqueue_after_commit=True, docname=docname)
	frappe.msgprint(_("Form 16 Part A requested from TRACES."), alert=True)
	return job.id if job else None


# -------------------------------------------------------------------- submit
def run_part_b(docname: str) -> None:
	doc = frappe.get_doc("Form 16", docname)
	client = SandboxTDSClient()
	resp = client.request(
		"POST",
		endpoint("b"),
		json_body={
			"tan": doc.tan,
			"pan": doc.pan,
			"financial_year": normalize_financial_year(doc.financial_year),
			"employee": doc.employee,
		},
		reference_doctype="Form 16",
		reference_name=doc.name,
	)
	job_id = _job_id(resp, "B")
	doc.db_set({"part_b_job_id": job_id, "part_b_status": "Requested"})


def run_part_a(docname: str) -> None:
	doc = frappe.get_doc("Form 16", docname)
	client = SandboxTDSClient()
	resp = client.request(
		"POST",
		endpoint("a"),
		json_body={
			"tan": doc.tan,
			"pan": doc.pan,
			"financial_year": normalize_financial_year(doc.financial_year),
		},
		reference_doctype="Form 16",
		reference_name=doc.name,
	)
	data = _data(resp)
	doc.db_set(
		{
			"part_a_job_id": _job_id(resp, "A"),
			"traces_request_id": data.get("request_id") or data.get("traces_request_id"),
			"part_a_status": "Requested",
		}
	)


def _job_id(resp: dict, part: str) -> str:
	job_id = _data(resp).get("job_id")
	if not job_id:
		frappe.throw(_("Sandbox did not return a job id for Form 16 Part {0}.").format(part))
	return job_id


def poll_form16_jobs() -> None:
	"""Scheduled: download Part A / Part B PDFs once Sandbox jobs complete."""
	for part in ("a", "b"):
		status_field = f"part_{part}_status"
		job_field = f"part_{part}_job_id"
		names = frappe.get_all(
			"Form 16",
			filters={status_field: "Requested", job_field: ["is", "set"]},
			pluck="name",
		)
		for name in names:
			try:
				_poll_one(name, part)
				frappe.db.commit()  # nosemgrep: commit each Form 16 so one item's failure and rollback doesn't discard earlier iterations' progress
			except Exception:
				frappe.db.rollback()
				frappe.log_error(title=f"Form 16 Part {part.upper()} poll failed for {name}")


def _poll_one(docname: str, part: str) -> None:
	doc = frappe.get_doc("Form 16", docname)
	job_id = doc.get(f"part_{part}_job_id")
	client = SandboxTDSClient()
	resp = client.request(
		"GET",
		endpoint(part),
		params={"job_id": job_id},
		reference_doctype="Form 16",
		reference_name=doc.name,
	)
	data = _data(resp)
	status = str(data.get("status") or "").lower()

	if status in FAILURE_STATUSES:
		doc.db_set(f"part_{part}_status", "Failed")
		return
	if status not in SUCCESS_STATUSES:
		return

	content = _certificate_bytes(client, data, part)
	if content:
		pdf = _extract_from_zip(content, ".pdf")
		if pdf:
			_attach(doc, f"part_{part}_file", f"{doc.name}-part-{part.upper()}.pdf", pdf)
		else:
			_attach(doc, f"part_{part}_file", f"{doc.name}-part-{part.upper()}.zip", content)
	doc.db_set(f"part_{part}_status", "Available")


def _certificate_bytes(client: SandboxTDSClient, data: dict, part: str) -> bytes | None:
	"""TRACES returns certificates as a list of zip URLs; reports return a single file."""
	urls = data.get("certificate_zip_urls")
	if isinstance(urls, list) and urls:
		return client.fetch_file(urls[0])

	key = "form_16_part_b" if part == "b" else "form_16_part_a"
	return _download_result(client, data, key, required=False) or _download_result(
		client, data, "form_16", required=False
	)
