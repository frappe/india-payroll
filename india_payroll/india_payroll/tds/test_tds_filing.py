# Copyright (c) 2026, Frappe Technologies Pvt. Ltd. and contributors
# License: GNU General Public License v3. See license.txt

import json
import os
import re
from unittest.mock import patch

import frappe
from frappe.tests.utils import FrappeTestCase
from frappe.utils import getdate

from india_payroll.india_payroll.tds import filing, sheet_json
from india_payroll.india_payroll.tds.data_assembly import quarter_range_from_start
from india_payroll.india_payroll.tds.sandbox_client import (
	SandboxTDSClient,
	_mock_file_bytes,
	_mock_response,
	mask_sensitive,
)
from india_payroll.india_payroll.tds.validators import (
	form_code,
	is_valid_deductee_pan,
	is_valid_pan,
	is_valid_tan,
	normalize_financial_year,
	normalize_tax_year,
	validate_pan,
	validate_tan,
)


class TestTDSValidators(FrappeTestCase):
	def test_valid_pan(self):
		self.assertTrue(is_valid_pan("ABCPK1234E"))
		self.assertTrue(is_valid_pan("abcpk1234e"))  # case-insensitive

	def test_invalid_pan(self):
		for bad in ("", None, "ABCPK1234", "1234567890", "ABCP1234EE"):
			self.assertFalse(is_valid_pan(bad))
		self.assertRaises(frappe.ValidationError, validate_pan, "BAD")

	def test_valid_tan(self):
		self.assertTrue(is_valid_tan("ABCD12345E"))

	def test_invalid_tan(self):
		for bad in ("", None, "ABC12345E", "ABCD1234EE"):
			self.assertFalse(is_valid_tan(bad))
		self.assertRaises(frappe.ValidationError, validate_tan, "NOPE")

	def test_normalize_financial_year(self):
		self.assertEqual(normalize_financial_year("2024-25"), "FY 2024-25")
		self.assertEqual(normalize_financial_year("2024-2025"), "FY 2024-25")
		self.assertEqual(normalize_financial_year("FY 2024-25"), "FY 2024-25")
		self.assertEqual(normalize_financial_year("2099-2100"), "FY 2099-00")

	def test_normalize_tax_year(self):
		self.assertEqual(normalize_tax_year("2026-27"), "TY 2026-27")
		self.assertEqual(normalize_tax_year("FY 2026-27"), "TY 2026-27")

	def test_deductee_pan_placeholders(self):
		self.assertTrue(is_valid_deductee_pan("ABCPK1234E"))
		self.assertTrue(is_valid_deductee_pan("pannotavbl"))
		self.assertFalse(is_valid_deductee_pan("NOTAPAN"))
		self.assertFalse(is_valid_deductee_pan(""))


class TestFormCodes(FrappeTestCase):
	def test_old_act_keeps_legacy_codes(self):
		# Periods before 1 April 2026 stay on the Income-tax Act 1961 form names.
		self.assertEqual(form_code("24Q", "2025-26"), "24Q")
		self.assertEqual(form_code("26Q", "FY 2024-25"), "26Q")

	def test_new_act_renumbers_forms(self):
		# Income-tax Act 2025: 24Q -> 138, 26Q -> 140, 27Q -> 144, 27EQ -> 143.
		self.assertEqual(form_code("24Q", "2026-27"), "138")
		self.assertEqual(form_code("26Q", "TY 2026-27"), "140")
		self.assertEqual(form_code("27Q", "2027-28"), "144")
		self.assertEqual(form_code("27EQ", "2026-27"), "143")


class TestJobStatusBuckets(FrappeTestCase):
	def test_documented_statuses_are_classified(self):
		# Sandbox documents exactly these five job states.
		for status in ("created", "queued", "in_progress"):
			self.assertIn(status, filing.PENDING_STATUSES)
		self.assertIn("succeeded", filing.SUCCESS_STATUSES)
		self.assertIn("failed", filing.FAILURE_STATUSES)

	def test_buckets_are_disjoint(self):
		self.assertFalse(filing.PENDING_STATUSES & filing.SUCCESS_STATUSES)
		self.assertFalse(filing.PENDING_STATUSES & filing.FAILURE_STATUSES)
		self.assertFalse(filing.SUCCESS_STATUSES & filing.FAILURE_STATUSES)

	def test_unknown_status_is_not_treated_as_success(self):
		self.assertNotIn("something_new", filing.SUCCESS_STATUSES)


