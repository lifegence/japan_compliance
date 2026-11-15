"""テストシナリオ一覧（E-1）のうち、他のテストモジュールで扱っていないケース。

テスト名の先頭の ts_xx_nn が E-1 のケース ID。期待値は要件定義書（B-3・B-4・B-5・B-6・C-1・C-2）の参考値。
日付は他のモジュールと重ならないよう、主に 2027年5月〜9月を使う。
"""

import io
import json
from unittest.mock import patch

import frappe
from frappe.utils import getdate, nowdate

from japan_compliance.jp_close import checks
from japan_compliance.jp_core.chart import account_tax_categories, load_chart
from japan_compliance.jp_form.report.jp_consumption_tax_summary.jp_consumption_tax_summary import execute as tax_summary
from japan_compliance.jp_form.report.jp_general_ledger.jp_general_ledger import execute as jp_ledger
from japan_compliance.jp_tax.year_end import create_nondeductible_transfer
from japan_compliance.tests import fixtures as fx
from japan_compliance.tests.test_invoice_tax import A, B, JPTaxTestCase, gl, make_pi, make_si, taxes
from japan_compliance.tests.test_permissions import ensure_user
from japan_compliance.tests.test_tax_summary import acct, make_je

# 未払金（21203）は Payable 型のため、振替伝票では取引先が必須（勘定科目一覧）
PAYABLE_PARTY = {"party_type": "Supplier", "party": fx.supplier(0)}


def messages():
	return " ".join(str(m.get("message", m)) if isinstance(m, dict) else str(m) for m in frappe.get_message_log())


def summary(view, from_date, to_date, company=A):
	return tax_summary({"company": company, "from_date": from_date, "to_date": to_date, "view": view})[1]


def rows_by(data, *keys):
	return {tuple(r.get(k) for k in keys): r for r in data}


def pay(invoice, amount, posting_date, to_account=None, deductions=(), company=A, **kw):
	from erpnext.accounts.doctype.payment_entry.payment_entry import get_payment_entry

	pe = get_payment_entry(invoice.doctype, invoice.name)
	pe.posting_date = posting_date
	pe.reference_no, pe.reference_date = "TEST", posting_date
	if invoice.doctype == "Sales Invoice":
		pe.paid_to = to_account or acct("11104", company)
		pe.paid_to_account_currency = "JPY"
		pe.paid_amount = pe.received_amount = amount
	else:
		pe.paid_from = to_account or acct("11104", company)
		pe.paid_from_account_currency = "JPY"
		pe.paid_amount = pe.received_amount = amount
	for account, value, category in deductions:
		pe.append("deductions", {"account": acct(account, company), "amount": value, "jp_tax_category": category,
			"cost_center": frappe.get_cached_value("Company", company, "cost_center")})
	pe.update(kw)
	pe.set_amounts()
	pe.insert()
	pe.submit()
	return pe


def pdf_text(doctype, name, print_format="適格請求書（JP）"):
	from frappe.utils.pdf import get_pdf
	from pypdf import PdfReader

	reader = PdfReader(io.BytesIO(get_pdf(frappe.get_print(doctype, name, print_format=print_format, no_letterhead=1))))
	return [p.extract_text() for p in reader.pages]


# ---------------------------------------------------------------- TS-01 税率別の売上・仕入


class TestTS01(JPTaxTestCase):
	def test_ts_01_02_cash_sale(self):
		si = make_si(items=[("ITEM-X", 2, 5000)], posting_date="2027-05-10", submit=True)
		pe = pay(si, 11000, "2027-05-10", to_account=acct("11101"))
		self.assertEqual(gl(si), {"11203": 11000, "401": -10000, "21216": -1000})
		self.assertEqual(gl(pe), {"11101": 11000, "11203": -11000})

	def test_ts_01_07_interest_income(self):
		je = make_je([("11104", 847, 0, None), ("9302", 153, 0, None), ("8101", 0, 1000, None)], "2027-05-15")
		self.assertEqual([a.jp_tax_category for a in je.accounts], [None, "P-OOS", "S-EXM"])
		rows = rows_by(summary("区分別集計", "2027-05-15", "2027-05-15"), "tax_category")
		self.assertEqual((rows[("S-EXM",)].net_amount, rows[("S-EXM",)].tax_amount), (1000, 0))

	def test_ts_01_08_payroll(self):
		je = make_je([("703", 300000, 0, None), ("21210", 0, 62000, None), ("11104", 0, 238000, None)], "2027-05-25")
		self.assertEqual(je.accounts[0].jp_tax_category, "P-OOS")
		rows = rows_by(summary("区分別集計", "2027-05-25", "2027-05-25"), "tax_category")
		self.assertEqual((rows[("P-OOS",)].net_amount, rows[("P-OOS",)].tax_amount), (300000, 0))

	def test_ts_01_09_expense_to_accrued_payable(self):
		# 未払金（21203）は Payable 型で仕入先別に管理する（勘定科目一覧）
		pi = make_pi(items=[("SVC-TEL", 1, 8000)], posting_date="2027-05-13", credit_to=acct("21203"), submit=True)
		self.assertEqual(gl(pi), {"717": 8000, "11509": 800, "21203": -8800})
		party = frappe.get_all("GL Entry", filters={"voucher_no": pi.name, "account": acct("21203")}, pluck="party")
		self.assertEqual(party, [fx.supplier(0)])


# ---------------------------------------------------------------- TS-02 適格請求書


