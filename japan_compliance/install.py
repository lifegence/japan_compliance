"""インストール・アンインストール時の処理。"""

import frappe

from japan_compliance.jp_tax import master_data
from japan_compliance.setup import custom_fields, sidebar

# 変更履歴（Version）を必ず残す DocType（監査証跡要件 AUD-02）
TRACKED_DOCTYPES = [
	"Sales Invoice", "Purchase Invoice", "Journal Entry", "Payment Entry", "Period Closing Voucher",
	"Account", "Supplier", "Customer", "Company", "Sales Taxes and Charges Template", "Purchase Taxes and Charges Template",
	# 権限設定と利用者のロール付与の変更（AUD-34）
	"Custom DocPerm", "User", "Role",
]


def after_install():
	after_migrate()


def after_migrate():
	custom_fields.setup()
	load_masters()
	enforce_settings()
	sync_print_formats()
	sidebar.setup()
	from japan_compliance.jp_audit import permissions

	permissions.setup()


def load_masters():
	"""存在しない行だけを追加する。既存行は変更しない（適用ルール #9）。"""
	for code, name, direction, tax_type, rate_code, nonreg, reason, ratio, bucket, enabled, phase1 in master_data.TAX_CATEGORIES:
		if not frappe.db.exists("JP Tax Category", code):
			frappe.get_doc({
				"doctype": "JP Tax Category", "code": code, "category_name": name, "direction": direction,
				"tax_type": tax_type, "rate_code": rate_code, "is_nonregistered": nonreg, "requires_reason": reason,
				"ratio_treatment": ratio, "report_bucket": bucket, "enabled": enabled, "phase1": phase1,
			}).insert(ignore_permissions=True)
	for code, name, rate, national, local, valid_from, valid_to, note in master_data.TAX_RATES:
		if not frappe.db.exists("JP Tax Rate", {"rate_code": code, "valid_from": valid_from}):
			frappe.get_doc({
				"doctype": "JP Tax Rate", "rate_code": code, "rate_name": name, "rate": rate, "national_rate": national,
				"local_rate": local, "valid_from": valid_from, "valid_to": valid_to, "note": note,
			}).insert(ignore_permissions=True)
	for code, name, rtype, value, vf, vt, psf, pst, desc in master_data.RULE_PERIODS:
		if not frappe.db.exists("JP Tax Rule Period", code):
			frappe.get_doc({
				"doctype": "JP Tax Rule Period", "rule_code": code, "rule_name": name, "rule_type": rtype, "value": value,
				"valid_from": vf, "valid_to": vt, "period_start_from": psf, "period_start_to": pst, "description": desc,
			}).insert(ignore_permissions=True)


def protect_versions():
	"""Log Settings の自動削除の対象から Version を外す（AUD-10）。インストール時と日次で検査する。"""
	settings = frappe.get_single("Log Settings")
	rows = [r for r in settings.get("logs_to_clear") or [] if r.ref_doctype in ("Version", "Deleted Document")]
	if rows:
		for r in rows:
			settings.remove(r)
		settings.save(ignore_permissions=True)
		frappe.log_error(title="japan_compliance：Version の自動削除設定を外しました",
			message="Log Settings の自動削除の対象から Version／Deleted Document を外しました（監査証跡の保全）。")


def enforce_settings():
	# 行単位の丸めは適格請求書の要件に反するため無効にする（端数処理方針 4.5）
	frappe.db.set_single_value("Accounts Settings", "round_row_wise_tax", 0)
	# 取消の反対仕訳を取消日に計上し、締めた月の帳簿を変えない（月次締め要件 CLS-31）
	frappe.db.set_single_value("Accounts Settings", "enable_immutable_ledger", 1)
	protect_versions()
	# 円は小数なし（帳票記載要件 FORM-01）。通貨マスタの書式が小数2桁だと円換算額に銭が残る
	frappe.db.set_value("Currency", "JPY", {"number_format": "#,###", "smallest_currency_fraction_value": 1, "fraction_units": 0,
		"symbol": "¥", "enabled": 1})
	# 通貨ごとの書式で精度を決める（円は 0 桁、米ドルは 2 桁）。既定のシステム書式（小数2桁）だと円にも銭が付く
	frappe.db.set_single_value("System Settings", "use_number_format_from_currency", 1)
	# 画面の書式は既定値（DefaultValue）を見るため、こちらにも入れる
	frappe.db.set_default("use_number_format_from_currency", 1)
	for doctype in TRACKED_DOCTYPES:
		if not frappe.get_meta(doctype).track_changes:
			frappe.make_property_setter(
				{"doctype": doctype, "doctype_or_field": "DocType", "property": "track_changes", "value": 1, "property_type": "Check"},
				validate_fields_for_doctype=False,
			)


PRINT_FORMATS = [
	# (名前, DocType, テンプレート)
	("適格請求書（JP）", "Sales Invoice", "qualified_invoice.html"),
]


def sync_print_formats():
	"""アプリ内のテンプレートを Print Format に反映する（テンプレートの変更はマイグレートで反映）。"""
	import os

	base = os.path.join(os.path.dirname(__file__), "jp_form", "print_templates")
	for name, doctype, template in PRINT_FORMATS:
		with open(os.path.join(base, template), encoding="utf-8") as f:
			html = f.read()
		doc = frappe.get_doc("Print Format", name) if frappe.db.exists("Print Format", name) else frappe.new_doc("Print Format")
		doc.update({
			"name": name, "__newname": name, "doc_type": doctype, "module": "JP Form", "standard": "No",
			"custom_format": 1, "print_format_type": "Jinja", "html": html, "disabled": 0, "pdf_generator": "wkhtmltopdf",
		})
		doc.save(ignore_permissions=True) if not doc.is_new() else doc.insert(ignore_permissions=True)


def before_uninstall():
	pass
