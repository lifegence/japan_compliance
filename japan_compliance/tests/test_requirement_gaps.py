"""インボイス・帳票・締めの個別要件のテスト（INV-05・06・45・46、B-3 #9、B-6 S-11、FORM-04、FORM-65）。

日付は他のモジュールと重ならないよう、2028年の月を使う。
"""

import io

import frappe

from japan_compliance.jp_close import checks
from japan_compliance.jp_form.report.jp_journal_book.jp_journal_book import execute as journal
from japan_compliance.jp_tax.year_end import create_nondeductible_transfer
from japan_compliance.tests import fixtures as fx
from japan_compliance.tests.test_e1_cases import messages, summary
from japan_compliance.tests.test_invoice_tax import A, B, JPTaxTestCase, make_pi, make_si
from japan_compliance.tests.test_tax_summary import acct, make_je

VALID_NUMBER = "T9000000000009"


def book(month, company=A):
	return journal({"company": company, "from_date": f"2028-{month}-01", "to_date": f"2028-{month}-28"})[1]


class TestCustomerRegistration(JPTaxTestCase):
	def customer(self):
		doc = frappe.get_doc("Customer", fx.customer(0))
		self.addCleanup(lambda: frappe.db.set_value("Customer", doc.name, {"jp_registration_number": None, "jp_registration_status": "不明"}))
		return doc

	def test_number_sets_status(self):
		doc = self.customer()
		doc.jp_registration_number = VALID_NUMBER.lower()
		doc.save()
		self.assertEqual((doc.jp_registration_number, doc.jp_registration_status), (VALID_NUMBER, "登録"))

	def test_invalid_number_rejected(self):
		doc = self.customer()
		doc.jp_registration_number = "T1234567890123"
		with self.assertRaises(frappe.ValidationError):
			doc.save()

	def test_registered_requires_number(self):
		doc = self.customer()
		doc.jp_registration_status = "登録"
		with self.assertRaises(frappe.ValidationError):
			doc.save()

	def test_journal_book_shows_customer_registration(self):
		doc = self.customer()
		make_si(items=[("ITEM-X", 1, 1000)], posting_date="2028-01-10", submit=True)
		first = next(r for r in book("01") if r.voucher_type == "Sales Invoice")
		self.assertEqual(first.registration, "")
		doc.jp_registration_number = VALID_NUMBER
		doc.save()
		first = next(r for r in book("01") if r.voucher_type == "Sales Invoice")
		self.assertEqual(first.registration, "有")


class TestCounterparty(JPTaxTestCase):
	LINES = [("717", 2000, 0, "P-TAX-10"), ("11509", 200, 0, None), ("11101", 0, 2200, None)]

	def test_counterparty_on_journal_book(self):
		frappe.clear_messages()
		make_je(self.LINES, "2028-02-10", jp_counterparty="株式会社テスト通信")
		self.assertNotIn("相手方", messages())
		row = next(r for r in book("02") if r.voucher_no and r.party == "株式会社テスト通信")
		self.assertEqual(row.voucher_type, "Journal Entry")

	def test_warn_without_counterparty(self):
		frappe.clear_messages()
		make_je(self.LINES, "2028-02-11")
		self.assertIn("相手方", messages())

	def test_no_warning_for_out_of_scope(self):
		frappe.clear_messages()
		make_je([("703", 1000, 0, "P-OOS"), ("11101", 0, 1000, None)], "2028-02-12")
		self.assertNotIn("相手方", messages())


class TestSpecialCaseAddress(JPTaxTestCase):
	"""INV-46：金額基準と住所（Q&A 問110）。"""

	def je_lines(self, net, account="720"):
		tax = net // 10
		return [(account, net, 0, "P-TAX-10"), ("11509", tax, 0, None), ("11101", 0, net + tax, None)]

	def test_second_hand_goods_address_only_when_required_by_law(self):
		make_je(self.je_lines(3000), "2028-03-10", jp_special_case="古物等の購入特例", jp_counterparty="個人A")  # 住所なしで可
		with self.assertRaises(frappe.ValidationError):
			make_je(self.je_lines(3000), "2028-03-10", jp_special_case="古物等の購入特例", jp_counterparty="個人B",
				jp_address_required_by_law=1)
		make_je(self.je_lines(3000), "2028-03-10", jp_special_case="古物等の購入特例", jp_counterparty="個人B",
			jp_address_required_by_law=1, jp_counterparty_address="東京都豊島区1-1")
		row = next(r for r in book("03") if r.special_case == "古物等の購入特例" and r.counterparty_address)
		self.assertEqual(row.counterparty_address, "東京都豊島区1-1")

	def test_public_transport_and_vending_under_30000_only(self):
		make_je(self.je_lines(27000, "716"), "2028-03-11", jp_special_case="公共交通機関特例", jp_counterparty="テスト鉄道")  # 29,700
		for case in ("公共交通機関特例", "自動販売機・自動サービス機特例"):
			with self.assertRaises(frappe.ValidationError, msg=case):
				make_je(self.je_lines(27273, "716"), "2028-03-11", jp_special_case=case, jp_counterparty="テスト鉄道")  # 30,000

	def test_admission_ticket_address_from_30000(self):
		make_je(self.je_lines(27000, "716"), "2028-03-14", jp_special_case="入場券等回収特例", jp_counterparty="テスト会館")
		with self.assertRaises(frappe.ValidationError):
			make_je(self.je_lines(30000, "716"), "2028-03-14", jp_special_case="入場券等回収特例", jp_counterparty="テスト会館")
		make_je(self.je_lines(30000, "716"), "2028-03-14", jp_special_case="入場券等回収特例", jp_counterparty="テスト会館",
			jp_counterparty_address="東京都豊島区3-3")

	def test_purchase_invoice(self):
		pi = make_pi(items=[("ITEM-X", 1, 1100)], posting_date="2028-03-12", jp_tax_inclusive=1, jp_special_case="古物等の購入特例",
			jp_address_required_by_law=1, jp_counterparty_address="東京都豊島区2-2", submit=True)
		row = next(r for r in book("03") if r.voucher_no == pi.name)
		self.assertEqual(row.counterparty_address, "東京都豊島区2-2")
		with self.assertRaises(frappe.ValidationError):
			make_pi(items=[("ITEM-X", 1, 1100)], posting_date="2028-03-13", jp_tax_inclusive=1, jp_special_case="古物等の購入特例",
				jp_address_required_by_law=1)
		with self.assertRaises(frappe.ValidationError):
			make_pi(items=[("ITEM-X", 1, 33000)], posting_date="2028-03-13", jp_tax_inclusive=1, jp_special_case="公共交通機関特例")


