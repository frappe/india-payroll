# Copyright (c) 2026, Frappe Technologies Pvt. Ltd. and contributors
# License: GNU General Public License v3. See license.txt

import json
from unittest.mock import patch

import frappe
from frappe.tests.utils import FrappeTestCase
from frappe.utils import getdate

from india_payroll.india_payroll.tds import filing
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
