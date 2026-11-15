"""テスト用のマスタ（テストデータ E-2 の「会社・設定」「取引先」「品目」）。名称・登録番号はすべて架空。

何度呼んでもよい（存在するものは作らない）。統合テストとテストデータの投入（L-30）の両方で使う。
"""

import frappe

from japan_compliance.jp_close.close import NO_LOCK
from japan_compliance.jp_core.chart import chart_name
from japan_compliance.jp_core.company import account_by_number, setup_company

COMPANY_A = "テスト商事A株式会社"
COMPANY_B = "テスト商事B株式会社"

COMPANIES = [
	# name, abbr, fiscal years (name, start, end), tax period, nondeductible method, small amount special
	(COMPANY_A, "TA", [("TA-2025", "2025-04-01", "2026-03-31"), ("TA-2026", "2026-04-01", "2027-03-31"), ("TA-2027", "2027-04-01", "2028-03-31")],
		("2026-04-01", "2027-03-31"), "本体価格に含める", 0),
	(COMPANY_B, "TB", [("TB-2025", "2025-10-01", "2026-09-30"), ("TB-2026", "2026-10-01", "2027-09-30")],
		("2026-10-01", "2027-09-30"), "仮払消費税等に含め決算で雑損失", 1),
]

# 自社の登録番号（架空。チェックデジットは正しい）
REGISTRATION_NUMBERS = {COMPANY_A: "T8000000000001", COMPANY_B: "T7000000000002"}

CUSTOMERS = [
	("テスト得意先A株式会社", "国内課税", "JPY"),
	("Test Customer B Inc.", "輸出（国外）", "USD"),
	("Test Customer C Ltd.", "輸出（国外）", "JPY"),
]

SUPPLIERS = [
	("テスト仕入先C株式会社", "T9000000000009", "登録"),
	("テスト工房D（個人事業）", None, "非登録"),
	("テスト商会E", None, "不明"),
	("テスト運送F株式会社", "T3000000000006", "登録"),
	("テストデザイン事務所G（個人）", "T2000000000007", "登録"),
	("テストストアH", "T1000000000008", "登録"),
	# 帳簿のみ保存の特例で使う汎用の仕入先。登録番号はなく、伝票の特例区分で控除の対象になる（B-6 P-04・G-06）
	("交通機関", None, "非登録"),
	("従業員（出張旅費）", None, "非登録"),
	("従業員（通勤手当）", None, "非登録"),
]

# code, name, reduced, sales rate, purchase rate, income account no, expense account no
ITEMS = [
	("ITEM-X", "商品X", 0, 5000, 2500, "401", "5201"),
	("ITEM-Y", "食品Y", 1, 2000, 1000, "401", "5201"),
	("ITEM-Z", "役務Z（外注）", 0, 0, 0, None, "711"),
	("SVC-TEL", "通信サービス", 0, 0, 0, None, "717"),
	("ASSET-EQ", "備品（工具器具備品）", 0, 0, 0, None, "12106"),
	("FREIGHT", "引取運賃", 0, 0, 0, None, "5201"),
	("SVC-FEE", "報酬（デザイン等）", 0, 0, 0, None, "733"),
	("GIFT-Y", "手土産（食品）", 1, 0, 0, None, "714"),
	("TRANS", "交通費", 0, 0, 0, None, "716"),
	("COMMUTE", "通勤定期", 0, 0, 0, None, "716"),
	("SUPPLY", "消耗品", 0, 0, 0, None, "720"),
]


def ensure_all():
	ensure_erpnext_fixtures()
	ensure_jpy_defaults()
	for company, abbr, fiscal_years, period, nondeductible, small in COMPANIES:
		ensure_company(company, abbr)
		for fy, start, end in fiscal_years:
			ensure_fiscal_year(fy, start, end, company)
		settings = frappe.get_doc("JP Tax Settings", company)
		settings.update({
			"registration_number": REGISTRATION_NUMBERS[company],
			"tax_period_start": period[0], "tax_period_end": period[1],
			"nondeductible_method": nondeductible, "small_amount_special": small,
			# 会社B は年一決算の想定で期中はロックしない（E-2、C-1 CLS-08）
			"monthly_lock_mode": NO_LOCK if company == COMPANY_B else "月次でロックする",
		})
		settings.save(ignore_permissions=True)
	for name, tax_class, currency in CUSTOMERS:
		if not frappe.db.exists("Customer", name):
			frappe.get_doc({
				"doctype": "Customer", "customer_name": name, "customer_group": leaf("Customer Group"),
				"territory": leaf("Territory"), "jp_tax_class": tax_class, "default_currency": currency,
			}).insert(ignore_permissions=True)
	for name, number, status in SUPPLIERS:
		if not frappe.db.exists("Supplier", name):
			frappe.get_doc({
				"doctype": "Supplier", "supplier_name": name, "supplier_group": leaf("Supplier Group"),
				"jp_registration_number": number, "jp_registration_status": status,
			}).insert(ignore_permissions=True)
	for code, name, reduced, sell, buy, income, expense in ITEMS:
		ensure_item(code, name, reduced, sell, buy, income, expense)
	ensure_company_contact(COMPANY_A)
	frappe.db.commit()


