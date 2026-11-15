frappe.query_reports["JP Journal Book"] = {
	filters: [
		{ fieldname: "company", label: __("会社"), fieldtype: "Link", options: "Company", reqd: 1, default: frappe.defaults.get_user_default("Company") },
		{ fieldname: "from_date", label: __("開始日"), fieldtype: "Date", reqd: 1, default: frappe.datetime.month_start() },
		{ fieldname: "to_date", label: __("終了日"), fieldtype: "Date", reqd: 1, default: frappe.datetime.month_end() },
	],
	formatter(value, row, column, data, default_formatter) {
		value = default_formatter(value, row, column, data);
		if (data && data.bold) value = `<b>${value}</b>`;
		if (data && data.separator && column.colIndex) value = `<div style="border-top:1px solid #999">${value}</div>`;
		return value;
	},
};