class TestTS02(JPTaxTestCase):
	def test_ts_02_03_customer_rename_keeps_printed_name(self):
		si = make_si(items=[("ITEM-X", 1, 5000)], posting_date="2027-05-13", submit=True)
		customer = frappe.get_doc("Customer", fx.customer(0))
		old = customer.customer_name
		customer.customer_name = "テスト得意先A（新）株式会社"
		customer.save()
		self.addCleanup(lambda: frappe.db.set_value("Customer", fx.customer(0), "customer_name", old))
		html = frappe.get_print("Sales Invoice", si.name, print_format="適格請求書（JP）", no_letterhead=1)
		self.assertIn(old, html)
		self.assertNotIn("テスト得意先A（新）株式会社", html)

	def test_ts_02_04_forty_lines_page_break(self):
		si = make_si(items=[("ITEM-X", 1, 1000), ("ITEM-Y", 1, 500)] * 20, posting_date="2027-05-14", submit=True)
		self.assertEqual(taxes(si), [2000, 800])
		pages = pdf_text("Sales Invoice", si.name)
		self.assertGreaterEqual(len(pages), 2)
		self.assertIn("税率区分", pages[-1])
		self.assertNotIn("税率区分", pages[0])
		self.assertRegex(pages[0].replace("\n", ""), rf"1\s*/\s*{len(pages)}")

	def test_ts_02_06_numbering_and_amend(self):
		first = make_si(items=[("ITEM-X", 1, 5000)], posting_date="2027-05-15", submit=True)
		second = make_si(items=[("ITEM-X", 1, 5000)], posting_date="2027-05-15", submit=True)
		self.assertGreater(second.name, first.name)
		second.reload()
		second.cancel()
		amended = frappe.copy_doc(second)
		amended.amended_from = second.name
		amended.docstatus = 0
		amended.insert()
		self.assertEqual(amended.name, f"{second.name}-1")


# ---------------------------------------------------------------- TS-03 登録・非登録


class TestTS03(JPTaxTestCase):
	def test_ts_03_05_partial_deliveries(self):
		before = make_pi(supplier=1, items=[("ITEM-X", 1, 55000)], posting_date="2026-09-25", jp_tax_inclusive=1, submit=True)
		after = make_pi(supplier=1, items=[("ITEM-X", 1, 55000)], posting_date="2026-10-05", jp_tax_inclusive=1, submit=True)
		self.assertEqual((taxes(before), taxes(after)), ([4000], [3500]))

	def test_ts_03_06_year_end_transfer_to_misc_loss(self):
		pi = make_pi(company=B, supplier=1, items=[("ITEM-Z", 1, 55000)], posting_date="2027-06-01", jp_tax_inclusive=1, submit=True)
		name = create_nondeductible_transfer(B, "2027-06-01", "2027-06-30")
		je = frappe.get_doc("Journal Entry", name)
		self.assertEqual(je.docstatus, 0)
		self.assertEqual(str(je.posting_date), "2027-06-30")
		self.assertEqual(je.accounts[0].jp_tax_category, "P-OOS")
		je.submit()
		self.addCleanup(lambda: frappe.db.set_value("Journal Entry", name, "jp_nondeductible_period", None))
		self.assertEqual(gl(je), {"8209": 1500, "11509": -1500})
		with self.assertRaises(frappe.ValidationError):
			create_nondeductible_transfer(B, "2027-06-01", "2027-06-30")
		self.assertEqual(pi.jp_tax_breakdown[0].nondeductible_tax, 1500)
		r = {c.check_code: c for c in checks.run(B, "2027-06-01")}
		self.assertEqual(r["C03"].result, "OK")

	def test_ts_03_09_cap_ten_billion(self):
		make_pi(supplier=1, items=[("ITEM-Z", 1, 55000)], posting_date="2026-10-01", jp_tax_inclusive=1, submit=True)
		data = summary("免税事業者等の仕入先別累計", "2026-04-01", "2027-03-31")
		row = next(r for r in data if r.supplier == fx.supplier(1))
		self.assertEqual((row.cap_rule, row.cap, row.over), ("CAP-1B", 1000000000, 0))

	def test_ts_03_11_small_amount_notice(self):
		frappe.clear_messages()
		make_pi(company=B, supplier=1, items=[("ITEM-X", 1, 9999)], posting_date="2026-10-21", jp_tax_inclusive=1)
		self.assertIn("少額特例", messages())
		self.assertIn("P-TAX-", messages())
		for kw in (dict(company=B, items=[("ITEM-X", 1, 10000)]), dict(company=A, items=[("ITEM-X", 1, 9999)]),
				dict(company=B, items=[("ITEM-X", 1, 9999)], jp_transaction_date="2029-10-01")):
			frappe.clear_messages()
			make_pi(supplier=1, posting_date="2026-10-21", jp_tax_inclusive=1, **kw)
			self.assertNotIn("少額特例", messages())

	def test_registration_period(self):
		supplier = frappe.get_doc("Supplier", fx.supplier(0))
		supplier.jp_registration_to = "2027-06-30"
		supplier.save()
		self.addCleanup(lambda: frappe.db.set_value("Supplier", fx.supplier(0), "jp_registration_to", None))
		self.assertEqual(make_pi(items=[("ITEM-Z", 1, 50000)], posting_date="2027-06-30").items[0].jp_tax_category, "P-TAX-10")
		self.assertEqual(make_pi(items=[("ITEM-Z", 1, 50000)], posting_date="2027-07-01").items[0].jp_tax_category, "P-NREG-10")


# ---------------------------------------------------------------- TS-04 端数処理


