from frappe.model.document import Document

from japan_compliance.jp_audit.permissions import apply_permission_model


class JPComplianceSettings(Document):
	def on_update(self):
		apply_permission_model(self.permission_model)
