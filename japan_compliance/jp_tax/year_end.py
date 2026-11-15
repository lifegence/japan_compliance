"""決算整理：控除不能分の雑損失への振替（端数処理方針 4.4、標準仕訳パターン Y-04）。

控除不能分の処理を「仮払消費税等に含め決算で雑損失」とした会社で、課税期間の控除不能分の合計を
仮払消費税等から雑損失へ振り替える振替伝票を、下書きで作る。内容を確かめてから提出する。
"""

import frappe
from frappe import _
from frappe.utils import flt, getdate

from japan_compliance.jp_tax import masters
from japan_compliance.jp_tax.calculator import MISC_LOSS


@frappe.whitelist()
def create_nondeductible_transfer(company, from_date=None, to_date=None, posting_date=None):
	frappe.has_permission("Journal Entry", "create", throw=True)
	settings = masters.get_settings(company)
	if not settings:
		frappe.throw(_("会社 {0} の JP Tax Settings がありません。").format(company))
	if settings.nondeductible_method != MISC_LOSS:
		frappe.throw(_("控除不能分の処理が「{0}」の会社では、この振替は使いません。").format(settings.nondeductible_method))

	from_date = from_date or settings.tax_period_start
	to_date = to_date or settings.tax_period_end
	if not from_date or not to_date:
		frappe.throw(_("対象期間を指定するか、JP Tax Settings に課税期間を設定してください。"))
	period = f"{getdate(from_date)}/{getdate(to_date)}"

	existing = frappe.db.get_value("Journal Entry",
		{"company": company, "jp_nondeductible_period": period, "docstatus": ["<", 2]}, "name")
	if existing:
		frappe.throw(_("この期間の振替伝票は作成済みです（{0}）。").format(existing))

	amount = nondeductible_total(company, from_date, to_date, settings)
	if amount <= 0:
		frappe.throw(_("対象期間に振り替える控除不能分がありません。"))

	cost_center = frappe.get_cached_value("Company", company, "cost_center")
	doc = frappe.get_doc({
		"doctype": "Journal Entry",
		"voucher_type": "Journal Entry",
		"company": company,
		"posting_date": posting_date or to_date,
		"jp_tax_settlement": 1,
		"jp_nondeductible_period": period,
		"user_remark": _("控除不能分の雑損失への振替（{0}〜{1}）").format(getdate(from_date), getdate(to_date)),
		"accounts": [
			{"account": settings.nondeductible_loss_account, "debit_in_account_currency": amount,
				"jp_tax_category": "P-OOS", "cost_center": cost_center},
			{"account": settings.purchase_tax_account, "credit_in_account_currency": amount, "cost_center": cost_center},
		],
	})
	doc.insert()
	return doc.name


def nondeductible_total(company, from_date, to_date, settings):
	"""消費税集計表と同じ集計で、控除不能額の合計を出す（取消の反対仕訳を含む）。"""
	from japan_compliance.jp_form.report.jp_consumption_tax_summary.jp_consumption_tax_summary import collect

	filters = frappe._dict(company=company, from_date=from_date, to_date=to_date)
	return round(sum(flt(r.get("nondeductible_tax")) for r in collect(filters, settings)))