class TestErrorBodyDetection(FrappeTestCase):
	def test_envelope_code_over_200_is_an_error(self):
		# Sandbox can return HTTP 200 with a 4xx code in the body.
		self.assertTrue(SandboxTDSClient._is_error_body({"code": 422, "message": "bad"}))
		self.assertTrue(SandboxTDSClient._is_error_body({"error": "nope"}))

	def test_success_envelope_is_not_an_error(self):
		self.assertFalse(SandboxTDSClient._is_error_body({"code": 200, "data": {}}))
		self.assertFalse(SandboxTDSClient._is_error_body({"data": {}}))


class TestSecretMasking(FrappeTestCase):
	def test_sensitive_values_masked(self):
		masked = mask_sensitive(
			{"x-api-key": "k", "x-api-secret": "s", "authorization": "tok", "x-api-version": "1.0.0"}
		)
		self.assertEqual(masked["x-api-key"], "***masked***")
		self.assertEqual(masked["x-api-secret"], "***masked***")
		self.assertEqual(masked["authorization"], "***masked***")
		self.assertEqual(masked["x-api-version"], "1.0.0")  # not sensitive

	def test_nested_masking(self):
		# Secrets nested in sub-dicts and lists must also be masked.
		masked = mask_sensitive({"data": {"access_token": "JWT", "items": [{"api_secret": "s"}]}, "ok": True})
		self.assertEqual(masked["data"]["access_token"], "***masked***")
		self.assertEqual(masked["data"]["items"][0]["api_secret"], "***masked***")
		self.assertEqual(masked["ok"], True)

	def test_auth_response_token_masked(self):
		# The authenticate() response carries access_token; it must not be logged raw.
		masked = mask_sensitive({"access_token": "supersecretjwt"})
		self.assertEqual(masked["access_token"], "***masked***")

	def test_set_is_immutable(self):
		from india_payroll.india_payroll.tds.sandbox_client import SENSITIVE_KEYS

		self.assertIsInstance(SENSITIVE_KEYS, frozenset)
		with self.assertRaises(AttributeError):
			SENSITIVE_KEYS.add("foo")


class TestTDSChallanValidation(FrappeTestCase):
	def _challan(self, **kw):
		doc = frappe.new_doc("TDS Challan")
		doc.update(
			{
				"tan": "ABCD12345E",
				"bsr_code": "1234567",
				"challan_serial_no": "00001",
				"deposit_amount": 1000,
				"tds_amount": 1000,
			}
		)
		doc.update(kw)
		return doc

	def test_bad_bsr_code(self):
		doc = self._challan(bsr_code="123")
		self.assertRaises(frappe.ValidationError, doc.validate_bsr_code)

	def test_good_bsr_code(self):
		doc = self._challan()
		doc.validate_bsr_code()  # should not raise

	def test_breakup_mismatch(self):
		doc = self._challan(tds_amount=500, deposit_amount=1000)
		self.assertRaises(frappe.ValidationError, doc.validate_breakup)

	def test_breakup_matches(self):
		doc = self._challan(tds_amount=900, education_cess=100, deposit_amount=1000)
		doc.validate_breakup()  # should not raise


class TestQuarterDateRange(FrappeTestCase):
	def test_quarter_ranges(self):
		# Indian financial year starting 1 April 2024.
		cases = {
			"Q1": ("2024-04-01", "2024-06-30"),
			"Q2": ("2024-07-01", "2024-09-30"),
			"Q3": ("2024-10-01", "2024-12-31"),
			"Q4": ("2025-01-01", "2025-03-31"),
		}
		for quarter, (start, end) in cases.items():
			s, e = quarter_range_from_start("2024-04-01", quarter)
			self.assertEqual(s, getdate(start), f"{quarter} start")
			self.assertEqual(e, getdate(end), f"{quarter} end")


class _FakeDoc:
	def __init__(self, **fields):
		self.name = "TDS-RET-TEST"
		self._fields = fields

	def get(self, key):
		return self._fields.get(key)

	def db_set(self, key, value):
		self._fields[key] = value


class TestTxtRegenerationClears(FrappeTestCase):
	def test_result_txt_clears_stale_downstream_artifacts(self):
		doc = _FakeDoc(
			txt_file="/old.txt",
			csi_file="/x.csi",
			fvu_file="/x.fvu",
			form_27a="/x-27A.pdf",
		)
		with (
			patch.object(filing, "_attach") as attach,
			patch.object(filing, "_download_result", return_value=b"NEW-TXT"),
		):
			filing._result_txt(doc, None, {"txt_base64": ""})

		attach.assert_called_once()
		self.assertEqual(attach.call_args[0][1], "txt_file")
		self.assertIsNone(doc.get("fvu_file"))
		self.assertIsNone(doc.get("csi_file"))
		self.assertIsNone(doc.get("form_27a"))


