// Copyright (c) 2026, Frappe Technologies Pvt. Ltd. and contributors
// For license information, please see license.txt

// India Payroll has no workspace, so frappe.breadcrumbs cannot natively show a
// "parent > page" trail here: a Custom breadcrumb renders a single link, and
// frappe.container.change_to() re-runs breadcrumbs.update() on every show (and on
// cold load), which re-renders only the single stored crumb - dropping anything we
// append afterwards. So wrap update() once: whenever this page is active, rebuild the
// breadcrumb deterministically as Home > Tax & Benefits > Tax Regime Selector. This
// survives a hard refresh and in-app navigation alike.
(function patch_tax_regime_breadcrumb() {
	if (frappe.breadcrumbs._trs_patched) return;
	frappe.breadcrumbs._trs_patched = true;

	const orig_update = frappe.breadcrumbs.update.bind(frappe.breadcrumbs);
	frappe.breadcrumbs.update = function () {
		orig_update();
		if (frappe.get_route_str() !== "tax-regime-selector") return;
		this.$breadcrumbs = $(".navbar-breadcrumbs").empty();
		this.append_breadcrumb_element("/desk", frappe.utils.icon("home"));
		this.append_breadcrumb_element(
			"/app/" + frappe.router.slug("Tax & Benefits"),
			__("Tax & Benefits")
		);
		this.append_breadcrumb_element("", __("Tax Regime Selector"));
		this.toggle(true);
	};
})();

frappe.pages["tax-regime-selector"].on_page_load = function (wrapper) {
	frappe.require(["tax_regime_selector.bundle.js", "tax_regime_selector.bundle.css"], () => {
		new window.india_payroll.ui.TaxRegimeSelector(wrapper);
		// The .navbar-breadcrumbs element lives in the page header built by
		// make_app_page (run inside the controller above). On a cold load this require
		// callback is async and runs after on_page_show, so the breadcrumb element does
		// not exist yet when on_page_show fires - render it now that it does.
		frappe.breadcrumbs.update();
	});
};

// Re-render on every show (covers in-app revisits, where the controller above is not
// re-instantiated). The wrapped update() builds the trail.
frappe.pages["tax-regime-selector"].on_page_show = function () {
	frappe.breadcrumbs.update();
};
