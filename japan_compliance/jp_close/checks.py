"""締め前チェック（月次締め・期間ロック要件 5章 C01〜C15。C18 は本アプリ独自の追加）。

各チェックは (件数, 対象の一覧) を返す。対象は「伝票種別 伝票番号」などの文字列。
"""

import frappe
from frappe.utils import flt, get_first_day, get_last_day, getdate

from japan_compliance.jp_core.company import account_by_number
from japan_compliance.jp_tax import masters

ACCOUNTING_DOCTYPES = ("Sales Invoice", "Purchase Invoice", "Journal Entry", "Payment Entry")
SUSPENSE_ACCOUNTS = (("11507", "仮払金"), ("21212", "仮受金"), ("11514", "期首残高仮勘定"), ("21219", "仕入未請求仮受"))
ADVANCE_ACCOUNTS = (("21209", "前受金"), ("11501", "前渡金"))

# (コード, 名称, 判定区分, 関数名)
CHECKS = [
	("C01", "下書きの会計伝票の残存", "エラー", "check_drafts"),
	("C02", "税区分が未設定の明細", "エラー", "check_missing_tax_category"),
	("C03", "仮受・仮払消費税等と税区分集計の一致", "警告", "check_tax_reconciliation"),
	("C04", "試算表の貸借一致", "エラー", "check_trial_balance"),
	("C05", "仮勘定の残高", "警告", "check_suspense"),
	("C06", "未消込の入出金・前受金・前渡金", "警告", "check_unallocated"),
	("C07", "マイナス残高", "警告", "check_negative"),
	("C08", "登録区分が未確認の仕入先", "警告", "check_unknown_suppliers"),
	("C09", "取引日と計上日の月のずれ", "警告", "check_transaction_month"),
	("C10", "定例仕訳の計上漏れ", "警告", "check_recurring"),
	("C14", "二重計上の疑い", "警告", "check_duplicates"),
	("C15", "勘定科目と税区分の整合", "警告", "check_account_category"),
	("C18", "凍結期間に記帳できるロールの設定", "警告", "check_frozen_role"),
	("C11", "預金残高の照合", "確認", None),
	("C12", "現金の実査", "確認", None),
	("C13", "売掛金・買掛金の残高確認", "確認", None),
]


class Period:
	def __init__(self, company, start):
		self.company = company
		self.start = get_first_day(start)
		self.end = get_last_day(start)
		self.filters = {"company": company, "from_date": self.start, "to_date": self.end}


def run(company, start):
	p = Period(company, start)
	disabled = set((frappe.db.get_value("JP Tax Settings", company, "disabled_close_checks") or "").split(","))
	results = []
	for code, name, severity, func in CHECKS:
		if severity != "エラー" and code in disabled:
			continue
		if func:
			count, refs = globals()[func](p)
			result = "NG" if count else "OK"
		else:
			count, refs, result = 0, [], "未確認"
		results.append(frappe._dict(check_code=code, check_name=name, severity=severity, result=result, count=count,
			references="\n".join(refs[:50]) + ("\n…" if len(refs) > 50 else "")))
	return results


def check_drafts(p):
	refs = []
	for dt in ACCOUNTING_DOCTYPES:
		for name in frappe.get_all(dt, filters={"company": p.company, "docstatus": 0, "posting_date": ("between", [p.start, p.end])}, pluck="name"):
			refs.append(f"{dt} {name}")
	return len(refs), refs


def check_missing_tax_category(p):
	settings = masters.get_settings(p.company)
	refs = []
	for dt, child in (("Sales Invoice", "Sales Invoice Item"), ("Purchase Invoice", "Purchase Invoice Item")):
		refs += [f"{dt} {r[0]}（行 {r[1]}）" for r in frappe.db.sql(
			f"""select p.name, i.idx from `tab{child}` i join `tab{dt}` p on p.name = i.parent
			where p.docstatus = 1 and p.company = %(company)s and p.posting_date between %(from_date)s and %(to_date)s
				and ifnull(i.jp_tax_category, '') = ''""", p.filters)]
	tax_accounts = [settings.sales_tax_account, settings.purchase_tax_account] if settings else []
	refs += [f"Journal Entry {r[0]}（行 {r[1]}）" for r in frappe.db.sql(
		"""select je.name, a.idx from `tabJournal Entry Account` a join `tabJournal Entry` je on je.name = a.parent
		join `tabAccount` acc on acc.name = a.account
		where je.docstatus = 1 and je.company = %(company)s and je.posting_date between %(from_date)s and %(to_date)s
			and ifnull(je.jp_tax_settlement, 0) = 0 and ifnull(a.jp_tax_category, '') = ''
			and acc.report_type = 'Profit and Loss' and a.account not in %(tax_accounts)s""",
		{**p.filters, "tax_accounts": tax_accounts or [""]})]
	return len(refs), refs


