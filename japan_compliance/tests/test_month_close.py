"""月次締め・期間ロックのテスト（E-1 TS-07-01〜06、TS-10-12）。他のテストと重ならない 2027年2月で確かめる。"""

import frappe

from japan_compliance.jp_close.close import NO_LOCK, notify_overdue_close
from japan_compliance.tests.test_invoice_tax import A, JPTaxTestCase, make_si
from japan_compliance.tests.test_permissions import ensure_user

MONTH = "2027-02-01"


def month_close(month=MONTH):
	doc = frappe.get_doc({"doctype": "JP Month Close", "company": A, "period_start": month})
	doc.insert()
	return doc


def results(doc):
	return {r.check_code: r for r in doc.checks}


class TestMonthClose(JPTaxTestCase):
	def test_ts_07_close_freeze_reopen(self):
		draft = make_si(items=[("ITEM-X", 1, 10000)], posting_date="2027-02-10")
		mc = month_close()
		mc.run_checks()
		r = results(mc)
		self.assertEqual((r["C01"].result, r["C01"].count), ("NG", 1))
		self.assertIn(draft.name, r["C01"].references)
		self.assertEqual(r["C03"].result, "OK")
		self.assertEqual(r["C11"].result, "未確認")
		with self.assertRaises(frappe.ValidationError):
			mc.close()

		draft.submit()
		mc.reload()
		for row in mc.checks:
			if row.severity == "確認":
				row.comment = "照合済み（差異なし）"
		mc.save()
		self.assertEqual(results(mc)["C12"].result, "確認済")
		mc.close()
		self.assertEqual(mc.status, "確定")

		mc.freeze()
		self.assertEqual(str(frappe.db.get_value("Company", A, "accounts_frozen_till_date")), "2027-02-28")
		with self.assertRaises(frappe.ValidationError):
			make_si(items=[("ITEM-X", 1, 5000)], posting_date="2027-02-15", submit=True)

		company = frappe.get_doc("Company", A)
		company.accounts_frozen_till_date = "2027-03-31"
		with self.assertRaises(frappe.ValidationError):
			company.save()

		with self.assertRaises(frappe.ValidationError):
			mc.reopen("")
		mc.reopen("2月分の請求書の訂正のため")
		self.assertEqual(mc.status, "解除")
		self.assertEqual(str(frappe.db.get_value("Company", A, "accounts_frozen_till_date")), "2027-01-31")
		self.assertIn("2月分の請求書の訂正のため", mc.reopen_log)
		make_si(items=[("ITEM-X", 1, 5000)], posting_date="2027-02-15", submit=True)
		versions = frappe.get_all("Version", filters={"ref_doctype": "Company", "docname": A}, pluck="data")
		self.assertTrue(any("accounts_frozen_till_date" in v for v in versions))

	def test_confirmed_month_cannot_be_edited(self):
		mc = month_close("2027-01-01")
		mc.run_checks()
		for row in mc.checks:
			if row.severity == "確認":
				row.comment = "確認済み"
		mc.save()
		mc.close()
		mc.reload()
		mc.checks[0].comment = "後から書き換え"
		with self.assertRaises(frappe.ValidationError):
			mc.save()

	def test_frozen_role_is_rejected(self):
		company = frappe.get_doc("Company", A)
		company.role_allowed_for_frozen_entries = "Accounts Manager"
		with self.assertRaises(frappe.ValidationError):
			company.save()

	def test_month_end_only(self):
		frappe.flags.jp_month_close_update = True
		self.addCleanup(lambda: setattr(frappe.flags, "jp_month_close_update", False))
		company = frappe.get_doc("Company", A)
		company.accounts_frozen_till_date = "2027-02-15"
		with self.assertRaises(frappe.ValidationError):
			company.save()


def confirm_all(doc):
	doc.run_checks()
	for row in doc.checks:
		if row.result in ("NG", "未確認"):
			row.comment = "確認済み"
	doc.save()


