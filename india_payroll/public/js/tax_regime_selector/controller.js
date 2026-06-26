// Copyright (c) 2026, Frappe Technologies Pvt. Ltd. and contributors
// For license information, please see license.txt

import { mainLayout, pageShell, placeholder } from "./templates";
import { setupDeclarationTable } from "./declarations";
import { renderComparison } from "./comparison";

const METHOD = "india_payroll.india_payroll.page.tax_regime_selector.tax_regime_selector";

export class TaxRegimeSelector {
	constructor(wrapper) {
		this.wrapper = wrapper;
		this.page = frappe.ui.make_app_page({
			parent: wrapper,
			title: __("Tax Regime Selector"),
			single_column: true,
		});
		this.employee_data = null;
		this.declarations = {};
		this.rent_monthly = 0;
		this.city_type = "non-metro";
		this.annual_gross = 0;
		this.selected_slab = null;
		this.setup_employee_filter();
	}

	setup_employee_filter() {
		$(this.page.main).html(pageShell());

		this.payroll_period_control = frappe.ui.form.make_control({
			df: {
				fieldtype: "Link",
				options: "Payroll Period",
				fieldname: "payroll_period",
				label: __("Payroll Period"),
				reqd: 1,
				read_only: 1,
			},
			parent: document.getElementById("payroll-period-wrapper"),
			render_input: true,
		});
		this.payroll_period_control.refresh();

		this.employee_control = frappe.ui.form.make_control({
			df: {
				fieldtype: "Link",
				options: "Employee",
				fieldname: "employee",
				label: __("Employee"),
				placeholder: __("Select Employee"),
				reqd: 1,
				change: () => {
					const employee = this.employee_control.get_value();
					if (employee && employee !== this._current_employee) {
						this._current_employee = employee;
						this.load_employee(employee);
					} else {
						this.annual_gross_control.set_value("");
						this.render_placeholder();
					}
				},
			},
			parent: document.getElementById("employee-field-wrapper"),
			render_input: true,
		});
		this.employee_control.refresh();

		this.annual_gross_control = frappe.ui.form.make_control({
			df: {
				fieldtype: "Currency",
				fieldname: "annual_gross_earning",
				label: __("Annual Gross Earning"),
				reqd: 1,
				change: () => {
					const val = flt(this.annual_gross_control.get_value());
					if (val > 0) {
						this.annual_gross = val;
						if (this.employee_data) this.compute();
					}
				},
			},
			parent: document.getElementById("annual-gross-wrapper"),
			render_input: true,
		});
		this.annual_gross_control.refresh();

		this.fetch_payroll_period();
		frappe.call({ method: `${METHOD}.setup_if_missing` });

		// Default to the logged-in user's employee, if any.
		window.hrms?.get_current_employee?.().then((employee) => {
			if (employee) this.employee_control.set_value(employee);
		});
	}

	fetch_payroll_period() {
		frappe.call({
			method: `${METHOD}.get_current_payroll_period`,
			callback: (r) => {
				if (!r.message?.payroll_period) {
					frappe.msgprint({
						title: __("Payroll Period Not Found"),
						message: __(
							"No active Payroll Period found for today's date. Please create a Payroll Period that includes today before proceeding."
						),
						indicator: "red",
						primary_action: {
							label: __("Create Payroll Period"),
							action: () => frappe.new_doc("Payroll Period"),
						},
					});
					return;
				}
				this.payroll_period_control.set_value(r.message.payroll_period);
			},
		});
	}

	render_placeholder() {
		this.page.clear_primary_action();
		$("#comparison-alert").html("");
		$("#page-body").html(placeholder());
	}

	load_employee(employee) {
		frappe.call({
			method: `${METHOD}.get_employee_details`,
			args: { employee },
			callback: (r) => {
				this.employee_data = r.message;
				this.rent_monthly = 0;
				this.city_type = "non-metro";
				this.annual_gross = flt(r.message.annual_gross);
				// Reflect the employee's current regime on load; stage starts at saved.
				this.selected_slab = r.message.current_income_tax_slab || null;
				this.staged_slab = this.selected_slab;
				// Submitted SSAs are read-only (radios + Save Regime hidden).
				this.is_submitted = r.message.ssa_docstatus === 1;

				// Prefill statutory deductions (EPF, employee NPS) matched on the server.
				this._prefill = r.message.prefill_declarations || {};
				this.declarations = {};
				Object.entries(this._prefill).forEach(([section, subs]) => {
					const total = Object.values(subs).reduce((a, b) => a + flt(b), 0);
					if (total > 0) this.declarations[section] = total;
				});

				this.payroll_period_control.set_value(r.message.payroll_period);
				this.annual_gross_control.set_value(r.message.annual_gross);
				frappe.call({
					method: `${METHOD}.compute_tax_comparison`,
					args: {
						employee,
						declarations: this.declarations,
						rent_monthly: 0,
						city_type: "non-metro",
						annual_gross: this.annual_gross,
					},
					callback: (comp) => {
						this.render_page();
						this.render_comparison(comp.message);
					},
				});
			},
		});
	}

