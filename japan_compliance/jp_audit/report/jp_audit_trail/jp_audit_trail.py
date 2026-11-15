"""監査証跡（JP）（監査証跡・権限要件 AUD-04）。

変更履歴（Version）と削除した文書（Deleted Document）から、訂正・削除の事実・変更前後・日時・操作者を一覧にする。
"""

import json

import frappe
from frappe import _
from frappe.utils import add_days, getdate

from japan_compliance.install import TRACKED_DOCTYPES

DOCSTATUS = {0: "下書き", 1: "提出済", 2: "取消済"}


def execute(filters=None):
	filters = frappe._dict(filters or {})
	doctypes = [filters.ref_doctype] if filters.get("ref_doctype") else list(TRACKED_DOCTYPES)
	conditions = {"ref_doctype": ("in", doctypes),
		"creation": ("between", [getdate(filters.from_date), add_days(getdate(filters.to_date), 1)])}
	if filters.get("docname"):
		conditions["docname"] = filters.docname
	if filters.get("user"):
		conditions["owner"] = filters.user
	data = []
	for v in frappe.get_all("Version", filters=conditions, fields=["creation", "owner", "ref_doctype", "docname", "data"], order_by="creation"):
		if not in_company(v.ref_doctype, v.docname, filters.get("company")):
			continue
		data += rows_for(v)
	deleted = {"deleted_doctype": ("in", doctypes),
		"creation": ("between", [getdate(filters.from_date), add_days(getdate(filters.to_date), 1)])}
	for d in frappe.get_all("Deleted Document", filters=deleted, fields=["creation", "owner", "deleted_doctype", "deleted_name"]):
		data.append(frappe._dict(timestamp=d.creation, user=d.owner, ref_doctype=d.deleted_doctype, docname=d.deleted_name,
			operation=_("削除（下書き）"), field="", before="", after=""))
	data.sort(key=lambda r: r.timestamp)
	return columns(), data


def in_company(doctype, name, company):
	if not company or not frappe.get_meta(doctype).has_field("company"):
		return True
	return frappe.db.get_value(doctype, name, "company") in (company, None)


def rows_for(v):
	payload = json.loads(v.data or "{}")
	base = dict(timestamp=v.creation, user=v.owner, ref_doctype=v.ref_doctype, docname=v.docname)
	rows = []
	changed = payload.get("changed") or []
	docstatus = next((c for c in changed if c[0] == "docstatus"), None)
	if docstatus:
		operation = {1: _("提出"), 2: _("取消")}.get(docstatus[2], _("状態の変更"))
	elif payload.get("amended_from") or any(c[0] == "amended_from" for c in changed):
		operation = _("修正版の作成")
	elif changed or payload.get("row_changed") or payload.get("added") or payload.get("removed"):
		operation = _("保存（変更）")
	else:
		operation = _("作成")
	# 状態（提出・取消）の変更を先頭に出す
	for field, before, after in sorted(changed, key=lambda c: c[0] != "docstatus"):
		if field == "docstatus":
			before, after = DOCSTATUS.get(int(before or 0), before), DOCSTATUS.get(int(after or 0), after)
		rows.append(frappe._dict(base, operation=operation, field=label(v.ref_doctype, field), before=fmt(before), after=fmt(after)))
	for table, idx, name, fields in payload.get("row_changed") or []:
		for field, before, after in fields:
			rows.append(frappe._dict(base, operation=operation, field=f"{label(v.ref_doctype, table)} 行{idx}：{field}", before=fmt(before), after=fmt(after)))
	for key, op in (("added", _("行の追加")), ("removed", _("行の削除"))):
		for table, row in payload.get(key) or []:
			rows.append(frappe._dict(base, operation=operation, field=f"{label(v.ref_doctype, table)}：{op}", before="", after=""))
	if not rows:
		rows.append(frappe._dict(base, operation=operation, field="", before="", after=""))
	return rows


def label(doctype, field):
	df = frappe.get_meta(doctype).get_field(field)
	return _(df.label) if df and df.label else field


def fmt(value):
	if value is None:
		return ""
	return str(value)[:140]


def columns():
	return [
		{"fieldname": "timestamp", "label": "日時", "fieldtype": "Datetime", "width": 160},
		{"fieldname": "user", "label": "操作者", "fieldtype": "Link", "options": "User", "width": 190},
		{"fieldname": "ref_doctype", "label": "伝票種別", "fieldtype": "Data", "width": 130},
		{"fieldname": "docname", "label": "伝票番号", "fieldtype": "Dynamic Link", "options": "ref_doctype", "width": 170},
		{"fieldname": "operation", "label": "操作", "fieldtype": "Data", "width": 110},
		{"fieldname": "field", "label": "項目", "fieldtype": "Data", "width": 180},
		{"fieldname": "before", "label": "変更前", "fieldtype": "Data", "width": 160},
		{"fieldname": "after", "label": "変更後", "fieldtype": "Data", "width": 160},
	]