class _FakeClient:
	"""Serves the mock:// URLs the mocked job responses hand out."""

	def fetch_file(self, url):
		return _mock_file_bytes(url)


class TestJobResponseParsing(FrappeTestCase):
	def test_create_job_returns_presigned_upload_urls(self):
		# Regression: the upload URL is `json_url`; missing it means the payload is
		# never uploaded, so the job never leaves "created".
		for endpoint, field in (
			("tds/analytics/potential-notices", "json_url"),
			("tds/reports/txt", "json_url"),
			("tds/compliance/e-file", "fvu_upload_file_url"),
		):
			data = _mock_response("POST", endpoint, {"tan": "ABCD12345E"})["data"]
			self.assertTrue(data.get("job_id"), endpoint)
			self.assertEqual(data.get("status"), "created", endpoint)
			self.assertTrue(data.get(field), f"{endpoint} -> {field}")

	def test_fvu_job_returns_both_upload_urls(self):
		data = _mock_response("POST", "tds/compliance/fvu/generate", {"tan": "ABCD12345E"})["data"]
		self.assertTrue(data.get("txt_file_upload_url"))
		self.assertTrue(data.get("csi_file_upload_url"))

	def test_poll_returns_documented_result_urls(self):
		cases = {
			"tds/analytics/potential-notices": "potential_notice_report_url",
			"tds/reports/txt": "txt_url",
			"tds/compliance/fvu/generate": "fvu_zip_file_url",
		}
		for endpoint, field in cases.items():
			data = _mock_response("GET", endpoint)["data"]
			self.assertEqual(data.get("status"), "succeeded", endpoint)
			self.assertTrue(data.get(field), f"{endpoint} -> {field}")

	def test_download_result_follows_result_url(self):
		data = _mock_response("GET", "tds/reports/txt")["data"]
		self.assertEqual(filing._download_result(_FakeClient(), data, "txt"), b"MOCK-TXT-FILE\n")

	def test_download_result_throws_when_absent(self):
		self.assertRaises(
			frappe.ValidationError, filing._download_result, _FakeClient(), {"status": "succeeded"}, "txt"
		)


class TestPollGuards(FrappeTestCase):
	def test_submitted_return_is_not_polled(self):
		# Filing log rows are not allow-on-submit, so polling a submitted return would
		# raise UpdateAfterSubmitError instead of quietly doing nothing.
		submitted = frappe._dict(docstatus=1, get_open_job=lambda: 1 / 0)
		with patch.object(filing.frappe, "get_doc", return_value=submitted):
			filing.poll_return("TDS-RET-SUBMITTED")

	def test_draft_without_open_job_is_a_noop(self):
		draft = frappe._dict(docstatus=0, get_open_job=lambda: None)
		with patch.object(filing.frappe, "get_doc", return_value=draft):
			filing.poll_return("TDS-RET-DRAFT")


class _RecordingClient:
	"""Captures what upload_to_presigned_url was called with."""

	def __init__(self, status=200):
		self.status = status
		self.uploads = []

	def upload_to_presigned_url(self, url, content, content_type, **kwargs):
		self.uploads.append((url, content, content_type, kwargs))
		return self.status


class TestPresignedUpload(FrappeTestCase):
	def test_s3_error_body_is_surfaced(self):
		# A 403 SignatureDoesNotMatch must raise with the S3 body, not log as "uploaded".
		client = SandboxTDSClient.__new__(SandboxTDSClient)
		client.mock = 0
		client.api_secret = None
		client._session = _FakeSession(403, "<Error><Code>SignatureDoesNotMatch</Code></Error>")
		logged = {}
		with patch.object(SandboxTDSClient, "_log_request", lambda self, **kw: logged.update(kw)):
			with self.assertRaises(frappe.ValidationError) as ctx:
				client.upload_to_presigned_url("https://s3.example/upload?sig=x", b"{}", "application/json")
		self.assertIn("SignatureDoesNotMatch", str(ctx.exception))
		self.assertIsNotNone(logged["error"])
		self.assertEqual(logged["output"]["status_code"], 403)

	def test_success_logs_real_status_and_etag(self):
		client = SandboxTDSClient.__new__(SandboxTDSClient)
		client.mock = 0
		client.api_secret = None
		client._session = _FakeSession(200, "", {"ETag": '"abc123"'})
		logged = {}
		with patch.object(SandboxTDSClient, "_log_request", lambda self, **kw: logged.update(kw)):
			status = client.upload_to_presigned_url(
				"https://s3.example/upload?sig=x", b"{}", "application/json"
			)
		self.assertEqual(status, 200)
		self.assertIsNone(logged["error"])
		self.assertEqual(logged["output"]["etag"], '"abc123"')

	def test_long_presigned_url_does_not_break_the_log_row(self):
		# request_description is Data/varchar(140); overflowing it used to drop the row.
		long_url = "https://bucket.s3.ap-south-1.amazonaws.com/" + "k" * 400
		desc = f"PUT {long_url}"[:140]
		self.assertEqual(len(desc), 140)