	render_page() {
		const is_senior = this.employee_data.is_senior_citizen;
		const categories = (this.employee_data.exemption_categories || []).filter((c) => {
			if (this.employee_data.has_hra && c.name === "80GG") return false;
			if (c.name === "80CCD(2)") return false;
			if (is_senior && c.name === "80TTA") return false;
			if (!is_senior && c.name === "80TTB") return false;
			return true;
		});
		this._cat_map = Object.fromEntries(categories.map((c) => [c.name, c]));
		this._declaration_cats = categories;

		$("#page-body").html(mainLayout(this.employee_data.has_hra));
		this.bind_input_events();
		setupDeclarationTable(this);

		// The card scrolls as a whole; the heading row is sticky at the top and the table
		// header sticks right below it. Publish the heading's height so the thead's sticky
		// offset (--trs-header-h) parks it under the heading instead of overlapping.
		const card = document.querySelector("#page-body .frappe-card");
		const heading = card?.firstElementChild;
		if (card && heading) card.style.setProperty("--trs-header-h", heading.offsetHeight + "px");
	}

	bind_input_events() {
		// Namespaced + .off first so repeated employee loads don't stack handlers.
		const $body = $("#page-body").off(".trs");

		$body.on("change.trs", ".regime-input", () => {
			this.collect_section10_inputs();
			this.compute();
		});

		$body.on("change.trs", ".declaration-input", (e) => {
			const el = e.currentTarget;
			const section = $(el).data("section");
			const sub = $(el).data("sub");
			const amount = flt($(el).val());
			if (amount > 0) {
				this._sub_amounts[section][sub] = amount;
			} else {
				delete this._sub_amounts[section][sub];
			}
			const total = Object.values(this._sub_amounts[section]).reduce((a, b) => a + b, 0);
			if (total > 0) {
				this.declarations[section] = total;
			} else {
				delete this.declarations[section];
			}
			if (this._total_cells?.[section]) {
				this._total_cells[section].textContent = this.fmt(total);
				this._total_cells[section].style.fontWeight = total > 0 ? "600" : "";
			}
			this.compute();
		});

		$body.on("click.trs", ".declare-btn", () => this.declare_exemptions());

		// Radios stage the choice; the toolbar's Save Regime action commits it.
		$body.on("change.trs", ".regime-radio", (e) => {
			this.staged_slab = $(e.currentTarget).val();
			if (this._last_result) this.render_comparison(this._last_result);
		});
	}

	save_regime() {
		const slab = this.staged_slab;
		if (!slab) return;
		const employee = this.employee_control.get_value();
		frappe.confirm(__("Set income tax slab to {0} for {1}?", [slab, employee]), () => {
			frappe.call({
				method: `${METHOD}.set_tax_regime`,
				args: { employee, income_tax_slab: slab },
				callback: (r) => {
					const assignment = r.message?.assignment;
					const link = assignment
						? `<a href="${frappe.utils.get_form_link(
								"Salary Structure Assignment",
								assignment
						  )}">${assignment}</a>`
						: "";
					frappe.show_alert({
						message: __("Tax Regime updated in Salary Structure Assignment {0}", [
							link,
						]),
						indicator: "green",
					});
					this.selected_slab = slab;
					if (this._last_result) this.render_comparison(this._last_result);
				},
			});
		});
	}

	collect_section10_inputs() {
		$("#page-body")
			.find(".regime-input")
			.each((_, el) => {
				const key = $(el).data("key");
				if (key === "rent_monthly") {
					this.rent_monthly = flt($(el).val());
				} else if (key === "city_type") {
					this.city_type = $(el).is(":checked") ? "metro" : "non-metro";
				}
			});
	}

	compute() {
		const employee = this.employee_control.get_value();
		frappe.call({
			method: `${METHOD}.compute_tax_comparison`,
			args: {
				employee,
				declarations: this.declarations,
				rent_monthly: this.rent_monthly,
				city_type: this.city_type,
				annual_gross: this.annual_gross,
			},
			callback: (r) => {
				this._last_result = r.message;
				this.render_comparison(r.message);
			},
		});
	}

	render_comparison(result) {
		renderComparison(this, result);
	}

	declare_exemptions() {
		const employee = this.employee_control.get_value();
		const payroll_period = this.payroll_period_control.get_value();
		const entries = [];
		Object.entries(this._sub_amounts || {}).forEach(([section, subs]) => {
			Object.entries(subs).forEach(([sub_name, amount]) => {
				if (amount > 0) {
					entries.push({
						exemption_category: section,
						exemption_sub_category: sub_name,
						amount,
					});
				}
			});
		});

		const rent_monthly = this.rent_monthly;
		const city_type = this.city_type;
		const has_hra = this.employee_data?.has_hra;

		frappe.route_hooks.after_load = (frm) => {
			if (frm.doctype !== "Employee Tax Exemption Declaration") return;
			frm.set_value("employee", employee);
			frm.set_value("payroll_period", payroll_period);
			if (has_hra && rent_monthly > 0) {
				frm.set_value("monthly_house_rent", rent_monthly);
				frm.set_value("rented_in_metro_city", city_type === "metro" ? 1 : 0);
			}
			entries.forEach((e) => {
				const row = frappe.model.add_child(frm.doc, "declarations");
				row.exemption_category = e.exemption_category;
				row.exemption_sub_category = e.exemption_sub_category;
				row.amount = e.amount;
			});
			frm.refresh_field("declarations");
			delete frappe.route_hooks.after_load;
		};
		frappe.new_doc("Employee Tax Exemption Declaration");
	}

	fmt(n) {
		return "₹" + Math.round(n || 0).toLocaleString("en-IN");
	}
}
