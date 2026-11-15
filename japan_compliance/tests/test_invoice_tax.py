"""売上・仕入請求書の消費税計算の統合テスト。

期待値は端数処理方針（B-3）5章の計算例と、標準仕訳パターン（B-6）の仕訳例。
ケース名の TS-xx-nn はテストシナリオ一覧（E-1）のケース ID。
"""

import frappe
from frappe.tests import IntegrationTestCase

from japan_compliance.tests import fixtures as fx

A, B = fx.COMPANY_A, fx.COMPANY_B


def make_si(company=A, customer=0, items=(), posting_date="2026-10-13", submit=False, **kw):
	doc = frappe.get_doc({
		"doctype": "Sales Invoice", "company": company, "currency": kw.pop("currency", "JPY"), "conversion_rate": kw.pop("conversion_rate", 1), "customer": fx.customer(customer),
		"posting_date": posting_date, "set_posting_time": 1, "due_date": posting_date,
		"items": [{"item_code": code, "qty": qty, "rate": rate, **extra} for code, qty, rate, *rest in items for extra in [rest[0] if rest else {}]],
		**kw,
	})
	doc.insert()
	if submit:
		doc.submit()
	return doc


def make_pi(company=A, supplier=0, items=(), posting_date="2026-10-13", submit=False, **kw):
	doc = frappe.get_doc({
		"doctype": "Purchase Invoice", "company": company, "currency": kw.pop("currency", "JPY"), "conversion_rate": kw.pop("conversion_rate", 1), "supplier": fx.supplier(supplier),
		"posting_date": posting_date, "set_posting_time": 1, "due_date": posting_date, "bill_date": posting_date,
		"items": [{"item_code": code, "qty": qty, "rate": rate, **extra} for code, qty, rate, *rest in items for extra in [rest[0] if rest else {}]],
		**kw,
	})
	doc.insert()
	if submit:
		doc.submit()
	return doc


def taxes(doc):
	return [round(t.tax_amount) for t in doc.taxes]


def gl(doc):
	"""科目番号 → 借方−貸方。"""
	out = {}
	for row in frappe.get_all("GL Entry", filters={"voucher_no": doc.name, "is_cancelled": 0}, fields=["account", "debit", "credit"]):
		number = row.account.split(" - ")[0]
		out[number] = out.get(number, 0) + round(row.debit - row.credit)
	return {k: v for k, v in out.items() if v}


class JPTaxTestCase(IntegrationTestCase):
	@classmethod
	def setUpClass(cls):
		super().setUpClass()
		# クラスごとのロールバックで伝票番号が再利用されるため、Redis の文書キャッシュに古い下書きが残らないようにする
		frappe.cache.delete_keys("document_cache::")
		fx.ensure_all()

	def setUp(self):
		# 項目の通貨（精度）は伝票番号ごとにメモリにキャッシュされる。番号が再利用されると円の精度が外貨伝票に残る
		frappe.local.field_currency = None

	def set_settings(self, company=A, **values):
		settings = frappe.get_doc("JP Tax Settings", company)
		before = {k: settings.get(k) for k in values}
		settings.update(values)
		settings.save()
		self.addCleanup(lambda: frappe.get_doc("JP Tax Settings", company).update(before).save())


