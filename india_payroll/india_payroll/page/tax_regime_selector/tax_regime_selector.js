// Copyright (c) 2026, Frappe Technologies Pvt. Ltd. and contributors
// For license information, please see license.txt

frappe.pages["tax-regime-selector"].on_page_load = function (wrapper) {
	frappe.require(["tax_regime_selector.bundle.js", "tax_regime_selector.bundle.css"], () => {
		new window.india_payroll.ui.TaxRegimeSelector(wrapper);
		frappe.breadcrumbs.add("India Payroll");
	});
};