class _FakeSession:
	def __init__(self, status_code, text, headers=None):
		self._status_code = status_code
		self._text = text
		self._headers = headers or {}

	def put(self, url, data=None, headers=None, timeout=None):
		return frappe._dict(status_code=self._status_code, text=self._text, headers=self._headers)


class TestStuckAtCreated(FrappeTestCase):
	def test_created_has_a_shorter_deadline_than_the_overall_timeout(self):
		# A job awaiting its payload should fail fast, not burn the full 180 min.
		self.assertLess(filing.MAX_CREATED_MINUTES, filing.MAX_JOB_AGE_MINUTES)
		self.assertIn("created", filing.PENDING_STATUSES)


class TestSkipValidation(FrappeTestCase):
	def test_skipped_status_unblocks_txt_generation(self):
		self.assertIn(filing.SKIPPED_STATUS, filing.TXT_READY_STATUSES)
		self.assertIn("Validated", filing.TXT_READY_STATUSES)

	def test_skipped_status_is_not_an_in_progress_state(self):
		# Otherwise every later step would report "still in progress".
		self.assertNotIn(filing.SKIPPED_STATUS, filing.IN_PROGRESS_STATUSES)

	def test_local_closures_end_a_filing_log_row(self):
		# An abandoned validate job must stop the poller picking it up again.
		for status in ("skipped", "abandoned"):
			self.assertIn(status, filing.TERMINAL_ACTION_STATUSES)

	def test_reason_is_required(self):
		for blank in ("", "   ", None):
			with patch.object(filing.frappe, "get_doc") as get_doc:
				get_doc.return_value = frappe._dict(docstatus=0, check_permission=lambda perm: None)
				self.assertRaises(frappe.ValidationError, filing.skip_validation, "TDS-RET-X", blank)

	def test_submitted_return_cannot_be_skipped(self):
		with patch.object(filing.frappe, "get_doc") as get_doc:
			get_doc.return_value = frappe._dict(docstatus=1, check_permission=lambda perm: None)
			self.assertRaises(frappe.ValidationError, filing.skip_validation, "TDS-RET-X", "because")

	def test_cannot_skip_while_another_step_runs(self):
		doc = frappe._dict(
			docstatus=0,
			check_permission=lambda perm: None,
			filing_status="Generating TXT",
		)
		with patch.object(filing.frappe, "get_doc", return_value=doc):
			with self.assertRaises(frappe.ValidationError) as ctx:
				filing.skip_validation("TDS-RET-X", "because")
		self.assertIn("Generating TXT", str(ctx.exception))


# --------------------------------------------------------------- workbook checks
# A purpose-built checker for Sandbox's workbook schemas. They have a fixed shape
# (workbook -> sheets oneOf -> blocks oneOf -> list|table), so a generic JSON Schema
# engine is not needed — and two upstream defects mean one would reject Sandbox's own
# example workbook anyway:
#
#   * `"type": "long"` is not a JSON Schema type.
#   * Some columns declare a `type` permitting values their `enum` forbids, e.g.
#     {"type": ["boolean", "null"], "enum": ["true", "false"]} — unsatisfiable.
#
# Both are handled below; genuine string enums (minor_head, nature_of_payment, …)
# stay enforced.

TYPE_CHECKS = {
	"string": str,
	"integer": int,
	"long": int,
	"number": (int, float),
	"boolean": bool,
	"object": dict,
	"array": list,
}


def _types_of(spec):
	declared = spec.get("type")
	return set(declared if isinstance(declared, list) else [declared])


def _type_ok(value, types):
	if value is None:
		return "null" in types
	for name in types:
		expected = TYPE_CHECKS.get(name)
		if not expected:
			continue
		# bool is an int subclass; keep them distinct.
		if isinstance(value, bool) != (name == "boolean"):
			continue
		if isinstance(value, expected):
			return True
	return False


def _enum_ok(value, spec, types):
	enum = spec.get("enum")
	if not enum:
		return True
	if value is None and "null" in types:
		return True
	# Contradictory enum (see note above) — the declared type is authoritative.
	if all(isinstance(v, str) for v in enum) and "string" not in types:
		return True
	return value in enum


