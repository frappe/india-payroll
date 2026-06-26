# Copyright (c) 2026, Frappe Technologies Pvt. Ltd. and contributors
# License: GNU General Public License v3. See license.txt

from india_payroll.india_payroll.epf import apply_epf
from india_payroll.india_payroll.esi import apply_esi
from india_payroll.india_payroll.lwf import apply_lwf
from india_payroll.india_payroll.professional_tax import apply_professional_tax


def apply_regional_deductions(doc) -> None:
	apply_professional_tax(doc)
	apply_esi(doc)
	apply_lwf(doc)
	apply_epf(doc)
