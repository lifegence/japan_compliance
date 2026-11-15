frappe.ui.form.on("JP Tax Settings", {
	refresh(frm) {
		if (frm.is_new() || frm.doc.nondeductible_method !== "仮払消費税等に含め決算で雑損失") return;
		frm.add_custom_button(__("控除不能分の雑損失への振替を作成"), () => {
			frappe.prompt(
				[
					{ fieldname: "from_date", label: __("開始日"), fieldtype: "Date", reqd: 1, default: frm.doc.tax_period_start },
					{ fieldname: "to_date", label: __("終了日"), fieldtype: "Date", reqd: 1, default: frm.doc.tax_period_end },
					{ fieldname: "posting_date", label: __("計上日"), fieldtype: "Date", reqd: 1, default: frm.doc.tax_period_end },
				],
				(values) => {
					frappe.call({
						method: "japan_compliance.jp_tax.year_end.create_nondeductible_transfer",
						args: { company: frm.doc.company, ...values },
						freeze: true,
						callback: (r) => r.message && frappe.set_route("Form", "Journal Entry", r.message),
					});
				},
				__("控除不能分の振替"),
				__("下書きを作成"),
			);
		});
	},
});