class TestSalesInvoiceTax(JPTaxTestCase):
	def test_ts_01_01_standard_rate(self):
		si = make_si(items=[("ITEM-X", 20, 5000)], submit=True)
		self.assertEqual(taxes(si), [10000])
		self.assertEqual(si.grand_total, 110000)
		self.assertEqual(gl(si), {"11203": 110000, "401": -100000, "21216": -10000})

	def test_ts_02_01_mixed_rates_use_item_defaults(self):
		si = make_si(items=[("ITEM-X", 6, 5000), ("ITEM-Y", 10, 2000)])
		self.assertEqual([i.jp_tax_category for i in si.items], ["S-TAX-10", "S-TAX-8R"])
		self.assertEqual(taxes(si), [3000, 1600])
		self.assertEqual(si.grand_total, 54600)

	def test_ts_02_02_one_rounding_per_rate(self):
		si = make_si(items=[
			("ITEM-X", 1, 1980), ("ITEM-X", 1, 2530), ("ITEM-X", 1, 715),
			("ITEM-Y", 2, 398), ("ITEM-Y", 1, 1058), ("ITEM-Y", 5, 129),
		])
		self.assertEqual(taxes(si), [522, 199])
		self.assertEqual(si.grand_total, 8445)

	def test_ts_04_01_02_rounding_methods(self):
		lines = [("ITEM-X", 3, 1234), ("ITEM-X", 1, 567), ("ITEM-X", 1, 89)]
		self.assertEqual(taxes(make_si(items=lines)), [435])
		self.set_settings(sales_rounding="四捨五入")
		self.assertEqual(taxes(make_si(items=lines)), [436])

	def test_ts_04_03_half_up_is_not_bankers(self):
		self.set_settings(sales_rounding="四捨五入")
		self.assertEqual([taxes(make_si(items=[("ITEM-X", 1, n)]))[0] for n in (1005, 1015, 1025)], [101, 102, 103])

	def test_ts_04_04_inclusive(self):
		for gross, item, tax in ((1000, "ITEM-X", 90), (10999, "ITEM-X", 999), (1080, "ITEM-Y", 80), (3218, "ITEM-Y", 238)):
			si = make_si(items=[(item, 1, gross)], jp_tax_inclusive=1, submit=True)
			self.assertEqual(taxes(si), [tax])
			self.assertEqual(si.grand_total, gross)
			self.assertEqual(si.net_total, gross - tax)
			self.assertEqual(gl(si), {"11203": gross, "401": -(gross - tax), "21216": -tax})

	def test_ts_01_06_export_customer(self):
		si = make_si(customer=2, items=[("ITEM-X", 40, 5000)], submit=True)
		self.assertEqual(si.items[0].jp_tax_category, "S-EXP")
		self.assertEqual(taxes(si), [])
		self.assertEqual(si.grand_total, 200000)

	def test_ts_05_01_credit_note(self):
		si = make_si(items=[("ITEM-X", 20, 5000)], submit=True)
		cn = make_si(items=[("ITEM-X", -1, 10000)], is_return=1, return_against=si.name, submit=True)
		self.assertEqual(taxes(cn), [-1000])
		self.assertEqual(cn.grand_total, -11000)
		self.assertEqual(cn.jp_tax_breakdown[0].transaction_kind, "返還等")

	def test_ts_04_08_snapshot_after_settings_change(self):
		si = make_si(items=[("ITEM-X", 3, 1234), ("ITEM-X", 1, 567), ("ITEM-X", 1, 89)], submit=True)
		self.set_settings(sales_rounding="切上げ")
		si.reload()
		self.assertEqual(taxes(si), [435])
		self.assertEqual(si.jp_tax_breakdown[0].posted_tax, 435)
		self.assertIn("切捨て", si.jp_calc_snapshot)

	def test_document_discount_is_rejected(self):
		with self.assertRaises(frappe.ValidationError):
			make_si(items=[("ITEM-X", 1, 5000)], discount_amount=100, apply_discount_on="Net Total")


