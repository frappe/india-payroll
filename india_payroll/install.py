import frappe
from frappe.custom.doctype.custom_field.custom_field import create_custom_fields

from india_payroll.india_payroll.tax_exemption_setup import setup_tax_exemption_categories

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


SURCHARGE_NEW_REGIME = [
	{
		"description": "Surcharge 10%",
		"percent": 10,
		"min_taxable_income": 5000001,
		"max_taxable_income": 10000000,
	},
	{
		"description": "Surcharge 15%",
		"percent": 15,
		"min_taxable_income": 10000001,
		"max_taxable_income": 20000000,
	},
	{"description": "Surcharge 25%", "percent": 25, "min_taxable_income": 20000001, "max_taxable_income": 0},
]

SURCHARGE_OLD_REGIME = [
	{
		"description": "Surcharge 10%",
		"percent": 10,
		"min_taxable_income": 5000001,
		"max_taxable_income": 10000000,
	},
	{
		"description": "Surcharge 15%",
		"percent": 15,
		"min_taxable_income": 10000001,
		"max_taxable_income": 20000000,
	},
	{
		"description": "Surcharge 25%",
		"percent": 25,
		"min_taxable_income": 20000001,
		"max_taxable_income": 50000000,
	},
	{"description": "Surcharge 37%", "percent": 37, "min_taxable_income": 50000001, "max_taxable_income": 0},
]

CESS = [
	{
		"description": "Health & Education Cess",
		"percent": 4,
		"min_taxable_income": 0,
		"max_taxable_income": 0,
	},
]


