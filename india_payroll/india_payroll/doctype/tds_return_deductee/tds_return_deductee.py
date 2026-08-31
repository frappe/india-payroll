# Copyright (c) 2026, Frappe Technologies Pvt. Ltd. and contributors
# For license information, please see license.txt

from frappe.model.document import Document


class TDSReturnDeductee(Document):
	# begin: auto-generated types
	# This code is auto-generated. Do not modify anything in this block.

	from typing import TYPE_CHECKING

	if TYPE_CHECKING:
		from frappe.types import DF

		amount_paid: DF.Currency
		challan: DF.Link | None
		date_of_deduction: DF.Date | None
		date_of_payment: DF.Date | None
		deductee_status: DF.Literal["Resident", "Non-Resident"]
		employee: DF.Link
		employee_name: DF.Data | None
		month: DF.Literal[
			"",
			"April",
			"May",
			"June",
			"July",
			"August",
			"September",
			"October",
			"November",
			"December",
			"January",
			"February",
			"March",
		]
		pan: DF.Data | None
		parent: DF.Data
		parentfield: DF.Data
		parenttype: DF.Data
		remarks: DF.SmallText | None
		tax_deducted: DF.Currency
	# end: auto-generated types

	pass
