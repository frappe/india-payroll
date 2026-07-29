"""Orchestration for the Form 24Q (Form 138 from TY 2026-27) filing pipeline.

Each step is a separate, user-triggered background job that submits an async
job to Sandbox and records it in the return's filing log. A scheduled poller
(`poll_open_jobs`) advances open jobs: on completion it stores the resulting
file/acknowledgement and moves the return to the next status.

    validate -> generate_txt -> generate_fvu -> e_file

Every step follows Sandbox's job-based contract: POST creates a job and returns
presigned S3 URLs, the payload is PUT to those URLs (which is what triggers
processing), and a GET with ?job_id= polls until the job reaches a terminal
status. Endpoint paths are overridable via site_config.
"""

import base64
import io
import json
import zipfile

import frappe
from frappe import _
from frappe.utils import flt, get_datetime, now_datetime, time_diff_in_seconds
from frappe.utils.file_manager import save_file
from frappe.utils.scheduler import is_scheduler_inactive

from india_payroll.india_payroll.tds.csi import fetch_csi
from india_payroll.india_payroll.tds.sandbox_client import SandboxTDSClient
from india_payroll.india_payroll.tds.sheet_json import build_sheet_json
from india_payroll.india_payroll.tds.validators import (
	form_code,
	normalize_financial_year,
	normalize_tax_year,
)

RECONCILIATION_TOLERANCE = 10.0  # rupees

# A job that never reaches a terminal status is failed rather than polled forever.
MAX_JOB_AGE_MINUTES = 180

# "created" means Sandbox is still waiting for the payload upload, which this
# integration performs immediately after creating the job. Staying there means the
# upload did not register, and Sandbox offers no way to re-trigger it.
MAX_CREATED_MINUTES = 20

STEP_SPECS = {
	"validate": {
		"label": "Validate",
		"in_progress": "Validating",
		"done": "Validated",
		"endpoint": "tds/analytics/potential-notices",
		"entity": "in.co.sandbox.tds.analytics.potential_notice.request",
		"period": "financial_year",
		"failure_urls": ("validation_report_url", "validation_report_file_url"),
	},
	"generate_txt": {
		"label": "Generate TXT",
		"in_progress": "Generating TXT",
		"done": "TXT Generated",
		"endpoint": "tds/reports/txt",
		"entity": "in.co.sandbox.tds.reports.request",
		"period": "tax_year",
		"failure_urls": ("validation_report_url", "validation_report_file_url"),
	},
	"generate_fvu": {
		"label": "Generate FVU",
		"in_progress": "Generating FVU",
		"done": "FVU Generated",
		"endpoint": "tds/compliance/fvu/generate",
		"entity": "in.co.sandbox.tds.compliance.fvu.generate.request",
		"period": "tax_year",
		"failure_urls": ("validation_report_file_url", "validation_report_url"),
	},
	"e_file": {
		"label": "E-File",
		"in_progress": "Filing",
		"done": "Filed",
		"endpoint": "tds/compliance/e-file",
		"entity": "in.co.sandbox.tds.compliance.e-file.request",
		"period": "tax_year",
		"failure_urls": (),
	},
}

LABEL_TO_STEP = {spec["label"]: step for step, spec in STEP_SPECS.items()}
IN_PROGRESS_STATUSES = [spec["in_progress"] for spec in STEP_SPECS.values()]

PENDING_STATUSES = frozenset({"created", "queued", "in_progress", "processing", "pending"})
SUCCESS_STATUSES = frozenset({"succeeded", "completed", "success"})
FAILURE_STATUSES = frozenset({"failed", "error", "rejected", "cancelled", "expired", "timed_out"})

TERMINAL_ACTION_STATUSES = SUCCESS_STATUSES | FAILURE_STATUSES


def endpoint_for(step: str) -> str:
	return frappe.conf.get(f"sandbox_tds_{step}_endpoint") or STEP_SPECS[step]["endpoint"]


def _data(resp: object) -> dict:
	if not isinstance(resp, dict):
		return {}
	data = resp.get("data")
	return data if isinstance(data, dict) else resp