class TestTS04(JPTaxTestCase):
	def test_ts_04_02_round_up(self):
		self.set_settings(sales_rounding="切上げ")
		self.assertEqual(taxes(make_si(items=[("ITEM-X", 3, 1234), ("ITEM-X", 1, 567), ("ITEM-X", 1, 89)], posting_date="2027-05-20")), [436])

	def test_ts_04_05_no_round_up_for_purchase(self):
		settings = frappe.get_doc("JP Tax Settings", A)
		settings.purchase_rounding = "切上げ"
		with self.assertRaises(frappe.ValidationError):
			settings.save()

	def test_ts_04_09_warning_only_over_one_yen(self):
		frappe.clear_messages()
		make_pi(items=[("ITEM-X", 1, 4793)], posting_date="2027-05-20", jp_tax_inclusive=1, jp_stated_tax_std=436)
		self.assertNotIn("請求書記載の消費税額", messages())
		frappe.clear_messages()
		make_pi(items=[("ITEM-X", 1, 4793)], posting_date="2027-05-20", jp_tax_inclusive=1, jp_stated_tax_std=440)
		self.assertIn("請求書記載の消費税額", messages())

	def test_ts_04_10_round_off_difference(self):
		# 外貨の明細3行を円換算すると、明細ごとの円の合計と伝票の円合計に 1 円の差が出る（B-6 O-03）
		si = make_si(customer=1, items=[("ITEM-X", 1, 0.33, {"income_account": acct("401")}), ("ITEM-X", 1, 0.33, {"income_account": acct("402")}),
			("ITEM-X", 1, 0.33, {"income_account": acct("403")})], posting_date="2027-05-20",
			currency="USD", conversion_rate=149.5, debit_to=frappe.db.get_value("Account", {"company": A, "account_currency": "USD",
				"account_type": "Receivable"}) or self.usd_receivable(), submit=True)
		# 明細の円換算額の端数は金額の大きい明細に寄せ、借方・貸方が一致する（以前は提出できなかった）
		self.assertEqual([i.base_net_amount for i in si.items], [50, 49, 49])
		self.assertEqual(si.base_grand_total, 148)
		entries = gl(si)
		self.assertEqual(sum(entries.values()), 0)
		self.assertNotIn("8211", entries)

	def usd_receivable(self):
		from japan_compliance.tests.e2_data import ensure_usd_receivable

		ensure_usd_receivable(A)
		return frappe.db.get_value("Account", {"company": A, "account_name": "売掛金（USD）"})


# ---------------------------------------------------------------- TS-05 返品・値引・取消・訂正


class TestTS05(JPTaxTestCase):
	def test_ts_05_01_credit_note_print(self):
		si = make_si(items=[("ITEM-X", 20, 5000)], posting_date="2027-06-01", submit=True)
		cn = make_si(items=[("ITEM-X", -1, 10000, {"income_account": acct("402")})], posting_date="2027-06-10",
			is_return=1, return_against=si.name, submit=True)
		self.assertEqual(gl(cn), {"402": 10000, "21216": 1000, "11203": -11000})
		html = frappe.get_print("Sales Invoice", cn.name, print_format="適格請求書（JP）", no_letterhead=1)
		for text in ("返還請求書", "登録番号 T8000000000001", si.name, "2027/06/01", "2027/06/10", "△1,000", "ご返金額（税込）"):
			self.assertIn(text, html)

	def test_ts_05_02_return_over_two_months(self):
		cn = make_si(items=[("ITEM-X", -4, 5000, {"income_account": acct("403")})], posting_date="2027-06-15", is_return=1,
			jp_original_period="2027年5月〜6月分", submit=True)
		self.assertEqual(gl(cn), {"403": 20000, "21216": 2000, "11203": -22000})
		html = frappe.get_print("Sales Invoice", cn.name, print_format="適格請求書（JP）", no_letterhead=1)
		self.assertIn("2027年5月〜6月分", html)
		self.assertEqual(cn.jp_tax_breakdown[0].transaction_kind, "返還等")

	def test_ts_05_03_debit_note(self):
		pi = make_pi(items=[("ITEM-X", 20, 2500)], posting_date="2027-06-01", submit=True)
		dn = make_pi(items=[("ITEM-X", -2, 2500, {"expense_account": acct("5202")})], posting_date="2027-06-10",
			is_return=1, return_against=pi.name, submit=True)
		self.assertEqual(gl(dn), {"21102": 5500, "5202": -5000, "11509": -500})

	def test_ts_05_04_cancel_and_amend_corrected_invoice(self):
		frappe.flags.jp_attach_copy_in_test = True
		self.addCleanup(lambda: setattr(frappe.flags, "jp_attach_copy_in_test", False))
		si = make_si(items=[("ITEM-X", 1, 5000)], posting_date="2027-06-05", submit=True)
		si.reload()
		si.cancel()
		amended = frappe.copy_doc(si)
		amended.amended_from = si.name
		amended.docstatus = 0
		amended.items[0].rate = 6000
		amended.insert()
		amended.submit()
		html = frappe.get_print("Sales Invoice", amended.name, print_format="適格請求書（JP）", no_letterhead=1)
		self.assertIn("修正インボイス", html)
		self.assertIn(f"本書は {si.name} を修正し再交付するものです", html)
		self.assertTrue(frappe.db.exists("File", {"attached_to_name": si.name, "file_name": ("like", "適格請求書の写し_%")}))
		self.assertEqual(taxes(amended), [600])

	def test_ts_05_05_customer_pays_net_of_bank_fee(self):
		si = make_si(items=[("ITEM-X", 20, 5000)], posting_date="2027-06-02", submit=True)
		pe = pay(si, 109340, "2027-06-30", deductions=[("402", 600, "S-TAX-10"), ("21216", 60, None)])
		self.assertEqual(gl(pe), {"11104": 109340, "402": 600, "21216": 60, "11203": -110000})
		rows = rows_by(summary("区分別集計", "2027-06-30", "2027-06-30"), "tax_category", "transaction_kind")
		r = rows[("S-TAX-10", "返還等")]
		self.assertEqual((r.net_amount, r.tax_amount), (-600, -60))

	def test_ts_05_07_supplier_payment_with_bank_fee(self):
		pi = make_pi(items=[("ITEM-X", 20, 2500)], posting_date="2027-06-03", submit=True)
		pe = pay(pi, 55440, "2027-06-29", deductions=[("726", 400, None), ("11509", 40, None)])
		self.assertEqual(gl(pe), {"21102": 55000, "726": 400, "11509": 40, "11104": -55440})
		self.assertEqual(pe.deductions[0].jp_tax_category, "P-TAX-10")
		rows = rows_by(summary("区分別集計", "2027-06-29", "2027-06-29"), "tax_category")
		self.assertEqual((rows[("P-TAX-10",)].net_amount, rows[("P-TAX-10",)].tax_amount), (400, 40))


