"""Self-contained HTTP client for the Sandbox (sandbox.co.in) TDS APIs.

Auth flow (Sandbox):
    POST /authenticate  with headers x-api-key, x-api-secret, x-api-version
        -> { "access_token": "<JWT, valid 24h>" }
    Subsequent calls send:  authorization: <token> (no "Bearer" prefix),
        x-api-key, x-api-version, Content-Type: application/json
"""

import base64
import hashlib
import io
import json
import zipfile
from datetime import datetime

import frappe
import requests
from frappe import _
from frappe.utils import add_to_date, cint, flt, get_datetime, now_datetime

from india_payroll.india_payroll.tds.settings import get_sandbox_credentials

# Sandbox selects the environment by host: live keys (key_live_…) work only against the
# production host, test keys (key_test_…) only against the test host. There is no mode header.
DEFAULT_BASE_URL = "https://api.sandbox.co.in"
TEST_BASE_URL = "https://test-api.sandbox.co.in"
DEFAULT_API_VERSION = "1.0.0"
# Sandbox tokens are valid 24h; refresh a little early to avoid edge expiry.
TOKEN_TTL_HOURS = 23
REQUEST_TIMEOUT = 60

# Header/body keys whose values must never be written to logs. A frozenset so it
# cannot be mutated in place at runtime (defense-in-depth against accidental edits).
SENSITIVE_KEYS = frozenset(
	{"x-api-key", "x-api-secret", "authorization", "access_token", "api_secret", "otp", "password"}
)


MOCK_SCHEME = "mock://"

# Presigned upload URLs a mocked job-creation response hands back, per endpoint.
MOCK_UPLOAD_FIELDS = {
	"potential-notices": ("json_url",),
	"txt": ("json_url",),
	"fvu": ("txt_file_upload_url", "csi_file_upload_url"),
	"e-file": ("fvu_upload_file_url",),
}


def _mock_endpoint_kind(endpoint: str) -> str:
	ep = (endpoint or "").lower()
	if "potential-notices" in ep:
		return "potential-notices"
	if "fvu" in ep:
		return "fvu"
	if "e-file" in ep:
		return "e-file"
	if "txt" in ep:
		return "txt"
	return "unknown"


def _mock_response(method: str, endpoint: str, json_body: dict | None = None) -> dict:
	"""Canned Sandbox responses for offline mock mode (SandboxTDSClient.mock).

	Mirrors the documented job-based contract: a POST creates a job and returns
	presigned upload URLs, a GET reports the job succeeded with the result URLs
	each step's handler reads. Result URLs use the `mock://` scheme and are served
	by `_mock_file_bytes`, so the full validate -> TXT -> FVU -> e-file pipeline
	(uploads, downloads, attachments, status transitions) runs without the real API.
	"""
	ep = (endpoint or "").lower()
	body = json_body or {}
	kind = _mock_endpoint_kind(endpoint)

	if "traces/deductors" in ep:
		return _mock_traces_response(ep, body)

	if "csi" in ep and kind == "unknown":
		return {
			"code": 200,
			"data": {"csi_file_base64": base64.b64encode(b"MOCK-CSI-FILE\n").decode()},
		}

	if method.upper() == "POST":
		data = {
			"job_id": f"MOCK-{kind}-{body.get('tan', 'TAN')}",
			"status": "created",
			"tan": body.get("tan"),
			"quarter": body.get("quarter"),
			"form": body.get("form"),
		}
		fields = MOCK_UPLOAD_FIELDS.get(kind, ())
		if kind == "fvu" and body.get("filing_type") == "correction":
			fields = (*fields, "conso_file_upload_url")
		for field in fields:
			data[field] = f"{MOCK_SCHEME}upload/{kind}/{field}"
		return {"code": 200, "data": data}

	data = {"status": "succeeded", "job_id": f"MOCK-{kind}"}
	if kind == "potential-notices":
		data["potential_notice_report_url"] = f"{MOCK_SCHEME}result/potential-notices.json"
	elif kind == "txt":
		data["txt_url"] = f"{MOCK_SCHEME}result/return.txt"
	elif kind == "fvu":
		data["fvu_zip_file_url"] = f"{MOCK_SCHEME}result/fvu.zip"
	elif kind == "e-file":
		data["receipt_number"] = MOCK_TOKEN_NUMBER
		data["receipt_file_url"] = f"{MOCK_SCHEME}result/receipt.pdf"
	return {"code": 200, "data": data}


