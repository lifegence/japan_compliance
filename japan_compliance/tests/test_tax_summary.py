"""消費税集計表と振替伝票の税区分のテスト（E-1 TS-03-08、TS-07-08、TS-12-06〜08、TS-13-07）。

他のテストと日付が重ならないよう 2026年11月・12月の伝票で確かめる。
"""

import frappe

from japan_compliance.jp_core.company import account_by_number
from japan_compliance.jp_form.report.jp_consumption_tax_summary.jp_consumption_tax_summary import execute
from japan_compliance.tests import fixtures as fx
from japan_compliance.tests.test_invoice_tax import A, B, JPTaxTestCase, make_pi, make_si


def acct(number, company=A):
	return account_by_number(company, number)


def make_je(lines, posting_date, company=A, submit=True, **kw):
	"""lines = [(科目番号, 借方, 貸方, 税区分 or None, extra)]"""
	rows = []
	for number, debit, credit, category, *extra in lines:
		row = {"account": acct(number, company), "debit_in_account_currency": debit, "credit_in_account_currency": credit}
		if category:
			row["jp_tax_category"] = category
		if extra:
			row.update(extra[0])
		rows.append(row)
	doc = frappe.get_doc({"doctype": "Journal Entry", "company": company, "posting_date": posting_date, "accounts": rows, **kw})
	doc.insert()
	if submit:
		doc.submit()
	return doc


def report(view, from_date, to_date, company=A):
	columns, data = execute({"company": company, "from_date": from_date, "to_date": to_date, "view": view})
	return data


def by_key(data):
	return {(r.tax_category, r.transaction_kind, r.deduction_ratio): r for r in data}


class TestTaxSummary(JPTaxTestCase):
	def test_category_view_mixed_documents(self):
		make_si(items=[("ITEM-X", 20, 5000)], posting_date="2026-11-05", submit=True)
		make_si(items=[("ITEM-Y", 10, 2000)], posting_date="2026-11-05", submit=True)
		si = make_si(items=[("ITEM-X", 6, 5000)], posting_date="2026-11-06", submit=True)
		# 貸倒（B-6 S-09）
		make_je([
			("739", 30000, 0, "S-TAX-10"), ("21216", 3000, 0, None),
			("11203", 0, 33000, None, {"party_type": "Customer", "party": fx.customer(0)}),
		], "2026-11-30", jp_transaction_kind="貸倒")
		make_pi(supplier=1, items=[("ITEM-Z", 1, 55000)], posting_date="2026-11-10", jp_tax_inclusive=1, submit=True)
		make_pi(items=[("ITEM-X", 20, 2500)], posting_date="2026-11-10", submit=True)
		# 電車賃（B-6 P-04）。旅費交通費の既定 P-TAX-10 が借方なので自動で入る
		je = make_je([("716", 1000, 0, None), ("11509", 100, 0, None), ("11101", 0, 1100, None)], "2026-11-14",
			jp_special_case="公共交通機関特例")
		self.assertEqual(je.accounts[0].jp_tax_category, "P-TAX-10")

		rows = by_key(report("区分別集計", "2026-11-01", "2026-11-30"))
		s10 = rows[("S-TAX-10", "通常", None)]
		self.assertEqual((s10.net_amount, s10.tax_amount, s10.count), (130000, 13000, 2))
		self.assertEqual(rows[("S-TAX-8R", "通常", None)].tax_amount, 1600)
		bad = rows[("S-TAX-10", "貸倒", None)]
		self.assertEqual((bad.net_amount, bad.tax_amount), (-30000, -3000))
		nreg = rows[("P-NREG-10", "通常", 70)]
		self.assertEqual((nreg.gross_amount, nreg.deductible_tax, nreg.nondeductible_tax), (55000, 3500, 1500))
		p10 = rows[("P-TAX-10", "通常", None)]
		self.assertEqual((p10.net_amount, p10.tax_amount, p10.deductible_tax, p10.count), (51000, 5100, 5100, 2))
		self.assertTrue(si.name)

	def test_settlement_entry_is_excluded_and_out_of_scope(self):
		make_si(items=[("ITEM-X", 20, 5000)], posting_date="2027-01-05", submit=True)
		je = make_je([
			("21216", 10000, 0, None), ("8106", 0, 100, "S-TAX-10"), ("21208", 0, 9900, None),
		], "2027-01-31", jp_tax_settlement=1)
		self.assertEqual(je.accounts[1].jp_tax_category, "S-OOS")
		rows = by_key(report("区分別集計", "2027-01-01", "2027-01-31"))
		self.assertEqual(rows[("S-TAX-10", "通常", None)].tax_amount, 10000)
		self.assertNotIn(("S-OOS", "通常", None), rows)
		filing = {r.item: r for r in report("申告用の計算", "2027-01-01", "2027-01-31")}
		self.assertIn("差額 0", filing["仮受消費税等：元帳／集計表／差額"].note)

	def test_fixed_asset_sale_counts_sale_price_only(self):
		# B-6 A-03：売却額を資産の科目で課税売上にし、売却益は対象外（E-1 TS-13-07）
		make_je([("11104", 110000, 0, None), ("12106", 0, 100000, "S-TAX-10"), ("21216", 0, 10000, None)], "2026-11-20")
		make_je([("12108", 220000, 0, None), ("12106", 0, 200000, None), ("9102", 0, 20000, "S-OOS")], "2026-11-20")
		rows = by_key(report("区分別集計", "2026-11-20", "2026-11-20"))
		self.assertEqual((rows[("S-TAX-10", "通常", None)].net_amount, rows[("S-TAX-10", "通常", None)].tax_amount), (100000, 10000))
		self.assertEqual(rows[("S-OOS", "通常", None)].net_amount, 20000)

	def test_filing_view_warimodoshi(self):
		make_si(items=[("ITEM-X", 20, 5000)], posting_date="2026-12-10", submit=True)
		filing = {r.item: r for r in report("申告用の計算", "2026-12-10", "2026-12-10")}
		self.assertEqual(filing["税込売上合計"].std, 110000)
		self.assertEqual(filing["課税標準額（千円未満切捨て）"].std, 100000)
		self.assertEqual(filing["消費税額（国税 7.8%／6.24%）"].std, 7800)

	def test_ts_03_08_supplier_cap(self):
		make_pi(company=B, supplier=1, items=[("ITEM-Z", 1, 60000000)], posting_date="2026-10-05", jp_tax_inclusive=1, submit=True)
		pi = make_pi(company=B, supplier=1, items=[("ITEM-Z", 1, 50000000)], posting_date="2026-11-05", jp_tax_inclusive=1, submit=True)
		make_pi(company=B, supplier=1, items=[("ITEM-Z", -1, 5000000)], posting_date="2026-11-20", jp_tax_inclusive=1,
			is_return=1, return_against=pi.name, submit=True)
		data = report("免税事業者等の仕入先別累計", "2026-10-01", "2026-11-30", company=B)
		row = next(r for r in data if r.supplier == fx.supplier(1))
		self.assertEqual((row.gross, row.cap_rule, row.cap, row.over), (105000000, "CAP-100M", 100000000, 5000000))