# ---------------------------------------------------------------- TS-06 外貨


class TestTS06(JPTaxTestCase):
	def test_ts_06_01_usd_sale_and_exchange_loss(self):
		from japan_compliance.tests.e2_data import ensure_usd_receivable

		ensure_usd_receivable(A)
		usd = frappe.db.get_value("Account", {"company": A, "account_name": "売掛金（USD）"})
		si = make_si(customer=1, items=[("ITEM-X", 1, 1000)], posting_date="2027-06-13", currency="USD", conversion_rate=150,
			debit_to=usd, submit=True)
		self.assertEqual(si.items[0].jp_tax_category, "S-EXP")
		self.assertEqual(gl(si), {"11203-USD": 150000, "401": -150000})
		from erpnext.accounts.doctype.payment_entry.payment_entry import get_payment_entry

		pe = get_payment_entry("Sales Invoice", si.name)
		pe.posting_date, pe.reference_no, pe.reference_date = "2027-06-30", "USD", "2027-06-30"
		pe.paid_to, pe.paid_to_account_currency = acct("11104"), "JPY"
		pe.source_exchange_rate = 145
		pe.received_amount = 145000
		pe.set_amounts()
		pe.insert()
		pe.submit()
		loss = frappe.db.sql("""select sum(debit) - sum(credit) from `tabGL Entry` where account = %s and is_cancelled = 0
			and posting_date = '2027-06-30'""", acct("8210"))[0][0]
		self.assertEqual(loss, 5000)
		rows = rows_by(summary("区分別集計", "2027-06-13", "2027-06-13"), "tax_category")
		self.assertEqual(rows[("S-EXP",)].net_amount, 150000)


# ---------------------------------------------------------------- TS-07 月次締め


def freeze(company, date):
	from japan_compliance.jp_close.doctype.jp_month_close.jp_month_close import set_frozen_date

	set_frozen_date(company, date)


class TestOpeningEntry(JPTaxTestCase):
	def test_ts_13_08_opening_entry_has_no_tax_category(self):
		"""期首残高は取引ではないので、科目の既定税区分を入れず、消費税集計に入れない（B-6 O-01）。"""
		from japan_compliance.jp_form.report.jp_consumption_tax_summary.jp_consumption_tax_summary import collect
		from japan_compliance.jp_tax import masters

		doc = frappe.get_doc({
			"doctype": "Journal Entry", "voucher_type": "Opening Entry", "is_opening": "Yes", "company": A,
			"posting_date": "2027-09-01", "accounts": [
				{"account": acct("12106"), "debit_in_account_currency": 300000},
				{"account": acct("11514"), "credit_in_account_currency": 300000},
			],
		}).insert()
		doc.submit()
		self.assertFalse([row.jp_tax_category for row in doc.accounts if row.jp_tax_category])
		rows = collect(frappe._dict(company=A, from_date="2027-09-01", to_date="2027-09-30"), masters.get_settings(A))
		self.assertFalse([r for r in rows if r.voucher_no == doc.name])


