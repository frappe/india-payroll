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

frappe.query_reports["ESIC Register"] = {
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
			fieldname: "contribution_period",
			label: __("Contribution Period"),
			fieldtype: "Select",
			options: "\nApr-Sep\nOct-Mar",
			description: __("Overrides the Month filter when set"),
		},
		{
			fieldname: "coverage_status",
			label: __("Coverage Status"),
			fieldtype: "Select",
			options: "\nCovered\nPwD\nExempt",
		},
	],

	formatter(value, row, column, data, default_formatter) {
		value = default_formatter(value, row, column, data);
		if (column.fieldname === "coverage_status" && data) {
			const colours = { Covered: "green", PwD: "blue", Exempt: "grey" };
			const bg = colours[data.coverage_status] || "grey";
			value = `<span class="indicator-pill ${bg}">${data.coverage_status}</span>`;
		}
		return value;
	},

	onload(report) {
		report.page.add_inner_button(__("Export for ESIC Portal"), () => {
			const data = frappe.query_report.data;
			if (!data || !data.length) {
				frappe.msgprint(__("No data to export."));
				return;
			}

			const headers = [
				"Employee",
				"ESIC IP Number",
				"Gross Wages",
				"Employee Contribution (0.75%)",
				"Employer Contribution (3.25%)",
				"Total Contribution (4%)",
				"Coverage Status",
			];

			const csvRows = [headers.join(",")];
			data.forEach((row) => {
				csvRows.push(
					[
						row.employee || "",
						row.esic_card_no || "",
						flt(row.gross_wages, 2),
						flt(row.employee_esi, 2),
						flt(row.employer_esi, 2),
						flt(row.total_esi, 2),
						row.coverage_status || "",
					].join(",")
				);
			});

			const blob = new Blob([csvRows.join("\n")], { type: "text/csv;charset=utf-8;" });
			const url = URL.createObjectURL(blob);
			const a = document.createElement("a");
			const filters = frappe.query_report.filters;
			const company = frappe.query_report.get_filter_value("company") || "ESIC";
			const month = frappe.query_report.get_filter_value("month") || "";
			const year = frappe.query_report.get_filter_value("year") || "";
			a.href = url;
			a.download = `ESIC_Register_${company}_${month}_${year}.csv`.replace(/\s+/g, "_");
			a.click();
			URL.revokeObjectURL(url);
		});
	},
};
