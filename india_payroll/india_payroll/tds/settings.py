"""TDS configuration: credential resolution and doc_event hooks.

Sandbox credentials live on Payroll Settings (a Single doctype); deductor
details live on Company. Both are core/HRMS doctypes extended via custom fields,
so the behavior is attached through hooks rather than a controller.

Credentials come from one of two places. A site provisioned by Frappe Cloud
receives them through site config, so the feature works without the user holding
their own sandbox.co.in account; any other site enters its own pair on Payroll
Settings. The site's own credentials win when both are present, so a bundled site
can still be pointed at a different Sandbox account.
"""

import frappe
from frappe import _
from frappe.utils import cint

from india_payroll.india_payroll.tds.validators import is_valid_pan, is_valid_tan

# site_config keys through which Frappe Cloud provisions Sandbox TDS access.
CONF_API_KEY = "ip_tds_api_key"
CONF_API_SECRET = "ip_tds_api_secret"
CONF_SANDBOX_MODE = "ip_tds_sandbox_mode"
CONF_API_VERSION = "ip_tds_api_version"


def has_conf_credentials() -> bool:
	"""True when site config carries a complete Sandbox key/secret pair."""
	return bool(frappe.conf.get(CONF_API_KEY) and frappe.conf.get(CONF_API_SECRET))


def can_enable_tds_filing(settings=None) -> bool:
	"""True when a Sandbox credential pair is available from either source.

	The enable switch is only offered once there is a credential behind it, so
	the feature cannot be turned on into a guaranteed failure.
	"""
	if has_conf_credentials():
		return True

	if settings is None:
		settings = get_tds_settings()

	return bool(settings.get("tds_api_key") and settings.get("tds_api_secret"))


def is_tds_filing_enabled(settings=None) -> bool:
	if settings is None:
		settings = get_tds_settings()

	return bool(settings.get("enable_tds_filing")) and can_enable_tds_filing(settings)


def get_tds_settings():
	"""Presence-only view of the TDS settings fields.

	The key and secret are Password fields, so what comes back for those is the
	`*****` placeholder stored in the table, not the value. Enough to test for
	presence; use `get_sandbox_credentials` for the real thing.
	"""
	return frappe.get_cached_value(
		"Payroll Settings",
		"Payroll Settings",
		("enable_tds_filing", "tds_api_key", "tds_api_secret"),
		as_dict=True,
	)


def get_sandbox_credentials(settings=None) -> dict:
	"""Resolve the credentials to authenticate with, site's own before cloud's.

	Never mixes the two sources: a partially-filled pair on Payroll Settings
	would otherwise pair a site key with a cloud secret and fail auth confusingly.
	"""
	if settings is None:
		settings = frappe.get_cached_doc("Payroll Settings")

	api_key = settings.get_password("tds_api_key", raise_exception=False)
	api_secret = settings.get_password("tds_api_secret", raise_exception=False)

	if api_key and api_secret:
		return {
			"api_key": api_key,
			"api_secret": api_secret,
			"api_version": settings.get("tds_api_version"),
			"sandbox_mode": cint(settings.get("tds_sandbox_mode")),
			"from_conf": False,
		}

	conf_key = frappe.conf.get(CONF_API_KEY)
	return {
		"api_key": conf_key,
		"api_secret": frappe.conf.get(CONF_API_SECRET),
		"api_version": frappe.conf.get(CONF_API_VERSION) or settings.get("tds_api_version"),
		"sandbox_mode": conf_sandbox_mode(conf_key),
		"from_conf": True,
	}


def conf_sandbox_mode(api_key=None) -> int:
	"""Which Sandbox environment the cloud-provisioned key belongs to.

	Sandbox picks the environment by host, and a key only works against its own
	host, so this is a property of the key rather than a user preference. Honour
	an explicit site config setting; otherwise read it off the key prefix.
	"""
	mode = frappe.conf.get(CONF_SANDBOX_MODE)
	if mode is not None:
		return cint(mode)

	if api_key is None:
		api_key = frappe.conf.get(CONF_API_KEY)

	return 1 if (api_key or "").startswith("key_test_") else 0


def validate_tds_filing_settings(doc, method=None):
	"""Refuse to enable TDS filing without a credential pair to file with."""
	if not doc.get("enable_tds_filing") or not doc.has_value_changed("enable_tds_filing"):
		return

	if not can_enable_tds_filing(doc):
		frappe.throw(
			_(
				"Set the Sandbox API Key and Secret before enabling TDS Return Filing, "
				"or install this site on Frappe Cloud to have them provisioned for you."
			),
			title=_("Credentials Unavailable"),
		)


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