class TestPurchaseInvoiceTax(JPTaxTestCase):
	def test_ts_01_03_registered_supplier(self):
		pi = make_pi(items=[("ITEM-X", 20, 2500)], submit=True)
		self.assertEqual(pi.items[0].jp_tax_category, "P-TAX-10")
		self.assertEqual(taxes(pi), [5000])
		self.assertEqual(gl(pi), {"5201": 50000, "11509": 5000, "21102": -55000})

	def test_ts_01_04_reduced_rate_purchase(self):
		pi = make_pi(items=[("ITEM-Y", 10, 1000)])
		self.assertEqual(pi.items[0].jp_tax_category, "P-TAX-8R")
		self.assertEqual(taxes(pi), [800])

	def test_ts_03_01_default_category_by_registration(self):
		self.assertEqual(make_pi(supplier=0, items=[("ITEM-Z", 1, 50000)]).items[0].jp_tax_category, "P-TAX-10")
		self.assertEqual(make_pi(supplier=1, items=[("ITEM-Z", 1, 50000)]).items[0].jp_tax_category, "P-NREG-10")
		self.assertEqual(make_pi(supplier=2, items=[("ITEM-Z", 1, 50000)]).items[0].jp_tax_category, "P-NREG-10")

	def test_ts_03_02_nonregistered_80_percent(self):
		pi = make_pi(supplier=1, items=[("ITEM-Z", 1, 55000)], posting_date="2026-09-30", jp_tax_inclusive=1, submit=True)
		self.assertEqual(taxes(pi), [4000])
		self.assertEqual(pi.grand_total, 55000)
		self.assertEqual(gl(pi), {"711": 51000, "11509": 4000, "21102": -55000})
		row = pi.jp_tax_breakdown[0]
		self.assertEqual((row.deduction_rule, row.deductible_tax, row.nondeductible_tax, row.display_label), ("DED-80", 4000, 1000, "80%控除対象"))

	def test_ts_03_03_nonregistered_70_percent(self):
		pi = make_pi(supplier=1, items=[("ITEM-Z", 1, 55000)], posting_date="2026-10-01", jp_tax_inclusive=1, submit=True)
		self.assertEqual(gl(pi), {"711": 51500, "11509": 3500, "21102": -55000})
		pi = make_pi(supplier=1, items=[("ITEM-Z", 1, 33333)], posting_date="2026-10-01", jp_tax_inclusive=1, submit=True)
		self.assertEqual(gl(pi), {"711": 31212, "11509": 2121, "21102": -33333})
		self.assertEqual(pi.jp_tax_breakdown[0].nondeductible_tax, 909)

	def test_ts_03_04_transaction_date_decides_ratio(self):
		pi = make_pi(supplier=1, items=[("ITEM-Z", 1, 55000)], posting_date="2026-09-21",
			jp_transaction_date="2026-10-20", jp_tax_inclusive=1)
		self.assertEqual(taxes(pi), [3500])
		self.assertEqual(pi.jp_tax_breakdown[0].deduction_rule, "DED-70")

	def test_nonregistered_exclusive_entry(self):
		pi = make_pi(supplier=1, items=[("ITEM-Z", 1, 50000)], posting_date="2026-10-15", submit=True)
		self.assertEqual(gl(pi), {"711": 51500, "11509": 3500, "21102": -55000})

	def test_ts_03_06_misc_loss_method(self):
		pi = make_pi(company=B, supplier=1, items=[("ITEM-Z", 1, 55000)], posting_date="2026-10-01", jp_tax_inclusive=1, submit=True)
		self.assertEqual(gl(pi), {"711": 50000, "11509": 5000, "21102": -55000})
		self.assertEqual(pi.jp_tax_breakdown[0].nondeductible_tax, 1500)

	def test_ts_04_09_stated_tax(self):
		pi = make_pi(items=[("ITEM-X", 1, 4793)], jp_tax_inclusive=1, jp_stated_tax_std=436, submit=True)
		self.assertEqual(gl(pi), {"5201": 4357, "11509": 436, "21102": -4793})
		pi = make_pi(items=[("ITEM-X", 1, 4793)], jp_tax_inclusive=1, jp_stated_tax_std=440)
		self.assertEqual(taxes(pi), [440])
		self.assertEqual(pi.jp_tax_breakdown[0].computed_tax, 435)


