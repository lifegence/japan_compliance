"""テストデータ（E-2）の投入（L-30）。

  bench --site <site> execute japan_compliance.tests.e2_data.load
  bench --site <site> execute japan_compliance.tests.e2_data.reload   … 会社A・B の伝票を消してから投入し直す（jp_allow_test_data のサイトのみ）

投入するのは、マスタ・期首残高と、E-2「投入・実行順」の 2〜3・5・6・10・10a の伝票。締め・取消・権限の切替・
承認フロー・端数処理の一時変更・税率改正など、テストの実行そのもの（L-31）にあたる操作は投入しない
（D-016 の提出、D-018、D-114 の取消と修正版、D-160、D-169 の取消、D-170〜172、D-131・132、D-136、D-180〜182）。
伝票はデータ ID（D-nnn）を remarks / user_remark の先頭に入れ、何度実行しても二重に作らない。
"""

import frappe
from frappe.utils import flt

from japan_compliance.jp_core.company import account_by_number
from japan_compliance.tests import fixtures as fx

A, B = fx.COMPANY_A, fx.COMPANY_B
CUST_A, CUST_B, CUST_C = (fx.customer(i) for i in range(3))
SUP_C, SUP_D, SUP_E, SUP_F, SUP_G, SUP_H, TRANSIT, STAFF_TRAVEL, STAFF_COMMUTE = (fx.supplier(i) for i in range(9))

# 再投入で消す伝票の種類（マスタ・JP Tax Settings・会計年度は消さない）
TRANSACTIONS = (
	"Sales Invoice", "Purchase Invoice", "Journal Entry", "Payment Entry", "GL Entry", "Payment Ledger Entry",
	"Advance Payment Ledger Entry", "JP Month Close", "Repost Accounting Ledger", "Repost Payment Ledger",
)


@frappe.whitelist(methods=["POST"])
def load_api():
	"""テスト用のサイトで、管理者がブラウザから投入するための入口。サイト設定 jp_allow_test_data が 1 のときだけ動く。"""
	frappe.only_for("System Manager")
	check_allowed()
	frappe.enqueue(load, queue="long", timeout=3600)
	return "テストデータの投入をバックグラウンドで開始しました。数分後に伝票一覧を確認してください。"


def check_allowed():
	if not frappe.conf.get("jp_allow_test_data"):
		frappe.throw("このサイトではテストデータを投入できません（site_config の jp_allow_test_data が未設定）。")


def load():
	frappe.flags.mute_emails = True
	fx.ensure_all()
	for company in (A, B):
		ensure_withholding_subaccounts(company)
	ensure_bank_subaccounts(A)
	ensure_usd_receivable(A)
	opening()
	september()
	october()
	november()
	company_b()
	frappe.db.commit()
	print("E-2 の投入が完了しました")


def reload():
	"""会社A・B の伝票をすべて消してから投入し直す（E-2 の版を上げたとき）。jp_allow_test_data のサイトだけ。"""
	check_allowed()
	frappe.set_user("Administrator")
	frappe.flags.jp_test_data_reload = True
	for company in (A, B):
		if frappe.db.exists("Company", company):
			delete_transactions(company)
			# 実行順 4 の凍結（e2_execute）を戻す。凍結したままだと9月の伝票を入れられない
			frappe.db.set_value("Company", company, "accounts_frozen_till_date", None)
	# 実行順 5 で有効にする Immutable Ledger を戻す（投入時点は無効。取消は取り消した伝票の日付で戻る）
	frappe.db.set_single_value("Accounts Settings", "enable_immutable_ledger", 0)
	frappe.db.commit()
	load()


def delete_transactions(company):
	tdr = frappe.get_doc({"doctype": "Transaction Deletion Record", "company": company, "process_in_single_transaction": 1})
	tdr.insert(ignore_permissions=True)
	tdr.generate_to_delete_list()
	tdr.reload()
	tdr.set("doctypes_to_delete", [d for d in tdr.doctypes_to_delete if d.doctype_name in TRANSACTIONS])
	tdr.save(ignore_permissions=True)
	tdr.submit()
	tdr.reload()
	if tdr.status != "Completed":
		frappe.throw(f"{company} の伝票の削除に失敗しました（{tdr.name}）：{tdr.error_log}")
	print(f"{company}：伝票を削除しました（{tdr.name}）")