INDIA_TAX_SLABS = {
	"Old Tax Regime: 2019": {
		"effective_from": "2019-04-01",
		"currency": "INR",
		"allow_tax_exemption": 1,
		"standard_tax_exemption_amount": 50000,
		"tax_relief_limit": 500000,
		"marginal_relief_limit": 512500,
		"slabs": [
			{"from_amount": 0, "to_amount": 250000, "percent_deduction": 0},
			{"from_amount": 250001, "to_amount": 500000, "percent_deduction": 5},
			{"from_amount": 500001, "to_amount": 1000000, "percent_deduction": 20},
			{"from_amount": 1000001, "to_amount": 0, "percent_deduction": 30},
		],
		"surcharge_slabs": SURCHARGE_OLD_REGIME,
		"other_taxes_and_charges": CESS,
	},
	"New Tax Regime: 2024-2025": {
		"effective_from": "2024-04-01",
		"currency": "INR",
		"allow_tax_exemption": 0,
		"standard_tax_exemption_amount": 75000,
		"tax_relief_limit": 700000,
		"marginal_relief_limit": 727777,
		"slabs": [
			{"from_amount": 0, "to_amount": 300000, "percent_deduction": 0},
			{"from_amount": 300001, "to_amount": 700000, "percent_deduction": 5},
			{"from_amount": 700001, "to_amount": 1000000, "percent_deduction": 10},
			{"from_amount": 1000001, "to_amount": 1200000, "percent_deduction": 15},
			{"from_amount": 1200001, "to_amount": 1500000, "percent_deduction": 20},
			{"from_amount": 1500001, "to_amount": 0, "percent_deduction": 30},
		],
		"surcharge_slabs": SURCHARGE_NEW_REGIME,
		"other_taxes_and_charges": CESS,
	},
	"New Tax Regime: 2025-2026": {
		"effective_from": "2025-04-01",
		"currency": "INR",
		"allow_tax_exemption": 0,
		"standard_tax_exemption_amount": 75000,
		"tax_relief_limit": 1200000,
		"marginal_relief_limit": 1275000,
		"slabs": [
			{"from_amount": 0, "to_amount": 400000, "percent_deduction": 0},
			{"from_amount": 400001, "to_amount": 800000, "percent_deduction": 5},
			{"from_amount": 800001, "to_amount": 1200000, "percent_deduction": 10},
			{"from_amount": 1200001, "to_amount": 1600000, "percent_deduction": 15},
			{"from_amount": 1600001, "to_amount": 2000000, "percent_deduction": 20},
			{"from_amount": 2000001, "to_amount": 2400000, "percent_deduction": 25},
			{"from_amount": 2400001, "to_amount": 0, "percent_deduction": 30},
		],
		"surcharge_slabs": SURCHARGE_NEW_REGIME,
		"other_taxes_and_charges": CESS,
	},
}


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
				"collapsible": 1,
			},
			{
				"fieldname": "enable_professional_tax",
				"label": "Enable Professional Tax Deduction",
				"fieldtype": "Check",
				"insert_after": "india_payroll_professional_tax_section",
			},
			{
				"fieldname": "india_payroll_esic_section",
				"label": "Employee State Insurance",
				"fieldtype": "Section Break",
				"insert_after": "enable_professional_tax",
				"collapsible": 1,
			},
			{
				"fieldname": "enable_esic",
				"label": "Enable ESIC Deduction",
				"fieldtype": "Check",
				"insert_after": "india_payroll_esic_section",
			},
			{
				"fieldname": "esic_registration_number",
				"label": "ESIC Registration Number",
				"fieldtype": "Data",
				"insert_after": "enable_esic",
				"depends_on": "eval:doc.enable_esic",
				"translatable": 0,
			},
			{
				"fieldname": "india_payroll_lwf_section",
				"label": "Labour Welfare Fund",
				"fieldtype": "Section Break",
				"insert_after": "esic_registration_number",
				"collapsible": 1,
			},
			{
				"fieldname": "enable_lwf",
				"label": "Enable LWF Deduction",
				"fieldtype": "Check",
				"insert_after": "india_payroll_lwf_section",
			},
			{
				"fieldname": "india_payroll_epf_section",
				"label": "Employee Provident Fund",
				"fieldtype": "Section Break",
				"insert_after": "enable_lwf",
				"collapsible": 1,
			},
			{
				"fieldname": "enable_epf",
				"label": "Enable EPF Deduction",
				"fieldtype": "Check",
				"insert_after": "india_payroll_epf_section",
			},
			{
				"fieldname": "epf_establishment_code",
				"label": "EPF Establishment Code",
				"fieldtype": "Data",
				"insert_after": "enable_epf",
				"depends_on": "eval:doc.enable_epf",
				"translatable": 0,
				"description": "EPFO Establishment Code used in the ECR file header.",
			},
		],
		"Employee": [
			{
				"fieldname": "india_payroll_bank_cb",
				"fieldtype": "Column Break",
				"insert_after": "bank_ac_no",
			},
			{
				"fieldname": "ifsc_code",
				"label": "IFSC Code",
				"fieldtype": "Data",
				"insert_after": "india_payroll_bank_cb",
				"print_hide": 1,
				"depends_on": 'eval:doc.salary_mode == "Bank"',
				"translatable": 0,
			},
			{
				"fieldname": "micr_code",
				"label": "MICR Code",
				"fieldtype": "Data",
				"insert_after": "ifsc_code",
				"print_hide": 1,
				"depends_on": 'eval:doc.salary_mode == "Bank"',
				"translatable": 0,
			},
			{
				"fieldname": "payment_mode",
				"label": "Payment Mode",
				"fieldtype": "Select",
				"options": "\nNEFT\nRTGS",
				"insert_after": "micr_code",
				"depends_on": 'eval:doc.salary_mode == "Bank"',
				"translatable": 0,
			},
			{
				"fieldname": "account_type",
				"label": "Account Type",
				"fieldtype": "Select",
				"options": "\nSavings\nCurrent\nSalary",
				"insert_after": "payment_mode",
				"depends_on": 'eval:doc.salary_mode == "Bank"',
				"translatable": 0,
			},
			{
				"fieldname": "india_payroll_esi_section",
				"label": "Employee State Insurance",
				"fieldtype": "Section Break",
				"insert_after": "account_type",
			},
			{
				"fieldname": "esic_card_no",
				"label": "ESIC IP Number",
				"fieldtype": "Data",
				"insert_after": "india_payroll_esi_section",
				"translatable": 0,
				"description": "Employee's ESIC Insurance Number (IP Number) issued by ESIC",
			},
			{
				"fieldname": "is_person_with_disability",
				"label": "Person with Disability",
				"fieldtype": "Check",
				"insert_after": "esic_card_no",
				"description": "ESIC wage ceiling is \u20b925,000 instead of \u20b921,000 for persons with disability",
			},
			{
				"fieldname": "india_payroll_lwf_section",
				"label": "Labour Welfare Fund",
				"fieldtype": "Section Break",
				"insert_after": "is_person_with_disability",
			},
			{
				"fieldname": "lwf_exempted",
				"label": "LWF Exempted",
				"fieldtype": "Check",
				"insert_after": "india_payroll_lwf_section",
				"description": "Manually exempt this employee from Labour Welfare Fund deduction. This setting is preserved across payroll runs.",
			},
			{
				"fieldname": "lwf_exemption_reason",
				"label": "LWF Exemption Reason",
				"fieldtype": "Small Text",
				"insert_after": "lwf_exempted",
				"depends_on": "eval:doc.lwf_exempted",
				"translatable": 0,
			},
			{
				"fieldname": "india_payroll_epf_section",
				"label": "Employee Provident Fund",
				"fieldtype": "Section Break",
				"insert_after": "lwf_exemption_reason",
			},
			{
				"fieldname": "epf_applicable",
				"label": "EPF Applicable",
				"fieldtype": "Check",
				"insert_after": "india_payroll_epf_section",
				"description": (
					"Opt this employee into EPF deduction. The system defers to this "
					"flag rather than enforcing a wage-based eligibility rule."
				),
			},
			{
				"fieldname": "uan_number",
				"label": "UAN",
				"fieldtype": "Data",
				"insert_after": "epf_applicable",
				"translatable": 0,
				"description": "12-digit Universal Account Number issued by EPFO.",
			},
			{
				"fieldname": "pf_name",
				"label": "Name as per UAN",
				"fieldtype": "Data",
				"insert_after": "uan_number",
				"translatable": 0,
				"description": "Employee name as registered with EPFO. May differ from HR name; used in the ECR file.",
			},
			{
				"fieldname": "india_payroll_epf_cb",
				"fieldtype": "Column Break",
				"insert_after": "pf_name",
			},
			{
				"fieldname": "contribute_on_actual_pf_wage",
				"label": "Contribute on Actual PF Wage",
				"fieldtype": "Check",
				"insert_after": "india_payroll_epf_cb",
				"description": (
					"If checked, employee + employer EPF contributions are computed on the "
					"actual PF wage when it exceeds ₹15,000. EPS and EDLI remain capped by law."
				),
			},
			{
				"fieldname": "vpf_mode",
				"label": "VPF Mode",
				"fieldtype": "Select",
				"options": "Amount\nPercentage",
				"default": "Amount",
				"insert_after": "contribute_on_actual_pf_wage",
				"description": "Whether VPF is deducted as a fixed monthly amount or a % of PF wages.",
			},
			{
				"fieldname": "vpf_percentage",
				"label": "VPF Percentage",
				"fieldtype": "Percent",
				"insert_after": "vpf_mode",
				"depends_on": "eval:doc.vpf_mode == 'Percentage'",
				"description": "Voluntary Provident Fund — additional employee contribution rate over 12%.",
			},
			{
				"fieldname": "vpf_amount",
				"label": "VPF Amount",
				"fieldtype": "Currency",
				"insert_after": "vpf_percentage",
				"depends_on": "eval:doc.vpf_mode == 'Amount'",
				"description": (
					"Fixed monthly VPF amount elected by the employee. "
					"Prorated by payment days when there is LOP."
				),
			},
		],
		"Income Tax Slab": [
			{
				"fieldname": "surcharge_slabs",
				"label": "Surcharge Slabs",
				"fieldtype": "Table",
				"options": "Income Tax Slab Other Charges",
				"insert_after": "taxes_and_charges_on_income_tax_section",
				"reqd": 0,
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
	from india_payroll.patches.v1_0.set_employment_state_from_company_address import execute

	create_custom_fields(get_custom_fields())
	create_professional_tax_component()
	create_esi_components()
	create_lwf_component()
	create_epf_components()
	create_income_tax_slabs()
	setup_tax_exemption_categories()
	add_tax_regime_selector_to_workspace()
	add_tax_regime_selector_to_sidebar()

	# setup employment states
	execute()


def after_migrate():
	create_custom_fields(get_custom_fields())
	add_tax_regime_selector_to_workspace()
	add_tax_regime_selector_to_sidebar()


def add_tax_regime_selector_to_workspace():
	"""Add the Tax Regime Selector page link to HRMS's 'Tax & Benefits' workspace
	under the 'Tax Setup' card. Idempotent."""
	workspace_name = "Tax & Benefits"
	page_route = "tax-regime-selector"

	if not frappe.db.exists("Workspace", workspace_name):
		return

	workspace = frappe.get_doc("Workspace", workspace_name)
	if any(link.link_type == "Page" and link.link_to == page_route for link in workspace.links):
		return

	new_link = workspace.append(
		"links",
		{
			"type": "Link",
			"label": "Tax Regime Selector",
			"link_to": page_route,
			"link_type": "Page",
			"onboard": 0,
			"is_query_report": 0,
			"hidden": 0,
		},
	)

	# Place the new link as the last child of the 'Tax Setup' card (and bump its
	# link_count). `append` adds at the end, so move the row into position.
	insert_at = None
	for idx, link in enumerate(workspace.links):
		if link.type == "Card Break" and link.label == "Tax Setup":
			link.link_count = (link.link_count or 0) + 1
			insert_at = idx + link.link_count  # after the card's existing children
			break

	if insert_at is not None:
		workspace.links.remove(new_link)
		workspace.links.insert(insert_at, new_link)
		for i, link in enumerate(workspace.links):
			link.idx = i + 1

	workspace.save(ignore_permissions=True)


def add_tax_regime_selector_to_sidebar():
	"""Add the Tax Regime Selector page link to HRMS's 'Tax & Benefits' workspace
	sidebar, right below the 'Home' entry. Idempotent."""
	sidebar_name = "Tax & Benefits"
	page_route = "tax-regime-selector"

	if not frappe.db.exists("Workspace Sidebar", sidebar_name):
		return

	sidebar = frappe.get_doc("Workspace Sidebar", sidebar_name)
	if any(item.link_type == "Page" and item.link_to == page_route for item in sidebar.items):
		return

	new_item = sidebar.append(
		"items",
		{
			"type": "Link",
			"label": "Tax Regime Selector",
			"link_to": page_route,
			"link_type": "Page",
			"icon": "chart-network",
			"child": 0,
			"collapsible": 1,
			"indent": 0,
			"keep_closed": 0,
			"show_arrow": 0,
		},
	)

	# Place it right after the 'Home' entry (the workspace self-link). `append`
	# adds at the end, so move the row into position.
	insert_at = next(
		(i + 1 for i, item in enumerate(sidebar.items) if item.link_type == "Workspace"),
		None,
	)
	if insert_at is not None:
		sidebar.items.remove(new_item)
		sidebar.items.insert(insert_at, new_item)
		for i, item in enumerate(sidebar.items):
			item.idx = i + 1

	sidebar.save(ignore_permissions=True)


def create_professional_tax_component():
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


def create_esi_components():
	"""
	Create the Employee State Insurance salary component if it does not
	already exist.

	Only the employee's deduction (0.75 %) is tracked as a salary component.
	The employer's contribution (3.25 %) is part of the CTC and is not shown
	as a separate component on the salary slip.
	"""
	if frappe.db.exists("Salary Component", "Employee State Insurance"):
		return

	doc = frappe.new_doc("Salary Component")
	doc.update(
		{
			"salary_component": "Employee State Insurance",
			"salary_component_abbr": "ESI",
			"type": "Deduction",
			"statistical_component": 0,
			"description": (
				"Employee's contribution to the Employee State Insurance scheme "
				"at 0.75% of gross wages (ESI Act, 1948)."
			),
		}
	)
	doc.insert(ignore_permissions=True)


def create_lwf_component():
	"""
	Create the Labour Welfare Fund salary component if it does not already exist.

	Only the employee's flat deduction is shown on the salary slip.
	The employer's contribution is remitted separately and is not deducted
	from the employee's salary.
	"""
	if frappe.db.exists("Salary Component", "Labour Welfare Fund"):
		return

	doc = frappe.new_doc("Salary Component")
	doc.update(
		{
			"salary_component": "Labour Welfare Fund",
			"salary_component_abbr": "LWF",
			"type": "Deduction",
			"statistical_component": 0,
			"description": (
				"Employee's flat contribution to the State Labour Welfare Fund. "
				"Amount varies by state; deduction frequency is monthly, half-yearly, or annual."
			),
		}
	)
	doc.insert(ignore_permissions=True)


def create_epf_components():
	"""
	Create the six EPF-scheme salary components if they don't already exist.

	Employee contributions (Provident Fund + VPF) are deductions that reduce
	net pay.  Employer contributions (EPF / EPS / EDLI / Admin) use the
	dedicated "Employer Contribution" component type — they are configured
	on the Salary Structure's Employer Contributions table and rolled into
	CTC by the Salary Structure Assignment.  They are not posted to the
	salary slip, so they don't affect gross / net pay.
	"""
	components = [
		{
			"salary_component": "Provident Fund",
			"salary_component_abbr": "PF",
			"type": "Deduction",
			"statistical_component": 0,
			"description": "Employee's 12% contribution to EPF (A/c 1) on PF wages.",
		},
		{
			"salary_component": "Voluntary Provident Fund",
			"salary_component_abbr": "VPF",
			"type": "Deduction",
			"statistical_component": 0,
			"description": "Employee's voluntary contribution to EPF over and above the mandatory 12%.",
		},
		{
			"salary_component": "Employer Provident Fund",
			"salary_component_abbr": "EREPF",
			"type": "Employer Contribution",
			"description": (
				"Employer's EPF share (A/c 1) = 12% of PF wages minus the EPS diversion. "
				"Part of CTC; not posted to the salary slip."
			),
		},
		{
			"salary_component": "Employer Pension Scheme",
			"salary_component_abbr": "EREPS",
			"type": "Employer Contribution",
			"description": (
				"Employer's EPS share (A/c 10) = 8.33% of capped PF wages. "
				"Zero for employees who first joined EPF on/after 1 Sept 2014 with PF wage > ₹15,000."
			),
		},
		{
			"salary_component": "Employees Deposit Linked Insurance",
			"salary_component_abbr": "EDLI",
			"type": "Employer Contribution",
			"description": "Employer's EDLI premium (A/c 21) = 0.5% of capped PF wages.",
		},
		{
			"salary_component": "EPF Admin Charges",
			"salary_component_abbr": "EPFADM",
			"type": "Employer Contribution",
			"description": "Employer's EPF administrative charges (A/c 2) = 0.5% of PF wages.",
		},
	]

	for data in components:
		if frappe.db.exists("Salary Component", data["salary_component"]):
			continue
		doc = frappe.new_doc("Salary Component")
		doc.update(data)
		doc.insert(ignore_permissions=True)


def create_income_tax_slabs():
	for name, data in INDIA_TAX_SLABS.items():
		if frappe.db.exists("Income Tax Slab", name):
			continue
		doc = frappe.new_doc("Income Tax Slab")
		doc.name = name
		doc.effective_from = data["effective_from"]
		doc.currency = data["currency"]
		doc.allow_tax_exemption = data["allow_tax_exemption"]
		doc.standard_tax_exemption_amount = data["standard_tax_exemption_amount"]
		doc.tax_relief_limit = data["tax_relief_limit"]
		doc.marginal_relief_limit = data["marginal_relief_limit"]

		for row in data["slabs"]:
			doc.append("slabs", row)
		for row in data["surcharge_slabs"]:
			doc.append("surcharge_slabs", row)
		for row in data["other_taxes_and_charges"]:
			doc.append("other_taxes_and_charges", row)

		doc.insert(ignore_permissions=True)
		doc.submit()
