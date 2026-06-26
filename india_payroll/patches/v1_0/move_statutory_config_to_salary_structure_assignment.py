import frappe
from frappe.custom.doctype.custom_field.custom_field import create_custom_fields

MOVED_FIELDS = [
	"is_person_with_disability",
	"lwf_exempted",
	"lwf_exemption_reason",
	"epf_applicable",
	"contribute_on_actual_pf_wage",
	"vpf_mode",
	"vpf_percentage",
	"vpf_amount",
]


ORPHANED_EMPLOYEE_FIELDS = [
	"india_payroll_lwf_section",
	"india_payroll_epf_cb",
]


def execute():
	"""Relocate India Payroll statutory config from Employee to Salary Structure Assignment.

	Re-runnable: once the Employee columns are gone, the copy step is skipped and
	the deletions are no-ops.
	"""
	from india_payroll.install import get_custom_fields

	create_custom_fields(get_custom_fields())

	present = [f for f in MOVED_FIELDS if frappe.db.has_column("Employee", f)]
	if present:
		_copy_values_to_assignments(present)

	for fieldname in MOVED_FIELDS + ORPHANED_EMPLOYEE_FIELDS:
		frappe.delete_doc_if_exists("Custom Field", f"Employee-{fieldname}")

	frappe.clear_cache(doctype="Employee")
	frappe.clear_cache(doctype="Salary Structure Assignment")


def _copy_values_to_assignments(fields: list[str]) -> None:
	"""Copy each employee's statutory values onto every Salary Structure Assignment
	they have. The fields were employee-global before, so applying them to all of
	an employee's assignments preserves the previous behaviour across periods."""
	employees = frappe.get_all("Employee", fields=["name", *fields])

	for emp in employees:
		# Only carry over meaningful values; blanks/zeros fall back to SSA defaults.
		values = {f: emp.get(f) for f in fields if emp.get(f)}
		if not values:
			continue

		assignments = frappe.get_all(
			"Salary Structure Assignment",
			filters={"employee": emp.name},
			pluck="name",
		)
		for ssa in assignments:
			frappe.db.set_value("Salary Structure Assignment", ssa, values, update_modified=False)
