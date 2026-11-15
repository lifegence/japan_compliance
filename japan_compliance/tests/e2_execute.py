"""テストデータ（E-2）の「投入・実行順」4〜7 を、帳票の出力（9・TS-12）の直前の状態まで進める（検証用）。

  bench --site <site> execute japan_compliance.tests.e2_execute.run

E-3 の期待値（消費税集計表）を、テストで帳票を出す時点の伝票で算定するために使う。e2_data.load の後に実行する。
画面での確認・権限の拒否・チェック結果の確認は行わず、仕訳が変わる操作だけを E-2 の日付で再現する。
操作はすべて Administrator で行う（権限の型の切替・承認フロー・会計事務所の利用者は仕訳に影響しないため再現しない）。
jp_allow_test_data のサイトだけで動く。

  4. 9月の締め：D-015 の訂正（取消→修正版）、D-016 の提出、締めの確定、凍結（TS-07-01〜03）
  5. Immutable Ledger を有効にし、D-010 を 10/5 に取消→修正版 D-160（TS-07-04）。D-114 を 10/15 に取消→修正版（TS-05-04）
  6. D-169 を 10/31 に取消（TS-07-11）
  7. D-170 を起票→10/22 に取消→修正版、D-171（承認済み）、D-172（TS-10-07〜11）
"""

import frappe

from japan_compliance.tests import e2_data as e2

A = e2.A


def run():
	e2.check_allowed()
	frappe.set_user("Administrator")
	frappe.flags.mute_emails = True
	step4_september_close()
	step5_cancel_and_amend()
	step6_cancel_duplicate()
	step7_permissions_and_workflow()
	frappe.db.commit()
	print("E-2 の実行順 4〜7 を反映しました")


def find(doctype, did, field="remarks"):
	name = frappe.db.get_value(doctype, {field: ("like", f"{did} %"), "company": A, "docstatus": 1}, "name") \
		or frappe.db.get_value(doctype, {field: did, "company": A, "docstatus": 1}, "name")
	return frappe.get_doc(doctype, name) if name else None


def cancel_on(doc, date):
	"""取消日を指定して取り消す。Immutable Ledger が有効なら、反対仕訳はこの日付で計上される。"""
	frappe.form_dict.posting_date = date
	try:
		doc.cancel()
	finally:
		frappe.form_dict.pop("posting_date", None)


def amend(doc, date, remark, **changes):
	"""取消済みの伝票から修正版（元番号-1）を作り、date の日付で提出する。"""
	new = frappe.copy_doc(doc)
	new.amended_from = doc.name
	new.docstatus = 0
	new.posting_date = date
	if new.meta.has_field("set_posting_time"):
		new.set_posting_time = 1
	if new.meta.has_field("due_date"):
		new.due_date = date
		new.set("payment_schedule", [])  # 元の期日が写ると計上日より前になるため、作り直させる
	new.set("remarks" if doc.doctype != "Journal Entry" else "user_remark", remark)
	for fn in changes.values():
		fn(new)
	new.insert(ignore_permissions=True)
	new.submit()
	return new


# ---------------------------------------------------------------- 4. 9月の締め


