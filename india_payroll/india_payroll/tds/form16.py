"""Form 16 generation: Part B from salary data, Part A from TRACES.

Part B (the salary/tax computation) is built from the employee's annual salary
figures and rendered by Sandbox. Part A (the TDS certificate from TRACES) can
only be requested after the Q4 return has been filed; it is an async TRACES
request that is polled to completion.
"""

import io
import zipfile
from datetime import UTC, datetime, time

import frappe
from frappe import _
from frappe.utils import flt, getdate

from india_payroll import telemetry
from india_payroll.india_payroll.tds.filing import (
	FAILURE_STATUSES,
	SUCCESS_STATUSES,
	_attach,
	_data,
	_download_result,
	_extract_from_zip,
)
from india_payroll.india_payroll.tds.sandbox_client import SandboxTDSClient
from india_payroll.india_payroll.tds.settings import get_traces_credentials
from india_payroll.india_payroll.tds.validators import is_valid_pan, normalize_financial_year

PART_B_ENDPOINT = "tds/reports/form-16-part-b"
# Part A comes from TRACES: POST the deductor's TRACES credentials plus a
# challan-based security challenge (see _security_captcha); the job is then
# polled via POST {endpoint}/status?job_id=.
PART_A_ENDPOINT = "tds/compliance/traces/deductors/forms/form16"

TRACES_CREDENTIALS_ENTITY = "in.co.sandbox.tds.compliance.traces.credentials"
SECURITY_CAPTCHA_ENTITY = f"{TRACES_CREDENTIALS_ENTITY}.security_captcha"

# TRACES accepts at most 3 PAN-amount rows (plus the header row) in the challenge.
MAX_PAN_AMOUNT_ROWS = 3


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

	if created:
		telemetry.on_form16_created(created)
	return created


# ------------------------------------------------------------------ enqueue
def enqueue_part_b(docname: str) -> str | None:
	job = frappe.enqueue(run_part_b, queue="long", timeout=600, enqueue_after_commit=True, docname=docname)
	telemetry.on_form16_part_requested("B")
	frappe.msgprint(_("Form 16 Part B generation started."), alert=True)
	return job.id if job else None


def enqueue_part_a(docname: str) -> str | None:
	doc = frappe.get_doc("Form 16", docname)
	if not (
		doc.tds_return
		and frappe.db.get_value("TDS Return", doc.tds_return, "filing_status") in ("Filed", "Accepted")
	):
		frappe.throw(_("Part A can only be requested after the linked Q4 return is filed."))
	# Build the payload here too, so missing credentials or challan details fail
	# in the request instead of silently in the background job.
	_part_a_payload(doc)
	job = frappe.enqueue(run_part_a, queue="long", timeout=600, enqueue_after_commit=True, docname=docname)
	telemetry.on_form16_part_requested("A")
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
	_submit_part_a(doc)


