from frappe.model.document import Document

from japan_compliance.jp_audit.guards import deny_master_delete


class JPTaxCategory(Document):
	def on_trash(self):
		deny_master_delete(self)