def _mock_traces_response(endpoint: str, body: dict) -> dict:
	"""TRACES has every challan recorded for the TAN, plus one deposited on the last day searched."""
	if endpoint.endswith("challans/search"):
		challans = [
			_mock_traces_challan(
				body.get("tan"), c.bsr_code, c.challan_serial_no, c.challan_date, c.deposit_amount
			)
			for c in frappe.get_all(
				"TDS Challan",
				filters={
					"tan": body.get("tan"),
					"docstatus": ("<", 2),
					"challan_date": (
						"between",
						[
							_mock_epoch_date(body.get("from_date_epoch")),
							_mock_epoch_date(body.get("to_date_epoch")),
						],
					),
				},
				fields=["bsr_code", "challan_serial_no", "challan_date", "deposit_amount"],
			)
		]
		challans.append(
			_mock_traces_challan(
				body.get("tan"), "0000000", "99999", _mock_epoch_date(body.get("to_date_epoch")), 1000
			)
		)
		return {"code": 200, "data": {"data": challans, "totalPages": 1, "totalElements": len(challans)}}
	if endpoint.endswith("challans"):
		paid_date = _mock_epoch_date(body.get("paid_date_epoch"))
		amount = frappe.db.get_value(
			"TDS Challan",
			{
				"tan": body.get("tan"),
				"bsr_code": body.get("bsr_code"),
				"challan_serial_no": body.get("challan_serial"),
				"challan_date": paid_date,
				"docstatus": ("<", 2),
			},
			"deposit_amount",
		)
		return {
			"code": 200,
			"data": _mock_traces_challan(
				body.get("tan"), body.get("bsr_code"), body.get("challan_serial"), paid_date, amount or 0
			),
		}
	return {"code": 200}


def _mock_epoch_date(epoch_ms):
	from india_payroll.india_payroll.tds.csi import IST

	return datetime.fromtimestamp((epoch_ms or 0) / 1000, tz=IST).date()


def _mock_traces_challan(tan, bsr_code, challan_serial, paid_date, amount) -> dict:
	from india_payroll.india_payroll.tds.csi import _epoch_ms

	return {
		"tan": tan,
		"bsr_code": bsr_code,
		"challan_serial": challan_serial,
		"paid_date_epoch": _epoch_ms(paid_date),
		"challan_identification_number": f"MOCK{bsr_code}{challan_serial}",
		"status": "MOCK",
		"total_amount": flt(amount),
		"minor_head": "200",
		"section_code": "92B",
		"tax_breakdown": [{"section": "92B", "tax_amount": flt(amount), "surcharge": 0, "cess": 0}],
		"consumption_details": {"claimed_amount": 0, "available_amount": flt(amount)},
	}


def _mock_file_bytes(url: str) -> bytes:
	"""Serve the files a mocked job's result URLs point at."""
	name = url.split("?")[0].rsplit("/", 1)[-1]

	if name == "potential-notices.json":
		return json.dumps([{"message": "MOCK: no potential notices found for this return."}]).encode()
	if name == "fvu.zip":
		buffer = io.BytesIO()
		# Only the .fvu member: a placeholder Form 27A would be an invalid PDF, and
		# Frappe parses PDF attachments to build a preview.
		with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as zf:
			zf.writestr("MOCK.fvu", "MOCK-FVU-FILE\n")
		return buffer.getvalue()
	if name == "receipt.pdf":
		return _mock_receipt_pdf()
	return b"MOCK-TXT-FILE\n"


