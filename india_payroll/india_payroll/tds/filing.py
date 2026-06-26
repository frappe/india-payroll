"""Orchestration for the Form 24Q filing pipeline.

Each step is a separate, user-triggered background job that submits an async
job to Sandbox and records it in the return's filing log. A scheduled poller
(`poll_open_jobs`) advances open jobs: on completion it stores the resulting
file/acknowledgement and moves the return to the next status.

    validate -> generate_txt -> generate_fvu -> e_file

Endpoint paths are centralised (and overridable via site_config) so the
integration can track Sandbox API changes without touching the flow.
"""

import base64
import io
import json
import zipfile

import frappe
import requests
from frappe import _
from frappe.utils import flt, now_datetime
from frappe.utils.file_manager import save_file

from india_payroll.india_payroll.tds.csi import fetch_csi, total_deposited
from india_payroll.india_payroll.tds.sandbox_client import SandboxTDSClient
from india_payroll.india_payroll.tds.sheet_json import build_sheet_json
from india_payroll.india_payroll.tds.validators import normalize_financial_year

EFILE_ENTITY = "in.co.sandbox.tds.compliance.e-file.request"
RECONCILIATION_TOLERANCE = 10.0  # rupees

# step -> (label, in-progress status, done status, default endpoint)
STEPS = {
	"validate": ("Validate", "Validating", "Validated", "tds/analytics/potential-notices"),
	"generate_txt": ("Generate TXT", "Generating TXT", "TXT Generated", "tds/reports/txt"),
	"generate_fvu": ("Generate FVU", "Generating FVU", "FVU Generated", "tds/compliance/fvu"),
	"e_file": ("E-File", "Filing", "Filed", "tds/compliance/e-file"),
}
LABEL_TO_STEP = {label: step for step, (label, *_rest) in STEPS.items()}
IN_PROGRESS_STATUSES = [v[1] for v in STEPS.values()]


def endpoint_for(step: str) -> str:
	return frappe.conf.get(f"sandbox_tds_{step}_endpoint") or STEPS[step][3]


# ------------------------------------------------------------------ enqueue
def enqueue_step(docname: str, step: str) -> str:
	"""Validate preconditions and enqueue a filing step. Returns the RQ job id."""
	doc = frappe.get_doc("TDS Return", docname)
	doc.check_permission("write")
	_check_precondition(doc, step)

	job = frappe.enqueue(
		run_step,
		queue="long",
		timeout=900,
		enqueue_after_commit=True,
		docname=docname,
		step=step,
	)
	frappe.msgprint(
		_("{0} started in the background. The return status will update automatically.").format(
			STEPS[step][0]
		),
		alert=True,
	)
	return job.id


def _check_precondition(doc, step: str) -> None:
	if step == "validate":
		if not doc.deductees:
			frappe.throw(_("Add deductee rows (use 'Fetch from Payroll') before validating."))
		_check_reconciliation(doc)
	elif step == "generate_txt":
		if doc.filing_status not in ("Validated", "TXT Generated", "FVU Generated"):
			frappe.throw(_("Validate the return before generating the TXT file."))
	elif step == "generate_fvu":
		if not doc.txt_file:
			frappe.throw(_("Generate the TXT file before generating the FVU."))
	elif step == "e_file":
		if not doc.fvu_file:
			frappe.throw(_("Generate the FVU file before e-filing."))


def _check_reconciliation(doc) -> None:
	deposited = total_deposited(doc.company, doc.financial_year, doc.quarter)
	deducted = sum(flt(d.tax_deducted) for d in doc.deductees)
	if deducted - deposited > RECONCILIATION_TOLERANCE:
		frappe.throw(
			_(
				"TDS deducted ({0}) exceeds TDS deposited via challans ({1}). "
				"Record the missing challan(s) before filing."
			).format(deducted, deposited)
		)


# -------------------------------------------------------------------- run
def run_step(docname: str, step: str) -> None:
	doc = frappe.get_doc("TDS Return", docname)
	client = SandboxTDSClient()
	label = STEPS[step][0]
	try:
		_SUBMIT[step](doc, client)
		frappe.db.commit()
	except Exception as e:
		doc.reload()
		doc.add_action(label, "Failed", message=str(e)[:500], save=False)
		doc.set_status("Failed", save=False)
		doc.save(ignore_permissions=True)
		frappe.db.commit()
		frappe.log_error(title=f"TDS {label} failed for {docname}")
		raise


