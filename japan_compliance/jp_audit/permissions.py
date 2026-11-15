"""職務分掌と権限区分（監査証跡・権限要件 4.3・4.4、5章の権限マトリクス）。"""

import frappe
from frappe.permissions import add_permission, setup_custom_perms, update_permission_property

ACCOUNTING_DOCTYPES = ("Sales Invoice", "Purchase Invoice", "Journal Entry", "Payment Entry")
BOOKKEEPING_FIRM_ROLE = "会計事務所（記帳代行）"


def apply_permission_model(model):
	"""分離型：経理担当（Accounts User）は起票・保存のみ。一体型：提出・取消・修正も許す（AUD-31）。"""
	allowed = 1 if model == "一体型" else 0
	for doctype in ACCOUNTING_DOCTYPES:
		setup_custom_perms(doctype)
		if not frappe.db.exists("Custom DocPerm", {"parent": doctype, "role": "Accounts User", "permlevel": 0}):
			add_permission(doctype, "Accounts User", 0)
		for ptype in ("submit", "cancel", "amend"):
			update_permission_property(doctype, "Accounts User", 0, ptype, allowed, validate=False)
	frappe.clear_cache()


def ensure_roles():
	"""記帳代行を行う会計事務所の担当者を識別するロール。権限は Accounts User と併せて付与する（AUD-32）。"""
	if not frappe.db.exists("Role", BOOKKEEPING_FIRM_ROLE):
		frappe.get_doc({"doctype": "Role", "role_name": BOOKKEEPING_FIRM_ROLE, "desk_access": 1}).insert(ignore_permissions=True)


# 承認フローのひな形（AUD-20〜22）。既定は無効。会社が使う場合に有効化する
WORKFLOW_STATES = (("下書き", "Primary"), ("承認待ち", "Warning"), ("差戻し", "Danger"), ("承認済", "Success"))
WORKFLOW_ACTIONS = ("申請", "承認", "差戻し", "再申請")


def ensure_workflows():
	for state, style in WORKFLOW_STATES:
		if not frappe.db.exists("Workflow State", state):
			frappe.get_doc({"doctype": "Workflow State", "workflow_state_name": state, "style": style}).insert(ignore_permissions=True)
	for action in WORKFLOW_ACTIONS:
		if not frappe.db.exists("Workflow Action Master", action):
			frappe.get_doc({"doctype": "Workflow Action Master", "workflow_action_name": action}).insert(ignore_permissions=True)
	for doctype in ("Journal Entry", "Purchase Invoice"):
		name = f"JP 承認フロー（{doctype}）"
		if frappe.db.exists("Workflow", name):
			continue
		frappe.get_doc({
			"doctype": "Workflow", "workflow_name": name, "document_type": doctype, "is_active": 0,
			"workflow_state_field": "workflow_state", "send_email_alert": 0,
			"states": [
				{"state": "下書き", "doc_status": "0", "allow_edit": "Accounts User"},
				# 承認待ちの間は承認者だけが編集できる。起票者が直すときは差戻しで下書きに戻す（AUD-22）
				{"state": "承認待ち", "doc_status": "0", "allow_edit": "Accounts Manager"},
				{"state": "差戻し", "doc_status": "0", "allow_edit": "Accounts User"},
				{"state": "承認済", "doc_status": "1", "allow_edit": "Accounts Manager"},
			],
			"transitions": [
				{"state": "下書き", "action": "申請", "next_state": "承認待ち", "allowed": "Accounts User", "allow_self_approval": 1},
				# 起票者は自分の伝票を承認できない（AUD-21）
				{"state": "承認待ち", "action": "承認", "next_state": "承認済", "allowed": "Accounts Manager", "allow_self_approval": 0},
				{"state": "承認待ち", "action": "差戻し", "next_state": "差戻し", "allowed": "Accounts Manager", "allow_self_approval": 1},
				{"state": "差戻し", "action": "再申請", "next_state": "承認待ち", "allowed": "Accounts User", "allow_self_approval": 1},
			],
		}).insert(ignore_permissions=True)


def setup():
	ensure_roles()
	ensure_workflows()
	model = frappe.db.get_single_value("JP Compliance Settings", "permission_model") or "分離型"
	if not frappe.db.get_single_value("JP Compliance Settings", "permission_model"):
		frappe.db.set_single_value("JP Compliance Settings", "permission_model", model)
	apply_permission_model(model)


def enforce_workflow_edit(doc, method=None):
	"""承認フローの状態ごとの編集可能ロールをサーバ側でも守る（AUD-22）。状態の遷移（申請・承認等）は対象外。"""
	if doc.is_new() or frappe.session.user == "Administrator":
		return
	from frappe.model.workflow import get_workflow_name

	workflow_name = get_workflow_name(doc.doctype)
	if not workflow_name:
		return
	workflow = frappe.get_cached_doc("Workflow", workflow_name)
	field = workflow.workflow_state_field
	before = doc.get_doc_before_save()
	if not before or before.get(field) != doc.get(field):
		return
	state = next((s for s in workflow.states if s.state == doc.get(field)), None)
	if state and state.allow_edit and state.allow_edit not in frappe.get_roles():
		frappe.throw(frappe._("「{0}」の伝票は編集できません。直す場合は差戻しで下書きに戻してください。").format(state.state),
			frappe.PermissionError)
