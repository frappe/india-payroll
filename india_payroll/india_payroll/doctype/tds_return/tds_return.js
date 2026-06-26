// Copyright (c) 2026, Frappe Technologies Pvt. Ltd. and contributors
// For license information, please see license.txt

frappe.ui.form.on("TDS Return", {
	refresh(frm) {
		if (frm.is_new()) return;

		const status = frm.doc.filing_status || "Draft";

		if (frm.doc.docstatus === 0) {
			frm.add_custom_button(__("Fetch from Payroll"), () => {
				frappe.dom.freeze(__("Fetching salary TDS for the quarter..."));
				frm.call("fetch_from_payroll")
					.then((r) => {
						frappe.dom.unfreeze();
						frm.reload_doc();
						frappe.show_alert({
							message: __("Loaded {0} deductee rows.", [r.message.deductee_rows]),
							indicator: "green",
						});
					})
					.catch(() => frappe.dom.unfreeze());
			});

			const step = (label, method) =>
				frm.add_custom_button(
					label,
					() => {
						frm.call(method).then(() => frm.reload_doc());
					},
					__("File Return")
				);

			step(__("1. Validate"), "validate_return");
			step(__("2. Generate TXT"), "generate_txt");
			step(__("3. Generate FVU"), "generate_fvu");
			step(__("4. E-File"), "file_return");
		}

		if (["Filed", "Accepted"].includes(status) && frm.doc.quarter === "Q4") {
			frm.add_custom_button(__("Create Form 16"), () => {
				frappe.call({
					method: "india_payroll.india_payroll.tds.form16.create_forms_for_return",
					args: { return_name: frm.doc.name },
					freeze: true,
					freeze_message: __("Creating Form 16 records..."),
					callback: (r) => {
						frappe.show_alert({
							message: __("Created {0} Form 16 record(s).", [r.message]),
							indicator: "green",
						});
					},
				});
			});
		}

		frm.dashboard.add_indicator(
			__("Status: {0}", [status]),
			status === "Filed" || status === "Accepted"
				? "green"
				: status === "Failed"
				? "red"
				: "orange"
		);

		render_validation_issues(frm);
	},
});

function render_validation_issues(frm) {
	if (!frm.doc.validation_issues) return;
	let issues;
	try {
		issues = JSON.parse(frm.doc.validation_issues);
	} catch (e) {
		return;
	}
	if (!issues || !issues.length) return;
	const rows = issues
		.map((i) => `<li>${frappe.utils.escape_html(i.message || JSON.stringify(i))}</li>`)
		.join("");
	frm.dashboard.add_comment(
		`<b>${__("Validation issues")}:</b><ul>${rows}</ul>`,
		"yellow",
		true
	);
}