def ensure_company_contact(company):
	"""帳票確認用の住所と振込先（架空）。"""
	if not frappe.db.exists("Dynamic Link", {"link_doctype": "Company", "link_name": company, "parenttype": "Address"}):
		frappe.get_doc({
			"doctype": "Address", "address_title": company, "address_type": "Billing", "address_line1": "架空町1-2-3 テストビル5階",
			"city": "千代田区", "state": "東京都", "pincode": "100-0000", "country": "Japan", "phone": "03-0000-0000",
			"is_your_company_address": 1, "links": [{"link_doctype": "Company", "link_name": company}],
		}).insert(ignore_permissions=True)
	if not frappe.db.exists("Bank", "テスト銀行"):
		frappe.get_doc({"doctype": "Bank", "bank_name": "テスト銀行"}).insert(ignore_permissions=True)
	if not frappe.db.exists("Bank Account", {"company": company, "is_company_account": 1}):
		frappe.get_doc({
			"doctype": "Bank Account", "account_name": "テストショウジエー（カ", "bank": "テスト銀行", "company": company,
			"is_company_account": 1, "is_default": 1, "branch_code": "本店営業部 001", "bank_account_no": "普通 1234567",
			"account": account_by_number(company, "11104"),
		}).insert(ignore_permissions=True)


def ensure_erpnext_fixtures():
	"""セットアップウィザードを通していないサイトに、ERPNext の基本データ（倉庫種別・UOM・品目グループ等）を入れる。"""
	if frappe.db.exists("Warehouse Type", "Transit"):
		return
	from erpnext.setup.setup_wizard.operations.install_fixtures import install

	install(country="Japan")


def ensure_jpy_defaults():
	"""ウィザード未実行のサイトは既定通貨が INR のため、円に揃える。"""
	frappe.db.set_single_value("Global Defaults", "default_currency", "JPY")
	frappe.db.set_single_value("Global Defaults", "country", "Japan")
	frappe.db.set_default("currency", "JPY")
	# 画面の日付は yyyy-mm-dd（Frappe に yyyy/mm/dd の選択肢がないため。帳票は jp_date で yyyy/mm/dd）
	frappe.db.set_single_value("System Settings", "date_format", "yyyy-mm-dd")
	# 画面は既定値（DefaultValue）を見るので、こちらも変える（set_single_value では System Settings の on_update が走らない）
	frappe.db.set_default("date_format", "yyyy-mm-dd")
	frappe.db.set_default("country", "Japan")
	frappe.clear_cache()
	for price_list in frappe.get_all("Price List", pluck="name"):
		frappe.db.set_value("Price List", price_list, "currency", "JPY")
	frappe.db.set_value("Currency", "JPY", "enabled", 1)
	for name, selling in (("標準販売", 1), ("標準購買", 0)):
		if not frappe.db.exists("Price List", name):
			frappe.get_doc({"doctype": "Price List", "price_list_name": name, "currency": "JPY",
				"selling": selling, "buying": 1 - selling, "enabled": 1}).insert(ignore_permissions=True)
	frappe.db.set_single_value("Selling Settings", "selling_price_list", "標準販売")
	frappe.db.set_single_value("Buying Settings", "buying_price_list", "標準購買")


def ensure_company(company, abbr):
	if not frappe.db.exists("Company", company):
		frappe.get_doc({
			"doctype": "Company", "company_name": company, "abbr": abbr, "default_currency": "JPY",
			"country": "Japan", "chart_of_accounts": chart_name(),
		}).insert(ignore_permissions=True)
	setup_company(company)


def ensure_fiscal_year(name, start, end, company):
	"""同じ期間の会計年度（セットアップウィザードが作ったものを含む）があれば、それに会社を加える。"""
	if frappe.db.exists("Fiscal Year", name):
		return
	existing = frappe.db.get_value("Fiscal Year", {"year_start_date": start, "year_end_date": end}, "name")
	if existing:
		fy = frappe.get_doc("Fiscal Year", existing)
		# 会社の指定がない会計年度（ウィザードが作る）は全社に効き、9月決算の会社B と食い違うため、会社に結び付ける
		if not any(c.company == company for c in fy.companies):
			fy.append("companies", {"company": company})
			fy.save(ignore_permissions=True)
		return
	frappe.get_doc({
		"doctype": "Fiscal Year", "year": name, "year_start_date": start, "year_end_date": end,
		"companies": [{"company": company}],
	}).insert(ignore_permissions=True)


def ensure_item(code, name, reduced, sell, buy, income, expense):
	if frappe.db.exists("Item", code):
		return
	defaults = []
	for company, abbr, *_ in COMPANIES:
		# 既定倉庫は会社ごとに指定する（ウィザードが全社既定に会社Aの倉庫を入れるため）
		row = {"company": company, "default_warehouse": frappe.db.get_value("Warehouse", {"company": company, "is_group": 0}, "name")}
		if income:
			row["income_account"] = account_by_number(company, income)
		if expense:
			row["expense_account"] = account_by_number(company, expense)
		defaults.append(row)
	frappe.get_doc({
		"doctype": "Item", "item_code": code, "item_name": name, "item_group": leaf("Item Group"), "stock_uom": "Nos",
		"is_stock_item": 0, "jp_reduced_rate": reduced, "item_defaults": defaults,
		"is_sales_item": 1 if income else 0, "is_purchase_item": 1,
	}).insert(ignore_permissions=True)


def leaf(doctype):
	"""グループでない最初のマスタ（セットアップウィザードが作る既定値）。"""
	return frappe.db.get_value(doctype, {"is_group": 0}, "name", order_by="lft")


def customer(i):
	return CUSTOMERS[i][0]


def supplier(i):
	return SUPPLIERS[i][0]
