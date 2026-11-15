"""適格請求書の発行（インボイス要件 INV-01・14・50・51）。"""

import frappe
from frappe import _

from japan_compliance.jp_tax import masters

PRINT_FORMAT = "適格請求書（JP）"
COPY_PREFIX = "適格請求書の写し_"


def sales_invoice_validate(doc, method=None):
	"""発行時点の自社の登録番号を伝票に残す（会社の登録番号を後で変えても過去の請求書は変わらない）。"""
	settings = masters.get_settings(doc.company)
	if settings and doc.docstatus == 0:
		doc.jp_issuer_registration_number = settings.registration_number


def attach_invoice_copy(doc, method=None):
	"""確定時に適格請求書の PDF を生成し、非公開ファイルとして添付する（INV-50）。

	PDF の生成は重く失敗もしうるため、確定の処理とは切り離し、コミット後のバックグラウンドジョブで行う。
	"""
	if not masters.get_settings(doc.company) or doc.get("is_opening") == "Yes":
		return
	if frappe.flags.in_test:
		if frappe.flags.jp_attach_copy_in_test:
			make_invoice_copy(doc.doctype, doc.name)
		return
	frappe.enqueue(make_invoice_copy, queue="short", enqueue_after_commit=True, doctype=doc.doctype, name=doc.name)


def make_invoice_copy(doctype, name):
	from frappe.utils.pdf import get_pdf

	file_name = f"{COPY_PREFIX}{name}.pdf"
	if frappe.db.exists("File", {"attached_to_doctype": doctype, "attached_to_name": name, "file_name": file_name}):
		return
	html = frappe.get_print(doctype, name, print_format=PRINT_FORMAT, no_letterhead=1)
	frappe.get_doc({
		"doctype": "File",
		"file_name": file_name,
		"attached_to_doctype": doctype,
		"attached_to_name": name,
		"is_private": 1,
		"content": get_pdf(html),
	}).insert(ignore_permissions=True)


def deny_invoice_copy_delete(doc, method=None):
	"""交付した適格請求書の写しは削除できない（INV-51）。"""
	if doc.attached_to_doctype == "Sales Invoice" and (doc.file_name or "").startswith(COPY_PREFIX):
		frappe.throw(_("交付した適格請求書の写しは削除できません。"))
