// Copyright (c) 2026, Frappe Technologies Pvt. Ltd. and contributors
// For license information, please see license.txt

// Pure markup builders for the Tax Regime Selector page. No state, no `this` —
// each returns an HTML string. Kept verbatim from the original page so the
// rendered DOM is unchanged.

export function pageShell() {
	return `
		<div style="padding: 20px;">
			<div style="display:flex; gap:16px; align-items:center;">
				<div style="flex:0 0 65%; max-width:65%; display:flex; gap:16px; min-width:0;">
					<div style="flex:1; min-width:0;"><div id="payroll-period-wrapper"></div></div>
					<div style="flex:1; min-width:0;"><div id="employee-field-wrapper"></div></div>
					<div style="flex:1; min-width:0; overflow:hidden;"><div id="annual-gross-wrapper"></div></div>
				</div>
				<div style="flex:1; min-width:0;" id="comparison-alert"></div>
			</div>
			<div id="page-body" style="margin-top: 20px;"></div>
		</div>
	`;
}

export function placeholder() {
	return `
		<div class="text-center text-muted" style="padding: 60px 0; font-size: var(--text-lg);">
			${__("Select an employee to compare tax regimes")}
		</div>
	`;
}

export function section10Inputs() {
	return `<div class="row" style="margin-bottom:20px;">
		<div class="col-md-4">
			<div class="form-group">
				<label class="control-label">${__("Monthly Rent (₹)")}</label>
				<div class="control-input">
					<input type="text" inputmode="decimal" class="form-control regime-input"
						data-key="rent_monthly">
				</div>
			</div>
		</div>
		<div class="col-md-6">
			<label style="display:flex; align-items:flex-start; gap:8px; cursor:pointer; font-weight:var(--weight-medium); margin:0;">
				<input type="checkbox" class="regime-input" data-key="city_type" style="margin-top:3px; flex-shrink:0;">
				<span>
					${__("Rented in Metro City")}
					<div class="text-extra-muted" style="margin-top:3px;font-size:var(--text-sm)">
						${__(
							"Metro cities include Mumbai, Delhi, Chennai, Kolkata, Ahmedabad, Bengaluru, Pune, Hyderabad"
						)}
					</div>
				</span>
			</label>
		</div>
	</div>`;
}

export function mainLayout(hasHra) {
	return `
		<div style="display:flex; gap:15px; align-items:flex-start;">
			<div style="flex:0 0 65%; max-width:65%; min-width:0;">
				<div class="frappe-card" style="padding: 0; max-height:calc(80vh + 1rem); overflow-y:auto; overflow-x:hidden;">
					<div style="display:flex; justify-content:space-between; align-items:start; gap:12px; padding:16px 16px 26px; position:sticky; top:0; z-index:3; background:var(--card-bg);">
						<span style="font-size:var(--text-lg); font-weight:600;">${__(
							"Add your exemptions to compare tax in both regimes"
						)}</span>
						<button class="btn btn-default btn-sm declare-btn" style="white-space:nowrap;">
							${__("Declare Tax Exemptions →")}
						</button>
					</div>
					<div style="margin:0 16px 16px;">
						${
							hasHra
								? `
						<h6 class="form-section-heading uppercase">${__("Section 10")}</h6>
						${section10Inputs()}`
								: ""
						}
						<div style="display:flex; justify-content:space-between; align-items:center; gap:12px; ${
							hasHra ? "margin-top:32px;" : ""
						} margin-bottom:12px;">
							<h6 class="form-section-heading uppercase" style="margin:0; white-space:nowrap;">${__(
								"Chapter VI-A Deductions"
							)}</h6>
							<div style="display:flex; align-items:center; gap:8px;">
								<div style="position:relative;" id="declaration-search-wrapper">
									<span style="position:absolute; left:8px; top:50%; transform:translateY(-50%); display:inline-flex; color:var(--gray-500); --icon-stroke:var(--gray-500); pointer-events:none; z-index:2;">
										${frappe.utils.icon("search", "sm")}
									</span>
									<input type="text" class="form-control input-sm" id="declaration-search"
										placeholder="${__("Search")}" style="padding-left:28px;">
								</div>
								<button class="btn btn-xs btn-default" id="expand-all-btn">${__("Expand All")}</button>
								<button class="btn btn-xs btn-default" id="collapse-all-btn">${__("Collapse All")}</button>
							</div>
						</div>
						<div id="declaration-table-wrapper"></div>
						<div id="declaration-empty" class="text-muted text-center" style="display:none; padding:20px;">
							${__("No deductions found")}
						</div>
					</div>
				</div>
			</div>
			<div style="flex:0 0 35%; max-width:35%; min-width:0; position:sticky; top:0; margin-right:15px; max-height:calc(80vh + 1rem); display:flex; flex-direction:column;" id="comparison-panel">
				<div class="text-center text-muted" style="padding: 40px 0;">
					${__("Computing...")}
				</div>
			</div>
		</div>
	`;
}
