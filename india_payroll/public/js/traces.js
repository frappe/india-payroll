frappe.provide("india_payroll.tds");

// Sandbox keeps the TRACES password per TAN. When it has none, or TRACES rejects it,
// ask for it once, hand it to Sandbox, and repeat the call that needed it.
window.india_payroll.tds.with_traces_credentials = function (company, call) {
	return call().then((result) => {
		if (!result || !result.credentials_required) return result;

		return new Promise((resolve) => {
			frappe.prompt(
				[
					{
						fieldname: "password",
						fieldtype: "Password",
						label: __("TRACES Password"),
						reqd: 1,
						description: result.message,
					},
				],
				(values) =>
					frappe
						.call({
							method: "india_payroll.india_payroll.tds.challans.set_traces_password",
							args: { company, password: values.password },
							freeze: true,
						})
						.then(() =>
							resolve(
								window.india_payroll.tds.with_traces_credentials(company, call)
							)
						),
				__("TRACES Login"),
				__("Save and Continue")
			);
		});
	});
};