def _submit_part_a(doc) -> None:
	# The TRACES job covers the whole TAN/quarter/FY (its zip carries every
	# employee's certificate) and Sandbox rejects a duplicate submission, so a
	# sibling Form 16's live job is joined instead of creating another.
	sibling = _lock_and_find_sibling(doc)
	if sibling:
		doc.db_set(
			{
				"part_a_job_id": sibling.part_a_job_id,
				"traces_request_id": sibling.traces_request_id,
				"part_a_status": "Requested",
			}
		)
		return

	client = SandboxTDSClient()
	resp = client.request(
		"POST",
		endpoint("a"),
		json_body=_part_a_payload(doc),
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


def _lock_and_find_sibling(doc) -> "frappe._dict | None":
	"""Row-lock every Form 16 of this TAN and year, then return a sibling with a live job.

	Concurrent jobs for the same TAN/FY would otherwise each find no sibling and
	both submit; Sandbox rejects the second, leaving that Form 16 with nothing
	to poll. The FOR UPDATE locks are held until this job's transaction commits
	when it finishes, so a concurrent job blocks on the same select and then
	reads the job id this one stored. No early commit is needed.
	"""
	rows = frappe.db.get_values(
		"Form 16",
		{"tan": doc.tan, "financial_year": doc.financial_year},
		["name", "part_a_job_id", "traces_request_id", "part_a_status"],
		as_dict=True,
		for_update=True,
	)
	return next(
		(
			row
			for row in rows or []
			if row.name != doc.name and row.part_a_job_id and row.part_a_status in ("Requested", "Available")
		),
		None,
	)


def _part_a_payload(doc) -> dict:
	credentials = get_traces_credentials(doc.company)
	return {
		"@entity": TRACES_CREDENTIALS_ENTITY,
		"username": credentials["username"],
		"password": credentials["password"],
		"tan": doc.tan,
		"security_captcha": _security_captcha(frappe.get_doc("TDS Return", doc.tds_return)),
		"remember_me": bool(credentials["remember_me"]),
	}


def _security_captcha(ret) -> dict:
	"""Build TRACES's challan-based security challenge from the filed return.

	TRACES authenticates the download with details of one challan of the
	statement plus the PAN-amount pairs deposited through it; a challan with
	more distinct pairs (up to 3) is preferred, per Sandbox's documentation.
	"""
	prn = (ret.acknowledgement_number or "").strip()
	if len(prn) != 15:
		frappe.throw(
			_(
				"The return's Provisional Receipt Number '{0}' is not the 15-character number TRACES expects."
			).format(prn)
		)

	challan_name, combos = _select_challan_combos(ret.deductees)
	if not challan_name:
		frappe.throw(
			_(
				"No challan with valid-PAN deductions found on return {0}; TRACES needs one "
				"for its security challenge."
			).format(ret.name)
		)

	challan = frappe.get_doc("TDS Challan", challan_name)
	bsr_code = (challan.bsr_code or "").strip()
	serial_no = (challan.challan_serial_no or "").strip().zfill(5)
	amount = round(flt(challan.deposit_amount))
	if len(bsr_code) != 7 or len(serial_no) != 5 or amount <= 0 or not challan.challan_date:
		frappe.throw(
			_(
				"Challan {0} is missing details TRACES needs: a 7-character BSR code, "
				"a 5-digit serial number, the deposit date and a positive deposit amount."
			).format(challan.name)
		)

	pan_amount_rows = [["sr_no", "pan", "total_amount_deposited_against_pan"]]
	pan_amount_rows += [[idx + 1, pan, amt] for idx, (pan, amt) in enumerate(combos)]

	return {
		"@entity": SECURITY_CAPTCHA_ENTITY,
		"quarter": ret.quarter,
		"financial_year": normalize_financial_year(ret.financial_year),
		"form": ret.form_type,
		"bsr_code": bsr_code,
		"challan_date": _epoch_ms(challan.challan_date),
		"challan_serial_no": serial_no,
		"provisional_receipt_number": prn,
		"challan_amount": amount,
		"unique_pan_amount_combination_for_challan": pan_amount_rows,
	}


def _select_challan_combos(deductees) -> tuple[str | None, list[tuple[str, float]]]:
	"""Pick the challan with the most distinct PAN-amount pairs (capped at 3).

	Returns (challan name, [(pan, total deposited against pan), ...]). Rows with
	placeholder PANs are skipped: TRACES only accepts real PANs in the challenge.
	"""
	totals_by_challan: dict[str, dict[str, float]] = {}
	for row in deductees or []:
		pan = (row.pan or "").strip().upper()
		if not row.challan or not is_valid_pan(pan):
			continue
		deposited = flt(row.tax_deposited) or flt(row.tax_deducted)
		by_pan = totals_by_challan.setdefault(row.challan, {})
		by_pan[pan] = by_pan.get(pan, 0.0) + deposited

	best_name, best_combos = None, []
	for name in sorted(totals_by_challan):
		combos = sorted(totals_by_challan[name].items())[:MAX_PAN_AMOUNT_ROWS]
		if len(combos) > len(best_combos):
			best_name, best_combos = name, combos

	return best_name, [(pan, flt(amount, 2)) for pan, amount in best_combos]


def _epoch_ms(date) -> int:
	"""EPOCH milliseconds at UTC midnight, the convention Sandbox's examples use."""
	return int(datetime.combine(getdate(date), time.min, tzinfo=UTC).timestamp() * 1000)


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
	if part == "a":
		# TRACES jobs are polled with the credentials in the body; with
		# remember_me the credentials are optional, but sending them keeps the
		# poll working in both modes (without them, one poll logs in and the
		# next reports the status, which the scheduler's repetition covers).
		resp = client.request(
			"POST",
			f"{endpoint('a')}/status",
			params={"job_id": job_id},
			json_body=_traces_poll_body(doc),
			reference_doctype="Form 16",
			reference_name=doc.name,
		)
	else:
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
		reason = data.get("error") or data.get("message") or data.get("error_message")
		if reason:
			doc.add_comment("Comment", _("Form 16 Part {0} failed: {1}").format(part.upper(), reason))
		return
	if status not in SUCCESS_STATUSES:
		return

	content = _certificate_bytes(client, data, part)
	if not content:
		return
	artifact = _form16_artifact(content, doc.name, part, doc.pan)
	if not artifact:
		# A multi-employee archive with no certificate for this PAN is never
		# attached: it would expose every other employee's Part A.
		doc.db_set(f"part_{part}_status", "Failed")
		doc.add_comment(
			"Comment",
			_("Form 16 Part {0}: the file TRACES returned has no certificate for PAN {1}.").format(
				part.upper(), doc.pan
			),
		)
		return
	filename, payload = artifact
	_attach(doc, f"part_{part}_file", filename, payload)
	doc.db_set(f"part_{part}_status", "Available")


def _traces_poll_body(doc) -> dict:
	body = {"@entity": TRACES_CREDENTIALS_ENTITY}
	credentials = get_traces_credentials(doc.company, required=False)
	if credentials["username"] and credentials["password"]:
		body.update(
			{
				"username": credentials["username"],
				"password": credentials["password"],
				"tan": doc.tan,
			}
		)
	return body


def _form16_artifact(
	content: bytes, docname: str, part: str, pan: str | None = None
) -> tuple[str, bytes] | None:
	"""The single PDF to attach, or None when there is no certificate for this employee.

	Only Part A archives hold several employees' certificates (one PDF per PAN),
	so only they are filtered by PAN. A Part B report is requested for one
	employee and its PDF is taken as is, whatever the file inside is called.
	"""
	base = f"{docname}-part-{part.upper()}"
	match_pan = part == "a" and pan
	pdf = _pdf_for_pan(content, pan) if match_pan else _extract_from_zip(content, ".pdf")
	if pdf:
		return f"{base}.pdf", pdf
	if content[:5] == b"%PDF-":
		return f"{base}.pdf", content
	return None


def _pdf_for_pan(content: bytes, pan: str | None) -> bytes | None:
	"""The TRACES zip holds one PDF per employee, named by PAN; pick this one's."""
	if not pan:
		return None
	try:
		with zipfile.ZipFile(io.BytesIO(content)) as zf:
			name = next(
				(n for n in zf.namelist() if pan.upper() in n.upper() and n.lower().endswith(".pdf")),
				None,
			)
			return zf.read(name) if name else None
	except zipfile.BadZipFile:
		return None


def _certificate_bytes(client: SandboxTDSClient, data: dict, part: str) -> bytes | None:
	"""TRACES returns certificates as a list of zip URLs; reports return a single file."""
	urls = data.get("certificate_zip_urls")
	if isinstance(urls, list) and urls:
		return client.fetch_file(urls[0])

	key = "form_16_part_b" if part == "b" else "form_16_part_a"
	return _download_result(client, data, key, required=False) or _download_result(
		client, data, "form_16", required=False
	)