class TestTS07(JPTaxTestCase):
	def tearDown(self):
		frappe.set_user("Administrator")
		frappe.db.set_value("Company", A, "accounts_frozen_till_date", None)
		frappe.db.set_single_value("Accounts Settings", "enable_immutable_ledger", 0)
		frappe.clear_cache(doctype="Company")

	def test_ts_07_03_all_roles_rejected(self):
		from japan_compliance.tests.test_permissions import set_model

		ensure_user("kanri@example.com", ["System Manager", "Accounts Manager", "Accounts User"])
		ensure_user("tanto@example.com", ["Accounts User"])
		ensure_user("sekinin@example.com", ["Accounts Manager", "Accounts User"])
		set_model("一体型")  # 経理担当が提出できる状態で、凍結日だけで拒否されることを確かめる
		self.addCleanup(lambda: set_model("分離型"))
		freeze(A, "2027-07-31")
		for user in ("tanto@example.com", "sekinin@example.com", "kanri@example.com", "Administrator"):
			frappe.set_user(user)
			with self.assertRaises(frappe.ValidationError, msg=user):
				make_si(items=[("ITEM-X", 1, 5000)], posting_date="2027-07-15", submit=True)
			frappe.set_user("Administrator")

	def test_ts_07_04_cancel_frozen_month_with_immutable_ledger(self):
		# C-1：Immutable Ledger を有効にし、取消の反対仕訳を取消日に計上する
		si = make_si(items=[("ITEM-X", 10, 5000)], posting_date="2026-09-15", submit=True)
		freeze(A, "2026-09-30")
		frappe.db.set_single_value("Accounts Settings", "enable_immutable_ledger", 1)
		si = frappe.get_doc("Sales Invoice", si.name)
		si.cancel()
		september = frappe.db.sql("""select sum(debit) - sum(credit) from `tabGL Entry` where voucher_no = %s and account = %s
			and posting_date = '2026-09-15'""", (si.name, acct("11203")))[0][0]
		reversal = frappe.db.sql("""select sum(debit) - sum(credit) from `tabGL Entry` where voucher_no = %s and account = %s
			and posting_date = %s""", (si.name, acct("11203"), nowdate()))[0][0]
		self.assertEqual((september, reversal), (55000, -55000))
		amended = frappe.copy_doc(si)
		amended.amended_from = si.name
		amended.docstatus = 0
		amended.items[0].rate = 5500
		amended.insert()
		self.assertEqual(getdate(amended.posting_date), getdate(nowdate()))
		amended.submit()
		self.assertEqual(taxes(amended), [5500])

	def test_cls_31_immutable_ledger_is_enforced(self):
		from japan_compliance import install

		frappe.db.set_single_value("Accounts Settings", "enable_immutable_ledger", 0)
		install.enforce_settings()
		self.assertEqual(frappe.db.get_single_value("Accounts Settings", "enable_immutable_ledger"), 1)

	def test_cls_31_cancelled_invoice_stays_in_closed_month_of_tax_summary(self):
		"""締めた月の集計は変えず、反対仕訳を入れた月にマイナスで集計する（元帳と同じ動き）。"""
		from japan_compliance.jp_form.report.jp_consumption_tax_summary.jp_consumption_tax_summary import collect
		from japan_compliance.jp_tax import masters
		from japan_compliance.tests.e2_execute import cancel_on

		si = make_si(items=[("ITEM-X", 10, 5000)], posting_date="2027-08-15", submit=True)
		frappe.db.set_single_value("Accounts Settings", "enable_immutable_ledger", 1)
		cancel_on(si, "2027-09-05")

		def summary(start, end):
			rows = collect(frappe._dict(company=A, from_date=start, to_date=end), masters.get_settings(A))
			return [(r.net_amount, r.posted_tax) for r in rows if r.voucher_no == si.name]

		self.assertEqual(summary("2027-08-01", "2027-08-31"), [(50000, 5000)])
		self.assertEqual(summary("2027-09-01", "2027-09-30"), [(-50000, -5000)])
		# 両方の月を含む期間では打ち消し合うので出さない
		self.assertEqual(summary("2027-08-01", "2027-09-30"), [])

	def test_cancel_without_immutable_ledger_drops_voucher(self):
		from japan_compliance.jp_form.report.jp_consumption_tax_summary.jp_consumption_tax_summary import collect
		from japan_compliance.jp_tax import masters

		si = make_si(items=[("ITEM-X", 1, 5000)], posting_date="2027-08-16", submit=True)
		frappe.db.set_single_value("Accounts Settings", "enable_immutable_ledger", 0)
		frappe.get_doc("Sales Invoice", si.name).cancel()
		rows = collect(frappe._dict(company=A, from_date="2027-08-01", to_date="2027-08-31"), masters.get_settings(A))
		self.assertFalse([r for r in rows if r.voucher_no == si.name])

	def test_ts_07_05_manager_cannot_freeze(self):
		ensure_user("sekinin@example.com", ["Accounts Manager", "Accounts User"])
		mc = frappe.get_doc({"doctype": "JP Month Close", "company": A, "period_start": "2027-08-01"}).insert()
		mc.run_checks()
		for row in mc.checks:
			if row.severity in ("確認", "警告"):
				row.comment = "確認済み"
		mc.save()
		mc.close()
		frappe.set_user("sekinin@example.com")
		with self.assertRaises(frappe.PermissionError):
			frappe.get_doc("JP Month Close", mc.name).freeze()
		company = frappe.get_doc("Company", A)
		company.accounts_frozen_till_date = "2027-08-31"
		with self.assertRaises(frappe.ValidationError):
			company.save()

	def test_ts_07_07_prior_year_correction(self):
		original = make_je([("717", 10000, 0, None), ("11509", 1000, 0, None), ("21203", 0, 11000, None, PAYABLE_PARTY)], "2026-09-15", company=B)
		with self.assertRaises(frappe.ValidationError):
			make_je([("9201", 22000, 0, None), ("21203", 0, 22000, None, PAYABLE_PARTY)], "2026-10-20", company=B,
				jp_correction_of_type="Journal Entry", jp_correction_of=original.name)
		frappe.clear_messages()
		je = make_je([("9201", 22000, 0, None), ("21203", 0, 22000, None, PAYABLE_PARTY)], "2026-10-20", company=B,
			jp_correction_of_type="Journal Entry", jp_correction_of=original.name, jp_correction_reason="前年度9月分の計上漏れ")
		self.assertIn("前年度", messages())
		self.assertEqual(je.accounts[0].jp_tax_category, "P-OOS")

	def test_ts_07_09_recurring_entries(self):
		from erpnext.accounts.doctype.journal_entry.journal_entry import make_reverse_journal_entry

		dep = make_je([("736", 5000, 0, None), ("12108", 0, 5000, None)], "2027-07-31", voucher_type="Depreciation Entry")
		make_je([("11503", 120000, 0, None), ("11104", 0, 120000, None)], "2027-07-01")
		make_je([("731", 10000, 0, None), ("11503", 0, 10000, None)], "2027-07-31")
		accrual = make_je([("723", 10000, 0, None), ("11509", 1000, 0, None), ("21204", 0, 11000, None)], "2027-07-31")
		reverse = make_reverse_journal_entry(accrual.name)
		reverse.posting_date = "2027-08-01"
		reverse.insert()
		reverse.submit()
		self.assertEqual(gl(reverse), {"723": -10000, "11509": -1000, "21204": 11000})
		self.assertEqual(dep.accounts[0].jp_tax_category, "P-OOS")
		auto = frappe.get_doc({"doctype": "Auto Repeat", "reference_doctype": "Journal Entry", "reference_document": dep.name,
			"frequency": "Monthly", "start_date": "2027-07-31", "submit_on_creation": 0}).insert()
		frappe.db.set_value("Auto Repeat", auto.name, "next_schedule_date", "2027-08-31")
		r = {c.check_code: c for c in checks.run(A, "2027-08-01")}
		self.assertEqual(r["C10"].result, "NG")

	def test_close_check_can_be_disabled(self):
		self.set_settings(disabled_close_checks="C10,C13")
		codes = {c.check_code for c in checks.run(A, "2027-08-01")}
		self.assertNotIn("C10", codes)
		self.assertIn("C01", codes)
		with self.assertRaises(frappe.ValidationError):
			self.set_settings(disabled_close_checks="C01")

	def test_overdue_close_notification(self):
		from japan_compliance.jp_close.close import notify_overdue_close

		ensure_user("kanri@example.com", ["System Manager", "Accounts Manager", "Accounts User"])
		make_si(items=[("ITEM-X", 1, 5000)], posting_date="2026-06-10", submit=True)
		notify_overdue_close()
		self.assertTrue(frappe.db.exists("Notification Log", {"for_user": "kanri@example.com", "document_name": A}))


