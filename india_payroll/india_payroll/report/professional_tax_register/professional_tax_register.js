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

function _yearOptions() {
	const current = new Date().getFullYear();
	const options = [];
	for (let y = current - 3; y <= current + 1; y++) options.push(String(y));
	return options.join("\n");
}

frappe.query_reports["Professional Tax Register"] = {
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
			fieldname: "employment_state",
			label: __("Employment State"),
			fieldtype: "Select",
			description: __("Filter by employee employment state"),
		},
		{
			fieldname: "deduction_status",
			label: __("Deduction Status"),
			fieldtype: "Select",
			options: "\nDeducted\nNil / Exempt\nNo PT State",
		},
	],

	formatter(value, row, column, data, default_formatter) {
		value = default_formatter(value, row, column, data);
		if (column.fieldname === "deduction_status" && data) {
			const colours = {
				Deducted: "green",
				"Nil / Exempt": "blue",
				"No PT State": "grey",
			};
			const bg = colours[data.deduction_status] || "grey";
			value = `<span class="indicator-pill ${bg}">${data.deduction_status}</span>`;
		}
		return value;
	},

	onload(report) {
		// offer every state an assignment can have, including those without PT
		frappe.model.with_doctype("Salary Structure Assignment", () => {
			const field = frappe.meta.get_docfield(
				"Salary Structure Assignment",
				"employment_state"
			);
			const filter = report.get_filter("employment_state");
			filter.df.options = "\n" + (field?.options || "");
			filter.refresh();
		});

		report.page.add_inner_button(__("Export for PT Remittance"), () => {
			const data = frappe.query_report.data;
			if (!data || !data.length) {
				frappe.msgprint(__("No data to export."));
				return;
			}

			const headers = [
				"Employee",
				"Employee Name",
				"Department",
				"Designation",
				"Employment State",
				"PT Frequency",
				"Gross Wages (₹)",
				"Professional Tax (₹)",
				"Deduction Status",
			];

			// a leading =, +, - or @ makes spreadsheets read the text as a formula
			const quote = (value) => {
				const text = (value || "").replace(/^[=+\-@]/, "'$&");
				return `"${text.replace(/"/g, '""')}"`;
			};
			const csvRows = [headers.join(",")];
			data.forEach((row) => {
				csvRows.push(
					[
						row.employee || "",
						quote(row.employee_name),
						quote(row.department),
						quote(row.designation),
						quote(row.employment_state),
						row.frequency || "",
						flt(row.gross_wages, 2),
						flt(row.professional_tax, 2),
						row.deduction_status || "",
					].join(",")
				);
			});

			const blob = new Blob([csvRows.join("\n")], { type: "text/csv;charset=utf-8;" });
			const url = URL.createObjectURL(blob);
			const a = document.createElement("a");
			const company = frappe.query_report.get_filter_value("company") || "PT";
			const month = frappe.query_report.get_filter_value("month") || "";
			const year = frappe.query_report.get_filter_value("year") || "";
			a.href = url;
			a.download = `PT_Register_${company}_${month}_${year}.csv`.replace(/\s+/g, "_");
			a.click();
			URL.revokeObjectURL(url);
		});
	},
};
