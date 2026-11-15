import frappe
from frappe import _
from frappe.model.document import Document

from japan_compliance.jp_invoice.registration import validate_registration_number


class JPTaxSettings(Document):
	def validate(self):
		# 売上を積上げ計算にする場合、仕入は積上げ計算のみ（消費税法施行令46条3項、端数処理方針 4.1 #2）
		if self.sales_calc_method == "積上げ計算" and self.purchase_calc_method == "割戻し計算":
			frappe.throw(_("売上税額を積上げ計算にする場合、仕入税額に割戻し計算は選べません。"))
		if self.registration_number:
			validate_registration_number(self.registration_number)
		for field in ("sales_tax_account", "purchase_tax_account", "nondeductible_loss_account"):
			account = self.get(field)
			if account and frappe.db.get_value("Account", account, "company") != self.company:
				frappe.throw(_("{0} は会社 {1} の科目を指定してください。").format(self.meta.get_label(field), self.company))
		self.validate_period_change()
		self.validate_close_checks()
		self.validate_lock_mode_change()

	def validate_lock_mode_change(self):
		"""月次ロックの運用を変更できるのは管理者だけ（CLS-08）。履歴は track_changes で Version に残る。"""
		before = self.get_doc_before_save()
		if before and before.monthly_lock_mode != self.monthly_lock_mode and "System Manager" not in frappe.get_roles():
			frappe.throw(_("月次ロックの運用を変更できるのは管理者（System Manager）だけです。"), frappe.PermissionError)

	def validate_close_checks(self):
		codes = [c.strip().upper() for c in (self.disabled_close_checks or "").split(",") if c.strip()]
		from japan_compliance.jp_close.checks import CHECKS

		severity = {c[0]: c[2] for c in CHECKS}
		for code in codes:
			if code not in severity:
				frappe.throw(_("締め前チェック {0} はありません。").format(code))
			if severity[code] == "エラー":
				frappe.throw(_("エラーの項目（{0}）は無効にできません。").format(code))
		self.disabled_close_checks = ",".join(codes)

	def validate_period_change(self):
		"""控除不能分の処理は課税期間の途中では変えない（端数処理方針 4.1 #7）。"""
		before = self.get_doc_before_save()
		if before and before.nondeductible_method != self.nondeductible_method and self.tax_period_start:
			if frappe.db.exists("Purchase Invoice", {
				"company": self.company, "docstatus": 1, "posting_date": (">=", self.tax_period_start),
			}):
				frappe.throw(_("課税期間の途中で控除不能分の処理は変更できません。次の課税期間の開始時に変更してください。"))
