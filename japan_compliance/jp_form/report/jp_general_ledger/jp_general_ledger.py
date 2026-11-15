"""総勘定元帳（JP）（帳票記載要件 FORM-30〜35）。

勘定科目ごとに、前期繰越・取引（日付順）・期間合計・次期繰越を出す。相手科目は1科目ならその名前、
複数なら「諸口」とし、伝票番号から元の仕訳を開ける（FORM-31）。
"""

from collections import defaultdict

import frappe
from frappe import _
from frappe.utils import flt

from japan_compliance.jp_form.books import VoucherInfo, account_label, gl_entries

DEBIT_NATURE = ("Asset", "Expense")


def execute(filters=None):
	filters = frappe._dict(filters or {})
	accounts = filters.get("account") or []
	if isinstance(accounts, str):
		accounts = [accounts]
	entries = gl_entries(filters, accounts)
	# 相手科目は、選んだ科目に限らず伝票のすべての行から決める
	by_voucher = defaultdict(list)
	for gle in all_lines({e.voucher_no for e in entries}):
		by_voucher[(gle.voucher_type, gle.voucher_no)].append(gle)

	opening = defaultdict(float)
	for gle in gl_entries(filters, accounts, before=True):
		if not is_pl(gle.account) or gle.posting_date >= fiscal_year_start(filters):
			opening[gle.account] += flt(gle.debit) - flt(gle.credit)

	info = VoucherInfo()
	data = []
	for account in sorted({e.account for e in entries} | set(opening), key=sort_key):
		sign = 1 if root_type(account) in DEBIT_NATURE else -1
		balance = opening.get(account, 0) * sign
		data.append(frappe._dict(account=account_label(account), remarks=_("前期繰越"), balance=balance, bold=1, account_head=1))
		total_debit = total_credit = 0
		for gle in (e for e in entries if e.account == account):
			debit, credit = flt(gle.debit), flt(gle.credit)
			balance += (debit - credit) * sign
			total_debit += debit
			total_credit += credit
			v = info.get(gle.voucher_type, gle.voucher_no)
			data.append(frappe._dict(
				posting_date=gle.posting_date, voucher_type=gle.voucher_type, voucher_no=gle.voucher_no,
				against=against(gle, by_voucher[(gle.voucher_type, gle.voucher_no)]), remarks=v.remarks,
				party=v.party or gle.party or "", tax_category=v.categories.get(gle.account, ""),
				debit=debit or None, credit=credit or None, balance=balance,
			))
		data.append(frappe._dict(remarks=_("期間合計"), debit=total_debit, credit=total_credit, bold=1))
		data.append(frappe._dict(remarks=_("次期繰越"), balance=balance, bold=1))
	return columns(), data


def all_lines(voucher_nos):
	if not voucher_nos:
		return []
	return frappe.get_all("GL Entry", filters={"voucher_no": ("in", list(voucher_nos)), "is_cancelled": 0},
		fields=["voucher_type", "voucher_no", "account", "debit", "credit"])


def against(gle, lines):
	"""相手科目：反対側の科目が1つならその名前、複数なら「諸口」。"""
	side = "credit" if flt(gle.debit) > 0 else "debit"
	others = {l.account for l in lines if flt(l.get(side)) > 0 and l.account != gle.account}
	if len(others) == 1:
		return account_label(others.pop())
	return _("諸口") if others else ""


def root_type(account):
	return frappe.get_cached_value("Account", account, "root_type")


def is_pl(account):
	return root_type(account) in ("Income", "Expense")


def sort_key(account):
	return frappe.get_cached_value("Account", account, "lft") or 0


def fiscal_year_start(filters):
	from erpnext.accounts.utils import get_fiscal_year

	return get_fiscal_year(filters.from_date, company=filters.company)[1]


def columns():
	return [
		{"fieldname": "account", "label": "勘定科目", "fieldtype": "Data", "width": 170},
		{"fieldname": "posting_date", "label": "日付", "fieldtype": "Date", "width": 95},
		{"fieldname": "voucher_type", "label": "伝票種別", "fieldtype": "Data", "hidden": 1},
		{"fieldname": "voucher_no", "label": "伝票番号", "fieldtype": "Dynamic Link", "options": "voucher_type", "width": 165},
		{"fieldname": "against", "label": "相手科目", "fieldtype": "Data", "width": 170},
		{"fieldname": "remarks", "label": "摘要", "fieldtype": "Data", "width": 200},
		{"fieldname": "party", "label": "取引先", "fieldtype": "Data", "width": 150},
		{"fieldname": "tax_category", "label": "税区分", "fieldtype": "Data", "width": 200},
		{"fieldname": "debit", "label": "借方", "fieldtype": "Currency", "width": 110},
		{"fieldname": "credit", "label": "貸方", "fieldtype": "Currency", "width": 110},
		{"fieldname": "balance", "label": "残高", "fieldtype": "Currency", "width": 120},
	]
