"""仕訳帳（JP）・総勘定元帳（JP）のテスト（E-1 TS-03-07・10、TS-12-02〜04）。2027年1月の伝票で確かめる。"""

import frappe

from japan_compliance.jp_form.report.jp_general_ledger.jp_general_ledger import execute as ledger
from japan_compliance.jp_form.report.jp_journal_book.jp_journal_book import execute as journal
from japan_compliance.tests.test_invoice_tax import A, JPTaxTestCase, make_pi, make_si
from japan_compliance.tests.test_tax_summary import acct, make_je

F = {"company": A, "from_date": "2027-01-01", "to_date": "2027-01-31"}


class TestBooks(JPTaxTestCase):
	@classmethod
	def setUpClass(cls):
		super().setUpClass()
		cls.pi = make_pi(supplier=1, items=[("ITEM-Z", 1, 55000)], posting_date="2027-01-10", jp_tax_inclusive=1, submit=True)
		cls.si = make_si(items=[("ITEM-X", 6, 5000), ("ITEM-Y", 10, 2000)], posting_date="2027-01-12", submit=True)
		cls.je = make_je([("716", 1000, 0, None), ("11509", 100, 0, None), ("11101", 0, 1100, None)], "2027-01-14",
			jp_special_case="公共交通機関特例", user_remark="電車賃 東京〜横浜")

	def test_journal_book_columns(self):
		_, data = journal(F)
		pi_rows = [r for r in data if r.get("voucher_no") == self.pi.name]
		self.assertEqual(pi_rows[0].registration, "無")
		self.assertEqual(pi_rows[0].gross, 55000)
		self.assertTrue(any("70%控除対象" in (r.tax_category or "") for r in data))
		si_rows = [r for r in data if r.get("voucher_no") == self.si.name]
		self.assertIn("食品Y※", si_rows[0].remarks)
		je_row = next(r for r in data if r.get("voucher_no") == self.je.name)
		self.assertEqual((je_row.special_case, je_row.remarks), ("公共交通機関特例", "電車賃 東京〜横浜"))
		total = data[-1]
		self.assertEqual(total.debit, total.credit)

	def test_general_ledger_against_and_balances(self):
		_, data = ledger({**F, "account": [acct("11203"), acct("11101"), acct("401")]})
		sales = next(r for r in data if r.get("voucher_no") == self.si.name and r.debit)
		self.assertEqual(sales.against, "諸口")
		cash = next(r for r in data if r.get("voucher_no") == self.je.name)
		self.assertEqual(cash.against, "諸口")
		_, single = ledger({**F, "account": [acct("401")]})
		income = next(r for r in single if r.get("voucher_no") == self.si.name)
		self.assertEqual(income.against, "売掛金（11203）")

	def test_closing_balance_matches_gl(self):
		_, data = ledger({**F, "account": [acct("11509")]})
		closing = next(r for r in data if r.get("remarks") == "次期繰越")
		gl = frappe.db.sql("select sum(debit) - sum(credit) from `tabGL Entry` where account = %s and is_cancelled = 0 and posting_date <= %s",
			(acct("11509"), F["to_date"]))[0][0]
		self.assertEqual(closing.balance, gl)
