app_name = "india_payroll"
app_title = "India Payroll"
app_publisher = "Frappe Technologies Pvt. Ltd."
app_description = (
	"A Frappe HR extension app to simplify payroll and taxes according to Indian rules and regulations"
)
app_email = "contact@frappe.io"
app_license = "gpl-3.0"

# Apps
# ------------------

required_apps = ["frappe/hrms"]

# Each item in the list will be shown as an app in the apps page
# add_to_apps_screen = [
# 	{
# 		"name": "india_payroll",
# 		"logo": "/assets/india_payroll/logo.png",
# 		"title": "India Payroll",
# 		"route": "/india_payroll",
# 		"has_permission": "india_payroll.api.permission.has_app_permission"
# 	}
# ]

# Includes in <head>
# ------------------

# include js, css files in header of desk.html
# app_include_css = "/assets/india_payroll/css/india_payroll.css"
# app_include_js = "/assets/india_payroll/js/india_payroll.js"

# include js, css files in header of web template
# web_include_css = "/assets/india_payroll/css/india_payroll.css"
# web_include_js = "/assets/india_payroll/js/india_payroll.js"

# include custom scss in every website theme (without file extension ".scss")
# website_theme_scss = "india_payroll/public/scss/website"

# include js, css files in header of web form
# webform_include_js = {"doctype": "public/js/doctype.js"}
# webform_include_css = {"doctype": "public/css/doctype.css"}

# include js in page
# page_js = {"page" : "public/js/file.js"}

# include js in doctype views
doctype_js = {"Salary Structure Assignment": "public/js/salary_structure_assignment.js"}
doctype_list_js = {"Salary Structure Assignment": "public/js/salary_structure_assignment_list.js"}
# doctype_tree_js = {"doctype" : "public/js/doctype_tree.js"}
# doctype_calendar_js = {"doctype" : "public/js/doctype_calendar.js"}

# Svg Icons
# ------------------
# include app icons in desk
# app_include_icons = "india_payroll/public/icons.svg"

# Home Pages
# ----------

# application home page (will override Website Settings)
# home_page = "login"

# website user home page (by Role)
# role_home_page = {
# 	"Role": "home_page"
# }

# Generators
# ----------

# automatically create page for each record of this doctype
# website_generators = ["Web Page"]

# automatically load and sync documents of this doctype from downstream apps
# importable_doctypes = [doctype_1]

# Jinja
# ----------

# add methods and filters to jinja environment
# jinja = {
# 	"methods": "india_payroll.utils.jinja_methods",
# 	"filters": "india_payroll.utils.jinja_filters"
# }

# Installation
# ------------

# before_install = "india_payroll.install.before_install"
after_install = "india_payroll.install.after_install"
after_migrate = "india_payroll.install.after_migrate"

# Uninstallation
# ------------

before_uninstall = "india_payroll.uninstall.before_uninstall"
# after_uninstall = "india_payroll.uninstall.after_uninstall"

# Integration Setup
# ------------------
# To set up dependencies/integrations with other apps
# Name of the app being installed is passed as an argument

# before_app_install = "india_payroll.utils.before_app_install"
# after_app_install = "india_payroll.utils.after_app_install"

# Integration Cleanup
# -------------------
# To clean up dependencies/integrations with other apps
# Name of the app being uninstalled is passed as an argument

# before_app_uninstall = "india_payroll.utils.before_app_uninstall"
# after_app_uninstall = "india_payroll.utils.after_app_uninstall"

# Desk Notifications
# ------------------
# See frappe.core.notifications.get_notification_config

# notification_config = "india_payroll.notifications.get_notification_config"

# Permissions
# -----------
# Permissions evaluated in scripted ways

# permission_query_conditions = {
# 	"Event": "frappe.desk.doctype.event.event.get_permission_query_conditions",
# }
#
# has_permission = {
# 	"Event": "frappe.desk.doctype.event.event.has_permission",
# }

# Document Events
# ---------------
# Hook on document methods and events