# ---------------------------------------------------------------- 共通


def exists(did):
	for dt, field in (("Sales Invoice", "remarks"), ("Purchase Invoice", "remarks"), ("Journal Entry", "user_remark"), ("Payment Entry", "remarks")):
		if frappe.db.exists(dt, {field: did, "docstatus": ("<", 2)}) or frappe.db.exists(dt, {field: ("like", f"{did} %"), "docstatus": ("<", 2)}):
			return True
	return False


def acc(number, company=A):
	name = account_by_number(company, number)
	if not name:
		raise frappe.ValidationError(f"科目 {number} が {company} にありません")
	return name


def sub(label, company=A):
	"""補助科目（普通預金・預り金の子科目）を科目名で引く。"""
	name = frappe.db.get_value("Account", {"company": company, "account_name": label}, "name")
	if not name:
		raise frappe.ValidationError(f"科目 {label} が {company} にありません")
	return name


def bank_a(company=A):
	return sub("普通預金 A銀行", company)


def bank_b(company=A):
	return sub("普通預金 B銀行", company)


def wht(kind, company=A):
	"""預り金の子科目（源泉所得税・住民税・社会保険料）。"""
	return sub(f"預り金 {kind}", company)


def ensure_subaccounts(company, number, children, account_type=None):
	"""科目を子科目に分ける（勘定科目一覧 補助科目方針）。記帳済みの科目はグループにできないので何もしない。"""
	parent = frappe.db.get_value("Account", {"company": company, "account_number": number}, "name")
	if not frappe.db.get_value("Account", parent, "is_group"):
		if frappe.db.exists("GL Entry", {"account": parent}):
			return
		frappe.db.set_value("Account", parent, "is_group", 1)
	for label, child_number in children:
		if not frappe.db.exists("Account", {"company": company, "account_name": label}):
			frappe.get_doc({"doctype": "Account", "account_name": label, "account_number": child_number, "parent_account": parent,
				"company": company, "account_type": account_type, "root_type": frappe.db.get_value("Account", parent, "root_type")}).insert(ignore_permissions=True)


def ensure_bank_subaccounts(company):
	"""普通預金（11104）を口座別の子科目に分ける（B-6 F-01）。"""
	ensure_subaccounts(company, "11104", (("普通預金 A銀行", "11104-01"), ("普通預金 B銀行", "11104-02")), "Bank")
	frappe.db.set_value("Company", company, "default_bank_account", bank_a(company))


def ensure_withholding_subaccounts(company):
	"""預り金（21210）を源泉所得税・住民税・社会保険料の子科目に分ける（E-2、B-6 G-01・G-07）。"""
	ensure_subaccounts(company, "21210", (("預り金 源泉所得税", "21210-01"), ("預り金 住民税", "21210-02"), ("預り金 社会保険料", "21210-03")))


def ensure_usd_receivable(company):
	"""外貨（USD）の得意先用の売掛金（B-6 O-02）。取引先勘定は通貨ごとに分ける必要がある。"""
	name = frappe.db.get_value("Account", {"company": company, "account_name": "売掛金（USD）"}, "name")
	if not name:
		parent = frappe.db.get_value("Account", acc("11203", company), "parent_account")
		name = frappe.get_doc({"doctype": "Account", "account_name": "売掛金（USD）", "account_number": "11203-USD", "parent_account": parent,
			"company": company, "account_type": "Receivable", "root_type": "Asset", "account_currency": "USD"}).insert(ignore_permissions=True).name
	customer = frappe.get_doc("Customer", CUST_B)
	if not any(a.company == company for a in customer.accounts):
		customer.append("accounts", {"company": company, "account": name})
		customer.save(ignore_permissions=True)
	# 入金の起票時に換算レートを引く。外部 API に頼らないよう登録しておく
	if not frappe.db.exists("Currency Exchange", {"from_currency": "USD", "to_currency": "JPY"}):
		frappe.get_doc({"doctype": "Currency Exchange", "date": "2020-01-01", "from_currency": "USD", "to_currency": "JPY",
			"exchange_rate": 150, "for_buying": 1, "for_selling": 1}).insert(ignore_permissions=True)


def cost_center(company):
	return frappe.get_cached_value("Company", company, "cost_center")