def check_tax_reconciliation(p):
	from japan_compliance.jp_form.report.jp_consumption_tax_summary.jp_consumption_tax_summary import collect, gl_balance

	settings = masters.get_settings(p.company)
	if not settings:
		return 0, []
	rows = collect(frappe._dict(p.filters), settings)
	refs = []
	for label, account, direction in (("仮受消費税等", settings.sales_tax_account, "売上"), ("仮払消費税等", settings.purchase_tax_account, "仕入")):
		gl = gl_balance(account, frappe._dict(p.filters), direction)
		tax = sum(flt(r.posted_tax) for r in rows if masters.get_category(r.tax_category).direction == direction)
		if round(gl) != round(tax):
			refs.append(f"{label}：元帳の増減 {gl:,.0f} ／ 税区分の集計 {tax:,.0f} ／ 差額 {gl - tax:,.0f}")
	return len(refs), refs


def check_trial_balance(p):
	debit, credit = frappe.db.sql(
		"""select sum(debit), sum(credit) from `tabGL Entry` where company = %s and is_cancelled = 0 and posting_date <= %s""",
		(p.company, p.end),
	)[0]
	diff = flt(debit) - flt(credit)
	return (1, [f"借方合計 {flt(debit):,.0f} ／ 貸方合計 {flt(credit):,.0f}"]) if round(diff) else (0, [])


def balance(company, account, end):
	return flt(frappe.db.sql(
		"select sum(debit) - sum(credit) from `tabGL Entry` where account = %s and company = %s and is_cancelled = 0 and posting_date <= %s",
		(account, company, end))[0][0])


def check_suspense(p):
	refs = []
	for number, label in SUSPENSE_ACCOUNTS:
		account = account_by_number(p.company, number)
		if account and round(balance(p.company, account, p.end)):
			refs.append(f"{label}（{number}）：残高 {balance(p.company, account, p.end):,.0f}")
	return len(refs), refs


def check_unallocated(p):
	refs = [f"Payment Entry {r.name}：未割当 {flt(r.unallocated_amount):,.0f}" for r in frappe.get_all(
		"Payment Entry", filters={"company": p.company, "docstatus": 1, "posting_date": ("<=", p.end), "unallocated_amount": (">", 0)},
		fields=["name", "unallocated_amount"])]
	for number, label in ADVANCE_ACCOUNTS:
		account = account_by_number(p.company, number)
		if account and round(balance(p.company, account, p.end)):
			refs.append(f"{label}（{number}）：残高 {abs(balance(p.company, account, p.end)):,.0f}")
	return len(refs), refs


def check_negative(p):
	refs = []
	for acc in frappe.get_all("Account", filters={"company": p.company, "is_group": 0, "account_type": ("in", ["Cash", "Bank"])}, fields=["name"]):
		if balance(p.company, acc.name, p.end) < 0:
			refs.append(f"{acc.name}：残高 {balance(p.company, acc.name, p.end):,.0f}")
	for account_type, sign, label in (("Receivable", 1, "売掛金"), ("Payable", -1, "買掛金")):
		for r in frappe.db.sql(
			"""select gle.party, sum(gle.debit) - sum(gle.credit) as bal from `tabGL Entry` gle join `tabAccount` a on a.name = gle.account
			where gle.company = %s and gle.is_cancelled = 0 and gle.posting_date <= %s and a.account_type = %s and ifnull(gle.party, '') != ''
			group by gle.party""", (p.company, p.end, account_type), as_dict=True):
			if flt(r.bal) * sign < 0:
				refs.append(f"{label} {r.party}：残高 {flt(r.bal) * sign:,.0f}")
	return len(refs), refs


def check_unknown_suppliers(p):
	refs = [f"Supplier {r[0]}" for r in frappe.db.sql(
		"""select distinct p.supplier from `tabPurchase Invoice` p join `tabSupplier` s on s.name = p.supplier
		where p.docstatus = 1 and p.company = %(company)s and p.posting_date between %(from_date)s and %(to_date)s
			and ifnull(s.jp_registration_status, '不明') = '不明'""", p.filters)]
	return len(refs), refs


def check_transaction_month(p):
	refs = []
	for dt in ("Sales Invoice", "Purchase Invoice", "Journal Entry"):
		for r in frappe.get_all(dt, filters={"company": p.company, "docstatus": 1, "posting_date": ("between", [p.start, p.end]),
				"jp_transaction_date": ("is", "set")}, fields=["name", "posting_date", "jp_transaction_date"]):
			if getdate(r.jp_transaction_date).strftime("%Y%m") != getdate(r.posting_date).strftime("%Y%m"):
				refs.append(f"{dt} {r.name}：計上日 {r.posting_date} ／ 取引日 {r.jp_transaction_date}")
	return len(refs), refs


