"""締め後の訂正の案内と締め遅れの通知（月次締め要件 CLS-06・CLS-33）。"""

import frappe
from frappe import _
from frappe.utils import add_days, add_months, get_first_day, getdate, nowdate

NO_LOCK = "期中はロックしない"


def locks_monthly(company):
	"""月次でロックする会社か（CLS-08）。設定がなければ既定の「月次でロックする」とする。"""
	return frappe.db.get_value("JP Tax Settings", company, "monthly_lock_mode") != NO_LOCK


def amended_posting_date(doc, method=None):
	"""締めた月の伝票の修正版は、当月の日付を既定にする（CLS-33）。"""
	if doc.docstatus != 0 or not doc.get("amended_from") or not doc.get("posting_date"):
		return
	frozen = frappe.get_cached_value("Company", doc.company, "accounts_frozen_till_date")
	if frozen and getdate(doc.posting_date) <= getdate(frozen):
		old = doc.posting_date
		doc.posting_date = nowdate()
		if doc.meta.has_field("set_posting_time"):
			doc.set_posting_time = 1
		if doc.get("due_date") and getdate(doc.due_date) < getdate(doc.posting_date):
			doc.due_date = doc.posting_date
		for row in doc.get("payment_schedule") or []:
			if row.due_date and getdate(row.due_date) < getdate(doc.posting_date):
				row.due_date = doc.posting_date
		frappe.msgprint(
			_("元の伝票の計上日（{0}）は締めた月のため、修正版の計上日を本日（{1}）にしました。").format(old, doc.posting_date),
			title=_("修正版の計上日"), indicator="blue",
		)


def notify_overdue_close():
	"""締めていない月が2か月以上ある会社を管理者に通知する（CLS-06、日次）。期中はロックしない会社は対象外。"""
	limit = get_first_day(add_months(nowdate(), -2))
	for company in frappe.get_all("JP Tax Settings", filters={"enabled": 1}, pluck="company"):
		if not locks_monthly(company):
			continue
		frozen = frappe.db.get_value("Company", company, "accounts_frozen_till_date")
		if frozen and getdate(frozen) >= add_days(limit, -1):
			continue
		if not frappe.db.exists("GL Entry", {"company": company, "posting_date": ("<", limit), "is_cancelled": 0}):
			continue
		message = _("{0} は {1} より前の月が締められていません（凍結日：{2}）。").format(company, limit, frozen or _("未設定"))
		for user in managers():
			if not frappe.db.exists("Notification Log", {"for_user": user, "subject": message}):
				frappe.get_doc({"doctype": "Notification Log", "for_user": user, "type": "Alert", "subject": message,
					"document_type": "Company", "document_name": company}).insert(ignore_permissions=True)


def managers():
	return frappe.get_all("Has Role", filters={"role": "System Manager", "parenttype": "User",
		"parent": ("not in", ["Administrator", "Guest"])}, pluck="parent", distinct=True)