def _identity(doc) -> dict:
	return {
		"tan": doc.tan,
		"financial_year": normalize_financial_year(doc.financial_year),
		"quarter": doc.quarter,
		"form": doc.form_type,
	}


def _parse_job(resp: dict) -> tuple:
	"""Extract (job_id, upload_url) from a Sandbox job-creation response."""
	data = resp.get("data", resp) if isinstance(resp, dict) else {}
	job_id = data.get("job_id") or data.get("id") or resp.get("transaction_id")
	upload_url = (
		data.get("presigned_url")
		or data.get("upload_url")
		or data.get("fvu_upload_file_url")
		or data.get("url")
	)
	return job_id, upload_url


def _submit_validate(doc, client: SandboxTDSClient) -> None:
	sheet = build_sheet_json(doc)
	_attach(doc, "sheet_json_file", "sheet.json", json.dumps(sheet, indent=2, default=str).encode())
	resp = client.request(
		"POST",
		endpoint_for("validate"),
		json_body=_identity(doc),
		reference_doctype="TDS Return",
		reference_name=doc.name,
	)
	job_id, upload_url = _parse_job(resp)
	if upload_url:
		client.upload_to_presigned_url(
			upload_url, json.dumps(sheet, default=str).encode(), "application/json"
		)
	doc.reload()
	doc.add_action("Validate", "created", job_id=job_id, save=False)
	doc.set_status(STEPS["validate"][1], save=False)
	doc.save(ignore_permissions=True)


def _submit_txt(doc, client: SandboxTDSClient) -> None:
	sheet = build_sheet_json(doc)
	body = _identity(doc)
	if doc.return_type == "Revised" and doc.previous_receipt_number:
		body["previous_receipt_number"] = doc.previous_receipt_number
	resp = client.request(
		"POST",
		endpoint_for("generate_txt"),
		json_body=body,
		reference_doctype="TDS Return",
		reference_name=doc.name,
	)
	job_id, upload_url = _parse_job(resp)
	if upload_url:
		client.upload_to_presigned_url(
			upload_url, json.dumps(sheet, default=str).encode(), "application/json"
		)
	doc.reload()
	doc.add_action("Generate TXT", "created", job_id=job_id, save=False)
	doc.set_status(STEPS["generate_txt"][1], save=False)
	doc.save(ignore_permissions=True)


def _submit_fvu(doc, client: SandboxTDSClient) -> None:
	# FVU needs the TXT plus the challan CSI file.
	if not doc.csi_file:
		csi_bytes = fetch_csi(client, doc.tan, doc.financial_year, doc.quarter)
		_attach(doc, "csi_file", f"{doc.tan}.csi", csi_bytes)
		doc.reload()
	txt_b64 = base64.b64encode(_read_attachment(doc.txt_file)).decode()
	csi_b64 = base64.b64encode(_read_attachment(doc.csi_file)).decode()
	resp = client.request(
		"POST",
		endpoint_for("generate_fvu"),
		json_body={**_identity(doc), "txt_file_base64": txt_b64, "csi_file_base64": csi_b64},
		reference_doctype="TDS Return",
		reference_name=doc.name,
	)
	job_id, _url = _parse_job(resp)
	doc.reload()
	doc.add_action("Generate FVU", "created", job_id=job_id, save=False)
	doc.set_status(STEPS["generate_fvu"][1], save=False)
	doc.save(ignore_permissions=True)


def _submit_efile(doc, client: SandboxTDSClient) -> None:
	resp = client.request(
		"POST",
		endpoint_for("e_file"),
		json_body={**_identity(doc), "entity": EFILE_ENTITY},
		reference_doctype="TDS Return",
		reference_name=doc.name,
	)
	job_id, upload_url = _parse_job(resp)
	if upload_url:
		client.upload_to_presigned_url(upload_url, _build_fvu_zip(doc), "application/zip")
	doc.reload()
	doc.add_action("E-File", "created", job_id=job_id, save=False)
	doc.set_status(STEPS["e_file"][1], save=False)
	doc.save(ignore_permissions=True)


_SUBMIT = {
	"validate": _submit_validate,
	"generate_txt": _submit_txt,
	"generate_fvu": _submit_fvu,
	"e_file": _submit_efile,
}