def si(did, posting_date, items, customer=CUST_A, company=A, submit=True, **kw):
	if exists(did):
		return None
	doc = frappe.get_doc({
		"doctype": "Sales Invoice", "company": company, "customer": customer, "posting_date": posting_date, "set_posting_time": 1,
		"due_date": kw.pop("due_date", posting_date), "currency": kw.pop("currency", "JPY"), "conversion_rate": kw.pop("conversion_rate", 1),
		"remarks": f"{did} {kw.pop('note', '')}".strip(),
		"items": [row(i) for i in items], **kw,
	})
	doc.insert(ignore_permissions=True)
	if submit:
		doc.submit()
	return doc


def pi(did, posting_date, items, supplier=SUP_C, company=A, submit=True, paid_from=None, **kw):
	"""仕入請求書。paid_from を指定すると支払済み（is_paid）で起票する（交通費・レシート・手土産など）。"""
	if exists(did):
		return None
	doc = frappe.get_doc({
		"doctype": "Purchase Invoice", "company": company, "supplier": supplier, "posting_date": posting_date, "set_posting_time": 1,
		"due_date": kw.pop("due_date", posting_date), "bill_date": posting_date, "currency": "JPY", "conversion_rate": 1,
		"remarks": f"{did} {kw.pop('note', '')}".strip(),
		"items": [row(i) for i in items], **kw,
	})
	if paid_from:
		doc.update({"is_paid": 1, "cash_bank_account": paid_from, "mode_of_payment": mode_of_payment(paid_from)})
	doc.insert(ignore_permissions=True)
	if paid_from:
		doc.paid_amount = doc.base_paid_amount = doc.grand_total
		doc.save(ignore_permissions=True)
	if submit:
		doc.submit()
	return doc


def mode_of_payment(account):
	"""支払済みの仕入請求書に必要な支払方法。現金なら Cash、預金なら Bank Draft（ERPNext の既定の支払方法）。"""
	account_type = frappe.get_cached_value("Account", account, "account_type")
	name = "Cash" if account_type == "Cash" else "Bank Draft"
	return name if frappe.db.exists("Mode of Payment", name) else None


def row(item):
	code, qty, rate, *extra = item
	r = {"item_code": code, "qty": qty, "rate": rate}
	if extra:
		r.update(extra[0])
	return r


def je(did, posting_date, lines, company=A, submit=True, remark="", **kw):
	"""lines = [(科目番号 or 科目名, 借方, 貸方, 税区分, extra)]"""
	if exists(did):
		return None
	rows = []
	for account, debit, credit, category, *extra in lines:
		r = {"account": account if " - " in str(account) else acc(account, company),
			"debit_in_account_currency": debit, "credit_in_account_currency": credit}
		if category:
			r["jp_tax_category"] = category
		if extra:
			r.update(extra[0])
		rows.append(r)
	if kw.get("voucher_type") in ("Bank Entry", "Contra Entry"):
		# 入出金の振替伝票は参照番号・参照日が必須
		kw.setdefault("cheque_no", did)
		kw.setdefault("cheque_date", posting_date)
	doc = frappe.get_doc({"doctype": "Journal Entry", "company": company, "posting_date": posting_date,
		"user_remark": f"{did} {remark}".strip(), "accounts": rows, **kw})
	doc.insert(ignore_permissions=True)
	if submit:
		doc.submit()
	return doc


def receive(did, posting_date, invoice, amount, deductions=(), company=A, to_account=None, **kw):
	"""入金。invoice の残高に amount＋控除を割り当てる。"""
	if exists(did):
		return None
	from erpnext.accounts.doctype.payment_entry.payment_entry import get_payment_entry

	pe = get_payment_entry("Sales Invoice", invoice.name)
	pe.paid_to = to_account or bank_a(company)
	pe.paid_to_account_currency = "JPY"
	pe.posting_date = posting_date
	pe.reference_no, pe.reference_date = did, posting_date
	pe.remarks = f"{did} 入金"
	pe.paid_amount = pe.received_amount = amount
	add_deductions(pe, deductions, company)
	pe.update(kw)
	pe.set_amounts()
	pe.insert(ignore_permissions=True)
	pe.submit()
	return pe


