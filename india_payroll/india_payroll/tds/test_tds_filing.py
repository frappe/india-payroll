# Copyright (c) 2026, Frappe Technologies Pvt. Ltd. and contributors
# License: GNU General Public License v3. See license.txt

from unittest.mock import patch

import frappe
from frappe.tests.utils import FrappeTestCase
from frappe.utils import getdate

from india_payroll.india_payroll.tds import filing
from india_payroll.india_payroll.tds.data_assembly import quarter_range_from_start
from india_payroll.india_payroll.tds.sandbox_client import mask_sensitive
from india_payroll.india_payroll.tds.validators import (
	is_valid_pan,
	is_valid_tan,
	normalize_financial_year,
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
			filing._result_txt(doc, {"txt_base64": ""})

		attach.assert_called_once()
		self.assertEqual(attach.call_args[0][1], "txt_file")
		self.assertIsNone(doc.get("fvu_file"))
		self.assertIsNone(doc.get("csi_file"))
		self.assertIsNone(doc.get("form_27a"))
