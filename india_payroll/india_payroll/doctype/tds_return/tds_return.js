// Copyright (c) 2026, Frappe Technologies Pvt. Ltd. and contributors
// For license information, please see license.txt

// Statuses at or past which validation is no longer outstanding.
const VALIDATION_SETTLED = [
	"Validated",
	"Validation Skipped",
	"TXT Generated",
	"FVU Generated",
	"Filed",
	"Accepted",
];

// Link field -> {field on Address/Contact: field on this return}. Kept in step with
// ADDRESS_AND_CONTACT_MAP in tds_return.py, which fills the same gaps on save.
const ADDRESS_AND_CONTACT_MAP = {
	deductor_address: {
		address_line1: "deductor_flat_door_block_number",
		address_line2: "deductor_road_street",
		county: "deductor_area_locality",
		city: "deductor_district",
		state: "deductor_state",
		pincode: "deductor_postal_code",
		country: "deductor_country",
	},
	deductor_contact: {
		email_id: "deductor_email",
		mobile_no: "deductor_contact_number",
	},
	responsible_person_contact: {
		full_name: "responsible_person_name",
		designation: "responsible_person_designation",
		email_id: "rp_email",
		mobile_no: "rp_contact_number",
	},
	rp_address: {
		address_line1: "rp_flat_door_block_number",
		address_line2: "rp_road_street",
		county: "rp_area_locality",
		city: "rp_district",
		state: "rp_state",
		pincode: "rp_postal_code",
		country: "rp_country",
	},
};

function pull_linked_details(frm, link_field) {
	const source = frm.doc[link_field];
	const mapping = ADDRESS_AND_CONTACT_MAP[link_field];
	if (!source || !mapping) return;

	const doctype = link_field.includes("address") ? "Address" : "Contact";
	frappe.db.get_doc(doctype, source).then((record) => {
		// Selecting a record replaces what it covers; fields it cannot supply
		// (post office, PAN, deductor type) are left untouched.
		Object.entries(mapping).forEach(([from, to]) => {
			frm.set_value(to, record[from] || "");
		});
	});
}

function company_link_query(frm, doctype) {
	const method =
		doctype === "Address"
			? "frappe.contacts.doctype.address.address.address_query"
			: "frappe.contacts.doctype.contact.contact.contact_query";
	return {
		query: method,
		filters: { link_doctype: "Company", link_name: frm.doc.company },
	};
}

