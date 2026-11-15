"""記録の保全と削除の禁止（監査証跡・権限要件 4.2）。

Administrator もロール権限の制約を受けないため、権限設定だけでなく削除時の処理で拒否する。
"""

import frappe
from frappe import _

ACCOUNTING_DOCTYPES = ("Sales Invoice", "Purchase Invoice", "Journal Entry", "Payment Entry", "Period Closing Voucher")


def deny_master_delete(doc):
	"""税関係マスタは削除しない。不要になった行は無効にする（AUD-16、適用ルール #9）。"""
	frappe.throw(_("{0} は削除できません。使わなくなった行は「有効」を外してください。").format(_(doc.doctype)))


def deny_version_delete(doc, method=None):
	"""変更履歴（Version）は削除できない（AUD-11）。"""
	if doc.ref_doctype in ACCOUNTING_DOCTYPES or frappe.get_meta(doc.ref_doctype).track_changes:
		frappe.throw(_("変更履歴（Version）は削除できません。"))


def deny_submitted_delete(doc, method=None):
	"""提出済・取消済の会計伝票は削除できない。下書きは削除でき、Deleted Document に残る（AUD-12・13）。"""
	if doc.docstatus in (1, 2):
		frappe.throw(_("提出済・取消済の {0} は削除できません。訂正は取消と修正版の作成で行ってください。").format(_(doc.doctype)))


def deny_transaction_deletion(doc, method=None):
	"""会社の取引の一括削除は帳簿の消去になるため実行させない（AUD-14）。

	例外は、テストデータを許可したサイト（site_config の jp_allow_test_data）でテストデータを入れ直すとき（tests.e2_data.reload）だけ。
	"""
	if frappe.flags.jp_test_data_reload and frappe.conf.get("jp_allow_test_data"):
		return
	frappe.throw(_("会社の取引の一括削除（Transaction Deletion Record）は、帳簿の保存のため使用できません。"))


def deny_row_wise_rounding(doc, method=None):
	"""行単位の丸めは適格請求書の要件に反する（端数処理方針 4.5）。"""
	if doc.get("round_row_wise_tax"):
		frappe.throw(_("「Round Tax Amount Row-wise」は日本の消費税の計算（伝票×税率ごとに1回の端数処理）と両立しないため有効にできません。"))