class TestMonthlyLockMode(JPTaxTestCase):
	"""月次ロックの運用（C-1 4.1、CLS-08、検証観点 #8）。"""

	def setUp(self):
		super().setUp()
		# 凍結日を動かすテストの後に、他のテストの日付で記帳できなくならないよう元に戻す
		frozen = frappe.db.get_value("Company", A, "accounts_frozen_till_date")
		self.addCleanup(frappe.db.set_value, "Company", A, "accounts_frozen_till_date", frozen)

	def test_default_is_monthly_lock(self):
		meta = frappe.get_meta("JP Tax Settings")
		self.assertEqual(meta.get_field("monthly_lock_mode").default, "月次でロックする")
		self.assertEqual(frappe.db.get_value("JP Tax Settings", A, "monthly_lock_mode"), "月次でロックする")
		self.assertTrue(meta.track_changes)

	def test_monthly_cannot_freeze_unconfirmed_month(self):
		with self.assertRaises(frappe.ValidationError):
			month_close("2027-06-01").freeze()

	def test_no_lock_close_does_not_advance_frozen_date(self):
		self.set_settings(monthly_lock_mode=NO_LOCK)
		frozen = frappe.db.get_value("Company", A, "accounts_frozen_till_date")
		mc = month_close("2027-03-01")
		confirm_all(mc)
		mc.close()
		self.assertEqual(mc.status, "確定")
		self.assertEqual(frappe.db.get_value("Company", A, "accounts_frozen_till_date"), frozen)
		make_si(items=[("ITEM-X", 1, 5000)], posting_date="2027-03-15", submit=True)

	def test_no_lock_freeze_any_month_end_and_reopen(self):
		self.set_settings(monthly_lock_mode=NO_LOCK)
		mc = month_close("2027-05-01")
		mc.freeze()
		self.assertEqual(str(frappe.db.get_value("Company", A, "accounts_frozen_till_date")), "2027-05-31")
		with self.assertRaises(frappe.ValidationError):
			make_si(items=[("ITEM-X", 1, 5000)], posting_date="2027-05-15", submit=True)
		# 凍結日に含まれる前月は、記録がなくても解除できる（後の月の凍結を先に解除する）
		april = month_close("2027-04-01")
		with self.assertRaises(frappe.ValidationError):
			april.reopen("4月の訂正のため")
		mc.reopen("5月の訂正のため")
		self.assertEqual(str(frappe.db.get_value("Company", A, "accounts_frozen_till_date")), "2027-04-30")
		april.reload()
		april.reopen("4月の訂正のため")
		self.assertEqual(str(frappe.db.get_value("Company", A, "accounts_frozen_till_date")), "2027-03-31")
		make_si(items=[("ITEM-X", 1, 5000)], posting_date="2027-04-15", submit=True)

	def test_no_lock_has_no_overdue_notification(self):
		ensure_user("kanri2@example.com", ["System Manager", "Accounts Manager", "Accounts User"])
		make_si(items=[("ITEM-X", 1, 5000)], posting_date="2026-06-10", submit=True)
		self.set_settings(monthly_lock_mode=NO_LOCK)
		notify_overdue_close()
		self.assertFalse(frappe.db.exists("Notification Log", {"for_user": "kanri2@example.com", "document_name": A}))
		self.set_settings(monthly_lock_mode="月次でロックする")
		notify_overdue_close()
		self.assertTrue(frappe.db.exists("Notification Log", {"for_user": "kanri2@example.com", "document_name": A}))

	def test_only_admin_can_change_mode(self):
		ensure_user("keiri-manager@example.com", ["Accounts Manager", "Accounts User"])
		self.addCleanup(frappe.set_user, "Administrator")
		frappe.set_user("keiri-manager@example.com")
		settings = frappe.get_doc("JP Tax Settings", A)
		settings.monthly_lock_mode = NO_LOCK
		with self.assertRaises(frappe.PermissionError):
			settings.save()
