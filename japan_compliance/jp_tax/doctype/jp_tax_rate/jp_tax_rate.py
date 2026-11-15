import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import getdate

from japan_compliance.jp_audit.guards import deny_master_delete

# 改正は行の追加で行う。既存行で変えてよいのは適用終了日・有効・備考だけ（適用ルール #9）
IMMUTABLE = ("rate_code", "rate", "national_rate", "local_rate", "valid_from")


class JPTaxRate(Document):
	def validate(self):
		if self.valid_to and getdate(self.valid_to) < getdate(self.valid_from):
			frappe.throw(_("適用終了日は適用開始日より後にしてください。"))
		self.validate_immutable()
		self.validate_overlap()

	def validate_immutable(self):
		if self.is_new():
			return
		before = self.get_doc_before_save()
		for field in IMMUTABLE:
			if before and str(before.get(field)) != str(self.get(field)):
				frappe.throw(_("{0} は変更できません。改正は新しい行を追加し、この行は適用終了日を入力してください。").format(self.meta.get_label(field)))

	def validate_overlap(self):
		if not self.enabled:
			return
		for other in frappe.get_all(
			"JP Tax Rate",
			filters={"rate_code": self.rate_code, "enabled": 1, "name": ("!=", self.name or "")},
			fields=["name", "valid_from", "valid_to"],
		):
			start = max(getdate(self.valid_from), getdate(other.valid_from))
			end_self = getdate(self.valid_to) if self.valid_to else getdate("9999-12-31")
			end_other = getdate(other.valid_to) if other.valid_to else getdate("9999-12-31")
			if start <= min(end_self, end_other):
				frappe.throw(_("税率コード {0} の適用期間が {1} と重なっています。既存行の適用終了日を先に入力してください。").format(
					self.rate_code, other.name))

	def on_trash(self):
		deny_master_delete(self)