# ---------------------------------------------------------------- TS-10 監査証跡


class TestTS10(JPTaxTestCase):
	def test_ts_10_01_and_13_audit_trail(self):
		from japan_compliance.jp_audit.report.jp_audit_trail.jp_audit_trail import execute as audit

		with patch.object(frappe, "in_test", False):
			si = make_si(items=[("ITEM-X", 1, 5000)], posting_date="2027-07-05", submit=True)
			si.cancel()
			amended = frappe.copy_doc(si)
			amended.amended_from = si.name
			amended.docstatus = 0
			amended.items[0].rate = 6000
			amended.insert()
			amended.submit()
		today = nowdate()
		_, data = audit({"company": A, "from_date": today, "to_date": today, "ref_doctype": "Sales Invoice"})
		ops = {(r.docname, r.operation) for r in data}
		self.assertIn((si.name, "提出"), ops)
		self.assertIn((si.name, "取消"), ops)
		self.assertTrue(any(name == amended.name for name, _ in ops))
		cancel = next(r for r in data if r.docname == si.name and r.operation == "取消" and r.before == "提出済")
		self.assertEqual((cancel.before, cancel.after, cancel.user), ("提出済", "取消済", "Administrator"))

	def test_ts_10_02_tracked_doctypes(self):
		from japan_compliance.install import TRACKED_DOCTYPES

		for doctype in TRACKED_DOCTYPES:
			self.assertTrue(frappe.get_meta(doctype).track_changes, doctype)

	def test_ts_10_03_version_cannot_be_deleted(self):
		si = make_si(items=[("ITEM-X", 1, 5000)], posting_date="2027-07-06", submit=True)
		version = frappe.get_doc({"doctype": "Version", "ref_doctype": "Sales Invoice", "docname": si.name,
			"data": json.dumps({"changed": [["remarks", "", "x"]]})}).insert(ignore_permissions=True)
		with self.assertRaises(frappe.ValidationError):
			frappe.delete_doc("Version", version.name)

	def test_versions_are_protected_from_log_cleanup(self):
		from japan_compliance.install import protect_versions

		settings = frappe.get_single("Log Settings")
		settings.append("logs_to_clear", {"ref_doctype": "Version", "days": 30})
		settings.flags.ignore_validate = True
		settings.save()
		protect_versions()
		self.assertFalse(any(r.ref_doctype == "Version" for r in frappe.get_single("Log Settings").logs_to_clear))


# ---------------------------------------------------------------- TS-11 税制改正


class TestTS11(JPTaxTestCase):
	def add_rate(self):
		std = frappe.get_doc("JP Tax Rate", {"rate_code": "STD10", "valid_from": "2019-10-01"})
		std.valid_to = "2027-03-31"
		std.save()
		frappe.get_doc({"doctype": "JP Tax Rate", "rate_code": "STD10", "rate": 12, "valid_from": "2027-04-01"}).insert()

	def test_ts_11_02_03_submitted_unchanged_draft_recalculated(self):
		submitted = make_si(items=[("ITEM-X", 2, 5000)], posting_date="2027-04-01", submit=True)
		draft = make_si(items=[("ITEM-X", 2, 5000)], posting_date="2027-04-01")
		self.assertEqual((taxes(submitted), taxes(draft)), ([1000], [1000]))
		self.add_rate()
		submitted.reload()
		self.assertEqual(taxes(submitted), [1000])
		self.assertIn("10%", frappe.get_print("Sales Invoice", submitted.name, print_format="適格請求書（JP）", no_letterhead=1))
		draft.reload()
		draft.save()
		self.assertEqual(taxes(draft), [1200])

	def test_ts_11_05_old_rate_requires_admin_and_reason(self):
		with self.assertRaises(frappe.ValidationError):
			make_si(items=[("ITEM-X", 2, 5000, {"jp_tax_category": "S-TAX-8", "jp_tax_reason": "経過措置"})], posting_date="2027-07-10")
		frappe.db.set_value("JP Tax Category", "S-TAX-8", "enabled", 1)
		frappe.clear_document_cache("JP Tax Category", "S-TAX-8")
		with self.assertRaises(frappe.ValidationError):
			make_si(items=[("ITEM-X", 2, 5000, {"jp_tax_category": "S-TAX-8"})], posting_date="2027-07-10")
		si = make_si(items=[("ITEM-X", 2, 5000, {"jp_tax_category": "S-TAX-8", "jp_tax_reason": "2019年9月以前の契約に基づく取引"})],
			posting_date="2027-07-10")
		self.assertEqual(taxes(si), [800])


# ---------------------------------------------------------------- TS-12 帳票