def step4_september_close():
	from japan_compliance.jp_close.doctype.jp_month_close.jp_month_close import set_frozen_date

	# 9月の締めの時点では Immutable Ledger は無効（有効にするのは実行順 5 の TS-07-04）。D-015 の訂正は 9/29 のまま取り消す
	if not frappe.db.exists("JP Month Close", {"company": A, "period_start": "2026-09-01", "status": "確定"}):
		frappe.db.set_single_value("Accounts Settings", "enable_immutable_ledger", 0)
	d015 = find("Journal Entry", "D-015", "user_remark")
	if d015 and not d015.accounts[0].get("jp_tax_category"):
		# 税区分のない明細を、通信費 8,000［P-TAX-10］・仮払消費税等 800 に訂正する（9月の締め前のため 9/29 のまま）
		d015.cancel()

		def fix(new):
			bank = new.accounts[1].account
			new.set("accounts", [
				{"account": e2.acc("717"), "debit_in_account_currency": 8000, "jp_tax_category": "P-TAX-10"},
				{"account": e2.acc("11509"), "debit_in_account_currency": 800},
				{"account": bank, "credit_in_account_currency": 8800},
			])
		amend(d015, "2026-09-29", "D-015 税区分の訂正（TS-07-02）", fix=fix)

	d016 = frappe.db.get_value("Sales Invoice", {"remarks": ("like", "D-016 %"), "company": A, "docstatus": 0}, "name")
	if d016:
		frappe.get_doc("Sales Invoice", d016).submit()

	if not frappe.db.exists("JP Month Close", {"company": A, "period_start": "2026-09-01", "status": "確定"}):
		name = frappe.db.get_value("JP Month Close", {"company": A, "period_start": "2026-09-01"}, "name")
		mc = frappe.get_doc("JP Month Close", name) if name else frappe.get_doc(
			{"doctype": "JP Month Close", "company": A, "period_start": "2026-09-01"}).insert(ignore_permissions=True)
		mc.run_checks()
		mc.reload()
		for row in mc.checks:
			if row.result not in ("OK",) and not row.comment:
				row.comment = "E-2 の想定どおり（TS-07-02 で理由を記入）"
		mc.save(ignore_permissions=True)
		mc.close()
	set_frozen_date(A, "2026-09-30")


# ---------------------------------------------------------------- 5. 当月取消と修正版


def step5_cancel_and_amend():
	frappe.db.set_single_value("Accounts Settings", "enable_immutable_ledger", 1)
	d010 = find("Sales Invoice", "D-010")
	if d010:
		cancel_on(d010, "2026-10-05")
		amend(d010, "2026-10-05", "D-160 D-010 の修正版（TS-07-04）",
			rate=lambda new: setattr(new.items[0], "rate", 5500))
	d114 = find("Sales Invoice", "D-114")
	if d114 and not d114.amended_from:
		cancel_on(d114, "2026-10-15")
		amend(d114, "2026-10-15", "D-114 修正版（TS-05-04、単価 6,000）",
			rate=lambda new: setattr(new.items[0], "rate", 6000))


# ---------------------------------------------------------------- 6. 二重計上の疑い（C14）の取消


def step6_cancel_duplicate():
	d169 = find("Purchase Invoice", "D-169")
	if d169:
		cancel_on(d169, "2026-10-31")


# ---------------------------------------------------------------- 7. 権限・承認フロー


def step7_permissions_and_workflow():
	if not frappe.db.exists("Journal Entry", {"user_remark": ("like", "D-170 %"), "company": A}):
		d170 = e2.je("D-170", "2026-10-22", [("720", 3000, 0, "P-TAX-10"), ("11509", 300, 0, None), ("11101", 0, 3300, None)],
			remark="消耗品費（TS-10-07 で取消→修正版）")
		cancel_on(d170, "2026-10-22")

		def fix(new):
			new.accounts[0].debit_in_account_currency = 3500
			new.accounts[1].debit_in_account_currency = 350
			new.accounts[2].credit_in_account_currency = 3850
		amend(d170, "2026-10-22", "D-170 修正版（TS-10-07、3,500＋350）", fix=fix)
	e2.je("D-171", "2026-10-22", [("715", 5000, 0, "P-TAX-10"), ("11509", 500, 0, None),
		("21203", 0, 5500, None, {"party_type": "Supplier", "party": e2.SUP_C})], remark="会議費（TS-10-08・09 で承認）")
	e2.je("D-172", "2026-10-23", [("717", 2000, 0, "P-TAX-10"), ("11509", 200, 0, None), ("11101", 0, 2200, None)],
		remark="通信費（TS-10-11、会計事務所（記帳代行型）の起票）")
