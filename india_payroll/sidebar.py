import frappe
from frappe.desk.doctype.custom_sidebar.custom_sidebar import (
	_save_customization,
	drop_layers_saying_nothing,
	get_customization,
	layer_arrangement,
)
from frappe.desk.doctype.sidebar.sidebar import item_key


def link(link_type, link_to, label=None):
	return {
		"type": "Link",
		"label": label or link_to,
		"link_type": link_type,
		"link_to": link_to,
		"child": 1,
		"open_in_new_tab": 1,
	}


def section(label, icon=None):
	return {
		"type": "Section Break",
		"label": label,
		"icon": icon,
		"indent": 1,
		"collapsible": 1,
	}


SIDEBAR_LINKS = {
	"Payroll": [
		{
			"after": {"type": "Link", "link_type": "Report", "link_to": "Salary Register"},
			"items": [link("Report", "Bank Mandate Report")],
		},
	],
	"Tax and Benefits": [
		{
			"after": section("Income Tax"),
			"items": [link("Page", "tax-regime-selector", "Tax Regime Selector")],
		},
		{
			"after_section": section("Income Tax"),
			"items": [
				section("Statutory Compliance", "indian-rupee"),
				link("DocType", "Form 16"),
				link("Report", "Employee Provident Fund Register"),
				link("Report", "ESIC Register"),
				link("Report", "LWF Register"),
				link("Report", "Professional Tax Register"),
				link("Report", "Provident Fund Deductions"),
				link("Report", "Professional Tax Deductions"),
			],
		},
	],
}


def add_sidebar_links():
	for module, placements in SIDEBAR_LINKS.items():
		add_module_links(module, placements)


def add_module_links(module, placements):
	arrangement = layer_arrangement(module, None)
	keys = [item_key(item) for item in arrangement]
	inserted = []

	for placement in placements:
		items = placement["items"]
		if all(item_key(row) in keys for row in items):
			continue

		cursor = anchor_position(arrangement, keys, placement)
		if cursor is None:
			cursor = len(arrangement)
			if items[0]["type"] != "Section Break":
				items = [{**row, "child": 0} for row in items]

		for row in items:
			row_key = item_key(row)
			if row_key in keys:
				cursor = keys.index(row_key) + 1
				continue

			arrangement.insert(cursor, {**row, "added": 1})
			keys.insert(cursor, row_key)
			inserted.append(row_key)
			cursor += 1

	if not inserted:
		return

	layer = get_customization(module, None)
	named = {item_key(row) for row in layer.sidebar_items} if layer else set()
	last_named = max(index for index, key in enumerate(keys) if key in named or key in inserted)

	_save_customization(module, arrangement[: last_named + 1], user=None)


def anchor_position(arrangement, keys, placement):
	anchor = placement.get("after") or placement["after_section"]
	anchor_key = item_key(anchor)
	if anchor_key not in keys:
		return None

	position = keys.index(anchor_key) + 1
	if "after_section" in placement:
		while position < len(arrangement) and arrangement[position].get("child"):
			position += 1

	return position


def remove_sidebar_links():
	for module, placements in SIDEBAR_LINKS.items():
		remove_module_links(module, {item_key(row) for placement in placements for row in placement["items"]})


def remove_module_links(module, link_keys):
	layer = get_customization(module, None)
	if not layer:
		return

	layer = frappe.get_doc("Custom Sidebar", layer.name)
	rows = [row for row in layer.sidebar_items if not (row.added and item_key(row) in link_keys)]
	if len(rows) == len(layer.sidebar_items):
		return

	layer.set("sidebar_items", rows)
	layer.save(ignore_permissions=True)
	drop_layers_saying_nothing([layer.name])
