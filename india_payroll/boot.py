import frappe

from india_payroll.india_payroll.tds.settings import conf_sandbox_mode, has_conf_credentials


def set_bootinfo(bootinfo):
	"""Expose whether Sandbox TDS credentials come from site config.

	The Payroll Settings credential fields hide themselves against this, so the
	check has to be answerable on the client without a round trip.
	"""
	bootinfo["ip_tds_credentials_from_conf"] = has_conf_credentials()
	bootinfo["ip_tds_sandbox_mode_from_conf"] = conf_sandbox_mode() if has_conf_credentials() else 0