# ------------------------------------------------------------------ enqueue
def enqueue_step(docname: str, step: str) -> str | None:
	"""Validate preconditions and enqueue a filing step. Returns the RQ job id, if available."""
	doc = frappe.get_doc("TDS Return", docname)
	doc.check_permission("write")
	_check_precondition(doc, step)

	# The filing pipeline is driven asynchronously: each step runs as a background job and the
	# scheduled poller (poll_open_jobs) advances it to the next status. If the scheduler is
	# inactive, the poller never runs and the return would stall mid-step — so bail out early
	# with a clear message instead of queueing a job that can't progress.
	if is_scheduler_inactive(verbose=False):
		frappe.msgprint(
			_("The background scheduler is disabled, so {0} cannot run. Enable it and try again.").format(
				STEP_SPECS[step]["label"]
			),
			title=_("Scheduler Disabled"),
			indicator="red",
		)
		return

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
			STEP_SPECS[step]["label"]
		),
		alert=True,
	)
	# `enqueue_after_commit` defers the job until the transaction commits, so `enqueue` returns
	# None here — guard against dereferencing it (the id is not consumed by the caller anyway).
	return job.id if job else None


def _check_precondition(doc, step: str) -> None:
	# A previous step's job is still in flight (submitted, but the poller hasn't advanced it
	# yet). Block starting the next step with a message that points at the async wait, rather
	# than the misleading "generate <artifact> first" (the artifact only appears post-poll).
	if doc.filing_status in IN_PROGRESS_STATUSES:
		frappe.throw(
			_(
				"'{0}' is still in progress. It runs in the background and its status updates when "
				"the poller runs (every few minutes) — wait for it to finish before this step."
			).format(doc.filing_status)
		)

	if step == "validate":
		if not doc.deductees:
			frappe.throw(_("Add deductee rows (use 'Fetch from Payroll') before validating."))
		_check_deductee_pans(doc)
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


def _check_deductee_pans(doc) -> None:
	"""Reject malformed deductee PANs locally rather than as an opaque Sandbox report."""
	from india_payroll.india_payroll.tds.validators import is_valid_deductee_pan

	bad = [
		_("Row {0}: {1} ({2})").format(row.idx, row.employee_name or row.employee, row.pan or _("blank"))
		for row in doc.deductees
		if not is_valid_deductee_pan(row.pan)
	]
	if bad:
		frappe.throw(
			_(
				"These deductees have an invalid PAN. Fix them, or set PANNOTAVBL if it is genuinely unavailable:"
			)
			+ "<br>"
			+ "<br>".join(bad)
		)


def _check_reconciliation(doc) -> None:
	from india_payroll.india_payroll.tds.csi import total_deposited

	deposited = total_deposited(doc.company, doc.financial_year, doc.quarter)
	deducted = sum(flt(d.tax_deducted) for d in doc.deductees)
	if deducted - deposited > RECONCILIATION_TOLERANCE:
		frappe.throw(
			_(
				"TDS deducted ({0}) exceeds TDS deposited via challans ({1}). "
				"Record the missing challan(s) before filing."
			).format(deducted, deposited)
		)


def run_step(docname: str, step: str) -> None:
	doc = frappe.get_doc("TDS Return", docname)
	client = SandboxTDSClient()
	label = STEP_SPECS[step]["label"]
	try:
		_submit_step(doc, client, step)
	except Exception as e:
		doc.reload()
		doc.add_action(label, "Failed", message=str(e)[:500], save=False)
		doc.set_status("Failed", save=False)
		doc.save(ignore_permissions=True)
		frappe.db.commit()  # nosemgrep: persist the Failed status before the re-raise, which the background job runner would otherwise roll back
		frappe.log_error(
			title=f"TDS {label} failed for {docname}",
			message=frappe.get_traceback(with_context=False),
		)
		raise


def _identity(doc, step: str) -> dict:
	spec = STEP_SPECS[step]
	body = {
		"@entity": spec["entity"],
		"tan": doc.tan,
		"quarter": doc.quarter,
		"form": form_code(doc.form_type, doc.financial_year),
	}
	if spec["period"] == "tax_year":
		body["tax_year"] = normalize_tax_year(doc.financial_year)
	else:
		body["financial_year"] = normalize_financial_year(doc.financial_year)
		body["form"] = doc.form_type
	return body