def _check_value(value, spec, where, errors):
	types = _types_of(spec)
	if not _type_ok(value, types):
		errors.append(f"{where}: {value!r} is not of type {sorted(t for t in types if t)}")
		return
	if not _enum_ok(value, spec, types):
		errors.append(f"{where}: {value!r} not in {spec['enum']}")
	pattern = spec.get("pattern")
	if pattern and isinstance(value, str) and not re.search(pattern, value):
		errors.append(f"{where}: {value!r} does not match {pattern}")
	maximum = spec.get("maximum")
	if maximum is not None and isinstance(value, (int, float)) and not isinstance(value, bool):
		if value > maximum:
			errors.append(f"{where}: {value} exceeds maximum {maximum}")


def _pick(specs, name, where, errors):
	for spec in specs:
		if spec["properties"]["name"]["enum"][0] == name:
			return spec
	errors.append(f"{where}: unexpected '{name}'")
	return None


def _check_list_block(block, spec, where, errors):
	properties = spec["properties"]["items"]["items"]["properties"]
	for item in block["items"]:
		for key, value in item.items():
			if key not in properties:
				errors.append(f"{where}: unknown key '{key}'")
				continue
			_check_value(value, properties[key], f"{where}.{key}", errors)


def _check_table_block(block, spec, where, errors):
	expected = [col["enum"][0] for col in spec["properties"]["header"]["items"]]
	if block["header"] != expected:
		extra = set(block["header"]) - set(expected)
		missing = set(expected) - set(block["header"])
		errors.append(f"{where}.header mismatch (unexpected={sorted(extra)}, missing={sorted(missing)})")
		return

	rows_spec = spec["properties"]["rows"]["items"]
	columns = rows_spec["items"]
	low = rows_spec.get("minItems", len(columns))
	high = rows_spec.get("maxItems", len(columns))
	for index, row in enumerate(block["rows"]):
		if not low <= len(row) <= high:
			errors.append(f"{where}.rows[{index}]: {len(row)} columns, expected {low}..{high}")
			continue
		for value, column, name in zip(row, columns, expected, strict=False):
			_check_value(value, column, f"{where}.rows[{index}].{name}", errors)


def validate_workbook(book, schema):
	"""Return a list of human-readable schema violations (empty when valid)."""
	errors = []
	for key in ("name", "@entity"):
		_check_value(book.get(key), schema["properties"][key], key, errors)

	sheet_specs = schema["properties"]["sheets"]["items"]["oneOf"]
	for sheet in book["sheets"]:
		sheet_spec = _pick(sheet_specs, sheet["name"], "sheets", errors)
		if not sheet_spec:
			continue
		block_specs = sheet_spec["properties"]["blocks"]["items"]["oneOf"]
		for block in sheet["blocks"]:
			where = f"{sheet['name']}.{block['name']}"
			block_spec = _pick(block_specs, block["name"], sheet["name"], errors)
			if not block_spec:
				continue
			if block["@entity"] == "list":
				_check_list_block(block, block_spec, where, errors)
			else:
				_check_table_block(block, block_spec, where, errors)
	return errors


