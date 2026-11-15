frappe.query_reports["JP General Ledger"] = {
	filters: [
		{ fieldname: "company", label: __("会社"), fieldtype: "Link", options: "Company", reqd: 1, default: frappe.defaults.get_user_default("Company") },
		{ fieldname: "from_date", label: __("開始日"), fieldtype: "Date", reqd: 1, default: frappe.datetime.month_start() },
		{ fieldname: "to_date", label: __("終了日"), fieldtype: "Date", reqd: 1, default: frappe.datetime.month_end() },
		{ fieldname: "account", label: __("勘定科目"), fieldtype: "MultiSelectList", options: "Account",
			get_data: (txt) => frappe.db.get_link_options("Account", txt, { company: frappe.query_report.get_filter_value("company"), is_group: 0 }) },
	],
	formatter(value, row, column, data, default_formatter) {
		// 「諸口」を含む相手科目は元の仕訳へのリンクにする（FORM-31）
		if (column.fieldname === "against" && data && data.voucher_no) {
			const route = frappe.utils.get_form_link(data.voucher_type, data.voucher_no);
			return `<a href="${route}">${frappe.utils.escape_html(value || "")}</a>`;
		}
		value = default_formatter(value, row, column, data);
		if (data && data.bold) value = `<b>${value}</b>`;
		return value;
	},
};
