"""振替伝票・入出金伝票の税区分（標準仕訳パターン 4.3・5章、月次締め要件 C02）。

振替伝票では税額を自動計算しない（仮受・仮払消費税等の行を起票者が入れる）。ここでは
明細の税区分の既定値の設定と、区分の妥当性・訂正仕訳の記録を扱う。
"""

import frappe
from frappe import _
from frappe.utils import cint, flt, getdate

from japan_compliance.jp_tax import masters
from japan_compliance.setup.custom_fields import (
	ADDRESS_REQUIRED_BY_LAW,
	ADDRESS_REQUIRED_FROM_LIMIT,
	INVOICE_REQUIRED_FROM_LIMIT,
	SPECIAL_CASE_LIMIT,
)


def account_category(account):
	return frappe.get_cached_value("Account", account, "jp_tax_category") if account else None


def default_for_side(account, is_debit):
	"""科目の既定税区分を、仕訳の向きが区分と合う場合だけ使う。

	例：工具器具備品（既定 P-TAX-10）を売却で貸方に立てる行には既定を入れず、起票者に選ばせる。
	"""
	code = account_category(account)
	if not code:
		return None
	direction = frappe.get_cached_value("JP Tax Category", code, "direction")
	if (direction == "仕入" and is_debit) or (direction == "売上" and not is_debit):
		return code
	return None


def journal_entry_validate(doc, method=None):
	settings = masters.get_settings(doc.company)
	if not settings:
		return
	if doc.voucher_type == "Opening Entry" or doc.get("is_opening") == "Yes":
		# 期首残高は取引ではない。科目の既定税区分を入れると課税仕入などに集計される（B-6 O-01）
		for row in doc.accounts:
			row.jp_tax_category = None
		validate_correction(doc)
		return
	tax_accounts = (settings.sales_tax_account, settings.purchase_tax_account)
	settlement = bool(doc.get("jp_tax_settlement"))
	for row in doc.accounts:
		is_debit = flt(row.debit_in_account_currency or row.debit) > 0
		if row.account in tax_accounts:
			# 仮受・仮払消費税等の行は税額そのもの。区分は持たせない（集計では同じ伝票の課税区分に按分する）
			row.jp_tax_category = None
			continue
		if settlement and row.get("jp_tax_category"):
			row.jp_tax_category = to_out_of_scope(row.jp_tax_category)
		if not row.get("jp_tax_category") and not settlement:
			row.jp_tax_category = default_for_side(row.account, is_debit)
		if row.get("jp_tax_category"):
			cat = masters.get_category(row.jp_tax_category)
			if not cat.enabled:
				frappe.throw(_("行 {0}：税区分 {1} は無効です。").format(row.idx, cat.name))
	validate_correction(doc)
	validate_special_case(doc)
	warn_missing_counterparty(doc)


def special_case_amount(doc):
	"""特例の金額基準で見る 1 回の取引の税込金額（会社通貨）。伝票 1 件を 1 回の取引とみなす。"""
	if doc.doctype == "Journal Entry":
		return flt(doc.get("total_debit"))
	return flt(doc.get("base_grand_total"))


def validate_special_case(doc, method=None):
	"""帳簿のみ保存の特例の金額基準と、相手方の住所又は所在地（INV-46。Q&A 問110）。

	- 公共交通機関特例・自動販売機・自動サービス機特例：税込 3 万円以上は特例の対象外（請求書等が必要）
	- 入場券等回収特例：税込 3 万円以上は住所が必要
	- 古物等の購入特例：業法で帳簿への住所の記載が義務付けられる取引だけ住所が必要
	"""
	case = doc.get("jp_special_case")
	if not case:
		return
	amount = special_case_amount(doc)
	if case in INVOICE_REQUIRED_FROM_LIMIT and amount >= SPECIAL_CASE_LIMIT:
		frappe.throw(
			_("帳簿のみ保存の特例「{0}」は税込 3 万円未満の取引が対象です（この伝票は {1} 円）。請求書等を保存し、特例を外してください。").format(
				case, f"{amount:,.0f}"
			)
		)
	need_address = (case in ADDRESS_REQUIRED_FROM_LIMIT and amount >= SPECIAL_CASE_LIMIT) or (
		case in ADDRESS_REQUIRED_BY_LAW and cint(doc.get("jp_address_required_by_law"))
	)
	if need_address and not (doc.get("jp_counterparty_address") or "").strip():
		frappe.throw(_("帳簿のみ保存の特例「{0}」のこの取引では、相手方の住所又は所在地を入力してください。").format(case))


def warn_missing_counterparty(doc):
	"""課税仕入の振替伝票で、帳簿に載せる相手方がない場合に知らせる（INV-45）。"""
	if doc.get("jp_tax_settlement") or doc.get("jp_counterparty") or any(row.get("party") for row in doc.accounts):
		return
	for row in doc.accounts:
		code = row.get("jp_tax_category")
		if not code:
			continue
		cat = masters.get_category(code)
		if cat.direction == "仕入" and cat.tax_type == "課税" and cat.rate_code and cat.rate_code != "ZERO":
			frappe.msgprint(_("課税仕入の仕訳に相手方がありません。帳簿の記載事項のため、「相手方（帳簿に記載）」を入力してください。"),
				title=_("相手方の確認"), indicator="orange")
			return


def to_out_of_scope(code):
	"""消費税の相殺・振替仕訳では、課税区分を対象外にする（B-6 4.3）。"""
	direction = frappe.get_cached_value("JP Tax Category", code, "direction")
	return "S-OOS" if direction == "売上" else "P-OOS"


def validate_correction(doc):
	"""締め後・前年度の訂正の記録（月次締め要件 7章）。"""
	if not doc.get("jp_correction_of"):
		return
	if not doc.get("jp_correction_reason"):
		frappe.throw(_("訂正元の伝票を指定した場合は、訂正の理由を入力してください。"))
	ref_date = frappe.db.get_value(doc.jp_correction_of_type, doc.jp_correction_of, "posting_date")
	if not ref_date:
		return
	from erpnext.accounts.utils import get_fiscal_year

	ref_fy = get_fiscal_year(ref_date, company=doc.company)[0]
	this_fy = get_fiscal_year(doc.posting_date, company=doc.company)[0]
	if ref_fy != this_fy and getdate(ref_date) < getdate(doc.posting_date):
		frappe.msgprint(
			_("訂正元 {0} は前年度（{1}）の伝票です。前年度の損益の訂正は「前期損益修正損（益）」の科目で当期に計上してください。").format(
				doc.jp_correction_of, ref_fy),
			title=_("前年度の訂正"), indicator="orange",
		)


def payment_entry_validate(doc, method=None):
	settings = masters.get_settings(doc.company)
	if not settings:
		return
	tax_accounts = (settings.sales_tax_account, settings.purchase_tax_account)
	for row in doc.get("deductions") or []:
		if row.account in tax_accounts:
			row.jp_tax_category = None
		elif not row.get("jp_tax_category"):
			row.jp_tax_category = account_category(row.account)
