"""月次締め（月次締め・期間ロック要件 4章・6章、CLS-01〜05・08・20〜25）。"""

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import add_days, get_first_day, get_last_day, getdate, now_datetime

from japan_compliance.jp_close import checks
from japan_compliance.jp_close.close import locks_monthly

CLOSERS = ("Accounts Manager", "System Manager")
ADMIN = "System Manager"


class JPMonthClose(Document):
	def before_naming(self):
		self.set_period()

	def set_period(self):
		self.period_start = get_first_day(self.period_start)
		self.period_end = get_last_day(self.period_start)
		self.period_label = getdate(self.period_start).strftime("%Y-%m")
		self.company_abbr = frappe.get_cached_value("Company", self.company, "abbr")
		self.title = f"{self.company} {self.period_label}"

	def validate(self):
		self.set_period()
		before = self.get_doc_before_save()
		if before and before.status == "確定" and not self.flags.jp_transition:
			frappe.throw(_("確定した月次締めは変更できません。直す場合は管理者が解除してください。"))
		for row in self.checks:
			if row.severity == "確認" and row.comment and row.result == "未確認":
				row.result, row.confirmed_by, row.confirmed_on = "確認済", frappe.session.user, now_datetime()

	@frappe.whitelist()
	def run_checks(self):
		if self.status == "確定":
			frappe.throw(_("確定済みの月はチェックできません。"))
		keep = {r.check_code: r for r in self.checks}
		self.set("checks", [])
		for r in checks.run(self.company, self.period_start):
			old = keep.get(r.check_code)
			if old and (r.severity == "確認" or (r.severity == "警告" and r.result == "NG")):
				r.comment = old.comment
				if r.severity == "確認":
					r.result, r.confirmed_by, r.confirmed_on = old.result, old.confirmed_by, old.confirmed_on
			self.append("checks", r)
		self.status, self.checked_by, self.checked_on = "チェック済", frappe.session.user, now_datetime()
		self.save()
		return self.summary()

	def summary(self):
		return {sev: sum(1 for r in self.checks if r.severity == sev and r.result in ("NG", "未確認")) for sev in ("エラー", "警告", "確認")}

	@frappe.whitelist()
	def close(self):
		"""確定（CLS-03〜05）。確定時にチェックを再実行する。"""
		if not set(frappe.get_roles()) & set(CLOSERS):
			frappe.throw(_("月次締めを確定できるのは経理責任者または管理者です。"), frappe.PermissionError)
		self.run_checks()
		problems = []
		for r in self.checks:
			if r.severity == "エラー" and r.result == "NG":
				problems.append(_("{0} {1}：{2} 件").format(r.check_code, r.check_name, r.count))
			elif r.severity == "警告" and r.result == "NG" and not r.comment:
				problems.append(_("{0} {1}：理由のコメントがありません").format(r.check_code, r.check_name))
			elif r.severity == "確認" and r.result != "確認済":
				problems.append(_("{0} {1}：未確認です").format(r.check_code, r.check_name))
		if problems:
			frappe.throw("<br>".join(problems), title=_("確定できません"))
		self.status, self.closed_by, self.closed_on = "確定", frappe.session.user, now_datetime()
		self.save_transition()

	@frappe.whitelist()
	def freeze(self):
		"""凍結日を対象月末に進める（CLS-22・23）。期中はロックしない会社は、確定していない月でも任意の月末に進められる。"""
		require_admin()
		monthly = locks_monthly(self.company)
		if monthly and self.status != "確定":
			frappe.throw(_("確定した月だけ凍結できます。"))
		current = frappe.db.get_value("Company", self.company, "accounts_frozen_till_date")
		if current and getdate(current) >= getdate(self.period_end):
			frappe.throw(_("凍結日はすでに {0} です。").format(current))
		if monthly and current and getdate(add_days(self.period_start, -1)) != getdate(current):
			frappe.throw(_("凍結日（{0}）の翌月から順に締めてください。").format(current))
		set_frozen_date(self.company, self.period_end)
		self.frozen = 1
		self.save_transition()

	@frappe.whitelist()
	def reopen(self, reason):
		"""解除（CLS-24）。理由を必須とし、凍結日を前月末に戻す。"""
		require_admin()
		if not (reason or "").strip():
			frappe.throw(_("解除の理由を入力してください。"))
		monthly = locks_monthly(self.company)
		current = frappe.db.get_value("Company", self.company, "accounts_frozen_till_date")
		covered = current and getdate(current) >= getdate(self.period_start)
		if self.status != "確定" and (monthly or not covered):
			frappe.throw(_("確定した月だけ解除できます。") if monthly else _("確定または凍結した月だけ解除できます。"))
		# 期中はロックしない会社では、後の月が確定していても凍結していなければ順番を問わない
		later_state = {"status": "確定"} if monthly else {"frozen": 1}
		later = frappe.db.exists("JP Month Close", {"company": self.company, "period_start": (">", self.period_start), **later_state})
		if later:
			frappe.throw(_("後の月（{0}）が確定または凍結されています。後の月から順に解除してください。").format(later))
		if covered:
			set_frozen_date(self.company, add_days(self.period_start, -1))
		now = now_datetime()
		self.reopen_log = (self.reopen_log or "") + f"{now:%Y-%m-%d %H:%M} {frappe.session.user}：{reason}\n"
		self.status, self.frozen = "解除", 0
		self.reopen_reason, self.reopened_by, self.reopened_on = reason, frappe.session.user, now
		self.save_transition()

	def save_transition(self):
		"""確定・凍結・解除の操作による保存だけを許す。"""
		self.flags.jp_transition = True
		try:
			self.save()
		finally:
			self.flags.jp_transition = False


def require_admin():
	if ADMIN not in frappe.get_roles():
		frappe.throw(_("凍結日の設定と解除は管理者（System Manager）だけが行えます。"), frappe.PermissionError)


def set_frozen_date(company, date):
	frappe.flags.jp_month_close_update = True
	try:
		doc = frappe.get_doc("Company", company)
		doc.accounts_frozen_till_date = date
		# 凍結日の変更履歴は必ず残す（CLS-25）
		doc.save(ignore_permissions=True, ignore_version=False)
	finally:
		frappe.flags.jp_month_close_update = False


def company_validate(doc, method=None):
	"""凍結日は JP Month Close からだけ変更でき、月末日に限る。凍結期間の例外ロールは置かない（CLS-21〜23）。"""
	if doc.role_allowed_for_frozen_entries:
		frappe.throw(_("凍結期間に記帳できるロールは設定できません。直す場合は管理者が月次締めを解除してください。"))
	before = doc.get_doc_before_save()
	old = before.accounts_frozen_till_date if before else None
	if str(old or "") == str(doc.accounts_frozen_till_date or ""):
		return
	if not frappe.flags.jp_month_close_update and frappe.db.exists("JP Tax Settings", doc.name):
		frappe.throw(_("凍結日は月次締め（JP Month Close）の「凍結」「解除」で変更してください。"))
	if doc.accounts_frozen_till_date and getdate(doc.accounts_frozen_till_date) != get_last_day(doc.accounts_frozen_till_date):
		frappe.throw(_("凍結日は月末日にしてください。"))