MOCK_TOKEN_NUMBER = "000000000000001"
MOCK_RECEIPT_NUMBER = "MOCKRCPT"


def _mock_receipt_pdf() -> bytes:
	"""A one-page receipt with the token and receipt numbers on one row, as Protean prints them."""
	text = f"{MOCK_TOKEN_NUMBER} MOCK DEDUCTOR {MOCK_RECEIPT_NUMBER}"
	stream = f"BT /F1 10 Tf 40 800 Td ({text}) Tj ET".encode()
	objects = [
		b"<< /Type /Catalog /Pages 2 0 R >>",
		b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
		b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 595 842] /Contents 4 0 R "
		+ b"/Resources << /Font << /F1 5 0 R >> >> >>",
		b"<< /Length %d >>\nstream\n%s\nendstream" % (len(stream), stream),
		b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
	]
	pdf = b"%PDF-1.4\n"
	offsets = []
	for number, body in enumerate(objects, 1):
		offsets.append(len(pdf))
		pdf += b"%d 0 obj\n%s\nendobj\n" % (number, body)
	xref = len(pdf)
	pdf += b"xref\n0 %d\n0000000000 65535 f \n" % (len(objects) + 1)
	pdf += b"".join(b"%010d 00000 n \n" % offset for offset in offsets)
	pdf += b"trailer\n<< /Size %d /Root 1 0 R >>\nstartxref\n%d\n%%%%EOF\n" % (len(objects) + 1, xref)
	return pdf


MASK = "***masked***"

INTEGRATION_SERVICE = "Sandbox TDS API"


class SandboxAPIError(frappe.ValidationError):
	"""Raised when the Sandbox API returns an error response."""


def mask_sensitive(data: object) -> object:
	"""Recursively mask values under sensitive keys in dicts/lists.

	Walks nested structures so a secret buried in a sub-object is masked too,
	not just top-level keys.
	"""
	if isinstance(data, dict):
		return {
			key: MASK
			if (isinstance(key, str) and key.lower() in SENSITIVE_KEYS and value)
			else mask_sensitive(value)
			for key, value in data.items()
		}
	if isinstance(data, (list, tuple)):
		return [mask_sensitive(item) for item in data]
	return data


