"""売上・仕入請求書の税計算を日本の方式に置き換える（hooks.override_doctype_class）。"""

from erpnext.accounts.doctype.purchase_invoice.purchase_invoice import PurchaseInvoice
from erpnext.accounts.doctype.sales_invoice.sales_invoice import SalesInvoice

from japan_compliance.jp_tax import calculator


class JPSalesInvoice(SalesInvoice):
	def calculate_taxes_and_totals(self):
		calculator.calculate(self, super().calculate_taxes_and_totals)


class JPPurchaseInvoice(PurchaseInvoice):
	def calculate_taxes_and_totals(self):
		calculator.calculate(self, super().calculate_taxes_and_totals)
