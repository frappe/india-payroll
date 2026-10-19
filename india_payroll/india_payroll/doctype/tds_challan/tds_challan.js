// Copyright (c) 2026, Frappe Technologies Pvt. Ltd. and contributors
// For license information, please see license.txt

frappe.ui.form.on("TDS Challan", {
	refresh(frm) {
		if (frm.is_new() || frm.doc.docstatus === 2) return;

		frm.add_custom_button(__("Check on TRACES"), () => {
			window.india_payroll.tds
				.with_traces_credentials(frm.doc.company, () =>
					frm
						.call({
							method: "check_on_traces",
							doc: frm.doc,
							freeze: true,
							freeze_message: __("Looking up the challan on TRACES..."),
						})
						.then((r) => r.message)
				)
				.then((result) => {
					if (!result) return;
					frm.reload_doc();
					if (result.status === "Not Found") {
						frappe.msgprint({
							title: __("Not on TRACES"),
							indicator: "red",
							message: __(
								"TRACES has no challan with this BSR code, serial number and deposit date. Check the particulars against the bank receipt. If they match, the bank has not reported the payment yet, and the FVU will fail until it does."
							),
						});
					} else if (!result.amount_matches) {
						frappe.msgprint({
							title: __("Amount Differs"),
							indicator: "orange",
							message: __(
								"TRACES has this challan, but its amount does not match Total Deposited."
							),
						});
					} else {
						frappe.show_alert({
							message: __("Challan found on TRACES: {0}", [result.status]),
							indicator: "green",
						});
					}
				});
		});

		if (frm.doc.traces_status) {
			frm.dashboard.add_indicator(
				__("TRACES: {0}", [frm.doc.traces_status]),
				frm.doc.traces_status === "Not Found"
					? "red"
					: Math.abs(frm.doc.traces_amount - frm.doc.deposit_amount) > 1
					? "orange"
					: "green"
			);
		}
	},
});
