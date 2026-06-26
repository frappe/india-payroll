"""Shared validators and format helpers for the TDS filing feature."""

import re

import frappe
from frappe import _

PAN_PATTERN = re.compile(r"^[A-Z]{5}[0-9]{4}[A-Z]$")
TAN_PATTERN = re.compile(r"^[A-Z]{4}[0-9]{5}[A-Z]$")

# Quarter -> (month range within the Indian financial year Apr-Mar)
QUARTERS = ("Q1", "Q2", "Q3", "Q4")
QUARTER_MONTHS = {
	"Q1": (4, 5, 6),
	"Q2": (7, 8, 9),
	"Q3": (10, 11, 12),
	"Q4": (1, 2, 3),
}


def is_valid_pan(pan: str | None) -> bool:
	return bool(pan) and bool(PAN_PATTERN.match(pan.strip().upper()))


def is_valid_tan(tan: str | None) -> bool:
	return bool(tan) and bool(TAN_PATTERN.match(tan.strip().upper()))


def validate_pan(pan: str | None, label: str = "PAN") -> str:
	pan = (pan or "").strip().upper()
	if not is_valid_pan(pan):
		frappe.throw(_("{0} '{1}' is not a valid PAN (expected format AAAAA9999A).").format(label, pan))
	return pan


def validate_tan(tan: str | None, label: str = "TAN") -> str:
	tan = (tan or "").strip().upper()
	if not is_valid_tan(tan):
		frappe.throw(_("{0} '{1}' is not a valid TAN (expected format AAAA99999A).").format(label, tan))
	return tan


def normalize_financial_year(fy: str) -> str:
	"""Return a Sandbox-style financial year label, e.g. 'FY 2024-25'.

	Accepts '2024-25', '2024-2025', 'FY 2024-25' and ERPNext fiscal-year names.
	"""
	fy = (fy or "").strip()
	digits = re.findall(r"\d{4}", fy)
	if not digits:
		frappe.throw(_("Could not parse financial year from '{0}'.").format(fy))
	start = int(digits[0])
	end_two = str((start + 1) % 100).zfill(2)
	return f"FY {start}-{end_two}"
