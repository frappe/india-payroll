"""Self-contained HTTP client for the Sandbox (sandbox.co.in) TDS APIs.

Auth flow (Sandbox):
    POST /authenticate  with headers x-api-key, x-api-secret, x-api-version
        -> { "access_token": "<JWT, valid 24h>" }
    Subsequent calls send:  authorization: <token> (no "Bearer" prefix),
        x-api-key, x-api-version, Content-Type: application/json
"""

import base64
import json

import frappe
import requests
from frappe import _
from frappe.utils import add_to_date, cint, get_datetime, now_datetime

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
SENSITIVE_KEYS = frozenset({"x-api-key", "x-api-secret", "authorization", "access_token", "api_secret"})


def _mock_response(method: str, endpoint: str, json_body: dict | None = None) -> dict:
	"""Canned Sandbox responses for offline mock mode (SandboxTDSClient.mock).

	Keyed by endpoint + method: a POST creates a job (no presigned URL, so the sheet upload is
	skipped); a GET polls a job and reports it completed with the payload each step's result
	handler expects. Lets the whole filing pipeline be exercised without the real API.
	"""
	ep = (endpoint or "").lower()
	body = json_body or {}

	def b64(text: str) -> str:
		return base64.b64encode(text.encode()).decode()

	# CSI file fetch (a POST, but returns a file rather than a job)
	if "csi" in ep:
		return {"data": {"csi_file_base64": b64("MOCK-CSI-FILE\n")}}

	# Job creation
	if method.upper() == "POST":
		tag = ep.rstrip("/").rsplit("/", 1)[-1]
		return {"data": {"job_id": f"MOCK-{tag}-{body.get('tan', 'TAN')}"}}

	# Job poll -> completed, with the result fields each step's handler reads
	data = {"status": "completed"}
	if "potential-notices" in ep:
		data["issues"] = []
	elif "txt" in ep:
		data["txt_file_base64"] = b64("MOCK-TXT-FILE\n")
	elif "fvu" in ep:
		data["fvu_file_base64"] = b64("MOCK-FVU-FILE\n")
		# Form 27A is optional; omit it so we don't attach an invalid PDF (Frappe parses PDF
		# attachments to build a preview, which would choke on placeholder bytes).
	elif "e-file" in ep:
		data["token_number"] = "MOCKTOKEN0001"
		data["provisional_receipt_number"] = "MOCKPRN0001"
	return {"data": data}


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

		self.api_key = self.settings.get_password("tds_api_key", raise_exception=False)
		self.api_secret = self.settings.get_password("tds_api_secret", raise_exception=False)
		self.api_version = self.settings.get("tds_api_version") or DEFAULT_API_VERSION
		self.sandbox_mode = cint(self.settings.get("tds_sandbox_mode"))

		# Offline mock mode: when site_config `sandbox_tds_mock` is set, every call returns a
		# canned success response instead of hitting Sandbox. Lets the full validate -> TXT ->
		# FVU -> e-file pipeline be exercised end to end (status transitions, attachments, filing
		# log) without depending on Sandbox's account-specific test-environment examples.
		self.mock = cint(frappe.conf.get("sandbox_tds_mock"))

		if not self.mock and not (self.api_key and self.api_secret):
			frappe.throw(_("Sandbox API Key and Secret are required in Payroll Settings."))

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
		frappe.db.set_single_value(
			"Payroll Settings",
			{
				"tds_access_token": token,
				"tds_token_expiry": add_to_date(now_datetime(), hours=TOKEN_TTL_HOURS),
			},
			update_modified=False,
		)
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
			return _mock_response(method, endpoint, json_body)

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

			if resp.status_code >= 400 or (isinstance(output, dict) and output.get("error")):
				error = output
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
	def _extract_error_message(output: object, status_code: int | None) -> str:
		if isinstance(output, dict):
			for key in ("message", "error", "detail", "transaction_message"):
				if output.get(key):
					return f"Sandbox API error ({status_code}): {output[key]}"
		return _("Sandbox API returned an error (HTTP {0}).").format(status_code)

	# ------------------------------------------------------ presigned upload
	def upload_to_presigned_url(self, presigned_url: str, content: bytes, content_type: str) -> None:
		"""PUT a file to an S3 presigned URL returned by Sandbox (no auth headers)."""
		try:
			resp = self._session.put(
				presigned_url,
				data=content,
				headers={"Content-Type": content_type},
				timeout=REQUEST_TIMEOUT,
			)
			resp.raise_for_status()
		except requests.RequestException as e:
			frappe.throw(_("Failed to upload file to Sandbox: {0}").format(str(e)))
		finally:
			self._log_request(
				url=presigned_url.split("?")[0],
				method="PUT",
				request_headers={"Content-Type": content_type},
				request_body={"bytes": len(content)},
				output={"status": "uploaded"},
				error=None,
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
					"request_description": f"{method} {url}",
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