def pay(did, posting_date, invoice, amount, deductions=(), company=A, from_account=None):
	"""支払。invoice の残高に支払額を割り当て、振込手数料などは控除欄に入れる。amount は口座から出る金額。"""
	if exists(did):
		return None
	from erpnext.accounts.doctype.payment_entry.payment_entry import get_payment_entry

	pe = get_payment_entry("Purchase Invoice", invoice.name)
	pe.paid_from = from_account or bank_a(company)
	pe.paid_from_account_currency = "JPY"
	pe.posting_date = posting_date
	pe.reference_no, pe.reference_date = did, posting_date
	pe.remarks = f"{did} 支払"
	pe.paid_amount = pe.received_amount = amount
	add_deductions(pe, deductions, company)
	pe.set_amounts()
	pe.insert(ignore_permissions=True)
	pe.submit()
	return pe


def advance(did, posting_date, payment_type, party_type, party, amount, company=A, note=""):
	"""請求書に割り当てない前受金の受領・前渡金の支払。前受金（21209）・前渡金（11501）に取引先別に計上される（B-6 S-08・P-12）。"""
	if exists(did):
		return None
	receive_ = payment_type == "Receive"
	party_account = acc("21209" if receive_ else "11501", company)
	bank = bank_a(company)
	pe = frappe.get_doc({
		"doctype": "Payment Entry", "payment_type": payment_type, "company": company, "posting_date": posting_date,
		"party_type": party_type, "party": party,
		"paid_from": party_account if receive_ else bank, "paid_to": bank if receive_ else party_account,
		"paid_from_account_currency": "JPY", "paid_to_account_currency": "JPY",
		"paid_amount": amount, "received_amount": amount, "source_exchange_rate": 1, "target_exchange_rate": 1,
		"reference_no": did, "reference_date": posting_date, "remarks": f"{did} {note}".strip(),
	})
	pe.insert(ignore_permissions=True)
	pe.submit()
	return pe


def add_deductions(pe, deductions, company):
	for account, value, category in deductions:
		pe.append("deductions", {"account": acc(account, company), "amount": value, "jp_tax_category": category,
			"cost_center": cost_center(company)})


def withholding(amount, company=A):
	"""源泉所得税の控除行（B-6 G-07、現在の版は金額を入力）。"""
	return {"charge_type": "Actual", "account_head": wht("源泉所得税", company), "description": "源泉所得税",
		"tax_amount": amount, "category": "Total", "add_deduct_tax": "Deduct", "cost_center": cost_center(company)}


# ---------------------------------------------------------------- 2. 期首残高（会社A・2026-04-01）


def opening():
	temp = acc("11514")
	si("D-001", "2026-04-01", [("ITEM-X", 1, 330000, {"income_account": temp, "jp_tax_category": "S-OOS"})], is_opening="Yes",
		note="期首の未決済請求書")
	pi("D-002", "2026-04-01", [("ITEM-X", 1, 220000, {"expense_account": temp, "jp_tax_category": "P-OOS"})], is_opening="Yes",
		note="期首の未決済請求書")
	je("D-003", "2026-04-01", [
		("11101", 500000, 0, None), (bank_a(), 4000000, 0, None), (bank_b(), 1000000, 0, None), ("11401", 200000, 0, None),
		("12106", 300000, 0, None), ("11514", 110000, 0, None), ("12108", 0, 220000, None),
		(wht("源泉所得税"), 0, 7000, None), (wht("住民税"), 0, 10000, None),
		("2201", 0, 3000000, None), ("31101", 0, 1000000, None), ("3142201", 0, 1873000, None),
	], voucher_type="Opening Entry", is_opening="Yes", remark="期首残高")


# ---------------------------------------------------------------- 3. 会社A 2026年9月


