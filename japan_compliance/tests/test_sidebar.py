"""財務レポートのメニューに日本の帳票が出ること（標準のメニューでは仕訳帳が検索しないと開けないため）。"""

import frappe
from frappe.tests import IntegrationTestCase

from japan_compliance.setup import sidebar


def items():
	return frappe.get_all(
		"Workspace Sidebar Item",
		filters={"parent": sidebar.SIDEBAR, "parenttype": "Workspace Sidebar"},
		fields=["name", "idx", "type", "label", "link_to"],
		order_by="idx asc",
	)


class TestSidebar(IntegrationTestCase):
	def setUp(self):
		# 新規サイト（CI など）では ERPNext の標準メニューがまだ作られていないことがある。その場合 setup() は何もしない
		if not frappe.db.exists("Workspace Sidebar", sidebar.SIDEBAR):
			self.skipTest("Workspace Sidebar「Financial Reports」がないサイト")

	def test_reports_listed_once_after_first_section(self):
		sidebar.setup()
		sidebar.setup()  # 2回目で重複しないこと
		rows = items()
		links = [r.link_to for r in rows if r.type == "Link"]
		for report in sidebar.REPORTS:
			self.assertEqual(links.count(report), 1, report)
		sections = [r.label for r in rows if r.type == "Section Break"]
		self.assertEqual(sections.count(sidebar.SECTION), 1)
		self.assertEqual(sections[1], sidebar.SECTION)  # 最初の区分（Financial Reports）の直後
		self.assertEqual([r.idx for r in rows], list(range(1, len(rows) + 1)))

	def test_restored_after_erpnext_resync(self):
		# ERPNext の同期で標準の行だけに戻った状態を作る
		for r in items():
			if r.label == sidebar.SECTION or r.link_to in sidebar.REPORTS:
				frappe.db.delete("Workspace Sidebar Item", r.name)
		sidebar.setup()
		links = [r.link_to for r in items() if r.type == "Link"]
		self.assertIn("JP Journal Book", links)
