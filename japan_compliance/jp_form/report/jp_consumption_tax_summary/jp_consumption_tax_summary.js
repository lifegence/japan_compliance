frappe.query_reports["JP Consumption Tax Summary"] = {
	filters: [
		{ fieldname: "company", label: __("会社"), fieldtype: "Link", options: "Company", reqd: 1,
			default: frappe.defaults.get_user_default("Company") },
		{ fieldname: "from_date", label: __("開始日"), fieldtype: "Date", reqd: 1,
			default: frappe.datetime.month_start() },
		{ fieldname: "to_date", label: __("終了日"), fieldtype: "Date", reqd: 1,
			default: frappe.datetime.month_end() },
		{ fieldname: "view", label: __("表示"), fieldtype: "Select", reqd: 1, default: "区分別集計",
			options: ["区分別集計", "申告用の計算", "免税事業者等の仕入先別累計"] },
	],
	formatter(value, row, column, data, default_formatter) {
		value = default_formatter(value, row, column, data);
		if (data && data.bold) value = `<b>${value}</b>`;
		return value;
	},
};