def september():
	si("D-010", "2026-09-10", [("ITEM-X", 10, 5000)], note="TS-07-04 で当月取消・修正版")
	si("D-011", "2026-09-15", [("ITEM-X", 4, 5000)], note="TS-05-02 の返品の基")
	je("D-012", "2026-09-20", [("11507", 30000, 0, None), ("11101", 0, 30000, None)], remark="仮払金（出張）")
	pi("D-013", "2026-09-21", [("ITEM-Z", 1, 55000)], supplier=SUP_D, jp_transaction_date="2026-10-20", jp_tax_inclusive=1,
		note="役務の完了日 10/20")
	pi("D-014", "2026-09-25", [("ITEM-X", 1, 55000)], supplier=SUP_D, jp_tax_inclusive=1, note="9/30 までの引渡し分")
	d015 = je("D-015", "2026-09-29", [("717", 8800, 0, None), (bank_a(), 0, 8800, None)], remark="税区分のない明細（データ移行を想定）")
	if d015:
		# 科目から税区分が自動で入るため、データ移行で欠けた状態を再現する（E-2 D-015）
		frappe.db.set_value("Journal Entry Account", d015.accounts[0].name, "jp_tax_category", None)
	si("D-016", "2026-09-28", [("ITEM-X", 1, 10000)], submit=False, note="下書きのまま残す（C01）")
	pi("D-017", "2026-09-30", [("ITEM-Z", 1, 55000)], supplier=SUP_D, jp_tax_inclusive=1, note="80%")
	je("D-019", "2026-09-29", [("11509", 1, 0, None), ("8106", 0, 1, "S-OOS")], remark="仮払消費税等の1円のずれ（C03）")


# ---------------------------------------------------------------- 5〜6. 会社A 2026年10月


