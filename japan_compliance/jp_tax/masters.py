"""税区分・税率・経過措置マスタの参照（税区分定義 適用ルール #2・#6）。"""

import frappe
from frappe import _
from frappe.utils import getdate


def get_settings(company):
	"""会社の JP Tax Settings。無効または未設定なら None。"""
	if not company or not frappe.db.exists("JP Tax Settings", company):
		return None
	settings = frappe.get_cached_doc("JP Tax Settings", company)
	return settings if settings.enabled else None


def get_category(code):
	return frappe.get_cached_doc("JP Tax Category", code)


def get_rate(rate_code, on_date):
	"""適用開始日 ≤ 適用日 ≤ 適用終了日 の有効な税率行を1件返す。"""
	on_date = getdate(on_date)
	rows = frappe.get_all(
		"JP Tax Rate",
		filters={"rate_code": rate_code, "enabled": 1, "valid_from": ("<=", on_date)},
		or_filters=[["valid_to", "is", "not set"], ["valid_to", ">=", on_date]],
		fields=["name", "rate", "national_rate", "local_rate"],
		order_by="valid_from desc",
	)
	if not rows:
		frappe.throw(_("{0} 時点で有効な税率（{1}）がありません。JP Tax Rate を確認してください。").format(on_date, rate_code))
	return rows[0]


def get_deduction_rule(on_date):
	"""免税事業者等からの課税仕入れの控除割合（DED-*）。課税仕入れの時期で判定する。"""
	on_date = getdate(on_date)
	rows = frappe.get_all(
		"JP Tax Rule Period",
		filters={"rule_type": "控除割合", "valid_from": ("<=", on_date)},
		or_filters=[["valid_to", "is", "not set"], ["valid_to", ">=", on_date]],
		fields=["rule_code", "value"],
		order_by="valid_from desc",
	)
	if not rows:
		frappe.throw(_("{0} 時点の控除割合（DED-*）がありません。JP Tax Rule Period を確認してください。").format(on_date))
	return rows[0]


def get_purchase_cap(period_start):
	"""課税期間の開始日に応じた一の者からの課税仕入れの上限（CAP-*）。該当なしは None。"""
	if not period_start:
		return None
	period_start = getdate(period_start)
	for row in frappe.get_all(
		"JP Tax Rule Period",
		filters={"rule_type": "上限"},
		fields=["rule_code", "value", "period_start_from", "period_start_to"],
	):
		if row.period_start_from and period_start < getdate(row.period_start_from):
			continue
		if row.period_start_to and period_start > getdate(row.period_start_to):
			continue
		return row
	return None
