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

const QUARTERS = ["Jan-Mar", "Apr-Jun", "Jul-Sep", "Oct-Dec"];

frappe.ui.form.on("Professional Tax Return", {
	setup(frm) {
		set_options_for_year(frm);
		set_options_for_month_or_quarter(frm);
	},

	refresh(frm) {
		frm.disable_save();

		frm.page.set_primary_action(__("Generate"), () => generate_report(frm));
	},

	filing_frequency(frm) {
		set_options_for_year(frm);
		set_options_for_month_or_quarter(frm);
		clear_report(frm);
	},

	year(frm) {
		set_options_for_month_or_quarter(frm);
		clear_report(frm);
	},

	company(frm) {
		clear_report(frm);
	},

	professional_tax_state(frm) {
		clear_report(frm);
	},

	month_or_quarter(frm) {
		clear_report(frm);
	},
});

function generate_report(frm) {
	const { company, year, month_or_quarter, professional_tax_state, filing_frequency } = frm.doc;

	if (!company || !year || !month_or_quarter || !professional_tax_state || !filing_frequency) {
		frappe.msgprint(__("Please fill all filter fields before generating the report."));
		return;
	}

	frappe.call({
		method: "india_payroll.india_payroll.doctype.professional_tax_return.professional_tax_return.get_report_data",
		args: { company, year, month_or_quarter, professional_tax_state, filing_frequency },
		freeze: true,
		freeze_message: __("Generating Professional Tax Return..."),
		callback(r) {
			if (!r.message) return;

			const html = frappe.render_template("professional_tax_return", {
				data: r.message,
			});

			const wrapper =
				frm.fields_dict.pt_return_html && frm.fields_dict.pt_return_html.wrapper;
			if (wrapper) $(wrapper).html(html);
		},
	});
}

function clear_report(frm) {
	const wrapper = frm.fields_dict.pt_return_html && frm.fields_dict.pt_return_html.wrapper;
	if (wrapper) $(wrapper).empty();
}

function set_options_for_year(frm) {
	const today = new Date();
	const current_month_idx = today.getMonth(); // 0-indexed
	let current_year = today.getFullYear();
	const start_year = 2018;
	const year_range = current_year - start_year + 1;

	const options = Array.from({ length: year_range }, (_, index) =>
		(start_year + year_range - index - 1).toString()
	);

	// If we are in the first month of a new filing period, the "current"
	// returnable period still belongs to the previous year.
	if (
		(frm.doc.filing_frequency === "Monthly" && current_month_idx === 0) ||
		(frm.doc.filing_frequency === "Quarterly" && current_month_idx < 3)
	) {
		current_year--;
	}

	frm.get_field("year").set_data(options);
	frm.set_value("year", current_year.toString());
}

function set_options_for_month_or_quarter(frm) {
	const today = new Date();
	const current_year = String(today.getFullYear());
	const current_month_idx = today.getMonth(); // 0-indexed
	const current_quarter_idx = Math.floor(current_month_idx / 3); // 0-indexed

	if (!frm.doc.year) frm.doc.year = current_year;

	const is_monthly = frm.doc.filing_frequency !== "Quarterly";
	let options;

	if (is_monthly) {
		if (frm.doc.year === current_year) {
			// Only months up to and including the current month
			options = MONTHS.slice(0, current_month_idx + 1);
		} else {
			options = [...MONTHS];
		}
	} else {
		if (frm.doc.year === current_year) {
			// Only quarters up to and including the current quarter
			options = QUARTERS.slice(0, current_quarter_idx + 1);
		} else {
			options = [...QUARTERS];
		}
	}

	set_field_options("month_or_quarter", options);

	// Default: second-last option for current year (last completed period),
	// otherwise last option (most recent period in a past year).
	if (frm.doc.year === current_year && options.length > 1) {
		frm.set_value("month_or_quarter", options[options.length - 2]);
	} else {
		frm.set_value("month_or_quarter", options[options.length - 1]);
	}
}