def _request_body(doc, step: str) -> dict:
	body = _identity(doc, step)
	if step == "generate_txt" and doc.return_type == "Revised" and doc.previous_receipt_number:
		body["previous_receipt_number"] = doc.previous_receipt_number
	if step == "generate_fvu":
		body["filing_type"] = "correction" if doc.return_type == "Revised" else "regular"
	return body


def _upload_payloads(doc, client: SandboxTDSClient, step: str) -> dict:
	"""Return {response_field: (bytes, content_type)} to PUT to the job's presigned URLs.

	Built before the job is created so a payload error does not orphan a job.
	"""
	if step in ("validate", "generate_txt"):
		sheet = json.dumps(build_sheet_json(doc), default=str).encode()
		return {"json_url": (sheet, "application/json")}

	if step == "generate_fvu":
		if not doc.csi_file:
			_attach(
				doc, "csi_file", f"{doc.tan}.csi", fetch_csi(client, doc.tan, doc.financial_year, doc.quarter)
			)
			doc.reload()
		return {
			"txt_file_upload_url": (_read_attachment(doc.txt_file), "text/plain"),
			"csi_file_upload_url": (_read_attachment(doc.csi_file), "application/octet-stream"),
		}

	if step == "e_file":
		return {"fvu_upload_file_url": (_read_attachment(doc.fvu_file), "application/zip")}

	return {}


def _submit_step(doc, client: SandboxTDSClient, step: str) -> None:
	spec = STEP_SPECS[step]
	payloads = _upload_payloads(doc, client, step)

	if step in ("validate", "generate_txt"):
		_attach(doc, "sheet_json_file", "sheet.json", payloads["json_url"][0])

	resp = client.request(
		"POST",
		endpoint_for(step),
		json_body=_request_body(doc, step),
		reference_doctype="TDS Return",
		reference_name=doc.name,
	)
	data = _data(resp)

	job_id = data.get("job_id")
	if not job_id:
		frappe.throw(_("Sandbox did not return a job id for {0}.").format(spec["label"]))

	# The presigned PUT is what triggers processing — a job whose payload is never
	# uploaded stays in "created" forever, so a missing URL must fail loudly.
	for field, (content, content_type) in payloads.items():
		url = data.get(field)
		if not url:
			frappe.throw(
				_("Sandbox did not return the upload URL '{0}' for {1}. It returned: {2}").format(
					field, spec["label"], ", ".join(sorted(data.keys())) or _("nothing")
				)
			)
		client.upload_to_presigned_url(
			url,
			content,
			content_type,
			reference_doctype="TDS Return",
			reference_name=doc.name,
		)

	doc.reload()
	doc.add_action(spec["label"], "created", job_id=job_id, save=False)
	doc.set_status(spec["in_progress"], save=False)
	doc.save(ignore_permissions=True)


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
			frappe.db.commit()  # nosemgrep: commit each return so one item's failure and rollback doesn't discard earlier iterations' progress
		except Exception:
			frappe.db.rollback()
			frappe.log_error(title=f"TDS poll failed for {name}")


