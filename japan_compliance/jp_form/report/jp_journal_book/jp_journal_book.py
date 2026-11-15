"""仕訳帳（JP）（帳票記載要件 FORM-21〜24、インボイス要件 INV-42・45・46）。

1仕訳を借方・貸方を並べた複数行で表し、伝票ごとに区切る。標準 General Ledger（FORM-20）とは別のレポート。
"""

from collections import OrderedDict

import frappe
from frappe import _
from frappe.utils import flt

from japan_compliance.jp_form.books import VoucherInfo, account_label, gl_entries


def execute(filters=None):
	filters = frappe._dict(filters or {})
	info = VoucherInfo()
	vouchers = OrderedDict()
	for gle in gl_entries(filters):
		vouchers.setdefault((gle.posting_date, gle.voucher_type, gle.voucher_no), []).append(gle)

	data = []
	total_debit = total_credit = 0
	for (date, vtype, vno), lines in vouchers.items():
		v = info.get(vtype, vno)
		debits = merge([l for l in lines if flt(l.debit) > 0], "debit")
		credits = merge([l for l in lines if flt(l.credit) > 0], "credit")
		for i in range(max(len(debits), len(credits))):
			d = debits[i] if i < len(debits) else None
			c = credits[i] if i < len(credits) else None
			categories = [v.categories.get(x[0]) for x in (d, c) if x and v.categories.get(x[0])]
			data.append(frappe._dict(
				posting_date=date if i == 0 else None, voucher_type=vtype, voucher_no=vno if i == 0 else None,
				debit_account=account_label(d[0]) if d else "", debit=d[1] if d else None,
				credit_account=account_label(c[0]) if c else "", credit=c[1] if c else None,
				remarks=v.remarks if i == 0 else "", party=v.party if i == 0 else "",
				tax_category="・".join(categories), registration=v.registration if i == 0 else "",
				special_case=v.special_case if i == 0 else "", counterparty_address=v.address if i == 0 else "", gross=v.gross if i == 0 else None,
				separator=1 if i == 0 else 0,
			))
			total_debit += flt(d[1]) if d else 0
			total_credit += flt(c[1]) if c else 0
	data.append(frappe._dict(debit_account=_("合計"), debit=total_debit, credit_account="", credit=total_credit, bold=1))
	return columns(), data


def merge(lines, field):
	"""同じ伝票の同じ科目の行をまとめる。"""
	out = OrderedDict()
	for l in lines:
		out[l.account] = out.get(l.account, 0) + flt(l.get(field))
	return list(out.items())


def columns():
	return [
		{"fieldname": "posting_date", "label": "日付", "fieldtype": "Date", "width": 95},
		{"fieldname": "voucher_type", "label": "伝票種別", "fieldtype": "Data", "hidden": 1},
		{"fieldname": "voucher_no", "label": "伝票番号", "fieldtype": "Dynamic Link", "options": "voucher_type", "width": 165},
		{"fieldname": "debit_account", "label": "借方科目", "fieldtype": "Data", "width": 170},
		{"fieldname": "debit", "label": "借方金額", "fieldtype": "Currency", "width": 110},
		{"fieldname": "credit_account", "label": "貸方科目", "fieldtype": "Data", "width": 170},
		{"fieldname": "credit", "label": "貸方金額", "fieldtype": "Currency", "width": 110},
		{"fieldname": "remarks", "label": "摘要", "fieldtype": "Data", "width": 200},
		{"fieldname": "party", "label": "取引先", "fieldtype": "Data", "width": 160},
		{"fieldname": "tax_category", "label": "税区分", "fieldtype": "Data", "width": 220},
		{"fieldname": "gross", "label": "支払対価（税込）", "fieldtype": "Currency", "width": 120},
		{"fieldname": "registration", "label": "登録番号", "fieldtype": "Data", "width": 70},
		{"fieldname": "special_case", "label": "帳簿のみ保存の特例", "fieldtype": "Data", "width": 140},
		{"fieldname": "counterparty_address", "label": "相手方の住所又は所在地", "fieldtype": "Data", "width": 180},
	]
