import frappe

from india_payroll.install import get_custom_fields

BROKEN_NAMESPACE = "india_payroll."
EXPRESSION_PROPERTIES = (
	"depends_on",
	"read_only_depends_on",
	"mandatory_depends_on",
	"collapsible_depends_on",
)


def execute():
	"""Repair TDS field visibility expressions that referenced a Python namespace.

	The TDS Filing fields shipped with `eval: india_payroll.<helper>(doc)`
	expressions. Those helpers only ever existed in Python, so the client-side
	eval raised a ReferenceError and every affected field threw
	`Invalid "depends_on" expression`, making the India Payroll tab in Payroll
	Settings unusable. The expressions now read `frappe.boot`, but sites that
	already have the fields keep the broken value until it is rewritten.
	"""
	for doctype, fields in get_custom_fields().items():
		for df in fields:
			name = f"{doctype}-{df['fieldname']}"
			if not frappe.db.exists("Custom Field", name):
				continue

			for prop in EXPRESSION_PROPERTIES:
				current = frappe.db.get_value("Custom Field", name, prop)
				if not current or BROKEN_NAMESPACE not in current:
					continue

				frappe.db.set_value("Custom Field", name, prop, df.get(prop))

			clear_broken_property_setters(doctype, df["fieldname"])

		frappe.clear_cache(doctype=doctype)


def clear_broken_property_setters(doctype, fieldname):
	"""Drop Customize Form overrides that carry the same unusable expression.

	A property setter wins over the Custom Field value, so leaving one behind
	would keep the error alive on a customised site.
	"""
	stale = frappe.get_all(
		"Property Setter",
		filters={
			"doc_type": doctype,
			"field_name": fieldname,
			"property": ("in", EXPRESSION_PROPERTIES),
			"value": ("like", f"%{BROKEN_NAMESPACE}%"),
		},
		pluck="name",
	)

	for name in stale:
		frappe.delete_doc("Property Setter", name, ignore_missing=True)