# Scheduled Tasks
# ---------------

# scheduler_events = {
# 	"all": [
# 		"india_payroll.tasks.all"
# 	],
# 	"daily": [
# 		"india_payroll.tasks.daily"
# 	],
# 	"hourly": [
# 		"india_payroll.tasks.hourly"
# 	],
# 	"weekly": [
# 		"india_payroll.tasks.weekly"
# 	],
# 	"monthly": [
# 		"india_payroll.tasks.monthly"
# 	],
# }

# Testing
# -------

# before_tests = "india_payroll.install.before_tests"

# Extend DocType Class
# ------------------------------
#
# Specify custom mixins to extend the standard doctype controller.
# extend_doctype_class = {
# 	"Task": "india_payroll.custom.task.CustomTaskMixin"
# }

# Overriding Methods
# ------------------------------
#
# override_whitelisted_methods = {
# 	"frappe.desk.doctype.event.event.get_events": "india_payroll.event.get_events"
# }

# Payroll Hooks
# -------------
# Called during salary slip creation to apply additional deductions (e.g. professional tax)
# override_doctype_class = {
# 	"Salary Slip": "india_payroll.india_payroll.professional_tax.ProfessionalTaxMixin"
# }

# Statutory salary-slip deductions (Professional Tax, ESI, LWF, EPF) are injected
# via the `apply_regional_deductions` regional override below — invoked inside the
# Salary Slip's calculate_net_pay — so they also appear in the preview generated by
# process_salary_structure, not only on save.
doc_events = {
	"Salary Structure Assignment": {
		"validate": "india_payroll.india_payroll.professional_tax.validate_employment_state",
	},
}
#
# each overriding function accepts a `data` argument;
# generated from the base implementation of the doctype dashboard,
# along with any modifications made in other Frappe apps
# override_doctype_dashboards = {
# 	"Task": "india_payroll.task.get_dashboard_data"
# }

# exempt linked doctypes from being automatically cancelled
#
# auto_cancel_exempted_doctypes = ["Auto Repeat"]

# Ignore links to specified DocTypes when deleting documents
# -----------------------------------------------------------

# ignore_links_on_delete = ["Communication", "ToDo"]

# Request Events
# ----------------
# before_request = ["india_payroll.utils.before_request"]
# after_request = ["india_payroll.utils.after_request"]

# Job Events
# ----------
# before_job = ["india_payroll.utils.before_job"]
# after_job = ["india_payroll.utils.after_job"]

# User Data Protection
# --------------------

# user_data_fields = [
# 	{
# 		"doctype": "{doctype_1}",
# 		"filter_by": "{filter_by}",
# 		"redact_fields": ["{field_1}", "{field_2}"],
# 		"partial": 1,
# 	},
# 	{
# 		"doctype": "{doctype_2}",
# 		"filter_by": "{filter_by}",
# 		"partial": 1,
# 	},
# 	{
# 		"doctype": "{doctype_3}",
# 		"strict": False,
# 	},
# 	{
# 		"doctype": "{doctype_4}"
# 	}
# ]

# Authentication and authorization
# --------------------------------

# auth_hooks = [
# 	"india_payroll.auth.validate"
# ]

# Automatically update python controller files with type annotations for this app.
regional_overrides = {
	"India": {
		"hrms.payroll.doctype.income_tax_slab.income_tax_slab.apply_surcharge_with_marginal_relief": "india_payroll.india_payroll.income_tax_utils.apply_surcharge_with_marginal_relief",
		"hrms.payroll.doctype.salary_slip.salary_slip.apply_regional_deductions": "india_payroll.india_payroll.salary_slip.apply_regional_deductions",
	}
}

export_python_type_annotations = True

# Require all whitelisted methods to have type annotations
require_type_annotated_api_methods = True

# default_log_clearing_doctypes = {
# 	"Logging DocType Name": 30  # days to retain logs
# }

# Translation
# ------------
# List of apps whose translatable strings should be excluded from this app's translations.
# ignore_translatable_strings_from = []