frappe.ui.form.on("TDS Return", {
	setup(frm) {
		frm.set_query("deductor_address", () => company_link_query(frm, "Address"));
		frm.set_query("rp_address", () => company_link_query(frm, "Address"));
		frm.set_query("deductor_contact", () => company_link_query(frm, "Contact"));
		frm.set_query("responsible_person_contact", () => company_link_query(frm, "Contact"));
	},

	deductor_address(frm) {
		pull_linked_details(frm, "deductor_address");
	},

	deductor_contact(frm) {
		pull_linked_details(frm, "deductor_contact");
	},

	responsible_person_contact(frm) {
		pull_linked_details(frm, "responsible_person_contact");
	},

	rp_address(frm) {
		pull_linked_details(frm, "rp_address");
	},

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

			if (!frm.doc.csi_file) {
				frm.add_custom_button(
					__("Fetch CSI"),
					() => prompt_csi_otp(frm),
					__("File Return")
				);
			}

			frm.add_custom_button(
				__("Check Challans on TRACES"),
				() => check_challans_on_traces(frm),
				__("File Return")
			);

			if (!VALIDATION_SETTLED.includes(status)) {
				frm.add_custom_button(
					__("Skip Validation"),
					() => prompt_skip_validation(frm),
					__("File Return")
				);
			}
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

// CSI download is OTP-verified against the deductor's TRACES-registered mobile,
// so it runs in two user-driven steps rather than inside the filing job.
function prompt_csi_otp(frm) {
	frappe.prompt(
		[
			{
				fieldname: "mobile_number",
				fieldtype: "Data",
				label: __("TRACES Mobile Number"),
				default: frm.doc.csi_mobile_number,
				reqd: 1,
				description: __("The OTP is sent to this number by TRACES."),
			},
			{
				fieldname: "reason",
				fieldtype: "Small Text",
				label: __("Reason"),
				default: __("CSI file for Form {0} {1} {2} TDS return filing", [
					frm.doc.form_type,
					frm.doc.quarter,
					frm.doc.financial_year,
				]),
				reqd: 1,
				description: __("At least 20 characters, as required by TRACES."),
			},
		],
		(values) => {
			frm.call("request_csi_otp", values).then(() => {
				frappe.prompt(
					[{ fieldname: "otp", fieldtype: "Data", label: __("OTP"), reqd: 1 }],
					(entered) => {
						frm.call("submit_csi_otp", { otp: entered.otp }).then(() => {
							frm.reload_doc();
							frappe.show_alert({
								message: __("CSI file attached."),
								indicator: "green",
							});
						});
					},
					__("Enter the OTP sent by TRACES"),
					__("Download CSI")
				);
			});
		},
		__("Fetch CSI File"),
		__("Send OTP")
	);
}

function check_challans_on_traces(frm) {
	window.india_payroll.tds
		.with_traces_credentials(frm.doc.company, () =>
			frm
				.call({
					method: "check_challans_on_traces",
					doc: frm.doc,
					freeze: true,
					freeze_message: __("Searching TRACES for this quarter's challans..."),
				})
				.then((r) => r.message)
		)
		.then((result) => result && show_traces_result(frm, result));
}

function show_traces_result(frm, result) {
	const fmt = (value) => format_currency(value, "INR");
	const recorded_rows = result.recorded
		.map((c) => {
			const missing = c.traces_status === "Not Found";
			const differs = !missing && Math.abs(c.traces_amount - c.amount) > 1;
			const color = missing ? "red" : differs ? "orange" : "green";
			return `<tr>
				<td><a href="/app/tds-challan/${encodeURIComponent(c.name)}">${frappe.utils.escape_html(
				c.name
			)}</a></td>
				<td>${frappe.datetime.str_to_user(c.deposit_date)}</td>
				<td>${frappe.utils.escape_html(c.bsr_code)} / ${frappe.utils.escape_html(c.challan_serial)}</td>
				<td class="text-right">${fmt(c.amount)}</td>
				<td><span class="indicator-pill ${color}">${frappe.utils.escape_html(
				differs ? __("Amount on TRACES {0}", [fmt(c.traces_amount)]) : c.traces_status
			)}</span></td>
			</tr>`;
		})
		.join("");

	const fields = [
		{
			fieldtype: "HTML",
			fieldname: "recorded",
			options: result.recorded.length
				? `<p class="text-muted">${__("Challans recorded for this quarter")}</p>
				<table class="table table-bordered table-sm">
					<thead><tr><th>${__("Challan")}</th><th>${__("Deposit Date")}</th><th>${__(
						"BSR / Serial"
				  )}</th><th class="text-right">${__("Amount")}</th><th>${__(
						"TRACES"
				  )}</th></tr></thead>
					<tbody>${recorded_rows}</tbody>
				</table>`
				: `<p class="text-muted">${__(
						"No challans are recorded for this quarter yet."
				  )}</p>`,
		},
	];

	if (result.unrecorded.length) {
		fields.push(
			{
				fieldtype: "HTML",
				fieldname: "unrecorded_help",
				options: `<p class="text-muted">${__(
					"On TRACES but not recorded here. Select the ones that belong to this return to create draft challans. Challans for other forms (such as non-salary TDS) are listed too."
				)}</p>`,
			},
			{
				fieldtype: "Table",
				fieldname: "unrecorded",
				cannot_add_rows: true,
				cannot_delete_rows: true,
				in_place_edit: true,
				data: result.unrecorded,
				fields: [
					{
						fieldname: "deposit_date",
						fieldtype: "Date",
						label: __("Deposit Date"),
						in_list_view: 1,
						read_only: 1,
					},
					{
						fieldname: "bsr_code",
						fieldtype: "Data",
						label: __("BSR Code"),
						in_list_view: 1,
						read_only: 1,
					},
					{
						fieldname: "challan_serial",
						fieldtype: "Data",
						label: __("Serial"),
						in_list_view: 1,
						read_only: 1,
					},
					{
						fieldname: "amount",
						fieldtype: "Currency",
						label: __("Amount"),
						in_list_view: 1,
						read_only: 1,
					},
					{
						fieldname: "section",
						fieldtype: "Data",
						label: __("Section"),
						in_list_view: 1,
						read_only: 1,
					},
					{ fieldname: "cin", fieldtype: "Data", label: __("CIN"), read_only: 1 },
					{ fieldname: "status", fieldtype: "Data", label: __("Status"), read_only: 1 },
					{
						fieldname: "minor_head",
						fieldtype: "Data",
						label: __("Minor Head"),
						read_only: 1,
					},
				],
			}
		);
	}

	const dialog = new frappe.ui.Dialog({
		title: __("Challans on TRACES"),
		size: "large",
		fields,
		primary_action_label: result.unrecorded.length ? __("Create Draft Challans") : __("Close"),
		primary_action() {
			if (!result.unrecorded.length) return dialog.hide();

			const selected = dialog.fields_dict.unrecorded.grid.get_selected_children();
			if (!selected.length) {
				frappe.msgprint(__("Select the challans to create."));
				return;
			}
			frm.call({
				method: "import_traces_challans",
				doc: frm.doc,
				args: {
					challans: selected.map((c) => ({
						bsr_code: c.bsr_code,
						challan_serial: c.challan_serial,
						deposit_date: c.deposit_date,
					})),
				},
				freeze: true,
			}).then((r) => {
				dialog.hide();
				frappe.msgprint(
					__("Created draft challans {0}. Review and submit them.", [
						(r.message || [])
							.map((name) => frappe.utils.get_form_link("TDS Challan", name, true))
							.join(", "),
					])
				);
			});
		},
	});
	dialog.show();
}

function prompt_skip_validation(frm) {
	frappe.prompt(
		[
			{
				fieldname: "warning",
				fieldtype: "HTML",
				options: `<div class="alert alert-warning">${__(
					"This skips Sandbox's potential-notice check only. Reconciliation and PAN checks still run, but the return will be filed without Sandbox screening it for issues that could trigger a notice. The reason below is recorded on the return."
				)}</div>`,
			},
			{
				fieldname: "reason",
				fieldtype: "Small Text",
				label: __("Reason for skipping"),
				reqd: 1,
			},
		],
		(values) => {
			frm.call("skip_validation", { reason: values.reason }).then(() => {
				frm.reload_doc();
				frappe.show_alert({
					message: __("Validation skipped. You can now generate the TXT."),
					indicator: "orange",
				});
			});
		},
		__("Skip Sandbox Validation"),
		__("Skip Validation")
	);
}

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