class TestSheetJsonWorkbook(FrappeTestCase):
	"""The uploaded payload must satisfy Sandbox's published workbook schemas.

	A malformed workbook is what produced "TAN mismatch" from the TXT job, so the
	output is validated against the vendored schemas rather than eyeballed.
	"""

	@staticmethod
	def _schema(name):
		path = os.path.join(os.path.dirname(sheet_json.__file__), "schemas", f"{name}.schema.json")
		with open(path) as f:
			return json.load(f)

	def _validate(self, book, name):
		errors = validate_workbook(book, self._schema(name))
		if errors:
			self.fail(f"{name}: " + "; ".join(errors[:5]))

	def _doc(self, quarter="Q1", financial_year="2026-2027"):
		deductee = frappe._dict(
			employee="EMP-0001",
			employee_name="Priya Patel",
			pan="XXXPX5678A",
			month="April",
			date_of_payment="2026-04-30",
			date_of_deduction="2026-04-30",
			amount_paid=100000,
			tax_deducted=5000,
			challan="TDS-CH-0001",
			opting_new_regime=1,
			employee_category="general",
			is_pan_operative=1,
			surcharge=0,
			health_and_education_cess=0,
			tax_deposited=5000,
			reason_for_lower_deduction=None,
			certificate_number=None,
		)
		return frappe._dict(
			company="ACME",
			financial_year=financial_year,
			quarter=quarter,
			tan="MUMW03366G",
			pan="AAACA1234Z",
			deductor_name="ACME PRIVATE LIMITED",
			deductor_branch="HO",
			deductor_gstin="24AAACA1234Z1ZP",
			deductor_type_code="K",
			deductor_email="tds@acme.test",
			deductor_contact_country_code="91",
			deductor_contact_number="9876543210",
			deductor_flat_door_block_number="A-901",
			deductor_post_office="NAVRANGPURA",
			deductor_road_street="RELIEF ROAD",
			deductor_area_locality="CBD",
			deductor_district="AHMEDABAD",
			deductor_state="GUJARAT",
			deductor_postal_code="380001",
			deductor_country="INDIA",
			government_state_code=None,
			ministry_code=None,
			ministry_name_other=None,
			account_office_identification_number=None,
			responsible_person_name="Tony Stark",
			responsible_person_pan="DKLPT3483J",
			responsible_person_designation="MANAGER",
			rp_email=None,
			rp_contact_country_code=None,
			rp_contact_number=None,
			rp_flat_door_block_number=None,
			rp_post_office=None,
			rp_road_street=None,
			rp_area_locality=None,
			rp_district=None,
			rp_state=None,
			rp_postal_code=None,
			rp_country=None,
			deductees=[deductee],
		)

	def _build(self, workbook, **kw):
		challan = frappe._dict(
			name="TDS-CH-0001",
			challan_serial_no="12345",
			bsr_code="1234567",
			challan_date="2026-06-07",
			tds_amount=5000,
			surcharge_amount=0,
			education_cess=0,
			interest=0,
			fee=0,
			others=0,
			deposit_amount=5000,
		)
		with (
			patch.object(sheet_json, "get_challan_rows", return_value=[challan]),
			patch.object(sheet_json.frappe.db, "get_value", return_value=("12345", "1234567")),
			patch.object(sheet_json, "_salary_annexure", return_value=[]),
		):
			return sheet_json.build_sheet_json(self._doc(**kw), workbook)

	def test_form138_matches_published_schema(self):
		self._validate(self._build("form138_workbook"), "form138_workbook")

	def test_form24q_matches_published_schema(self):
		self._validate(self._build("form24q_workbook"), "form24q_workbook")

	def test_form24q_q4_with_annexure_matches_schema(self):
		annexure = [{"employee": "EMP-0001", "gross_salary": 1200000, "total_tax": 60000}]
		challan = frappe._dict(
			name="TDS-CH-0001",
			challan_serial_no="12345",
			bsr_code="1234567",
			challan_date="2027-03-07",
			tds_amount=5000,
			surcharge_amount=0,
			education_cess=0,
			interest=0,
			fee=0,
			others=0,
			deposit_amount=5000,
		)
		with (
			patch.object(sheet_json, "get_challan_rows", return_value=[challan]),
			patch.object(sheet_json.frappe.db, "get_value", return_value=("12345", "1234567")),
			patch.object(sheet_json, "_salary_annexure", return_value=annexure),
		):
			book = sheet_json.build_sheet_json(self._doc(quarter="Q4"), "form24q_workbook")
		self._validate(book, "form24q_workbook")
		salary = book["sheets"][4]["blocks"][0]
		self.assertEqual(salary["name"], "salary_detail_table")
		self.assertEqual(len(salary["rows"]), 1)

	def test_tan_is_present_in_the_payer_sheet(self):
		# The exact defect behind Sandbox's "TAN mismatch": no payer_sheet TAN.
		for workbook in ("form138_workbook", "form24q_workbook"):
			book = self._build(workbook)
			payer = book["sheets"][0]["blocks"][0]
			tan = next(v for item in payer["items"] for k, v in item.items() if k == "tan")
			self.assertEqual(tan, "MUMW03366G", workbook)

	def test_dates_are_epoch_milliseconds(self):
		book = self._build("form138_workbook")
		challan_row = book["sheets"][2]["blocks"][0]["rows"][0]
		epoch = challan_row[2]
		self.assertIsInstance(epoch, int)
		# Milliseconds, not seconds — a seconds value would be ~1e9.
		self.assertGreater(epoch, 1_000_000_000_000)

	def test_payment_rows_reference_the_payee_serial(self):
		book = self._build("form138_workbook")
		payee_sr = book["sheets"][1]["blocks"][0]["rows"][0][0]
		payment_sr = book["sheets"][3]["blocks"][0]["rows"][0][0]
		self.assertEqual(payee_sr, payment_sr)

	def test_workbook_chosen_by_act_year(self):
		self.assertEqual(
			sheet_json.workbook_name_for(frappe._dict(financial_year="2026-2027")), "form138_workbook"
		)
		self.assertEqual(
			sheet_json.workbook_name_for(frappe._dict(financial_year="2025-2026")), "form24q_workbook"
		)

	def test_complete_profile_reports_nothing_missing(self):
		with (
			patch.object(sheet_json, "get_challan_rows", return_value=[]),
			patch.object(sheet_json.frappe.db, "get_value", return_value=(None, None)),
			patch.object(sheet_json, "_salary_annexure", return_value=[]),
		):
			for workbook in ("form138_workbook", "form24q_workbook"):
				self.assertEqual(sheet_json.missing_payer_fields(self._doc(), workbook), [], workbook)

	def test_missing_deductor_pan_is_reported(self):
		# The exact hole behind the live "TAN mismatch": a non-nullable payer column
		# left blank. It must be named locally, not bounced back by Sandbox.
		doc = self._doc()
		doc.pan = None
		with (
			patch.object(sheet_json, "get_challan_rows", return_value=[]),
			patch.object(sheet_json.frappe.db, "get_value", return_value=(None, None)),
			patch.object(sheet_json, "_salary_annexure", return_value=[]),
		):
			missing = sheet_json.missing_payer_fields(doc, "form138_workbook")
		self.assertTrue(any(item.endswith("pan") for item in missing), missing)

	def test_missing_deductor_type_reported_for_new_act_only(self):
		doc = self._doc()
		doc.deductor_type_code = None
		with (
			patch.object(sheet_json, "get_challan_rows", return_value=[]),
			patch.object(sheet_json.frappe.db, "get_value", return_value=(None, None)),
			patch.object(sheet_json, "_salary_annexure", return_value=[]),
		):
			self.assertTrue(
				any("deductor_type" in m for m in sheet_json.missing_payer_fields(doc, "form138_workbook"))
			)
			# form24q has no deductor_type column at all.
			self.assertEqual(sheet_json.missing_payer_fields(doc, "form24q_workbook"), [])

	def _corrupt(self, mutate, workbook="form138_workbook"):
		book = self._build(workbook)
		mutate(book)
		return validate_workbook(book, self._schema(workbook))

	def test_validator_catches_wrong_type(self):
		def mutate(book):
			book["sheets"][0]["blocks"][0]["items"][1]["tan"] = 12345

		self.assertTrue(any("not of type" in e for e in self._corrupt(mutate)))

	def test_validator_catches_bad_pattern(self):
		# Only form24q constrains the TAN format; form138 declares a plain string.
		def mutate(book):
			book["sheets"][0]["blocks"][0]["items"][1]["tan"] = "NOTATAN"

		self.assertTrue(any("does not match" in e for e in self._corrupt(mutate, "form24q_workbook")))

	def test_validator_catches_unknown_key(self):
		def mutate(book):
			book["sheets"][0]["blocks"][0]["items"].append({"nonsense": "x"})

		self.assertTrue(any("unknown key" in e for e in self._corrupt(mutate)))

	def test_validator_catches_header_drift(self):
		# The original defect class: a column named differently from the contract.
		def mutate(book):
			book["sheets"][3]["blocks"][0]["header"][8] = "heath_and_education_cess"

		self.assertTrue(any("header mismatch" in e for e in self._corrupt(mutate, "form24q_workbook")))

	def test_validator_catches_short_row(self):
		def mutate(book):
			book["sheets"][1]["blocks"][0]["rows"][0].pop()

		self.assertTrue(any("columns, expected" in e for e in self._corrupt(mutate)))

	def test_validator_catches_bad_enum(self):
		def mutate(book):
			book["sheets"][2]["blocks"][0]["rows"][0][3] = "not_a_minor_head"

		self.assertTrue(any("not in" in e for e in self._corrupt(mutate, "form24q_workbook")))

	def test_validator_catches_unexpected_sheet(self):
		def mutate(book):
			book["sheets"][0]["name"] = "mystery_sheet"

		self.assertTrue(any("unexpected" in e for e in self._corrupt(mutate)))

	def _mismatches(self, doc, workbook="form138_workbook", challans=None):
		challan = frappe._dict(
			name="TDS-CH-0001",
			challan_serial_no="12345",
			bsr_code="1234567",
			challan_date="2026-06-07",
			tds_amount=5000,
			surcharge_amount=0,
			education_cess=0,
			interest=0,
			fee=0,
			others=0,
			deposit_amount=5000,
		)
		with (
			patch.object(sheet_json, "get_challan_rows", return_value=challans or [challan]),
			patch.object(sheet_json.frappe.db, "get_value", return_value=("12345", "1234567")),
		):
			return sheet_json.challan_payment_mismatches(doc, workbook)

	def test_balanced_challan_reports_no_mismatch(self):
		doc = self._doc()
		doc.deductees[0].tax_deducted = 5000
		doc.deductees[0].tax_deposited = 5000
		self.assertEqual(self._mismatches(doc), [])

	def test_mismatch_names_challan_and_difference(self):
		doc = self._doc()
		doc.deductees[0].tax_deducted = 4000  # challan carries 5000
		doc.deductees[0].tax_deposited = 4000
		problems = self._mismatches(doc)
		self.assertEqual(len(problems), 1)
		self.assertIn("12345", problems[0])
		self.assertIn("1234567", problems[0])
		self.assertIn("5000", problems[0])
		self.assertIn("4000", problems[0])

	def test_interest_and_fee_do_not_count_as_tax(self):
		# The live defect: deposit_amount carries interest/fee, deduction rows never do.
		challan = frappe._dict(
			name="TDS-CH-0001",
			challan_serial_no="12345",
			bsr_code="1234567",
			challan_date="2026-06-07",
			tds_amount=5000,
			surcharge_amount=0,
			education_cess=0,
			interest=300,
			fee=200,
			others=0,
			deposit_amount=5500,
		)
		doc = self._doc()
		doc.deductees[0].tax_deducted = 5000
		doc.deductees[0].tax_deposited = 5000
		self.assertEqual(self._mismatches(doc, challans=[challan]), [])

	def test_challan_sheet_excludes_interest_from_tax_columns(self):
		challan = frappe._dict(
			name="TDS-CH-0001",
			challan_serial_no="12345",
			bsr_code="1234567",
			challan_date="2026-06-07",
			tds_amount=5000,
			surcharge_amount=0,
			education_cess=0,
			interest=300,
			fee=200,
			others=0,
			deposit_amount=5500,
		)
		with (
			patch.object(sheet_json, "get_challan_rows", return_value=[challan]),
			patch.object(sheet_json.frappe.db, "get_value", return_value=("12345", "1234567")),
			patch.object(sheet_json, "_salary_annexure", return_value=[]),
		):
			book = sheet_json.build_sheet_json(self._doc(), "form138_workbook")
		row = book["sheets"][2]["blocks"][0]["rows"][0]
		self.assertEqual(row[6], 5000, "total_tax_deducted must exclude interest/fee")
		self.assertEqual(row[10], 5000, "total_tax_deposited must exclude interest/fee")
		self.assertEqual(row[7], 300)
		self.assertEqual(row[8], 200)

	def test_unlinked_deduction_row_is_reported(self):
		doc = self._doc()
		doc.deductees[0].challan = None
		with (
			patch.object(sheet_json, "get_challan_rows", return_value=[]),
			patch.object(sheet_json.frappe.db, "get_value", return_value=(None, None)),
		):
			problems = sheet_json.challan_payment_mismatches(doc, "form138_workbook")
		self.assertTrue(any("not linked to any challan" in p for p in problems), problems)

	def test_unknown_workbook_is_rejected(self):
		self.assertRaises(
			frappe.ValidationError, sheet_json.build_sheet_json, self._doc(), "form999_workbook"
		)


