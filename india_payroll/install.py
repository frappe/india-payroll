import frappe
from frappe.custom.doctype.custom_field.custom_field import create_custom_fields

INDIA_STATES = [
	"Andhra Pradesh",
	"Arunachal Pradesh",
	"Assam",
	"Bihar",
	"Chhattisgarh",
	"Goa",
	"Gujarat",
	"Haryana",
	"Himachal Pradesh",
	"Jharkhand",
	"Karnataka",
	"Kerala",
	"Madhya Pradesh",
	"Maharashtra",
	"Manipur",
	"Meghalaya",
	"Mizoram",
	"Nagaland",
	"Odisha",
	"Punjab",
	"Rajasthan",
	"Sikkim",
	"Tamil Nadu",
	"Telangana",
	"Tripura",
	"Uttar Pradesh",
	"Uttarakhand",
	"West Bengal",
	"Andaman and Nicobar Islands",
	"Chandigarh",
	"Dadra and Nagar Haveli and Daman and Diu",
	"Delhi",
	"Jammu and Kashmir",
	"Ladakh",
	"Lakshadweep",
	"Puducherry",
]


def get_custom_fields():
	return {
		"Payroll Settings": [
			{
				"fieldname": "india_payroll_tab",
				"label": "India Payroll",
				"fieldtype": "Tab Break",
				"insert_after": "create_overtime_slip",
			},
			{
				"fieldname": "india_payroll_professional_tax_section",
				"label": "Professional Tax",
				"fieldtype": "Section Break",
				"insert_after": "india_payroll_tab",
			},
			{
				"fieldname": "enable_professional_tax",
				"label": "Enable Professional Tax Deduction",
				"fieldtype": "Check",
				"insert_after": "india_payroll_professional_tax_section",
			},
			{
				"fieldname": "professional_tax_enrollment_number",
				"label": "Professional Tax Enrollment Number",
				"fieldtype": "Data",
				"insert_after": "enable_professional_tax",
				"depends_on": "eval:doc.enable_professional_tax == 1",
			},
		],
		"Salary Structure Assignment": [
			{
				"fieldname": "employment_state",
				"label": "Employment State",
				"fieldtype": "Autocomplete",
				"options": "\n".join(INDIA_STATES),
				"insert_after": "employee",
			},
		],
	}


def after_install():
	create_custom_fields(get_custom_fields())
	create_professional_tax_component()


def after_migrate():
	create_custom_fields(get_custom_fields())


def create_professional_tax_component():
	"""
	Create the 'Professional Tax' Salary Component if it does not already exist.
	The component is a plain deduction with no statistical or account settings —
	the employer must link it to the appropriate GL account per company via
	Salary Component Account.
	"""
	if frappe.db.exists("Salary Component", "Professional Tax"):
		return

	doc = frappe.new_doc("Salary Component")
	doc.salary_component = "Professional Tax"
	doc.salary_component_abbr = "PT"
	doc.type = "Deduction"
	doc.description = (
		"State-level professional tax levied on salaried employees "
		"under Article 276 of the Indian Constitution (max ₹2,500/year)."
	)
	doc.insert(ignore_permissions=True)