def poll_return(docname: str) -> None:
	doc = frappe.get_doc("TDS Return", docname)
	# Filing log rows are not allow-on-submit, so advancing a job on a submitted return
	# would fail the save outright. A submitted return is done; leave it alone.
	if doc.docstatus != 0:
		return

	job = doc.get_open_job()
	if not job:
		return

	step = LABEL_TO_STEP.get(job["request_type"])
	if not step:
		return

	spec = STEP_SPECS[step]
	client = SandboxTDSClient()
	resp = client.request(
		"GET",
		endpoint_for(step),
		params={"job_id": job["job_id"]},
		reference_doctype="TDS Return",
		reference_name=doc.name,
	)
	data = _data(resp)
	status = str(data.get("status") or "").lower()

	if status in FAILURE_STATUSES:
		_update_action(doc, job["row"], status)
		_fail(doc, client, step, data)
		return

	if status in SUCCESS_STATUSES:
		_update_action(doc, job["row"], "succeeded")
		_HANDLE_RESULT[step](doc, client, data)
		doc.set_status(spec["done"], save=False)
		doc.save(ignore_permissions=True)
		return

	# Still running, or a status this integration does not recognise. Either way the job is
	# not terminal, so keep polling — but give up once it is clearly never going to finish.
	age = _job_age_seconds(doc, job["row"])

	# "created" means Sandbox is still waiting for the payload. The upload happens
	# seconds after the job is created, so a job still sitting at "created" minutes
	# later means the PUT never registered. Sandbox has no re-trigger mechanism, so
	# the only remedy is a fresh job — fail early rather than burn the full timeout.
	if status == "created" and age and age > MAX_CREATED_MINUTES * 60:
		_update_action(doc, job["row"], "failed")
		doc.set_status("Failed", save=False)
		doc.add_action(
			spec["label"],
			"Failed",
			message=_(
				"Sandbox job {0} is still awaiting its payload after {1} minutes, so the upload did not "
				"register. Check the PUT row in Integration Request for the storage response, then run "
				"{2} again to create a fresh job (presigned URLs cannot be reused)."
			).format(job["job_id"], MAX_CREATED_MINUTES, spec["label"]),
			save=False,
		)
		doc.save(ignore_permissions=True)
		return

	if age and age > MAX_JOB_AGE_MINUTES * 60:
		_update_action(doc, job["row"], "timed_out")
		doc.set_status("Failed", save=False)
		doc.add_action(
			spec["label"],
			"Failed",
			message=_("Sandbox job {0} did not complete within {1} minutes (last status: {2}).").format(
				job["job_id"], MAX_JOB_AGE_MINUTES, status or _("unknown")
			),
			save=False,
		)
		doc.save(ignore_permissions=True)
		return

	_update_action(doc, job["row"], status or "polled")
	doc.save(ignore_permissions=True)


def _job_age_seconds(doc, row_name: str) -> float | None:
	for action in doc.filing_actions:
		if action.name == row_name and action.creation_time:
			return time_diff_in_seconds(now_datetime(), get_datetime(action.creation_time))
	return None


def _update_action(doc, row_name: str, status: str) -> None:
	for action in doc.filing_actions:
		if action.name == row_name:
			action.status = status
			break


def _fail(doc, client: SandboxTDSClient, step: str, data: dict) -> None:
	"""Record a failed job, attaching whatever report Sandbox produced."""
	spec = STEP_SPECS[step]
	message = _first_message(data)
	report_url = next((data[key] for key in spec["failure_urls"] if data.get(key)), None)

	if report_url:
		filename, content = _save_report(doc, client, report_url, f"{doc.name}-{step}-report")
		if filename:
			message = _("{0} See the attached validation report: {1}").format(message, filename)
			if step == "validate":
				doc.validation_issues = _issues_json(content, filename)

	doc.set_status("Failed", save=False)
	doc.add_action(spec["label"], "Failed", message=message[:500], save=False)
	doc.save(ignore_permissions=True)


# ------------------------------------------------------------------ results
def _result_validate(doc, client: SandboxTDSClient, data: dict) -> None:
	"""Store the potential-notice report. Sandbox returns a URL, not inline issues."""
	url = data.get("potential_notice_report_url") or data.get("potential_notice_report_file_url")
	if not url:
		doc.validation_issues = json.dumps(
			[{"message": _("Sandbox reported no potential notices.")}], indent=2
		)
		return

	filename, content = _save_report(doc, client, url, f"{doc.name}-potential-notices")
	doc.validation_issues = _issues_json(content, filename)


def _result_txt(doc, client: SandboxTDSClient, data: dict) -> None:
	_attach(doc, "txt_file", f"{doc.name}.txt", _download_result(client, data, "txt"))
	for stale in ("fvu_file", "csi_file", "form_27a"):
		if doc.get(stale):
			doc.db_set(stale, None)


def _result_fvu(doc, client: SandboxTDSClient, data: dict) -> None:
	"""Sandbox returns a zip of the FVU plus Form 27A.

	The zip is stored verbatim so e-filing uploads exactly what Sandbox produced;
	Form 27A is also extracted so it can be previewed and printed on its own.
	"""
	content = _download_result(client, data, "fvu")
	_attach(doc, "fvu_file", f"{doc.name}-fvu.zip", content)

	form_27a = _extract_from_zip(content, ".pdf")
	if form_27a:
		_attach(doc, "form_27a", f"{doc.name}-27A.pdf", form_27a)