class SandboxTDSClient:
	def __init__(self) -> None:
		self.settings = frappe.get_cached_doc("Payroll Settings")
		if not self.settings.get("enable_tds_filing"):
			frappe.throw(_("TDS Return Filing is not enabled in Payroll Settings."))

		credentials = get_sandbox_credentials(self.settings)
		self.api_key = credentials["api_key"]
		self.api_secret = credentials["api_secret"]
		self.api_version = credentials["api_version"] or DEFAULT_API_VERSION
		self.sandbox_mode = credentials["sandbox_mode"]
		self.credentials_from_conf = credentials["from_conf"]

		# Offline mock mode: when site_config `sandbox_tds_mock` is set, every call returns a
		# canned success response instead of hitting Sandbox. Lets the full validate -> TXT ->
		# FVU -> e-file pipeline be exercised end to end (status transitions, attachments, filing
		# log) without depending on Sandbox's account-specific test-environment examples.
		self.mock = cint(frappe.conf.get("sandbox_tds_mock"))

		if not self.mock and not (self.api_key and self.api_secret):
			frappe.throw(
				_(
					"Sandbox API Key and Secret are required. Set them in Payroll Settings, or "
					"contact support if this site's credentials are managed by Frappe Cloud."
				)
			)

		# Route to the environment that matches the configured key: test host in sandbox mode,
		# production host otherwise. A site-level override still wins (e.g. for a staging host).
		default_base_url = TEST_BASE_URL if self.sandbox_mode else DEFAULT_BASE_URL
		self.base_url = (frappe.conf.get("sandbox_tds_base_url") or default_base_url).rstrip("/")
		self._session = requests.Session()

	# ---------------------------------------------------------------- auth
	def _is_token_valid(self) -> bool:
		token = self.settings.get("tds_access_token")
		expiry = self.settings.get("tds_token_expiry")
		return bool(token and expiry and get_datetime(expiry) > now_datetime())

	def get_access_token(self, force_refresh: bool = False) -> str:
		if not force_refresh and self._is_token_valid():
			return self.settings.get_password("tds_access_token")
		return self.authenticate()

	def authenticate(self) -> str:
		headers = {
			"x-api-key": self.api_key,
			"x-api-secret": self.api_secret,
			"x-api-version": self.api_version,
			"Content-Type": "application/json",
		}
		url = f"{self.base_url}/authenticate"
		response = self._raw_request("POST", url, headers=headers)
		token = response.get("access_token")
		if not token:
			frappe.throw(_("Sandbox authentication failed: no access token returned."))

		# Persist the token on the (Single) Payroll Settings for reuse.
		settings = frappe.get_doc("Payroll Settings")
		settings.tds_access_token = token
		settings.tds_token_expiry = add_to_date(now_datetime(), hours=TOKEN_TTL_HOURS)
		settings.save(ignore_permissions=True)
		# Keep the in-memory copy fresh too.
		self.settings = frappe.get_cached_doc("Payroll Settings")
		return token

	# ------------------------------------------------------------- requests
	def request(
		self,
		method: str,
		endpoint: str,
		json_body: dict | None = None,
		params: dict | None = None,
		reference_doctype: str | None = None,
		reference_name: str | None = None,
		_retry_on_auth: bool = True,
	) -> dict:
		"""Make an authenticated call to a Sandbox endpoint and return JSON.

		Re-authenticates once on a 401 before giving up.
		"""
		if self.mock:
			resp = _mock_response(method, endpoint, json_body)
			self._log_request(
				url=f"{MOCK_SCHEME}{endpoint.lstrip('/')}",
				method=method,
				request_headers=None,
				request_body=json_body or params,
				output=resp,
				error=None,
				reference_doctype=reference_doctype,
				reference_name=reference_name,
			)
			return resp

		url = f"{self.base_url}/{endpoint.lstrip('/')}"
		headers = {
			"authorization": self.get_access_token(),
			"x-api-key": self.api_key,
			"x-api-version": self.api_version,
			"Content-Type": "application/json",
		}
		try:
			return self._raw_request(
				method,
				url,
				headers=headers,
				json_body=json_body,
				params=params,
				reference_doctype=reference_doctype,
				reference_name=reference_name,
			)
		except SandboxAPIError:
			if _retry_on_auth and self._last_status == 401:
				self.get_access_token(force_refresh=True)
				return self.request(
					method,
					endpoint,
					json_body=json_body,
					params=params,
					reference_doctype=reference_doctype,
					reference_name=reference_name,
					_retry_on_auth=False,
				)
			raise

	def _raw_request(
		self,
		method: str,
		url: str,
		headers: dict | None = None,
		json_body: dict | None = None,
		params: dict | None = None,
		reference_doctype: str | None = None,
		reference_name: str | None = None,
	) -> dict:
		self._last_status = None
		output = None
		error = None
		try:
			resp = self._session.request(
				method,
				url,
				headers=headers,
				json=json_body,
				params=params,
				timeout=REQUEST_TIMEOUT,
			)
			self._last_status = resp.status_code
			try:
				output = resp.json()
			except ValueError:
				output = {"raw": resp.text}

			if resp.status_code >= 400 or self._is_error_body(output):
				error = output
				if self._looks_like_aws_rejection(output):
					raise SandboxAPIError(
						_(
							"Sandbox has no endpoint at '{0}' — the request was rejected by AWS before "
							"reaching the API. Check the path against the API reference."
						).format(url)
					)
				message = self._extract_error_message(output, resp.status_code)
				raise SandboxAPIError(message)
			return output
		except requests.RequestException as e:
			error = {"exception": str(e)}
			raise SandboxAPIError(_("Could not reach the Sandbox API: {0}").format(str(e)))
		finally:
			self._log_request(
				url=url,
				method=method,
				request_headers=headers,
				request_body=json_body or params,
				output=output,
				error=error,
				reference_doctype=reference_doctype,
				reference_name=reference_name,
			)

	@staticmethod
	def _is_error_body(output: object) -> bool:
		"""Sandbox echoes its own status in the body; a 2xx envelope can still carry a 4xx code."""
		if not isinstance(output, dict):
			return False
		if output.get("error"):
			return True
		code = output.get("code")
		return isinstance(code, int) and not isinstance(code, bool) and code >= 400

	@staticmethod
	def _looks_like_aws_rejection(output: object) -> bool:
		"""True when AWS, not Sandbox, rejected the call.

		Sandbox sits behind AWS API Gateway. A path that matches no route falls
		through to an IAM-authorized default, which tries to read our JWT as a SigV4
		signature and complains about missing Credential/Signature/X-Amz-Date. It
		reads like an auth problem but always means the endpoint is wrong.
		"""
		if not isinstance(output, dict):
			return False
		message = str(output.get("message") or "")
		return "Authorization header requires" in message or "X-Amz-Date" in message

	@staticmethod
	def _extract_error_message(output: object, status_code: int | None) -> str:
		if isinstance(output, dict):
			code = output.get("code") if isinstance(output.get("code"), int) else status_code
			for key in ("message", "error", "detail", "transaction_message"):
				if output.get(key):
					return f"Sandbox API error ({code}): {output[key]}"
		return _("Sandbox API returned an error (HTTP {0}).").format(status_code)

	def fetch_file(self, url: str) -> bytes:
		"""Download a result/report file from the (presigned) URL Sandbox returned."""
		if url.startswith(MOCK_SCHEME):
			return _mock_file_bytes(url)

		error = None
		try:
			resp = self._session.get(url, timeout=REQUEST_TIMEOUT)
			resp.raise_for_status()
			return resp.content
		except requests.RequestException as e:
			error = {"exception": str(e)}
			raise SandboxAPIError(_("Could not download the file from Sandbox: {0}").format(str(e)))
		finally:
			self._log_request(
				url=url.split("?")[0],
				method="GET",
				request_headers=None,
				request_body=None,
				output=None if error else {"status": "downloaded"},
				error=error,
			)

	# ------------------------------------------------------ presigned upload
	def upload_to_presigned_url(
		self,
		presigned_url: str,
		content: bytes,
		content_type: str,
		reference_doctype: str | None = None,
		reference_name: str | None = None,
	) -> int:
		"""PUT a payload to an S3 presigned URL returned by Sandbox.

		Per Sandbox's job-based contract the URL is used exactly as returned, carries
		its own signature (so no Sandbox auth headers are sent), and needs a
		Content-Type matching the payload. A 200 is what starts job processing, so the
		real status code, ETag and any S3 error body are logged — without them a job
		stuck at "created" is indistinguishable from a successful upload.
		"""
		if self.mock or presigned_url.startswith(MOCK_SCHEME):
			self._log_request(
				url=presigned_url,
				method="PUT",
				request_headers={"Content-Type": content_type},
				# S3 returns the body's MD5 as the ETag for a single-part PUT, so logging it
				# here lets you confirm the stored object is byte-identical to what was sent.
				request_body={
					"bytes": len(content),
					"content_type": content_type,
					"md5": hashlib.md5(content).hexdigest(),
				},
				output={"status_code": 200, "status": "uploaded (mock)"},
				error=None,
				reference_doctype=reference_doctype,
				reference_name=reference_name,
			)
			return 200

		output = None
		error = None
		try:
			resp = self._session.put(
				presigned_url,
				data=content,
				headers={"Content-Type": content_type},
				timeout=REQUEST_TIMEOUT,
			)
			output = {
				"status_code": resp.status_code,
				"etag": resp.headers.get("ETag"),
				"x-amz-request-id": resp.headers.get("x-amz-request-id"),
				"x-amz-id-2": resp.headers.get("x-amz-id-2"),
				# S3 reports failures as an XML body (e.g. SignatureDoesNotMatch).
				"body": (resp.text or "")[:2000],
			}
			if resp.status_code >= 400:
				error = output
				raise SandboxAPIError(
					_("Upload to Sandbox storage failed (HTTP {0}): {1}").format(
						resp.status_code, (resp.text or "").strip()[:300] or _("no response body")
					)
				)
			return resp.status_code
		except requests.RequestException as e:
			error = {"exception": str(e)}
			raise SandboxAPIError(_("Could not upload the payload to Sandbox storage: {0}").format(str(e)))
		finally:
			self._log_request(
				url=presigned_url.split("?")[0],
				method="PUT",
				request_headers={"Content-Type": content_type},
				# S3 returns the body's MD5 as the ETag for a single-part PUT, so logging it
				# here lets you confirm the stored object is byte-identical to what was sent.
				request_body={
					"bytes": len(content),
					"content_type": content_type,
					"md5": hashlib.md5(content).hexdigest(),
				},
				output=output,
				error=error,
				reference_doctype=reference_doctype,
				reference_name=reference_name,
			)

	# --------------------------------------------------------------- logging
	def _log_request(
		self,
		url: str,
		method: str,
		request_headers: dict | None,
		request_body: object,
		output: object,
		error: object,
		reference_doctype: str | None = None,
		reference_name: str | None = None,
	) -> None:
		# Collect the live secret strings so they can be scrubbed from serialized
		# payloads even if they surface under an unexpected key (e.g. echoed in an
		# error message, or the access_token in the auth response).
		secrets = self._live_secrets(request_headers)
		try:
			frappe.get_doc(
				{
					"doctype": "Integration Request",
					"integration_request_service": INTEGRATION_SERVICE,
					# Data field, varchar(140): a presigned S3 URL overflows it and would
					# make the whole row fail to insert, losing the log entry entirely.
					"request_description": f"{method} {url}"[:140],
					"status": "Failed" if error else "Completed",
					"url": url,
					"request_headers": _pretty(mask_sensitive(request_headers), secrets),
					"data": _pretty(mask_sensitive(request_body), secrets),
					"output": _pretty(mask_sensitive(output), secrets),
					"error": _pretty(mask_sensitive(error), secrets),
					"reference_doctype": reference_doctype,
					"reference_docname": reference_name,
				}
			).insert(ignore_permissions=True)
		except Exception:
			# Logging must never break the actual API workflow.
			frappe.log_error(title="Sandbox TDS request log failed")

	def _live_secrets(self, request_headers: dict | None) -> set:
		"""The concrete secret values in play for this call (api secret + any
		sensitive header values), used as a last-resort value scrub."""
		secrets = {self.api_secret}
		for key, value in (request_headers or {}).items():
			if isinstance(key, str) and key.lower() in SENSITIVE_KEYS and value:
				secrets.add(value)
		return {s for s in secrets if s}


def _pretty(value: object, secrets: set | None = None) -> str | None:
	if value is None:
		return None
	if isinstance(value, str):
		text = value
	else:
		try:
			text = json.dumps(value, indent=2, default=str)
		except (TypeError, ValueError):
			text = str(value)
	# Last-resort: scrub any concrete secret string that slipped through key masking.
	for secret in secrets or ():
		if secret:
			text = text.replace(secret, MASK)
	return text
