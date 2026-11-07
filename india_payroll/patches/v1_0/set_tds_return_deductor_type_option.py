import frappe

DEFAULT_DEDUCTOR_TYPE = "K - Company"


def execute():
	options = frappe.get_meta("TDS Return").get_field("deductor_type_code").options.split("\n")
	by_code = {option.split(" - ", 1)[0]: option for option in options if option}

	returns = frappe.get_all("TDS Return", fields=["name", "docstatus", "deductor_type_code"])
	for row in returns:
		value = (row.deductor_type_code or "").strip()
		if value and value in options:
			continue

		if value:
			new_value = by_code.get(value.upper())
		elif row.docstatus == 0:
			new_value = DEFAULT_DEDUCTOR_TYPE
		else:
			new_value = None

		if new_value:
			frappe.db.set_value(
				"TDS Return", row.name, "deductor_type_code", new_value, update_modified=False
			)
