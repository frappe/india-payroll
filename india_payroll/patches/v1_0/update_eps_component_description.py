import frappe

OLD_DESCRIPTION = (
	"Employer's EPS share (A/c 10) = 8.33% of capped PF wages. "
	"Zero for employees who first joined EPF on/after 1 Sept 2014 with PF wage > ₹15,000."
)
NEW_DESCRIPTION = (
	"Employer's EPS share (A/c 10) = 8.33% of capped PF wages. "
	"Zero for employees who first joined EPF on/after 1 Sept 2014 with PF wage above the "
	"statutory ceiling (₹25,000 from 17 Sept 2026, ₹15,000 before)."
)


def execute():
	"""
	Refresh the Employer Pension Scheme description for the revised PF wage ceiling.

	`create_epf_components` skips components that already exist, so existing sites
	keep the old ₹15,000 text. Only the stock description is replaced; one a user
	has edited is left alone.
	"""
	if frappe.db.get_value("Salary Component", "Employer Pension Scheme", "description") != OLD_DESCRIPTION:
		return

	frappe.db.set_value("Salary Component", "Employer Pension Scheme", "description", NEW_DESCRIPTION)
