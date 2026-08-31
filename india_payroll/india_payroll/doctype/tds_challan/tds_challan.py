# Copyright (c) 2026, Frappe Technologies Pvt. Ltd. and contributors
# For license information, please see license.txt

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import add_months, flt, getdate

from india_payroll.india_payroll.tds.validators import validate_tan


class TDSChallan(Document):
	# begin: auto-generated types
	# This code is auto-generated. Do not modify anything in this block.

	from typing import TYPE_CHECKING

	if TYPE_CHECKING:
		from frappe.types import DF

		amended_from: DF.Link | None
		bank_name: DF.Data | None
		bsr_code: DF.Data
		challan_date: DF.Date
		challan_serial_no: DF.Data
		company: DF.Link
		deposit_amount: DF.Currency
		education_cess: DF.Currency
		fee: DF.Currency
		financial_year: DF.Link
		interest: DF.Currency
		minor_head: DF.Literal["TDS Payable by Taxpayer (200)", "TDS Regular Assessment (400)"]
		naming_series: DF.Literal["TDS-CHL-.YYYY.-"]
		others: DF.Currency
		payment_mode: DF.Literal["", "Net Banking", "Debit Card", "Over the Counter", "NEFT/RTGS"]
		quarter: DF.Literal["Q1", "Q2", "Q3", "Q4"]
		section: DF.Data | None
		surcharge_amount: DF.Currency
		tan: DF.Data | None
		tds_amount: DF.Currency
	# end: auto-generated types

	def validate(self) -> None:
		self.validate_tan()
		self.validate_bsr_code()
		self.validate_breakup()
		self.set_deduction_month()

	def set_deduction_month(self) -> None:
		"""Default the salary month this challan pays for, from the challan date.

		TDS for a month is deposited by the 7th of the next one, so a challan dated
		7 May pays April's deduction. Explicitly set months are left alone.
		"""
		if self.deduction_month or not self.challan_date:
			return
		self.deduction_month = deduction_month_from_date(self.challan_date)

	def validate_tan(self) -> None:
		if not self.tan and self.company:
			self.tan = frappe.db.get_value("Company", self.company, "tan")
		if not self.tan:
			frappe.throw(
				_("Company {0} has no TAN. Set it in the company's TDS Deductor Details.").format(
					self.company
				)
			)
		self.tan = validate_tan(self.tan, "TAN")

	def validate_bsr_code(self) -> None:
		bsr = (self.bsr_code or "").strip()
		if not (bsr.isdigit() and len(bsr) == 7):
			frappe.throw(_("BSR Code must be a 7-digit number."))
		self.bsr_code = bsr

	def validate_breakup(self) -> None:
		"""Total deposited should equal the sum of the breakup components."""
		breakup = (
			flt(self.tds_amount)
			+ flt(self.surcharge_amount)
			+ flt(self.education_cess)
			+ flt(self.interest)
			+ flt(self.fee)
			+ flt(self.others)
		)
		# Only enforce when a breakup has actually been entered.
		if breakup and abs(breakup - flt(self.deposit_amount)) > 1.0:
			frappe.throw(
				_("Total Deposited ({0}) does not match the sum of the amount breakup ({1}).").format(
					self.deposit_amount, breakup
				)
			)


def deduction_month_from_date(challan_date) -> str:
	"""Salary month a challan paid on `challan_date` covers (the previous month)."""
	from india_payroll.india_payroll.tds.data_assembly import MONTH_NAMES

	return MONTH_NAMES[add_months(getdate(challan_date), -1).month]


def get_challans_for_period(company: str, financial_year: str, quarter: str) -> list[dict]:
	"""Return submitted challans for a company/FY/quarter (used to build the CSI)."""
	return frappe.get_all(
		"TDS Challan",
		filters={
			"company": company,
			"financial_year": financial_year,
			"quarter": quarter,
			"docstatus": 1,
		},
		fields=[
			"name",
			"bsr_code",
			"challan_serial_no",
			"challan_date",
			"deduction_month",
			"deposit_amount",
			"tds_amount",
			"surcharge_amount",
			"education_cess",
			"interest",
			"fee",
			"others",
			"section",
			"minor_head",
		],
		order_by="challan_date asc",
	)
