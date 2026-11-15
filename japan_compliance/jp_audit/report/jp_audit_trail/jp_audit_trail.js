frappe.query_reports["JP Audit Trail"] = {
	filters: [
		{ fieldname: "company", label: __("会社"), fieldtype: "Link", options: "Company", default: frappe.defaults.get_user_default("Company") },
		{ fieldname: "from_date", label: __("開始日"), fieldtype: "Date", reqd: 1, default: frappe.datetime.month_start() },
		{ fieldname: "to_date", label: __("終了日"), fieldtype: "Date", reqd: 1, default: frappe.datetime.get_today() },
		{ fieldname: "ref_doctype", label: __("伝票種別"), fieldtype: "Select",
			options: ["", "Sales Invoice", "Purchase Invoice", "Journal Entry", "Payment Entry", "Company", "Account", "Supplier", "Custom DocPerm", "User"] },
		{ fieldname: "docname", label: __("伝票番号"), fieldtype: "Data" },
		{ fieldname: "user", label: __("操作者"), fieldtype: "Link", options: "User" },
	],
};
