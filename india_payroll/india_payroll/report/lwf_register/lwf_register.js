// Copyright (c) 2026, Frappe Technologies Pvt. Ltd. and contributors
// For license information, please see license.txt

const MONTHS = [
	"January",
	"February",
	"March",
	"April",
	"May",
	"June",
	"July",
	"August",
	"September",
	"October",
	"November",
	"December",
];

// States that have LWF (must stay in sync with LWF_STATE_CONFIG in lwf.py)
const LWF_STATES = [
	"Haryana",
	"Kerala",
	"Punjab",
	"Chandigarh",
	"Chhattisgarh",
	"Delhi",
	"Goa",
	"Gujarat",
	"Madhya Pradesh",
	"Maharashtra",
	"Odisha",
	"West Bengal",
	"Andhra Pradesh",
	"Karnataka",
	"Tamil Nadu",
	"Telangana",
];

function _yearOptions() {
	const current = new Date().getFullYear();
	const options = [];
	for (let y = current - 3; y <= current + 1; y++) options.push(String(y));
	return options.join("\n");
}

frappe.query_reports["LWF Register"] = {
	filters: [
		{
			fieldname: "company",
			label: __("Company"),
			fieldtype: "Link",
			options: "Company",
			reqd: 1,
			default: frappe.defaults.get_user_default("Company"),
		},
		{
			fieldname: "year",
			label: __("Year"),
			fieldtype: "Select",
			options: _yearOptions(),
			default: String(new Date().getFullYear()),
			reqd: 1,
		},
		{
			fieldname: "month",
			label: __("Month"),
			fieldtype: "Select",
			options: "\n" + MONTHS.join("\n"),
			default: MONTHS[new Date().getMonth()],
		},
		{
			fieldname: "work_state",
			label: __("Work State"),
			fieldtype: "Select",
			options: "\n" + LWF_STATES.join("\n"),
			description: __("Filter by employee work state"),
		},
		{
			fieldname: "deduction_status",
			label: __("Deduction Status"),
			fieldtype: "Select",
			options: "\nDeducted\nNon-Deduction Month\nExempted\nNo LWF State",
		},
	],

	formatter(value, row, column, data, default_formatter) {
		value = default_formatter(value, row, column, data);
		if (column.fieldname === "deduction_status" && data) {
			const colours = {
				Deducted: "green",
				"Non-Deduction Month": "darkgrey",
				Exempted: "blue",
				"No LWF State": "grey",
			};
			const bg = colours[data.deduction_status] || "#888";
			value = `<span class="indicator-pill ${bg}">${data.deduction_status}</span>`;
		}
		if (column.fieldname === "frequency" && data && data.frequency) {
			value = `<span style="font-size:0.85em;color:#555;">${data.frequency}</span>`;
		}
		return value;
	},

	onload(report) {
		report.page.add_inner_button(__("Export for LWF Remittance"), () => {
			const data = frappe.query_report.data;
			if (!data || !data.length) {
				frappe.msgprint(__("No data to export."));
				return;
			}

			const headers = [
				"Employee",
				"Department",
				"Designation",
				"Work State",
				"LWF Frequency",
				"Employee LWF (₹)",
				"Employer LWF (₹)",
				"Total LWF (₹)",
				"Deduction Status",
			];

			const csvRows = [headers.join(",")];
			data.forEach((row) => {
				csvRows.push(
					[
						row.employee || "",
						`"${(row.department || "").replace(/"/g, '""')}"`,
						`"${(row.designation || "").replace(/"/g, '""')}"`,
						`"${(row.work_state || "").replace(/"/g, '""')}"`,
						row.frequency || "",
						flt(row.employee_lwf, 2),
						flt(row.employer_lwf, 2),
						flt(row.total_lwf, 2),
						row.deduction_status || "",
					].join(",")
				);
			});

			const blob = new Blob([csvRows.join("\n")], { type: "text/csv;charset=utf-8;" });
			const url = URL.createObjectURL(blob);
			const a = document.createElement("a");
			const company = frappe.query_report.get_filter_value("company") || "LWF";
			const month = frappe.query_report.get_filter_value("month") || "";
			const year = frappe.query_report.get_filter_value("year") || "";
			a.href = url;
			a.download = `LWF_Register_${company}_${month}_${year}.csv`.replace(/\s+/g, "_");
			a.click();
			URL.revokeObjectURL(url);
		});
	},
};