def october():
	d101 = si("D-101", "2026-10-13", [("ITEM-X", 20, 5000)])
	d102 = si("D-102", "2026-10-13", [("ITEM-X", 2, 5000)], note="現金売上")
	if d102:
		receive("D-102P", "2026-10-13", d102, 11000, to_account=acc("11101"))
	d103 = pi("D-103", "2026-10-13", [("ITEM-X", 20, 2500)])
	pi("D-104", "2026-10-13", [("ITEM-Y", 10, 1000)])
	si("D-105", "2026-10-13", [("ITEM-X", 1, 5000), ("ITEM-Y", 1, 2000)], submit=False, note="税区分の自動選択（TS-10-04 で削除）")
	si("D-106", "2026-10-13", [("ITEM-X", 40, 5000)], customer=CUST_C)
	je("D-107", "2026-10-15", [(bank_a(), 847, 0, None), ("9302", 153, 0, "P-OOS"), ("8101", 0, 1000, "S-EXM")], remark="預金利息")
	je("D-108", "2026-10-25", [("703", 300000, 0, "P-OOS"), (wht("源泉所得税"), 0, 7000, None), (wht("住民税"), 0, 10000, None),
		(wht("社会保険料"), 0, 45000, None), (bank_a(), 0, 238000, None)], voucher_type="Bank Entry", remark="10月分給与")
	# 経費の請求書は未払金（21203、仕入先別に管理）に計上する（B-6 P-03）
	pi("D-109", "2026-10-13", [("SVC-TEL", 1, 8000)], credit_to=acc("21203"), note="通信費")
	si("D-110", "2026-10-13", [("ITEM-X", 6, 5000), ("ITEM-Y", 10, 2000)], note="混在請求書")
	si("D-111", "2026-10-14", [("ITEM-X", 1, 1980), ("ITEM-X", 1, 2530), ("ITEM-X", 1, 715),
		("ITEM-Y", 2, 398), ("ITEM-Y", 1, 1058), ("ITEM-Y", 5, 129)], note="B-3 5.2")
	si("D-112", "2026-10-14", [("ITEM-X", 1, 1000), ("ITEM-Y", 1, 500)] * 20, note="明細40行")
	si("D-113", "2026-10-15", [("ITEM-X", 1, 5000)], note="採番の確認")
	si("D-114", "2026-10-15", [("ITEM-X", 1, 5000)], note="TS-05-04 で取消→修正版")
	pi("D-121", "2026-10-01", [("ITEM-Z", 1, 55000)], supplier=SUP_D, jp_tax_inclusive=1, note="70%")
	pi("D-122", "2026-10-01", [("ITEM-Z", 1, 33333)], supplier=SUP_D, jp_tax_inclusive=1, note="70%・端数")
	pi("D-123", "2026-10-05", [("ITEM-X", 1, 55000)], supplier=SUP_D, jp_tax_inclusive=1, note="10/1 以後の引渡し分")
	# 交通費・通勤定期は税込で入力し、システムが割り戻して税額を計算する（B-6 P-04・G-06）
	pi("D-124", "2026-10-14", [("TRANS", 1, 1100, {"description": "電車賃"})], supplier=TRANSIT, jp_tax_inclusive=1,
		jp_special_case="公共交通機関特例", paid_from=acc("11101"), note="電車賃")
	pi("D-125", "2026-10-14", [("TRANS", 1, 3300, {"description": "出張の日当"})], supplier=STAFF_TRAVEL, jp_tax_inclusive=1,
		jp_special_case="出張旅費等特例", paid_from=acc("11101"), note="出張の日当")
	pi("D-126", "2026-10-24", [("COMMUTE", 1, 33000)], supplier=STAFF_COMMUTE, jp_tax_inclusive=1,
		jp_special_case="通勤手当", paid_from=bank_a(), note="通勤定期")
	si("D-130", "2026-10-16", [("ITEM-X", 3, 1234), ("ITEM-X", 1, 567), ("ITEM-X", 1, 89)], note="B-3 5.1")
	for i, gross, item in ((1, 1000, "ITEM-X"), (2, 10999, "ITEM-X"), (3, 1080, "ITEM-Y"), (4, 3218, "ITEM-Y")):
		si(f"D-133-{i}", "2026-10-16", [(item, 1, gross)], jp_tax_inclusive=1, note="B-3 5.4 内税")
	pi("D-134", "2026-10-16", [("ITEM-X", 1, 4793)], jp_tax_inclusive=1, jp_stated_tax_std=436, note="記載税額 436")
	pi("D-135", "2026-10-16", [("ITEM-X", 1, 4793)], jp_tax_inclusive=1, jp_stated_tax_std=440, note="記載税額 440")
	# 適格簡易請求書（レシート。税額の記載なし）は税込から割り戻す（TS-04-11）
	pi("D-137", "2026-10-24", [("SUPPLY", 1, 1230)], supplier=SUP_H, jp_tax_inclusive=1, paid_from=acc("11101"), note="適格簡易請求書（消耗品）")
	pi("D-138", "2026-10-24", [("ITEM-Y", 1, 1078)], supplier=SUP_H, jp_tax_inclusive=1, paid_from=acc("11101"), note="適格簡易請求書（食品）")
	if d101:
		si("D-140", "2026-10-20", [("ITEM-X", -1, 10000, {"income_account": acc("402")})], is_return=1, return_against=d101.name, note="値引")
		si("D-141", "2026-10-20", [("ITEM-X", -4, 5000, {"income_account": acc("403")})], is_return=1,
			jp_original_period="2026年9月〜10月分", note="返品（D-011・D-101）")
	if d103:
		pi("D-142", "2026-10-20", [("ITEM-X", -2, 2500, {"expense_account": acc("5202")})], is_return=1, return_against=d103.name, note="仕入値引")
	d143 = si("D-143", "2026-10-02", [("ITEM-X", 20, 5000)], note="入金の基（税込 110,000）")
	if d143:
		receive("D-144", "2026-10-31", d143, 109340, deductions=[("402", 600, "S-TAX-10"), ("21216", 60, None)])
	d145 = si("D-145", "2026-10-02", [("ITEM-X", 6, 5000)], note="貸倒の基")
	if d145:
		je("D-146", "2026-10-31", [("739", 30000, 0, "S-TAX-10"), ("21216", 3000, 0, None),
			("11203", 0, 33000, None, {"party_type": "Customer", "party": CUST_A, "reference_type": "Sales Invoice", "reference_name": d145.name})],
			voucher_type="Write Off Entry", jp_transaction_kind="貸倒", remark="売掛金の貸倒れ")
	if d103:
		pay("D-147", "2026-10-31", d103, 55440, deductions=[("726", 400, "P-TAX-10"), ("11509", 40, None)])
	d150 = si("D-150", "2026-10-13", [("ITEM-X", 1, 1000)], customer=CUST_B, currency="USD", conversion_rate=150, note="USD 建て")
	pi("D-152", "2026-10-16", [("FREIGHT", 1, 2000)], supplier=SUP_F, note="引取運賃")
	advance("D-153", "2026-10-06", "Pay", "Supplier", SUP_C, 22000, note="前渡金の支払")
	pi("D-154", "2026-10-20", [("ITEM-X", 40, 2500)], allocate_advances_automatically=1, note="前渡金 22,000 を充当")
	pi("D-155", "2026-10-21", [("GIFT-Y", 1, 5000)], supplier=SUP_H, paid_from=acc("11101"), note="手土産（食品）")
	je("D-156", "2026-10-20", [(bank_a(), 1000000, 0, None), ("8106", 0, 1000000, "S-OOS")], voucher_type="Bank Entry", remark="補助金の受取")
	je("D-157", "2026-10-25", [("701", 500000, 0, "P-OOS"), (wht("源泉所得税"), 0, 15000, None), (wht("住民税"), 0, 20000, None),
		(wht("社会保険料"), 0, 70000, None), (bank_a(), 0, 395000, None)], voucher_type="Bank Entry", remark="10月分役員報酬")
	d158 = pi("D-158", "2026-10-20", [("SVC-FEE", 1, 100000)], supplier=SUP_G, credit_to=acc("21203"),
		taxes=[withholding(10210)], note="デザイン報酬（源泉所得税 10,210 を控除）")
	if d158:
		pay("D-159", "2026-10-31", d158, 99790)
	je("D-162", "2026-10-31", [("736", 5000, 0, None), ("12108", 0, 5000, None)], voucher_type="Depreciation Entry", remark="減価償却費（月割）")
	je("D-163", "2026-10-01", [("11503", 120000, 0, None), (bank_a(), 0, 120000, None)], remark="年払保険料")
	je("D-164", "2026-10-31", [("731", 10000, 0, None), ("11503", 0, 10000, None)], remark="保険料の月割")
	je("D-165", "2026-10-31", [("723", 10000, 0, None), ("11509", 1000, 0, None), ("21204", 0, 11000, None)], remark="未払費用（水道光熱費）")
	je("D-167", "2026-10-31", [("5101", 200000, 0, "P-OOS"), ("11401", 0, 200000, None), ("11401", 250000, 0, None), ("5401", 0, 250000, "P-OOS")],
		remark="期末商品の棚卸（三分法）")
	# C14（二重計上の疑い）：同じ請求書番号の2件。D-169 はチェック後に取り消す（TS-07-11 の実行で行う）
	pi("D-168", "2026-10-22", [("ITEM-X", 2, 2500)], bill_no="INV-C-1001", note="C14 の1件目")
	pi("D-169", "2026-10-22", [("ITEM-X", 2, 2500)], bill_no="INV-C-1001", note="C14 の2件目（TS-07-11 で取消）")
	je("D-173", "2026-10-22", [("717", 2000, 0, "P-OOS"), ("11101", 0, 2000, None)], remark="科目と税区分の不整合（C15）")
	advance("D-190", "2026-10-05", "Receive", "Customer", CUST_A, 55000, note="前受金の受領")
	si("D-191", "2026-10-20", [("ITEM-X", 10, 5000)], allocate_advances_automatically=1, note="前受金 55,000 を充当")
	je("D-192", "2026-10-09", [(wht("源泉所得税"), 7000, 0, None), (wht("住民税"), 10000, 0, None), (bank_a(), 0, 17000, None)],
		voucher_type="Bank Entry", remark="9月分の源泉所得税・住民税の納付")
	je("D-193", "2026-10-05", [(bank_b(), 100000, 0, None), (bank_a(), 0, 100000, None)], voucher_type="Contra Entry", remark="口座間の振替")
	je("D-194", "2026-10-27", [("2201", 80000, 0, None), ("8201", 5000, 0, None), (bank_a(), 0, 85000, None)], voucher_type="Bank Entry",
		remark="借入金の返済")
	pi("D-195", "2026-10-08", [("ASSET-EQ", 1, 300000)], credit_to=acc("21203"), note="備品の取得")
	je("D-196", "2026-10-30", [(bank_a(), 110000, 0, None), ("12106", 0, 100000, "S-TAX-10"), ("21216", 0, 10000, None)], remark="備品の売却（売却額）")
	je("D-197", "2026-10-30", [("12108", 220000, 0, None), ("12106", 0, 200000, None), ("9102", 0, 20000, "S-OOS")], remark="備品の売却（簿価と売却益）")
	if d150 and not exists("D-151"):
		from erpnext.accounts.doctype.payment_entry.payment_entry import get_payment_entry

		pe = get_payment_entry("Sales Invoice", d150.name)
		pe.paid_to, pe.paid_to_account_currency = bank_a(), "JPY"
		pe.posting_date, pe.reference_no, pe.reference_date, pe.remarks = "2026-10-30", "D-151", "2026-10-30", "D-151 USD 入金"
		pe.source_exchange_rate = 145
		pe.received_amount = flt(pe.paid_amount) * 145
		pe.insert(ignore_permissions=True)
		pe.submit()


