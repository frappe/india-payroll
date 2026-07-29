# Copyright (c) 2026, Frappe Technologies Pvt. Ltd. and contributors
# For license information, please see license.txt

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import flt, now_datetime

from india_payroll.india_payroll.tds.validators import (
	is_valid_deductee_pan,
	validate_pan,
	validate_tan,
)


class TDSReturn(Document):
	# begin: auto-generated types
	# This code is auto-generated. Do not modify anything in this block.

	from typing import TYPE_CHECKING

	if TYPE_CHECKING:
		from frappe.types import DF

		from india_payroll.india_payroll.doctype.tds_return_action.tds_return_action import TDSReturnAction
		from india_payroll.india_payroll.doctype.tds_return_deductee.tds_return_deductee import (
			TDSReturnDeductee,
		)

		acknowledgement_number: DF.Data | None
		amended_from: DF.Link | None
		company: DF.Link
		csi_file: DF.Attach | None
		deductees: DF.Table[TDSReturnDeductee]
		deductor_name: DF.Data | None
		filing_actions: DF.Table[TDSReturnAction]
		filing_date: DF.Datetime | None
		filing_status: DF.Data | None
		financial_year: DF.Link
		form_27a: DF.Attach | None
		form_type: DF.Literal["24Q"]
		fvu_file: DF.Attach | None
		pan: DF.Data | None
		previous_receipt_number: DF.Data | None
		quarter: DF.Literal["Q1", "Q2", "Q3", "Q4"]
		responsible_person_designation: DF.Data | None
		responsible_person_name: DF.Data | None
		responsible_person_pan: DF.Data | None
		return_type: DF.Literal["Original", "Revised"]
		sheet_json_file: DF.Attach | None
		tan: DF.Data | None
		token_number: DF.Data | None
		total_amount_paid: DF.Currency
		total_tax_deducted: DF.Currency
		total_tax_deposited: DF.Currency
		txt_file: DF.Attach | None
		validation_issues: DF.Code | None
	# end: auto-generated types

	def validate(self) -> None:
		self.set_missing_deductor_values()
		self.validate_identity()
		self.validate_deductees()
		self.calculate_totals()
		if not self.filing_status:
			self.filing_status = "Draft"

	def set_missing_deductor_values(self) -> None:
		if not self.tan:
			self.tan = frappe.db.get_value("Company", self.company, "tan")
		if not self.pan:
			self.pan = frappe.db.get_value("Company", self.company, "tax_id")

	def validate_identity(self) -> None:
		if not self.tan:
			frappe.throw(
				_("Company {0} has no TAN. Set it in the company's TDS Deductor Details.").format(
					self.company
				)
			)
		self.tan = validate_tan(self.tan, "Deductor TAN")
		if self.pan:
			self.pan = validate_pan(self.pan, "Deductor PAN")

		if self.return_type == "Revised" and not self.previous_receipt_number:
			frappe.throw(_("Previous Receipt Number is required for a Revised return."))

		# One return per (TAN, FY, quarter, form, return_type) — guard duplicates.
		duplicate = frappe.db.exists(
			"TDS Return",
			{
				"tan": self.tan,
				"financial_year": self.financial_year,
				"quarter": self.quarter,
				"form_type": self.form_type,
				"return_type": self.return_type,
				"docstatus": ["<", 2],
				"name": ["!=", self.name],
			},
		)
		if duplicate:
			frappe.throw(
				_("A {0} return for {1} {2} ({3}) already exists: {4}").format(
					self.return_type, self.financial_year, self.quarter, self.tan, duplicate
				)
			)

	def validate_deductees(self) -> None:
		invalid = []
		for row in self.deductees:
			if row.pan:
				row.pan = row.pan.strip().upper()
			if row.pan and not is_valid_deductee_pan(row.pan):
				invalid.append(_("Row {0}: {1}").format(row.idx, row.pan))

		if invalid:
			frappe.throw(
				_("Invalid deductee PAN (expected format AAAAA9999A, or PANNOTAVBL if unavailable):")
				+ "<br>"
				+ "<br>".join(invalid)
			)

	def calculate_totals(self) -> None:
		from india_payroll.india_payroll.tds.csi import total_deposited

		self.total_amount_paid = sum(flt(d.amount_paid) for d in self.deductees)
		self.total_tax_deducted = sum(flt(d.tax_deducted) for d in self.deductees)
		self.total_tax_deposited = total_deposited(self.company, self.financial_year, self.quarter)

	def before_submit(self) -> None:
		if self.filing_status not in ("Filed", "Accepted"):
			frappe.throw(
				_("A TDS Return can only be submitted after it has been successfully filed with Sandbox.")
			)

	# -------------------------------------------------------------- helpers
	def add_action(
		self,
		request_type: str,
		status: str,
		job_id: str | None = None,
		request_id: str | None = None,
		integration_request: str | None = None,
		message: str | None = None,
		save: bool = True,
	) -> None:
		"""Append a row to the filing log and (optionally) persist it."""
		self.append(
			"filing_actions",
			{
				"request_type": request_type,
				"status": status,
				"creation_time": now_datetime(),
				"job_id": job_id,
				"request_id": request_id,
				"integration_request": integration_request,
				"message": message,
			},
		)
		if save:
			self.save(ignore_permissions=True)

	def set_status(self, status: str, save: bool = True) -> None:
		self.db_set("filing_status", status)
		if save:
			frappe.db.commit()  # nosemgrep: commit is required to ensure that the status change is persisted before any subsequent actions are taken.

	def get_open_job(self) -> dict | None:
		"""Return the most recent action that has an open (incomplete) job, if any."""
		from india_payroll.india_payroll.tds.filing import TERMINAL_ACTION_STATUSES

		for action in reversed(self.filing_actions):
			if action.job_id and (action.status or "").lower() not in TERMINAL_ACTION_STATUSES:
				return {
					"request_type": action.request_type,
					"job_id": action.job_id,
					"row": action.name,
				}
		return None

	# --------------------------------------------------- whitelisted actions
	@frappe.whitelist()
	def fetch_from_payroll(self) -> dict:
		from india_payroll.india_payroll.tds.data_assembly import populate_deductees

		count = populate_deductees(self)
		self.calculate_totals()
		self.save()
		return {"deductee_rows": count}

	@frappe.whitelist()
	def validate_return(self) -> str:
		from india_payroll.india_payroll.tds.filing import enqueue_step

		return enqueue_step(self.name, "validate")

	@frappe.whitelist()
	def skip_validation(self, reason: str) -> None:
		from india_payroll.india_payroll.tds.filing import skip_validation

		return skip_validation(self.name, reason)

	@frappe.whitelist()
	def generate_txt(self) -> str:
		from india_payroll.india_payroll.tds.filing import enqueue_step

		return enqueue_step(self.name, "generate_txt")

	@frappe.whitelist()
	def generate_fvu(self) -> str:
		from india_payroll.india_payroll.tds.filing import enqueue_step

		return enqueue_step(self.name, "generate_fvu")

	@frappe.whitelist()
	def file_return(self) -> str:
		from india_payroll.india_payroll.tds.filing import enqueue_step

		return enqueue_step(self.name, "e_file")