def _result_efile(doc, client: SandboxTDSClient, data: dict) -> None:
	receipt = (
		data.get("receipt_number") or data.get("provisional_receipt_number") or data.get("acknowledgement")
	)
	doc.acknowledgement_number = str(receipt) if receipt is not None else None
	doc.token_number = data.get("token_number") or data.get("token")
	doc.filing_date = now_datetime()

	if data.get("receipt_file_url"):
		_save_report(doc, client, data["receipt_file_url"], f"{doc.name}-receipt")


_HANDLE_RESULT = {
	"validate": _result_validate,
	"generate_txt": _result_txt,
	"generate_fvu": _result_fvu,
	"e_file": _result_efile,
}


RESULT_URL_KEYS = {
	"txt": ("txt_url", "txt_file_url"),
	"fvu": ("fvu_zip_file_url", "fvu_file_url", "fvu_url"),
}


def _download_result(client: SandboxTDSClient, data: dict, key: str, required: bool = True) -> bytes | None:
	"""Return file bytes from a result payload via base64 or a download URL."""
	b64 = data.get(f"{key}_file_base64") or data.get(f"{key}_base64")
	if b64:
		return base64.b64decode(b64)

	for url_key in RESULT_URL_KEYS.get(key, (f"{key}_url", f"{key}_file_url")):
		if data.get(url_key):
			return client.fetch_file(data[url_key])

	if required:
		frappe.throw(_("Sandbox response did not contain the {0} file.").format(key.upper()))
	return None


def _save_report(doc, client: SandboxTDSClient, url: str, stem: str) -> tuple[str | None, bytes | None]:
	"""Download a Sandbox report and attach it to the return.

	Reports live on S3 for 30 days, so they are copied locally rather than linked.
	Never raises: a report is diagnostic, and losing it must not mask the result.
	"""
	try:
		content = client.fetch_file(url)
	except Exception:
		frappe.log_error(title=f"TDS report download failed for {doc.name}")
		return None, None

	filename = f"{stem}{_extension_for(url)}"
	try:
		save_file(filename, content, doc.doctype, doc.name, is_private=1)
	except Exception:
		frappe.log_error(title=f"TDS report attach failed for {doc.name}")
		return None, content
	return filename, content


def _extension_for(url: str) -> str:
	path = url.split("?")[0].rsplit("/", 1)[-1]
	if "." in path:
		return "." + path.rsplit(".", 1)[-1]
	return ".dat"


def _issues_json(content: bytes | None, filename: str | None) -> str:
	"""Normalise a validation/notice report into the list of {message} the form renders."""
	if content:
		try:
			parsed = json.loads(content.decode("utf-8", errors="replace"))
		except (ValueError, AttributeError):
			parsed = None

		if isinstance(parsed, dict):
			for key in ("issues", "potential_notices", "errors", "data"):
				if isinstance(parsed.get(key), list):
					parsed = parsed[key]
					break
		if isinstance(parsed, list) and parsed:
			return json.dumps(
				[item if isinstance(item, dict) else {"message": str(item)} for item in parsed],
				indent=2,
				default=str,
			)
		if isinstance(parsed, list):
			return json.dumps([{"message": _("Sandbox reported no potential notices.")}], indent=2)

	message = (
		_("Sandbox returned a report: {0}. Open it from the attachments.").format(filename)
		if filename
		else _("Sandbox returned a report that could not be downloaded.")
	)
	return json.dumps([{"message": message}], indent=2)


def _extract_from_zip(content: bytes, suffix: str) -> bytes | None:
	try:
		with zipfile.ZipFile(io.BytesIO(content)) as zf:
			name = next((n for n in zf.namelist() if n.lower().endswith(suffix)), None)
			return zf.read(name) if name else None
	except zipfile.BadZipFile:
		return None


def _attach(doc, fieldname: str, filename: str, content: bytes) -> None:
	saved = save_file(filename, content, doc.doctype, doc.name, is_private=1)
	doc.db_set(fieldname, saved.file_url)


def _read_attachment(file_url: str) -> bytes:
	file_doc = frappe.get_doc("File", {"file_url": file_url})
	content = file_doc.get_content()
	# get_content() returns str for text files (e.g. the pipe-delimited TDS .txt); callers
	# (the presigned upload, zip inspection) need bytes.
	return content.encode() if isinstance(content, str) else content


def _first_message(data: dict) -> str:
	for key in ("message", "error", "detail", "transaction_message"):
		if data.get(key):
			return str(data[key])[:500]
	return _("Filing step failed.")
