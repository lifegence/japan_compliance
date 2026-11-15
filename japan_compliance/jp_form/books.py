"""仕訳帳（JP）・総勘定元帳（JP）の共通処理（帳票記載要件 4.3・4.4）。"""

from collections import OrderedDict, defaultdict

import frappe
from frappe.utils import flt

from japan_compliance.jp_tax import masters

VOUCHER_PARTY = {"Sales Invoice": "customer_name", "Purchase Invoice": "supplier_name"}


def gl_entries(filters, accounts=None, before=False):
	conditions = ["company = %(company)s", "is_cancelled = 0"]
	conditions.append("posting_date < %(from_date)s" if before else "posting_date between %(from_date)s and %(to_date)s")
	if accounts:
		conditions.append("account in %(accounts)s")
	return frappe.db.sql(
		f"""select name, posting_date, voucher_type, voucher_no, account, debit, credit, party_type, party, remarks,
			is_opening, creation
		from `tabGL Entry` where {' and '.join(conditions)}
		order by posting_date, creation, voucher_no""",
		{**filters, "accounts": accounts or [""]},
		as_dict=True,
	)


def account_label(account):
	number, name = frappe.get_cached_value("Account", account, ["account_number", "account_name"])
	return f"{name}（{number}）" if number else name


class VoucherInfo:
	"""伝票ごとの摘要・取引先・税区分・特例区分などを、伝票種別に応じて集める。"""

	def __init__(self):
		self.cache = {}

	def get(self, voucher_type, voucher_no):
		key = (voucher_type, voucher_no)
		if key not in self.cache:
			self.cache[key] = self.load(voucher_type, voucher_no)
		return self.cache[key]

	def load(self, voucher_type, voucher_no):
		info = frappe._dict(remarks="", party="", registration="", special_case="", address="", gross=None, categories={})
		if voucher_type in ("Sales Invoice", "Purchase Invoice"):
			doc = frappe.get_doc(voucher_type, voucher_no)
			info.party = doc.get(VOUCHER_PARTY[voucher_type])
			labels = {b.tax_category: b.display_label for b in doc.get("jp_tax_breakdown") or []}
			by_account = defaultdict(set)
			names = []
			for item in doc.items:
				account = item.income_account if voucher_type == "Sales Invoice" else item.expense_account
				by_account[account].add(category_label(item.jp_tax_category, labels))
				names.append(item.item_name + ("※" if (item.jp_tax_category or "").endswith("-8R") else ""))
			info.categories = {a: "・".join(sorted(v)) for a, v in by_account.items()}
			info.remarks = "、".join(OrderedDict.fromkeys(names))
			if voucher_type == "Purchase Invoice":
				status = frappe.db.get_value("Supplier", doc.supplier, "jp_registration_status")
				info.registration = {"登録": "有", "非登録": "無", "不明": "不明"}.get(status, "")
				info.special_case = doc.get("jp_special_case") or ""
				info.address = doc.get("jp_counterparty_address") or ""
				info.gross = flt(doc.grand_total)
			else:
				# 登録区分が未確認の得意先は空欄にする（国外の得意先などで「不明」が並ばないように）
				status = frappe.db.get_value("Customer", doc.customer, "jp_registration_status")
				info.registration = {"登録": "有", "非登録": "無"}.get(status, "")
		elif voucher_type == "Journal Entry":
			doc = frappe.get_doc(voucher_type, voucher_no)
			info.remarks = doc.user_remark or ""
			info.special_case = doc.get("jp_special_case") or ""
			info.address = doc.get("jp_counterparty_address") or ""
			info.party = doc.get("jp_counterparty") or ""
			by_account = defaultdict(set)
			for row in doc.accounts:
				if row.get("jp_tax_category"):
					by_account[row.account].add(category_label(row.jp_tax_category, {}))
				if row.party and not info.party:
					info.party = row.party
			info.categories = {a: "・".join(sorted(v)) for a, v in by_account.items()}
		elif voucher_type == "Payment Entry":
			doc = frappe.get_doc(voucher_type, voucher_no)
			info.party = doc.party_name or doc.party
			info.remarks = doc.remarks or ""
			info.categories = {d.account: category_label(d.jp_tax_category, {}) for d in doc.get("deductions") or [] if d.get("jp_tax_category")}
		return info


def category_label(code, labels):
	"""帳簿の税区分欄の表示。経過措置は「70%控除対象」などを添える（INV-42、FORM-22）。"""
	if not code:
		return ""
	name = masters.get_category(code).category_name
	extra = labels.get(code)
	return f"{name}（{extra}）" if extra and extra != name else name
