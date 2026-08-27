import frappe

from india_payroll.india_payroll.tds.settings import conf_sandbox_mode, has_conf_credentials


def execute():
	"""Keep the displayed environment in step with the cloud-provisioned key.

	The field is read-only for these sites, so it exists to be read; the key the
	site was handed is what actually decides which Sandbox host answers.
	"""
	if not has_conf_credentials():
		return

	frappe.db.set_single_value("Payroll Settings", "tds_sandbox_mode", conf_sandbox_mode())