# ---------------------------------------------------------------- 10a. 会社A 翌月の納付


def november():
	je("D-166", "2026-11-10", [(wht("源泉所得税"), 10210, 0, None), (bank_a(), 0, 10210, None)], voucher_type="Bank Entry",
		remark="10月分の源泉所得税（報酬）の納付")


# ---------------------------------------------------------------- 10. 会社B


def nondeductible_transfer(did, from_date, to_date, posting_date):
	"""控除不能分の振替をシステムの機能で作る（TS-03-06）。対象は D-203 の日付だけ。D-205 は上限の確認用で振り替えない。"""
	from japan_compliance.jp_tax.year_end import create_nondeductible_transfer

	if exists(did):
		return
	doc = frappe.get_doc("Journal Entry", create_nondeductible_transfer(B, from_date, to_date, posting_date))
	doc.user_remark = f"{did} {doc.user_remark}"
	doc.save(ignore_permissions=True)
	doc.submit()


def company_b():
	# 前課税期間（2025-10-01〜2026-09-30）
	si("D-210", "2026-08-20", [("ITEM-X", 20, 5000)], company=B, note="前課税期間の売上")
	d211 = si("D-211", "2026-07-15", [("ITEM-X", 6, 5000)], company=B, note="後で貸倒れとなる売掛金")
	if d211:
		je("D-212", "2026-09-20", [("739", 30000, 0, "S-TAX-10"), ("21216", 3000, 0, None),
			("11203", 0, 33000, None, {"party_type": "Customer", "party": CUST_A, "reference_type": "Sales Invoice", "reference_name": d211.name})],
			company=B, voucher_type="Write Off Entry", jp_transaction_kind="貸倒", remark="売掛金の貸倒れ（前課税期間）")
	pi("D-213", "2026-09-10", [("ITEM-X", 16, 2500)], company=B, note="前課税期間の仕入")
	d201 = je("D-201", "2026-09-15", [("717", 10000, 0, None), ("11509", 1000, 0, None),
		("21203", 0, 11000, None, {"party_type": "Supplier", "party": SUP_C})], company=B, remark="前年度の通信費")
	je("D-214", "2026-09-30", [("21216", 10000, 0, None), ("11509", 0, 5000, None), ("21208", 0, 5000, None)], company=B,
		jp_tax_settlement=1, remark="仮受・仮払消費税等の相殺（期末）")
	# 当課税期間（2026-10-01〜）
	d201 = d201 or frappe.get_doc("Journal Entry", {"user_remark": ("like", "D-201%"), "docstatus": 1, "company": B})
	je("D-202", "2026-10-20", [("9201", 22000, 0, "P-OOS"), ("21203", 0, 22000, None, {"party_type": "Supplier", "party": SUP_C})], company=B,
		jp_correction_of_type="Journal Entry", jp_correction_of=d201.name, jp_correction_reason="前年度9月分の計上漏れ", remark="前期損益修正")
	pi("D-203", "2026-10-01", [("ITEM-Z", 1, 55000)], supplier=SUP_D, company=B, jp_tax_inclusive=1, note="70%・雑損失方式")
	nondeductible_transfer("D-204", "2026-10-01", "2026-10-04", "2026-10-31")
	pi("D-205", "2026-10-05", [("ITEM-Z", 1, 60000000)], supplier=SUP_D, company=B, jp_tax_inclusive=1, note="上限の確認")
	d206 = pi("D-206", "2026-11-05", [("ITEM-Z", 1, 50000000)], supplier=SUP_D, company=B, jp_tax_inclusive=1, note="上限の確認")
	if d206:
		pi("D-207", "2026-11-20", [("ITEM-Z", -1, 5000000)], supplier=SUP_D, company=B, jp_tax_inclusive=1, is_return=1,
			return_against=d206.name, note="返品")
	je("D-215", "2026-10-15", [(acc("11104", B), 33000, 0, None), ("8108", 0, 30000, "S-TAX-10"), ("21216", 0, 3000, None)], company=B,
		voucher_type="Bank Entry", jp_transaction_kind="貸倒回収", remark="貸倒処理した売掛金の回収")
	si("D-216", "2026-10-31", [("ITEM-X", 1, 5000)], company=B, note="10月の締めの確定後に提出（期中はロックしない）")
