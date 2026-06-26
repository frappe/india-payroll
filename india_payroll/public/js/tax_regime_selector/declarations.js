// Copyright (c) 2026, Frappe Technologies Pvt. Ltd. and contributors
// For license information, please see license.txt

// Builds and wires the Chapter VI-A deductions table (prefill, section totals,
// collapse/expand, search). Operates on the controller instance (`ctrl`).

export function setupDeclarationTable(ctrl) {
	const categories = ctrl._declaration_cats;
	if (!categories.length) return;

	// Seed per-section amounts from the server-matched prefill (EPF, NPS).
	const prefill = ctrl._prefill || {};
	ctrl._sub_amounts = {};
	categories.forEach((cat) => {
		ctrl._sub_amounts[cat.name] = { ...(prefill[cat.name] || {}) };
	});

	const expanded_icon = frappe.utils.icon("es-line-down", "sm");
	const collapsed_icon = frappe.utils.icon("chevron-right", "sm");

	const rows = categories
		.map((cat) => {
			const sub_rows = (cat.sub_categories || [])
				.map(
					(sub) => `
			<tr class="section-sub-row" data-section-parent="${cat.name}"
				style="height:64px; border-bottom:1px solid var(--border-color);">
				<td style="padding:0 12px 0 32px;">${sub.name}</td>
				<td class="text-right text-muted" style="padding:0 12px;">${
					sub.max_amount ? ctrl.fmt(sub.max_amount) : ""
				}</td>
				<td class="text-right" style="padding:0 12px;">
					<input type="number" class="form-control input-sm declaration-input"
						data-section="${cat.name}"
						data-sub="${sub.name}"
						${sub.max_amount ? `max="${sub.max_amount}"` : ""}
						min="0"
						style="width:100%; text-align:right; -moz-appearance:textfield;">
				</td>
			</tr>`
				)
				.join("");

			const desc = cat.description
				? `<div class="help-box small text-extra-muted" style="padding-left:0; margin-bottom:0;">${cat.description}</div>`
				: "";

			return `
			<tr class="section-header-row" data-section-id="${cat.name}"
				style="border-bottom:1px solid var(--border-color); cursor:pointer;">
				<td style="padding:14px 12px;">
					<div style="display:flex; align-items:flex-start; gap:4px;">
						<span class="section-toggle" style="display:inline-flex; flex-shrink:0; width:16px; margin-top:2px; transition:transform 0.15s;">${expanded_icon}</span>
						<div>
							<strong>${cat.name}</strong>
							${desc}
						</div>
					</div>
				</td>
				<td class="text-right text-muted" style="padding:14px 12px; vertical-align:top;">${
					cat.max_amount ? ctrl.fmt(cat.max_amount) : "-"
				}</td>
				<td class="text-right" data-total-for="${
					cat.name
				}" style="padding:14px 12px; vertical-align:top; color:var(--text-muted);">${ctrl.fmt(
				0
			)}</td>
			</tr>
			${sub_rows}`;
		})
		.join("");

	const wrapper = document.getElementById("declaration-table-wrapper");
	wrapper.innerHTML = `
		<style>
			.declaration-input::-webkit-outer-spin-button,
			.declaration-input::-webkit-inner-spin-button { -webkit-appearance: none; margin: 0; }
		</style>
		<table style="width:100%; table-layout:fixed; border-collapse:collapse;" class="declaration-table">
			<colgroup>
				<col style="width:50%">
				<col style="width:20%">
				<col style="width:30%">
			</colgroup>
			<thead>
				<tr>
					<th style="padding:10px 12px; font-weight:500; color:var(--text-muted); position:sticky; top:calc(var(--trs-header-h, 0px) - 1px); z-index:1; background:var(--gray-50); box-shadow:inset 0 -2px 0 var(--border-color);">${__(
						"Deduction / Investment"
					)}</th>
					<th style="padding:10px 12px; font-weight:500; color:var(--text-muted); text-align:right; position:sticky; top:calc(var(--trs-header-h, 0px) - 1px); z-index:1; background:var(--gray-50); box-shadow:inset 0 -2px 0 var(--border-color);">${__(
						"Limit"
					)}</th>
					<th style="padding:10px 12px; font-weight:500; color:var(--text-muted); text-align:right; position:sticky; top:calc(var(--trs-header-h, 0px) - 1px); z-index:1; background:var(--gray-50); box-shadow:inset 0 -2px 0 var(--border-color);">${__(
						"Declared Amount"
					)}</th>
				</tr>
			</thead>
			<tbody>${rows}</tbody>
		</table>`;

	ctrl._total_cells = {};
	wrapper.querySelectorAll("[data-total-for]").forEach((td) => {
		ctrl._total_cells[td.dataset.totalFor] = td;
	});

	// Reflect prefilled (EPF/NPS) amounts into the sub-category inputs and section totals.
	Object.entries(ctrl._sub_amounts).forEach(([section, subs]) => {
		Object.entries(subs).forEach(([sub, amount]) => {
			const input = wrapper.querySelector(
				`.declaration-input[data-section="${section}"][data-sub="${sub}"]`
			);
			if (input) input.value = amount;
		});
		const total = Object.values(subs).reduce((a, b) => a + flt(b), 0);
		if (ctrl._total_cells[section]) {
			ctrl._total_cells[section].textContent = total > 0 ? ctrl.fmt(total) : ctrl.fmt(0);
			ctrl._total_cells[section].style.fontWeight = total > 0 ? "600" : "";
		}
	});

	const search = document.getElementById("declaration-search");
	if (search) {
		search.addEventListener("input", (e) => filterDeclarations(e.target.value));
	}

	const section_states = new Map();

	$(wrapper).on("click", ".section-header-row", function () {
		const section_id = $(this).data("section-id");
		const is_collapsed = section_states.get(section_id) || false;
		const $row = $(this);
		const sub_rows = $row.nextUntil(".section-header-row");
		const toggle = $row.find(".section-toggle");
		if (is_collapsed) {
			sub_rows.show();
			toggle.html(expanded_icon);
			section_states.set(section_id, false);
		} else {
			sub_rows.hide();
			toggle.html(collapsed_icon);
			section_states.set(section_id, true);
		}
	});

	document.getElementById("expand-all-btn").addEventListener("click", () => {
		wrapper.querySelectorAll(".section-sub-row").forEach((r) => $(r).show());
		wrapper.querySelectorAll(".section-toggle").forEach((t) => {
			t.innerHTML = expanded_icon;
		});
		section_states.forEach((_, k) => section_states.set(k, false));
	});
	document.getElementById("collapse-all-btn").addEventListener("click", () => {
		wrapper.querySelectorAll(".section-sub-row").forEach((r) => $(r).hide());
		wrapper.querySelectorAll(".section-toggle").forEach((t) => {
			t.innerHTML = collapsed_icon;
		});
		section_states.forEach((_, k) => section_states.set(k, true));
	});
}

export function filterDeclarations(query) {
	const q = (query || "").trim().toLowerCase();
	const wrapper = document.getElementById("declaration-table-wrapper");
	if (!wrapper) return;

	let any_section_visible = false;
	wrapper.querySelectorAll(".section-header-row").forEach((header) => {
		const $header = $(header);
		const sub_rows = $header.nextUntil(".section-header-row");
		const header_text = $header.text().toLowerCase();
		const header_match = header_text.includes(q);

		let any_sub_match = false;
		sub_rows.each((_, sub) => {
			// A sub is visible if it matches, or its section header matches.
			const match = !q || header_match || $(sub).text().toLowerCase().includes(q);
			$(sub).toggle(match);
			if (match) any_sub_match = true;
		});

		const header_visible = !q || header_match || any_sub_match;
		$header.toggle(header_visible);
		if (header_visible) any_section_visible = true;
	});

	$("#declaration-empty").toggle(!any_section_visible);
}
