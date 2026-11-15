"""E-2 を実行した状態のシステム出力を、照合用に JSON で書き出す（L-31・L-32）。読み取りのみ。

  bench --site <site> execute japan_compliance.tests.e2_export.run --kwargs "{'path': '/tmp/e2_export.json'}"
"""

import json
import re

import frappe

from japan_compliance.jp_close import checks
from japan_compliance.jp_form.report.jp_consumption_tax_summary.jp_consumption_tax_summary import execute as tax_summary
from japan_compliance.jp_form.report.jp_general_ledger.jp_general_ledger import execute as ledger
from japan_compliance.jp_form.report.jp_journal_book.jp_journal_book import execute as journal
from japan_compliance.tests import e2_data as e2

A, B = e2.A, e2.B
DID = re.compile(r"^(D-\d+P?)(?:-\d+)?\b")


def voucher_rows():
	out = {}
	for doctype, field in (("Sales Invoice", "remarks"), ("Purchase Invoice", "remarks"), ("Payment Entry", "reference_no"), ("Journal Entry", "user_remark")):
		for row in frappe.get_all(doctype, fields=["name", "company", "docstatus", "posting_date", field + " as remark", "amended_from"], order_by="creation"):
			m = DID.match(row.remark or "")
			if not m:
				continue
			out.setdefault(m.group(1), []).append(detail(doctype, row))
	return out


def detail(doctype, row):
	gl = frappe.db.sql(
		"""select g.account, a.account_number, a.account_name, g.debit, g.credit, g.posting_date, g.party, g.remarks
		from `tabGL Entry` g join tabAccount a on a.name = g.account
		where g.voucher_type = %s and g.voucher_no = %s and g.is_cancelled = 0 order by g.creation""",
		(doctype, row.name), as_dict=True)
	doc = frappe.get_doc(doctype, row.name)
	data = {"doctype": doctype, "name": row.name, "company": row.company, "docstatus": row.docstatus,
		"posting_date": row.posting_date, "remark": row.remark, "amended_from": row.amended_from, "gl": gl}
	if doctype in ("Sales Invoice", "Purchase Invoice"):
		data["grand_total"] = doc.grand_total
		data["total_taxes"] = doc.total_taxes_and_charges
		data["party"] = doc.get("customer_name") or doc.get("supplier_name")
		data["breakdown"] = [b.as_dict(no_default_fields=True) for b in doc.jp_tax_breakdown]
		data["items"] = [{"item": i.item_name, "qty": i.qty, "rate": i.rate, "amount": i.amount, "net": i.net_amount, "category": i.jp_tax_category} for i in doc.items]
		data["special_case"] = doc.get("jp_special_case")
	if doctype == "Journal Entry":
		data["lines"] = [{"account": a.account, "debit": a.debit, "credit": a.credit, "category": a.jp_tax_category} for a in doc.accounts]
		data["kind"] = doc.get("jp_transaction_kind")
		data["settlement"] = doc.get("jp_tax_settlement")
	if doctype == "Payment Entry":
		data["party"] = doc.party_name
		data["deductions"] = [{"account": d.account, "amount": d.amount, "category": d.jp_tax_category} for d in doc.deductions]
	return data


def report(fn, **filters):
	columns, rows = fn(filters)[:2]
	return {"columns": [c.get("fieldname") if isinstance(c, dict) else c for c in columns], "rows": rows}


def run(path="/tmp/e2_export.json"):
	frappe.set_user("Administrator")
	periods = {"A": (A, "2026-04-01", "2026-10-31"), "A_sep": (A, "2026-09-01", "2026-09-30"), "A_oct": (A, "2026-10-01", "2026-10-31"),
		"B_prev": (B, "2025-10-01", "2026-09-30"), "B": (B, "2026-10-01", "2026-11-30")}
	result = {"vouchers": voucher_rows(), "reports": {}, "checks": {}, "site": frappe.local.site,
		"frozen": {c: frappe.db.get_value("Company", c, "accounts_frozen_till_date") for c in (A, B)},
		"immutable": frappe.db.get_single_value("Accounts Settings", "enable_immutable_ledger")}
	for key, (company, start, end) in periods.items():
		for view in ("区分別集計", "申告用の計算", "免税事業者等の仕入先別累計"):
			result["reports"][f"{key}:{view}"] = report(tax_summary, company=company, from_date=start, to_date=end, view=view)
		result["reports"][f"{key}:journal"] = report(journal, company=company, from_date=start, to_date=end)
	result["reports"]["A_oct:ledger"] = report(ledger, company=A, from_date="2026-10-01", to_date="2026-10-31")
	for label, company, month in (("A_sep", A, "2026-09-01"), ("A_oct", A, "2026-10-01"), ("B_sep", B, "2026-09-01"), ("B_oct", B, "2026-10-01")):
		result["checks"][label] = [c if isinstance(c, dict) else c.as_dict() for c in checks.run(company, month)]
	with open(path, "w", encoding="utf-8") as f:
		json.dump(result, f, ensure_ascii=False, indent=1, default=str)
	print(path, len(result["vouchers"]))
