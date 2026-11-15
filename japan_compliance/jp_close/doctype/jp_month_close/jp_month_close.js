frappe.ui.form.on("JP Month Close", {
	async refresh(frm) {
		if (frm.is_new()) return;
		const call = (method, args) => frm.call(method, args).then(() => frm.reload_doc());
		if (frm.doc.status !== "確定") {
			frm.add_custom_button(__("締め前チェック"), () => call("run_checks"));
			frm.add_custom_button(__("確定"), () => frappe.confirm(__("チェックを再実行し、問題がなければ確定します。"), () => call("close")));
		}
		if (!frappe.user.has_role("System Manager")) return;
		// 期中はロックしない会社は、確定していない月でも凍結でき、凍結日に含まれる月を解除できる（CLS-08・22）
		const [settings, company] = await Promise.all([
			frappe.db.get_value("JP Tax Settings", frm.doc.company, "monthly_lock_mode"),
			frappe.db.get_value("Company", frm.doc.company, "accounts_frozen_till_date"),
		]);
		const no_lock = settings.message?.monthly_lock_mode === "期中はロックしない";
		const frozen_till = company.message?.accounts_frozen_till_date;
		const covered = frozen_till && frozen_till >= frm.doc.period_start;
		const confirmed = frm.doc.status === "確定";
		if (!frm.doc.frozen && (confirmed || no_lock)) {
			frm.add_custom_button(__("凍結日を月末に進める"), () => call("freeze"));
		}
		if (confirmed || (no_lock && covered)) {
			frm.add_custom_button(__("解除"), () => frappe.prompt(
				{ fieldname: "reason", fieldtype: "Small Text", label: __("解除の理由"), reqd: 1 },
				(v) => call("reopen", { reason: v.reason }), __("月次締めの解除")));
		}
	},
});
