"""職務分掌・権限・承認フローのテスト（E-1 TS-10-06〜09）。"""

import frappe
from frappe.model.workflow import apply_workflow

from japan_compliance.jp_audit.permissions import BOOKKEEPING_FIRM_ROLE
from japan_compliance.tests import fixtures as fx
from japan_compliance.tests.test_invoice_tax import JPTaxTestCase, make_si
from japan_compliance.tests.test_tax_summary import make_je

USERS = {
	"tanto@example.com": ["Accounts User"],
	"sekinin@example.com": ["Accounts Manager", "Accounts User"],
	"firm-book@example.com": ["Accounts User", BOOKKEEPING_FIRM_ROLE],
	"firm-view@example.com": ["Auditor"],
}


def ensure_user(email, roles):
	if not frappe.db.exists("User", email):
		frappe.get_doc({"doctype": "User", "email": email, "first_name": email.split("@")[0], "send_welcome_email": 0,
			"roles": [{"role": r} for r in roles]}).insert(ignore_permissions=True)


def set_model(model):
	settings = frappe.get_single("JP Compliance Settings")
	settings.permission_model = model
	settings.save()


class TestPermissions(JPTaxTestCase):
	@classmethod
	def setUpClass(cls):
		super().setUpClass()
		for email, roles in USERS.items():
			ensure_user(email, roles)

	def tearDown(self):
		frappe.set_user("Administrator")
		set_model("分離型")

	def test_ts_10_06_separated_model(self):
		set_model("分離型")
		frappe.set_user("tanto@example.com")
		si = make_si(items=[("ITEM-X", 1, 5000)], posting_date="2027-03-01")
		with self.assertRaises(frappe.PermissionError):
			si.submit()

	def test_ts_10_07_combined_model(self):
		set_model("一体型")
		frappe.set_user("tanto@example.com")
		si = make_si(items=[("ITEM-X", 1, 5000)], posting_date="2027-03-01")
		si.submit()
		si.cancel()
		self.assertEqual(si.docstatus, 2)

	def test_ts_10_10_auditor_cannot_create(self):
		frappe.set_user("firm-view@example.com")
		with self.assertRaises(frappe.PermissionError):
			make_si(items=[("ITEM-X", 1, 5000)], posting_date="2027-03-01")

	def test_ts_10_11_bookkeeping_firm_can_create(self):
		frappe.set_user("firm-book@example.com")
		si = make_si(items=[("ITEM-X", 1, 5000)], posting_date="2027-03-01")
		self.assertEqual(si.owner, "firm-book@example.com")

	def test_ts_10_08_09_workflow(self):
		frappe.db.set_value("Workflow", "JP 承認フロー（Journal Entry）", "is_active", 1)
		frappe.clear_cache(doctype="Journal Entry")
		self.addCleanup(lambda: (frappe.db.set_value("Workflow", "JP 承認フロー（Journal Entry）", "is_active", 0),
			frappe.clear_cache(doctype="Journal Entry")))
		frappe.set_user("sekinin@example.com")
		je = make_je([("715", 5000, 0, None), ("11509", 500, 0, None), ("21203", 0, 5500, None, {"party_type": "Supplier", "party": fx.supplier(0)})], "2027-03-02", submit=False)
		je = apply_workflow(je, "申請")
		with self.assertRaises(frappe.ValidationError):
			apply_workflow(je, "承認")

		frappe.set_user("tanto@example.com")
		je2 = make_je([("715", 3000, 0, None), ("11509", 300, 0, None), ("21203", 0, 3300, None, {"party_type": "Supplier", "party": fx.supplier(0)})], "2027-03-02", submit=False)
		je2 = apply_workflow(je2, "申請")
		je2.reload()
		je2.user_remark = "承認待ちの間に書き換え"
		with self.assertRaises(frappe.PermissionError):
			je2.save()
		frappe.set_user("sekinin@example.com")
		je2 = apply_workflow(frappe.get_doc("Journal Entry", je2.name), "差戻し")
		frappe.set_user("tanto@example.com")
		je2.reload()
		je2.user_remark = "差戻し後に修正"
		je2.save()
		je2 = apply_workflow(je2, "再申請")
		frappe.set_user("sekinin@example.com")
		je2 = apply_workflow(frappe.get_doc("Journal Entry", je2.name), "承認")
		self.assertEqual(je2.docstatus, 1)
