// Copyright (c) 2026, Frappe Technologies Pvt. Ltd. and contributors
// For license information, please see license.txt

// Renders the right-side comparison panel: the two regime radio cards, the
// savings banner, and the old-vs-new breakdown table. Operates on the
// controller instance (`ctrl`).

export function renderComparison(ctrl, result) {
	const old = result.old_regime;
	const new_ = result.new_regime;
	const old_wins = result.recommended === "old";
	const savings = result.savings;

	const is_tie = savings === 0;

	// A regime choice rendered as a radio card. Picking it stages the choice; the
	// staged card gets a colored border (green, or blue on a tie). Label precedence
	// is Saved > Suggested/Default. The regime in force is the saved choice (when
	// it still matches a current card), or - on a submitted/locked SSA with nothing
	// valid saved - New Regime as the system default.
	const regime_card = (label, regime, is_winner, tie_pill) => {
		const is_staged = ctrl.staged_slab === regime.slab;
		// The saved regime, but only if it still matches one of the two cards. A blank
		// field - or a stale/unknown slab name - counts as "nothing saved".
		const saved_slab =
			ctrl.selected_slab === old.slab || ctrl.selected_slab === new_.slab
				? ctrl.selected_slab
				: null;
		// Drafts keep no default so the employee actively picks one.
		const effective_slab = saved_slab || (ctrl.is_submitted ? new_.slab : null);
		const is_selected = effective_slab && regime.slab === effective_slab;

		// True when the suggested (winning) regime sits on a different card than the
		// in-force one - i.e. the recommendation disagrees with the current choice.
		const winner_slab = is_tie ? null : old_wins ? old.slab : new_.slab;
		const selected_differs = !!effective_slab && !is_tie && effective_slab !== winner_slab;

		let pill_color = "";
		let pill_label = "";
		if (is_selected) {
			pill_label = __("Saved");
			// Blue (neutral) in a tie - neither regime is cheaper, so green would falsely
			// claim "optimal" - or on an editable SSA where the recommendation differs (a
			// distinct green "Suggested" sits alongside). Otherwise the lone choice is green.
			pill_color = is_tie || (selected_differs && !ctrl.is_submitted) ? "blue" : "green";
		} else if (is_tie) {
			// Blue "Default" marks New as the tie-breaker default, only while no valid
			// regime is saved - never override an explicit, matching choice.
			if (tie_pill && !saved_slab) {
				pill_color = "blue";
				pill_label = __("Default");
			}
		} else if (is_winner) {
			// On a submitted SSA the employee can't switch, so the "Suggested" pill is just
			// noise when it disagrees with the locked-in choice - drop it.
			if (!(ctrl.is_submitted && selected_differs)) {
				pill_color = "green";
				pill_label = __("Suggested");
			}
		}

		const pill_html = pill_label
			? `<span class="badge indicator-pill ${pill_color} no-indicator-dot" style="flex-shrink:0;">${pill_label}</span>`
			: "";

		// In draft the staged radio drives the border; on a submitted SSA there is no
		// staging, so the in-force (selected) card carries the highlight instead. The
		// highlight follows the pill color so the blue "Saved" card gets a blue border.
		const is_highlighted = is_staged || (ctrl.is_submitted && is_selected);
		const border = is_highlighted
			? pill_color === "blue" || is_tie
				? "var(--blue-500)"
				: "var(--green-500)"
			: "var(--border-color)";

		const radio_html = ctrl.is_submitted
			? ""
			: `<input type="radio" name="regime-choice" class="regime-radio" value="${
					regime.slab
			  }" ${is_staged ? "checked" : ""} style="flex-shrink:0;">`;

		return `
		<label class="regime-radio-card${
			ctrl.is_submitted ? " no-hover" : ""
		} frappe-card" style="flex:1 1 200px; min-width:200px; max-width:100%; margin:0; padding:14px 16px; ${
			ctrl.is_submitted ? "" : "cursor:pointer;"
		} display:block;
			border:1px solid ${border};">
			<div style="display:flex; align-items:center; justify-content:space-between; gap:8px; margin-bottom:8px;">
				<strong style="white-space:nowrap;">${label}</strong>
				${radio_html}
			</div>
			<div class="text-muted" style="font-size:var(--text-sm); margin-bottom:4px;">${__(
				"Tax + Cess"
			)}</div>
			<div style="display:flex; align-items:flex-end; justify-content:space-between; gap:8px;">
				<span style="font-size:var(--text-2xl); font-weight:700; line-height:1; color:${
					is_winner ? "var(--primary)" : "inherit"
				};">${ctrl.fmt(regime.tax)}</span>
				${pill_html}
			</div>
		</label>`;
	};

	const alert_color = is_tie ? "blue" : "green";
	const winner_label = is_tie
		? __("Tax payable will be same for both the regimes")
		: old_wins
		? __("Old Regime saves {0} over New Regime", [ctrl.fmt(savings)])
		: __("New Regime saves {0} over Old Regime", [ctrl.fmt(savings)]);

	const { top, middle, bottom } = buildComparisonRows(ctrl, old, new_);

	const col_th = (label, star) =>
		`<th class="text-right" style="padding:8px;">${label}${star ? " ★" : ""}</th>`;
	const thead = `<tr style="background:var(--gray-50);">
		<th style="padding:8px;">${__("Item")}</th>
		${col_th(__("Old Regime"), !is_tie && old_wins)}
		${col_th(__("New Regime"), false)}
	</tr>`;

	const colgroup = `<colgroup><col style="width:50%"><col style="width:25%"><col style="width:25%"></colgroup>`;

	$("#comparison-alert").html("");

	// Save Regime lives in the page toolbar (top-right); behaves like Frappe's dirty
	// indicator. Shown only for editable (draft) assignments when the staged choice is a
	// real, current regime that differs from the one already saved - so it hides after a
	// save (feedback) and reappears the moment a different regime is picked. A stale
	// staged slab keeps it hidden until the user picks one. Submitted SSAs are read-only.
	const staged_valid = ctrl.staged_slab === old.slab || ctrl.staged_slab === new_.slab;
	const is_dirty = ctrl.staged_slab !== ctrl.selected_slab;
	if (staged_valid && is_dirty && !ctrl.is_submitted) {
		ctrl.page.set_primary_action(__("Save Regime"), () => ctrl.save_regime());
	} else {
		ctrl.page.clear_primary_action();
	}

	const html = `
		<div style="display:flex; flex-wrap:wrap; gap:15px; margin-bottom:15px; margin-right:15px; align-items:stretch; flex-shrink:0;">
			${regime_card(__("Old Regime"), old, !is_tie && old_wins, false)}
			${regime_card(__("New Regime"), new_, !is_tie && !old_wins, true)}
		</div>
		<div class="form-message ${alert_color}" style="margin-bottom:15px; font-weight:normal; margin-right:15px; flex-shrink:0; border-radius:8px;">
			${winner_label}
		</div>
		<div style="display:flex; flex-direction:column; border:1px solid var(--border-color); border-radius:8px; overflow:hidden; font-size:var(--text-sm); margin-right:15px; flex:1; min-height:0;">
			<table style="width:100%; border-collapse:collapse; table-layout:fixed; flex-shrink:0;">
				${colgroup}
				<thead>${thead}</thead>
				<tbody>${top}</tbody>
			</table>
			<div style="overflow-y:auto; flex:1; min-height:0; border-top:1px solid var(--border-color);">
				<table style="width:100%; border-collapse:collapse; table-layout:fixed;">
					${colgroup}
					<tbody>${middle}</tbody>
				</table>
			</div>
			<table style="width:100%; border-collapse:collapse; table-layout:fixed; border-top:2px solid var(--border-color); flex-shrink:0;">
				${colgroup}
				<tbody>${bottom}</tbody>
			</table>
		</div>`;

	$("#comparison-panel").html(html);
}