class TestTS12(JPTaxTestCase):
	@classmethod
	def setUpClass(cls):
		super().setUpClass()
		cls.si = make_si(items=[("ITEM-X", 20, 5000)], posting_date="2027-08-05", submit=True)
		cls.cn = make_si(items=[("ITEM-X", -1, 10000)], posting_date="2027-08-10", is_return=1, return_against=cls.si.name, submit=True)
		cls.pi = make_pi(items=[("ITEM-X", 20, 2500)], posting_date="2027-08-06", submit=True)
		cls.f = {"company": A, "from_date": "2027-08-01", "to_date": "2027-08-31"}

	def test_ts_12_01_standard_general_ledger_by_voucher(self):
		from erpnext.accounts.report.general_ledger.general_ledger import execute as erp_gl

		_, data = erp_gl(frappe._dict(self.f, group_by="Group by Voucher (Consolidated)", categorize_by="Categorize by Voucher (Consolidated)",
			presentation_currency="JPY"))
		lines = [r for r in data if isinstance(r, dict) and r.get("voucher_no") == self.si.name]
		self.assertEqual({(l["account"].split(" - ")[0], round(l["debit"]), round(l["credit"])) for l in lines},
			{("11203", 110000, 0), ("401", 0, 100000), ("21216", 0, 10000)})
		self.assertTrue(all(l.get("posting_date") for l in lines))

	def test_ts_12_04_and_11_trial_balance(self):
		from erpnext.accounts.report.trial_balance.trial_balance import execute as tb

		fy = frappe.db.get_value("Fiscal Year", {"year_start_date": "2027-04-01"})
		_, data = tb(frappe._dict(company=A, fiscal_year=fy, from_date="2027-04-01", to_date="2027-08-31", presentation_currency="JPY",
			show_zero_values=0, show_group_accounts=1))
		accounts = [r for r in data if r.get("account")]
		closing = {r["account"].split(" - ")[0]: round(r["closing_debit"] - r["closing_credit"]) for r in accounts}
		_, ledger = jp_ledger({**self.f, "from_date": "2027-04-01", "account": [acct("11509")]})
		jp_closing = next(r for r in ledger if r.get("remarks") == "次期繰越").balance
		self.assertEqual(closing["11509"], jp_closing)
		total = [r for r in data if r.get("closing_debit") is not None and "Total" in str(r.get("account_name") or r.get("account") or "")]
		self.assertTrue(total)
		self.assertEqual(round(total[-1]["debit"]), round(total[-1]["credit"]))
		self.assertIn("流動資産", " ".join(r["account"] for r in accounts))
		group = next(r for r in accounts if r["account"].startswith("11 - 流動資産") or r["account"].startswith("流動資産"))
		self.assertTrue(group.get("indent") is not None)

	def test_ts_12_05_customer_ledger(self):
		from erpnext.accounts.report.general_ledger.general_ledger import execute as erp_gl

		_, data = erp_gl(frappe._dict(self.f, party_type="Customer", party=[fx.customer(0)], account=[acct("11203")],
			group_by="Group by Party", categorize_by="Categorize by Party", presentation_currency="JPY"))
		lines = [r for r in data if isinstance(r, dict) and r.get("voucher_no") in (self.si.name, self.cn.name)]
		self.assertEqual([l["voucher_no"] for l in lines], [self.si.name, self.cn.name])
		movement = sum(l["debit"] - l["credit"] for l in lines)
		self.assertEqual(round(movement), 99000)

	def test_ts_12_07_calculation_methods(self):
		self.set_settings(sales_calc_method="積上げ計算", purchase_calc_method="請求書等積上げ")
		filing = {r.item: r for r in summary("申告用の計算", "2027-08-01", "2027-08-31")}
		self.assertEqual(filing["適格請求書記載の消費税額の合計"].std, 10000)
		self.assertEqual(filing["売上税額 × 78/100（国税分）"].std, 7800)
		self.set_settings(sales_calc_method="割戻し計算", purchase_calc_method="割戻し計算")
		filing = {r.item: r for r in summary("申告用の計算", "2027-08-01", "2027-08-31")}
		self.assertEqual(filing["税込課税仕入れの合計"].std, 55000)
		self.assertEqual(filing["仕入税額 × 7.8/110・6.24/108（国税分）"].std, 3900)

	def test_ts_12_09_negative_marker(self):
		from japan_compliance.jp_form.formatting import jp_yen

		self.assertEqual(jp_yen(-1000), "△1,000")
		html = frappe.get_print("Sales Invoice", self.cn.name, print_format="適格請求書（JP）", no_letterhead=1)
		self.assertIn("△10,000", html)

	def test_ts_12_10_csv_export(self):
		from frappe.desk.query_report import _export_query
		from frappe.desk.utils import pop_csv_params

		params = frappe._dict(report_name="JP Journal Book", file_format_type="CSV", filters=json.dumps(self.f),
			ignore_visible_idx=1, visible_idx="[]")
		_, ext, content = _export_query(params, pop_csv_params(frappe._dict()), populate_response=False)
		text = content.decode("utf-8-sig") if isinstance(content, bytes) else content
		self.assertEqual(ext, "csv")
		self.assertIn(self.si.name, text)
		self.assertIn("110000", text.replace(",", ""))

	def test_ts_12_12_taxable_sales_ratio(self):
		filing = {r.item: r for r in summary("申告用の計算", "2027-08-01", "2027-08-31")}
		self.assertEqual(filing["課税売上割合 %"].std, 100)

	def test_ts_12_13_report_print_headers(self):
		import os

		base = frappe.get_app_path("japan_compliance", "jp_form", "report")
		for report in ("jp_journal_book", "jp_general_ledger", "jp_consumption_tax_summary"):
			with open(os.path.join(base, report, f"{report}.html"), encoding="utf-8") as f:
				html = f.read()
			for text in ("filters.company", "期間", "出力日時", "△", 'id="footer-html"', "topage"):
				self.assertIn(text, html)
		self.assertIn("topage", frappe.get_print("Sales Invoice", self.si.name, print_format="適格請求書（JP）", no_letterhead=1))


# ---------------------------------------------------------------- TS-13 標準仕訳パターン