# ------------------------------------------------------------------ polling
def poll_open_jobs() -> None:
	"""Scheduled: advance every TDS Return that has an open Sandbox job."""
	names = frappe.get_all(
		"TDS Return",
		filters={"docstatus": 0, "filing_status": ["in", IN_PROGRESS_STATUSES]},
		pluck="name",
	)
	for name in names:
		try:
			poll_return(name)
			frappe.db.commit()
		except Exception:
			frappe.db.rollback()
			frappe.log_error(title=f"TDS poll failed for {name}")


def poll_return(docname: str) -> None:
	doc = frappe.get_doc("TDS Return", docname)
	job = doc.get_open_job()
	if not job:
		return

	step = LABEL_TO_STEP.get(job["request_type"])
	if not step:
		return

	client = SandboxTDSClient()
	resp = client.request(
		"GET",
		endpoint_for(step),
		params={"job_id": job["job_id"]},
		reference_doctype="TDS Return",
		reference_name=doc.name,
	)
	data = resp.get("data", resp) if isinstance(resp, dict) else {}
	status = str(data.get("status") or resp.get("status") or "").lower()

	_update_action(doc, job["row"], status or "polled")

	if status in ("created", "queued", "processing", "in_progress", "pending", ""):
		doc.save(ignore_permissions=True)
		return

	if status in ("failed", "error", "rejected"):
		doc.set_status("Failed", save=False)
		doc.add_action(STEPS[step][0], "Failed", message=_first_message(data), save=False)
		doc.save(ignore_permissions=True)
		return

	# Completed — handle the result for this step.
	_HANDLE_RESULT[step](doc, data)
	doc.set_status(STEPS[step][2], save=False)
	doc.save(ignore_permissions=True)


def _update_action(doc, row_name: str, status: str) -> None:
	for action in doc.filing_actions:
		if action.name == row_name:
			action.status = status
			break


def _result_validate(doc, data: dict) -> None:
	issues = data.get("issues") or data.get("potential_notices") or data.get("errors") or []
	doc.validation_issues = json.dumps(issues, indent=2, default=str)


def _result_txt(doc, data: dict) -> None:
	_attach(doc, "txt_file", f"{doc.name}.txt", _download_result(data, "txt"))


def _result_fvu(doc, data: dict) -> None:
	_attach(doc, "fvu_file", f"{doc.name}.fvu", _download_result(data, "fvu"))
	form_27a = _download_result(data, "form_27a", required=False)
	if form_27a:
		_attach(doc, "form_27a", f"{doc.name}-27A.pdf", form_27a)


def _result_efile(doc, data: dict) -> None:
	doc.token_number = data.get("token_number") or data.get("token")
	doc.acknowledgement_number = (
		data.get("provisional_receipt_number") or data.get("acknowledgement") or data.get("receipt_number")
	)
	doc.filing_date = now_datetime()


_HANDLE_RESULT = {
	"validate": _result_validate,
	"generate_txt": _result_txt,
	"generate_fvu": _result_fvu,
	"e_file": _result_efile,
}


# ----------------------------------------------------------------- file utils
def _download_result(data: dict, key: str, required: bool = True) -> bytes | None:
	"""Return file bytes from a result payload via base64 or a download URL."""
	b64 = data.get(f"{key}_file_base64") or data.get(f"{key}_base64")
	if b64:
		return base64.b64decode(b64)
	url = data.get(f"{key}_file_url") or data.get(f"{key}_url")
	if url:
		resp = requests.get(url, timeout=60)
		resp.raise_for_status()
		return resp.content
	if required:
		frappe.throw(_("Sandbox response did not contain the {0} file.").format(key.upper()))
	return None


def _attach(doc, fieldname: str, filename: str, content: bytes) -> None:
	saved = save_file(filename, content, doc.doctype, doc.name, is_private=1)
	doc.db_set(fieldname, saved.file_url)


def _read_attachment(file_url: str) -> bytes:
	file_doc = frappe.get_doc("File", {"file_url": file_url})
	return file_doc.get_content()


def _build_fvu_zip(doc) -> bytes:
	"""Zip the FVU (and Form 27A, if present) for the e-file upload."""
	buffer = io.BytesIO()
	with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as zf:
		zf.writestr(f"{doc.name}.fvu", _read_attachment(doc.fvu_file))
		if doc.form_27a:
			zf.writestr(f"{doc.name}-27A.pdf", _read_attachment(doc.form_27a))
	return buffer.getvalue()


def _first_message(data: dict) -> str:
	for key in ("message", "error", "detail"):
		if data.get(key):
			return str(data[key])[:500]
	return _("Filing step failed.")