def check_recurring(p):
	refs = []
	for r in frappe.get_all("Auto Repeat", filters={"reference_doctype": ("in", ACCOUNTING_DOCTYPES), "status": "Active",
			"disabled": 0, "next_schedule_date": ("<=", p.end)}, fields=["name", "reference_doctype", "reference_document", "next_schedule_date"]):
		if frappe.db.get_value(r.reference_doctype, r.reference_document, "company") == p.company:
			refs.append(f"Auto Repeat {r.name}（{r.reference_document}）：次回 {r.next_schedule_date}")
	return len(refs), refs


def check_frozen_role(p):
	role = frappe.db.get_value("Company", p.company, "role_allowed_for_frozen_entries")
	return (1, [f"Company {p.company}：{role}"]) if role else (0, [])


def check_duplicates(p):
	"""同じ仕入先の同じ請求書番号と、同じ取引先・日付・金額の請求書（C-1 C14）。"""
	refs = []
	for row in frappe.db.sql(
		"""select supplier, bill_no, group_concat(name order by name separator '、') as names from `tabPurchase Invoice`
		where company = %(company)s and docstatus = 1 and posting_date between %(start)s and %(end)s and ifnull(bill_no, '') != ''
		group by supplier, bill_no having count(*) > 1""",
		{"company": p.company, "start": p.start, "end": p.end}, as_dict=True):
		refs.append(f"請求書番号 {row.bill_no}（{row.supplier}）：{row.names}")
	for doctype, party in (("Sales Invoice", "customer"), ("Purchase Invoice", "supplier")):
		for row in frappe.db.sql(
			f"""select {party} as party, posting_date, grand_total, group_concat(name order by name separator '、') as names from `tab{doctype}`
			where company = %(company)s and docstatus = 1 and posting_date between %(start)s and %(end)s and is_return = 0
			group by {party}, posting_date, grand_total having count(*) > 1""",
			{"company": p.company, "start": p.start, "end": p.end}, as_dict=True):
			refs.append(f"同じ取引先・日付・金額 {row.party} {row.posting_date} {flt(row.grand_total):,.0f}：{row.names}")
	return len(refs), refs


def category_exception(account, code, default):
	"""科目の既定と異なるが B-6 4.3 の想定どおりの場合の理由。該当しなければ None。"""
	cat, base = masters.get_category(code), masters.get_category(default)
	if cat.direction != base.direction:
		return "固定資産の売却額など、科目の既定と逆の取引"
	if code == "S-EXP":
		return "輸出免税の売上"
	if base.tax_type == "課税" and cat.tax_type == "課税" and cat.rate_code != base.rate_code:
		return "軽減税率の売上・経費など、税率の違い"
	if cat.is_nonregistered or base.is_nonregistered:
		return "免税事業者等からの仕入"
	if account.startswith("8106 ") and code == "S-OOS":
		return "補助金など、雑収入の不課税"
	return None


def check_account_category(p):
	"""明細の税区分と科目の既定税区分の整合（C-1 C15）。想定どおりの例外は区別して表示し、件数に入れない。"""
	lines = frappe.db.sql(
		"""select je.name as voucher, 'Journal Entry' as doctype, a.account, a.jp_tax_category as code
		from `tabJournal Entry Account` a join `tabJournal Entry` je on je.name = a.parent
		where je.company = %(company)s and je.docstatus = 1 and je.posting_date between %(start)s and %(end)s
			and ifnull(a.jp_tax_category, '') != '' and ifnull(je.jp_tax_settlement, 0) = 0
		union all
		select si.name, 'Sales Invoice', i.income_account, i.jp_tax_category
		from `tabSales Invoice Item` i join `tabSales Invoice` si on si.name = i.parent
		where si.company = %(company)s and si.docstatus = 1 and si.posting_date between %(start)s and %(end)s and ifnull(i.jp_tax_category, '') != ''
		union all
		select pi.name, 'Purchase Invoice', i.expense_account, i.jp_tax_category
		from `tabPurchase Invoice Item` i join `tabPurchase Invoice` pi on pi.name = i.parent
		where pi.company = %(company)s and pi.docstatus = 1 and pi.posting_date between %(start)s and %(end)s and ifnull(i.jp_tax_category, '') != ''""",
		{"company": p.company, "start": p.start, "end": p.end}, as_dict=True)
	warnings, exceptions = [], []
	for line in lines:
		default = frappe.get_cached_value("Account", line.account, "jp_tax_category")
		if not default or default == line.code:
			continue
		text = f"{line.doctype} {line.voucher}：{line.account} 既定 {default}／明細 {line.code}"
		reason = category_exception(line.account, line.code, default)
		if reason:
			exceptions.append(f"{text}（想定どおりの例外：{reason}）")
		else:
			warnings.append(text)
	return len(warnings), warnings + exceptions
