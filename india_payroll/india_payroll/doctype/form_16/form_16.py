# Copyright (c) 2026, Frappe Technologies Pvt. Ltd. and contributors
# For license information, please see license.txt

import frappe
from frappe.model.document import Document


class Form16(Document):
	# begin: auto-generated types
	# This code is auto-generated. Do not modify anything in this block.

	from typing import TYPE_CHECKING

	if TYPE_CHECKING:
		from frappe.types import DF

		company: DF.Link
		employee: DF.Link
		employee_name: DF.Data | None
		financial_year: DF.Link
		gross_salary: DF.Currency
		pan: DF.Data | None
		part_a_file: DF.Attach | None
		part_a_job_id: DF.Data | None
		part_a_status: DF.Literal["Pending", "Requested", "Available", "Failed"]
		part_b_file: DF.Attach | None
		part_b_job_id: DF.Data | None
		part_b_status: DF.Literal["Pending", "Requested", "Available", "Failed"]
		tan: DF.Data | None
		tds_return: DF.Link | None
		total_tax_deducted: DF.Currency
		total_taxable_income: DF.Currency
		traces_request_id: DF.Data | None
	# end: auto-generated types

	@frappe.whitelist()
	def generate_part_b(self) -> str:
		from india_payroll.india_payroll.tds.form16 import enqueue_part_b

		return enqueue_part_b(self.name)

	@frappe.whitelist()
	def request_part_a(self) -> str:
		from india_payroll.india_payroll.tds.form16 import enqueue_part_a

		return enqueue_part_a(self.name)
