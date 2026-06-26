import frappe

WORKSPACE_NAME = "Tax & Benefits"
PAGE_ROUTE = "tax-regime-selector"


def execute():
	"""India Payroll now ships its own Workspace, so the Tax Regime Selector link
	previously injected into HRMS's 'Tax & Benefits' Workspace and its Sidebar by
	install.py is an orphan. Remove it from existing installs (new installs never
	get it). Idempotent."""
	remove_workspace_link()
	remove_sidebar_item()


def remove_workspace_link():
	if not frappe.db.exists("Workspace", WORKSPACE_NAME):
		return

	workspace = frappe.get_doc("Workspace", WORKSPACE_NAME)
	link = next(
		(l for l in workspace.links if l.link_type == "Page" and l.link_to == PAGE_ROUTE),
		None,
	)
	if not link:
		return

	workspace.links.remove(link)

	# Decrement the 'Tax Setup' card's child count since we removed one of its links.
	for row in workspace.links:
		if row.type == "Card Break" and row.label == "Tax Setup":
			row.link_count = max((row.link_count or 0) - 1, 0)
			break

	for i, row in enumerate(workspace.links):
		row.idx = i + 1

	workspace.save(ignore_permissions=True)


def remove_sidebar_item():
	if not frappe.db.exists("Workspace Sidebar", WORKSPACE_NAME):
		return

	sidebar = frappe.get_doc("Workspace Sidebar", WORKSPACE_NAME)
	item = next(
		(i for i in sidebar.items if i.link_type == "Page" and i.link_to == PAGE_ROUTE),
		None,
	)
	if not item:
		return

	sidebar.items.remove(item)
	for i, row in enumerate(sidebar.items):
		row.idx = i + 1

	sidebar.save(ignore_permissions=True)
