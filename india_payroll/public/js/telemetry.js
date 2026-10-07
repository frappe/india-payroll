const APP = "india_payroll";

const WORKSPACES = new Set(["India Payroll"]);

const DOCTYPES = new Set(["TDS Return", "TDS Challan", "Form 16"]);

const SETTINGS_DOCTYPES = new Set(["Payroll Settings"]);

const REPORTS = new Set([
	"Bank Mandate Report",
	"Employee Provident Fund Register",
	"ESIC Register",
	"LWF Register",
]);

const PAGES = new Set(["tax-regime-selector"]);

function capture(event, props) {
	if (!frappe.telemetry?.enabled) return;
	try {
		frappe.telemetry.capture(event, APP, props || {});
	} catch (e) {
		// telemetry must never break navigation
	}
}

function classify(route) {
	if (!route || !route.length) return null;
	const [head, target, name] = route;

	if (head === "Workspaces") {
		const workspace = route[route.length - 1];
		return WORKSPACES.has(workspace)
			? { event: "viewed_workspace", props: { workspace } }
			: null;
	}

	if (head === "List" && DOCTYPES.has(target)) {
		return { event: "viewed_list", props: { doctype: target, view: route[2] || "List" } };
	}

	if (head === "Form" && (DOCTYPES.has(target) || SETTINGS_DOCTYPES.has(target))) {
		const is_new = typeof name === "string" && name.startsWith("new-");
		return { event: is_new ? "started_creating" : "viewed_form", props: { doctype: target } };
	}

	if ((head === "query-report" || head === "report") && REPORTS.has(target)) {
		return { event: "viewed_report", props: { report: target } };
	}

	if (PAGES.has(head)) {
		return { event: "viewed_page", props: { page: head } };
	}

	return null;
}

let pending_draft = null;

function count_filled_fields(doctype, draft) {
	try {
		const meta = frappe.get_meta(doctype);
		if (!meta) return null;
		return meta.fields.filter((df) => {
			if (frappe.model.no_value_type.includes(df.fieldtype)) return false;
			const value = draft[df.fieldname];
			return value !== undefined && value !== null && value !== "";
		}).length;
	} catch (e) {
		return null;
	}
}

function resolve_pending_draft(route) {
	if (!pending_draft) return;

	const { doctype, name } = pending_draft;
	if (route[0] === "Form" && route[1] === doctype && route[2] === name) return;

	const draft = locals?.[doctype]?.[name];
	if (draft) {
		capture("creation_abandoned", {
			doctype,
			fields_filled: count_filled_fields(doctype, draft),
		});
	}
	pending_draft = null;
}

function remember_pending_draft(route) {
	const [head, doctype, name] = route;
	const is_new =
		head === "Form" &&
		DOCTYPES.has(doctype) &&
		typeof name === "string" &&
		name.startsWith("new-");

	pending_draft = is_new ? { doctype, name } : null;
}

function track_route() {
	const route = frappe.get_route() || [];
	resolve_pending_draft(route);

	const hit = classify(route);
	if (hit) capture(hit.event, hit.props);

	remember_pending_draft(route);
}

$(document).on("app_ready", function () {
	if (!frappe.telemetry?.enabled) return;

	frappe.after_ajax(() => {
		track_route();
		frappe.router.on("change", track_route);
	});

	window.addEventListener("pagehide", () => resolve_pending_draft([]), { capture: true });
});
