"""財務レポートのメニュー（Workspace Sidebar）に日本の帳票を加える。

ERPNext の標準メニュー「Financial Reports」には仕訳帳がなく、検索しないと開けない。標準メニューの直後に「日本の帳票」の区分を足す。

ERPNext 更新時の同期で標準メニューが上書きされても戻るよう、マイグレートのたびに検査する。
doc.save() は開発者モードで ERPNext 側の JSON を書き換えるため使わず、行を直接追加する。
"""

import frappe

SIDEBAR = "Financial Reports"
SECTION = "Japan Reports"
REPORTS = ["JP Journal Book", "JP General Ledger", "JP Consumption Tax Summary", "JP Audit Trail"]


def setup():
	if not frappe.db.exists("Workspace Sidebar", SIDEBAR):
		return
	rows = frappe.get_all(
		"Workspace Sidebar Item",
		filters={"parent": SIDEBAR, "parenttype": "Workspace Sidebar"},
		fields=["name", "idx", "type", "label", "link_to"],
		order_by="idx asc",
	)
	present = {r.link_to for r in rows if r.type == "Link"}
	missing = [r for r in REPORTS if r not in present and frappe.db.exists("Report", r)]
	has_section = any(r.type == "Section Break" and r.label == SECTION for r in rows)
	if not missing and has_section:
		return

	new_rows = []
	if not has_section:
		new_rows.append({"type": "Section Break", "label": SECTION, "icon": "file-text", "indent": 1, "child": 0, "collapsible": 1})
	for report in missing:
		new_rows.append({"type": "Link", "label": report, "link_type": "Report", "link_to": report, "indent": 0, "child": 1, "collapsible": 1})

	# 挿入位置：日本の帳票の区分があればその末尾、なければ最初の区分（Financial Reports）の末尾
	if has_section:
		start = next(i for i, r in enumerate(rows) if r.type == "Section Break" and r.label == SECTION)
	else:
		start = 0
	end = next((i for i in range(start + 1, len(rows)) if rows[i].type == "Section Break"), len(rows))
	order = [r.name for r in rows[:end]] + [None] * len(new_rows) + [r.name for r in rows[end:]]

	inserted = []
	for spec in new_rows:
		item = frappe.new_doc("Workspace Sidebar Item")
		item.update({**spec, "parent": SIDEBAR, "parenttype": "Workspace Sidebar", "parentfield": "items", "keep_closed": 0, "show_arrow": 0})
		item.db_insert()
		inserted.append(item.name)
	it = iter(inserted)
	order = [n if n else next(it) for n in order]
	for idx, name in enumerate(order, start=1):
		frappe.db.set_value("Workspace Sidebar Item", name, "idx", idx, update_modified=False)
	frappe.clear_cache()