class TestNondeductibleTransfer(JPTaxTestCase):
	def test_rejected_when_method_is_cost(self):
		with self.assertRaises(frappe.ValidationError):
			create_nondeductible_transfer(A, "2028-04-01", "2028-04-30")

	def test_rejected_when_nothing_to_transfer(self):
		with self.assertRaises(frappe.ValidationError):
			create_nondeductible_transfer(B, "2028-05-01", "2028-05-31")

	def test_sums_period_and_excluded_from_summary(self):
		make_pi(company=B, supplier=1, items=[("ITEM-Z", 1, 55000)], posting_date="2027-02-05", jp_tax_inclusive=1, submit=True)
		make_pi(company=B, supplier=1, items=[("ITEM-Z", 1, 110000)], posting_date="2027-02-20", jp_tax_inclusive=1, submit=True)
		je = frappe.get_doc("Journal Entry", create_nondeductible_transfer(B, "2027-02-01", "2027-02-28"))
		self.assertEqual(je.total_debit, 4500)
		je.submit()
		data = summary("区分別", "2027-02-01", "2027-02-28", company=B)
		self.assertFalse([r for r in data if r.tax_category == "P-OOS"])


class TestBadDebtRecoveryFiling(JPTaxTestCase):
	def test_recovery_added_and_ratio_unaffected(self):
		make_si(items=[("ITEM-X", 1, 100000)], posting_date="2027-12-05", submit=True)
		make_je([("739", 30000, 0, "S-TAX-10"), ("21216", 3000, 0, None), ("11101", 0, 33000, None)], "2027-12-20", jp_transaction_kind="貸倒")
		make_je([("11101", 11000, 0, None), ("8108", 0, 10000, "S-TAX-10"), ("21216", 0, 1000, None)], "2027-12-25", jp_transaction_kind="貸倒回収")
		filing = {r.item: r for r in summary("申告用の計算", "2027-12-01", "2027-12-31")}
		self.assertEqual(filing["貸倒回収に係る消費税額（売上の税額に加算）"].std, 1000)
		self.assertEqual(filing["同上の国税分（× 78/100）"].std, 780)
		self.assertEqual(filing["貸倒れに係る消費税額"].std, -3000)
		# 分子は通常の売上のみ。貸倒れ・貸倒回収の 30,000／10,000 は入らない
		self.assertEqual(filing["分子（課税売上＋免税売上）"].std, 100000)


class TestReportPdfPageNumbers(JPTaxTestCase):
	def test_footer_renders_page_numbers(self):
		from frappe.utils.pdf import get_pdf
		from pypdf import PdfReader

		rows = "".join(f"<tr><td>{i}</td></tr>" for i in range(200))
		html = ('<div class="jp-report"><table>' + rows + '</table>'
			'<div id="footer-html" class="visible-pdf"><div>仕訳帳　<span class="page"></span> / <span class="topage"></span></div></div></div>')
		reader = PdfReader(io.BytesIO(get_pdf(html)))
		self.assertGreater(len(reader.pages), 1)
		n = len(reader.pages)
		compact = lambda page: "".join(page.extract_text().split())
		self.assertIn(f"仕訳帳1/{n}", compact(reader.pages[0]))
		self.assertIn(f"仕訳帳{n}/{n}", compact(reader.pages[-1]))


class TestCloseChecksC14C15(JPTaxTestCase):
	def result(self, code):
		return next(c for c in checks.run(A, "2028-02-01") if c.check_code == code)

	def test_ts_07_11_duplicate_bill_no(self):
		for _i in range(2):
			make_pi(items=[("ITEM-X", 1, 2500 + _i)], posting_date="2028-02-20", bill_no="INV-DUP-1", submit=True)
		c14 = self.result("C14")
		self.assertEqual(c14.result, "NG")
		self.assertIn("INV-DUP-1", c14.references)

	def test_ts_07_11_category_mismatch_and_exceptions(self):
		mismatch = make_je([("717", 2000, 0, "P-OOS"), ("11101", 0, 2000, None)], "2028-02-21")
		subsidy = make_je([("11101", 500, 0, None), ("8106", 0, 500, "S-OOS")], "2028-02-22")
		c15 = self.result("C15")
		self.assertEqual(c15.result, "NG")
		lines = c15.references.split("\n")
		self.assertTrue(any(mismatch.name in l and "例外" not in l for l in lines))
		self.assertTrue(any(subsidy.name in l and "想定どおりの例外" in l for l in lines))
