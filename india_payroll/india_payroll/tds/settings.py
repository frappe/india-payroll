"""doc_event hooks for the doctypes that hold TDS configuration.

Sandbox credentials live on Payroll Settings (a Single doctype); deductor
details live on Company. Both are core/HRMS doctypes extended via custom fields,
so the behavior is attached through hooks rather than a controller.
"""

import frappe
from frappe import _

from india_payroll.india_payroll.tds.validators import is_valid_pan, is_valid_tan


def clear_token_cache_on_change(doc, method=None):
	"""Invalidate the cached Sandbox access token when credentials change.

	The JWT is tied to a specific api_key/api_secret pair, so a credential
	change must force re-authentication on the next API call.
	"""
	before = doc.get_doc_before_save()
	if not before:
		return

	credential_fields = ("tds_api_key", "tds_api_secret", "tds_sandbox_mode")
	if any(doc.get(f) != before.get(f) for f in credential_fields):
		doc.tds_access_token = None
		doc.tds_token_expiry = None


def validate_deductor_details(doc, method=None):
	"""Validate TAN and responsible-person PAN format on Company, if provided."""
	if doc.get("tan"):
		doc.tan = doc.tan.strip().upper()
		if not is_valid_tan(doc.tan):
			frappe.throw(_("TAN '{0}' is not a valid TAN (expected format AAAA99999A).").format(doc.tan))

	if doc.get("responsible_person_pan"):
		doc.responsible_person_pan = doc.responsible_person_pan.strip().upper()
		if not is_valid_pan(doc.responsible_person_pan):
			frappe.throw(
				_("Responsible Person PAN '{0}' is not a valid PAN (expected format AAAAA9999A).").format(
					doc.responsible_person_pan
				)
			)