class TestValidationIssues(FrappeTestCase):
	def test_report_json_list_becomes_issue_rows(self):
		content = b'[{"message": "PAN mismatch for row 3"}]'
		issues = json.loads(filing._issues_json(content, "report.json"))
		self.assertEqual(issues[0]["message"], "PAN mismatch for row 3")

	def test_wrapped_issues_key_is_unwrapped(self):
		content = b'{"issues": [{"message": "short deduction"}]}'
		issues = json.loads(filing._issues_json(content, "report.json"))
		self.assertEqual(issues[0]["message"], "short deduction")

	def test_non_json_report_points_at_the_attachment(self):
		# Failure reports are XLSX; the user gets a pointer, never a silent pass.
		issues = json.loads(filing._issues_json(b"PK\x03\x04binary", "report.xlsx"))
		self.assertEqual(len(issues), 1)
		self.assertIn("report.xlsx", issues[0]["message"])

	def test_undownloadable_report_is_still_surfaced(self):
		issues = json.loads(filing._issues_json(None, None))
		self.assertEqual(len(issues), 1)
		self.assertTrue(issues[0]["message"])


class TestFvuZipHandling(FrappeTestCase):
	def test_fvu_member_extracted_from_zip(self):
		content = _mock_file_bytes("mock://result/fvu.zip")
		self.assertEqual(filing._extract_from_zip(content, ".fvu"), b"MOCK-FVU-FILE\n")

	def test_missing_member_returns_none(self):
		content = _mock_file_bytes("mock://result/fvu.zip")
		self.assertIsNone(filing._extract_from_zip(content, ".pdf"))

	def test_non_zip_payload_is_tolerated(self):
		self.assertIsNone(filing._extract_from_zip(b"not-a-zip", ".fvu"))