class TestSettingsAndGuards(JPTaxTestCase):
	def test_ts_04_07_invalid_method_combination(self):
		settings = frappe.get_doc("JP Tax Settings", A)
		settings.sales_calc_method, settings.purchase_calc_method = "積上げ計算", "割戻し計算"
		with self.assertRaises(frappe.ValidationError):
			settings.save()

	def test_ts_04_06_row_wise_rounding_is_rejected(self):
		accounts = frappe.get_doc("Accounts Settings")
		accounts.round_row_wise_tax = 1
		with self.assertRaises(frappe.ValidationError):
			accounts.save()

	def test_ts_11_04_overlapping_rate_is_rejected(self):
		with self.assertRaises(frappe.ValidationError):
			frappe.get_doc({"doctype": "JP Tax Rate", "rate_code": "STD10", "rate": 12, "valid_from": "2027-04-01"}).insert()

	def test_ts_11_rate_change_by_adding_row(self):
		std = frappe.get_doc("JP Tax Rate", {"rate_code": "STD10", "valid_from": "2019-10-01"})
		std.valid_to = "2027-03-31"
		std.save()
		frappe.get_doc({"doctype": "JP Tax Rate", "rate_code": "STD10", "rate": 12, "valid_from": "2027-04-01"}).insert()
		self.assertEqual(taxes(make_si(items=[("ITEM-X", 2, 5000)], posting_date="2027-03-31")), [1000])
		self.assertEqual(taxes(make_si(items=[("ITEM-X", 2, 5000)], posting_date="2027-04-01")), [1200])

	def test_ts_10_03_cancelled_invoice_cannot_be_deleted(self):
		si = make_si(items=[("ITEM-X", 1, 5000)], submit=True)
		si.cancel()
		with self.assertRaises(frappe.ValidationError):
			frappe.delete_doc("Sales Invoice", si.name)

	def test_ts_10_04_draft_can_be_deleted(self):
		si = make_si(items=[("ITEM-X", 1, 5000)])
		frappe.delete_doc("Sales Invoice", si.name)
		self.assertTrue(frappe.db.exists("Deleted Document", {"deleted_name": si.name}))

	def test_ts_10_05_transaction_deletion_is_rejected(self):
		with self.assertRaises(frappe.ValidationError):
			frappe.get_doc({"doctype": "Transaction Deletion Record", "company": A}).insert()

	def test_master_cannot_be_deleted(self):
		with self.assertRaises(frappe.ValidationError):
			frappe.delete_doc("JP Tax Category", "S-TAX-10")

	def test_supplier_registration_number_check_digit(self):
		with self.assertRaises(frappe.ValidationError):
			frappe.get_doc({"doctype": "Supplier", "supplier_name": "誤った番号の仕入先", "supplier_group": fx.leaf("Supplier Group"),
				"jp_registration_number": "T9000000000001"}).insert()


class TestQualifiedInvoice(JPTaxTestCase):
	def test_issuer_registration_number_snapshot(self):
		si = make_si(items=[("ITEM-X", 1, 5000)], submit=True)
		self.assertEqual(si.jp_issuer_registration_number, fx.REGISTRATION_NUMBERS[A])

	def test_ts_02_01_print_contains_required_items(self):
		si = make_si(items=[("ITEM-X", 6, 5000), ("ITEM-Y", 10, 2000)], submit=True)
		html = frappe.get_print("Sales Invoice", si.name, print_format="適格請求書（JP）", no_letterhead=1)
		for text in ("登録番号 T8000000000001", "テスト得意先A株式会社 御中", "2026/10/13", "食品Y ※", "8%（軽減）",
				"30,000", "3,000", "20,000", "1,600", "※は軽減税率（8%）対象"):
			self.assertIn(text, html)

	def test_ts_02_05_copy_attached_and_protected(self):
		frappe.flags.jp_attach_copy_in_test = True
		self.addCleanup(lambda: setattr(frappe.flags, "jp_attach_copy_in_test", False))
		si = make_si(items=[("ITEM-X", 1, 5000)], submit=True)
		name = frappe.db.get_value("File", {"attached_to_doctype": "Sales Invoice", "attached_to_name": si.name}, "name")
		self.assertTrue(name)
		with self.assertRaises(frappe.ValidationError):
			frappe.delete_doc("File", name)