class TestTS13(JPTaxTestCase):
	def test_ts_13_01_and_02_patterns_same_month(self):
		make_si(items=[("ITEM-X", 20, 5000)], posting_date="2027-09-02", submit=True)
		make_si(items=[("ITEM-X", 6, 5000), ("ITEM-Y", 10, 2000)], posting_date="2027-09-02", submit=True)
		make_pi(items=[("ITEM-X", 20, 2500)], posting_date="2027-09-03", submit=True)
		make_pi(supplier=1, items=[("ITEM-Z", 1, 55000)], posting_date="2027-09-03", jp_tax_inclusive=1, submit=True)
		make_je([("716", 1000, 0, None), ("11509", 100, 0, None), ("11101", 0, 1100, None)], "2027-09-04")
		make_je([("739", 30000, 0, "S-TAX-10"), ("21216", 3000, 0, None),
			("11203", 0, 33000, None, {"party_type": "Customer", "party": fx.customer(0)})], "2027-09-30", jp_transaction_kind="貸倒")
		rows = rows_by(summary("区分別集計", "2027-09-01", "2027-09-30"), "tax_category", "transaction_kind")
		expect = {("S-TAX-10", "通常"): (130000, 13000), ("S-TAX-8R", "通常"): (20000, 1600), ("S-TAX-10", "貸倒"): (-30000, -3000),
			("P-TAX-10", "通常"): (51000, 5100)}
		for key, (net, tax) in expect.items():
			self.assertEqual((rows[key].net_amount, rows[key].tax_amount), (net, tax), key)
		nreg = rows[("P-NREG-10", "通常")]
		self.assertEqual((nreg.gross_amount, nreg.deductible_tax, nreg.nondeductible_tax), (55000, 3500, 1500))
		r = {c.check_code: c for c in checks.run(A, "2027-09-01")}
		self.assertEqual(r["C03"].result, "OK")

	def test_ts_13_03_advance_received(self):
		from erpnext.accounts.doctype.payment_entry.payment_entry import get_payment_entry  # noqa: F401

		pe = frappe.get_doc({"doctype": "Payment Entry", "payment_type": "Receive", "company": A, "posting_date": "2027-09-05",
			"party_type": "Customer", "party": fx.customer(0), "paid_to": acct("11104"), "paid_to_account_currency": "JPY",
			"paid_amount": 55000, "received_amount": 55000, "reference_no": "ADV", "reference_date": "2027-09-05"})
		pe.setup_party_account_field()
		pe.set_missing_values()
		pe.insert()
		pe.submit()
		self.assertEqual(gl(pe), {"11104": 55000, "21209": -55000})
		si = make_si(items=[("ITEM-X", 10, 5000)], posting_date="2027-09-20", allocate_advances_automatically=1)
		self.assertEqual([a.allocated_amount for a in si.advances], [55000])
		si.submit()
		self.assertEqual(si.outstanding_amount, 0)
		advance = frappe.db.sql("""select sum(debit) - sum(credit) from `tabGL Entry` where account = %s and is_cancelled = 0
			and party = %s and posting_date <= '2027-09-30'""", (acct("21209"), fx.customer(0)))[0][0]
		self.assertEqual(advance, 0)

	def test_ts_13_04_to_06_other_patterns(self):
		g02 = make_je([("21210", 17000, 0, None), ("11104", 0, 17000, None)], "2027-09-10")
		f04 = make_je([("2201", 80000, 0, None), ("8201", 5000, 0, None), ("11104", 0, 85000, None)], "2027-09-27")
		a01 = make_pi(items=[("ASSET-EQ", 1, 300000)], posting_date="2027-09-08", submit=True)
		self.assertEqual(gl(g02), {"21210": 17000, "11104": -17000})
		self.assertEqual(f04.accounts[1].jp_tax_category, "P-OOS")
		self.assertEqual(gl(a01), {"12106": 300000, "11509": 30000, "21102": -330000})
		self.assertEqual(a01.items[0].jp_tax_category, "P-TAX-10")


# ---------------------------------------------------------------- TS-14 マスタ・初期設定


class TestTS14(JPTaxTestCase):
	def test_ts_14_01_chart(self):
		def numbers(node):
			out = []
			for key, child in node.items():
				if isinstance(child, dict):
					if child.get("account_number"):
						out.append(child["account_number"])
					out += numbers(child)
			return out

		chart = set(numbers(load_chart()["tree"]))
		self.assertEqual(len(chart), 242)  # 勘定科目一覧（償却債権取立益 8108 を含む）
		in_company = set(frappe.get_all("Account", filters={"company": A, "account_number": ("in", list(chart))}, pluck="account_number"))
		self.assertEqual(in_company, chart)
		for number, category in account_tax_categories().items():
			self.assertEqual(frappe.db.get_value("Account", {"company": A, "account_number": number}, "jp_tax_category"), category, number)

	def test_ts_14_02_masters(self):
		from japan_compliance.jp_tax import master_data

		for code, name, direction, tax_type, rate_code, *_ in master_data.TAX_CATEGORIES:
			cat = frappe.get_doc("JP Tax Category", code)
			self.assertEqual((cat.category_name, cat.direction, cat.tax_type, cat.rate_code or ""), (name, direction, tax_type, rate_code))
		for code, _, rate, national, local, valid_from, *_ in master_data.TAX_RATES:
			row = frappe.get_doc("JP Tax Rate", {"rate_code": code, "valid_from": valid_from})
			self.assertEqual((row.rate, row.national_rate, row.local_rate), (rate, national, local))
		for code, _, rtype, value, vf, vt, *_ in master_data.RULE_PERIODS:
			row = frappe.get_doc("JP Tax Rule Period", code)
			self.assertEqual((row.rule_type, row.value, str(row.valid_from or ""), str(row.valid_to or "")), (rtype, value, vf or "", vt or ""))

	def test_ts_14_03_company_settings(self):
		a, b = frappe.get_doc("JP Tax Settings", A), frappe.get_doc("JP Tax Settings", B)
		self.assertEqual((str(a.tax_period_start), a.nondeductible_method, a.small_amount_special), ("2026-04-01", "本体価格に含める", 0))
		self.assertEqual((str(b.tax_period_start), b.nondeductible_method, b.small_amount_special), ("2026-10-01", "仮払消費税等に含め決算で雑損失", 1))

	def test_ts_14_04_display_settings(self):
		from japan_compliance.jp_form.formatting import jp_date, jp_yen

		self.assertEqual(frappe.get_cached_value("Currency", "JPY", "smallest_currency_fraction_value"), 1)
		self.assertEqual(jp_yen(1234567.4), "1,234,567")
		self.assertEqual(jp_date("2027-08-05"), "2027/08/05")
		self.assertEqual(frappe.db.get_single_value("System Settings", "date_format"), "yyyy-mm-dd")
		# 画面が見る既定値も揃っていること（画面が dd-mm-yyyy のままにならないこと）
		self.assertEqual(frappe.db.get_default("date_format"), "yyyy-mm-dd")
