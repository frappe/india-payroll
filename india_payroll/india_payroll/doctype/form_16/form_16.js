// Copyright (c) 2026, Frappe Technologies Pvt. Ltd. and contributors
// For license information, please see license.txt

frappe.ui.form.on("Form 16", {
	refresh(frm) {
		if (frm.is_new()) return;

		frm.add_custom_button(__("Generate Part B"), () => {
			frm.call("generate_part_b").then(() => frm.reload_doc());
		});

		frm.add_custom_button(__("Request Part A (TRACES)"), () => {
			frm.call("request_part_a").then(() => frm.reload_doc());
		});
	},
});