function buildComparisonRows(ctrl, old, new_) {
	const make_row = (label, old_val, new_val, bold = false) => {
		const s = bold ? "font-weight:600;" : "";
		const cell = (v) =>
			v !== null && v !== undefined
				? `<td class="text-right" style="${s} padding:8px;">${ctrl.fmt(v)}</td>`
				: `<td class="text-right text-muted" style="${s} padding:8px;">—</td>`;
		return `<tr><td style="${s} padding:8px;">${label}</td>${cell(old_val)}${cell(
			new_val
		)}</tr>`;
	};

	const ob = old.breakdown;
	const nb = new_.breakdown;

	const top = make_row(__("Gross Income"), ob.gross, nb.gross);

	const middle_rows = [];
	middle_rows.push(
		make_row(__("Standard Deduction"), -ob.standard_deduction, -nb.standard_deduction)
	);
	if (ob.hra_exemption) {
		middle_rows.push(make_row(__("HRA - Section 10(13A)"), -ob.hra_exemption, null));
	}
	if (ob.lta_exemption) {
		middle_rows.push(make_row(__("LTA - Section 10(5)"), -ob.lta_exemption, null));
	}
	if (ob.employer_nps || nb.employer_nps) {
		middle_rows.push(
			make_row(
				__("Employer NPS - 80CCD(2)"),
				ob.employer_nps ? -ob.employer_nps : null,
				nb.employer_nps ? -nb.employer_nps : null
			)
		);
	}
	Object.entries(ob.via_deductions || {}).forEach(([section, amount]) => {
		if (amount > 0) middle_rows.push(make_row(section, -amount, null));
	});

	const bottom = [
		make_row(__("Taxable Income"), ob.taxable_income, nb.taxable_income, true),
		make_row(__("Income Tax + Cess"), old.tax, new_.tax, true),
	].join("");

	return { top, middle: middle_rows.join(""), bottom };
}
