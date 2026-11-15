"""会社の初期設定：科目の既定税区分と JP Tax Settings（会社作成時・インストール時）。"""

import frappe

from japan_compliance.jp_core.chart import account_tax_categories, chart_name

SALES_TAX_ACCOUNT = "21216"  # 仮受消費税等
PURCHASE_TAX_ACCOUNT = "11509"  # 仮払消費税等
MISC_LOSS_ACCOUNT = "8209"  # 雑損失


def account_by_number(company, number):
	return frappe.db.get_value("Account", {"company": company, "account_number": number, "is_group": 0}, "name")


def after_company_insert(doc, method=None):
	if doc.chart_of_accounts == chart_name():
		setup_company(doc.name)


def company_on_update(doc, method=None):
	"""科目は会社の on_update で作られるため、作成直後に設定できなかった会社をここで設定する（セットアップウィザード経由など）。"""
	if doc.chart_of_accounts == chart_name() and not frappe.db.exists("JP Tax Settings", doc.name) \
			and account_by_number(doc.name, SALES_TAX_ACCOUNT):
		setup_company(doc.name)


# 会社の既定科目（勘定科目一覧「システム科目」）
COMPANY_DEFAULTS = {
	"default_receivable_account": "11203",  # 売掛金
	"default_payable_account": "21102",  # 買掛金
	"default_cash_account": "11101",  # 現金
	"default_bank_account": "11104",  # 普通預金
	"default_income_account": "401",  # 売上高
	"default_expense_account": "5201",  # 仕入高（非在庫品目の仕入計上先）
	"round_off_account": "8211",  # 端数差額
	"round_off_for_opening": "11514",  # 期首残高仮勘定
	# ERPNext v16 の為替差損益は1科目。差損益とも為替差損（8210）に計上し、差益は貸方残となる
	"exchange_gain_loss_account": "8210",
	"write_off_account": "739",  # 貸倒損失(販)
	"default_advance_received_account": "21209",  # 前受金
	"default_advance_paid_account": "11501",  # 前渡金
	"stock_received_but_not_billed": "21219",  # 仕入未請求仮受
	"accumulated_depreciation_account": "12108",
	"depreciation_expense_account": "736",
}


def setup_company(company):
	"""日本式テンプレートで作った会社に、既定科目・科目の既定税区分・JP Tax Settings を設定する。何度実行してもよい。"""
	# 棚卸資産は三分法（仕入高に計上し期末に振り替える）。継続記録法の自動仕訳は使わない（B-6 #1）
	# 前受金・前渡金を別の取引先勘定（21209／11501）で計上する（B-6 S-08）
	values = {"enable_perpetual_inventory": 0, "book_advance_payments_in_separate_party_account": 1}
	for field, number in COMPANY_DEFAULTS.items():
		name = account_by_number(company, number)
		if name:
			values[field] = name
	frappe.db.set_value("Company", company, values)
	# 前受金・前渡金を取引先別に持つため、取引先勘定の型にする（ERPNext の前受・前払の別勘定計上の前提）。
	# 未払金も仕入先別に残高を管理する（勘定科目一覧、B-6 P-03）
	for number, account_type in (("21209", "Receivable"), ("11501", "Payable"), ("21203", "Payable")):
		name = account_by_number(company, number)
		if name:
			frappe.db.set_value("Account", name, "account_type", account_type)
	ensure_added_accounts(company)
	for number, category in account_tax_categories().items():
		name = account_by_number(company, number)
		if name and frappe.db.exists("JP Tax Category", category):
			frappe.db.set_value("Account", name, "jp_tax_category", category, update_modified=False)

	if not frappe.db.exists("JP Tax Settings", company):
		sales, purchase = account_by_number(company, SALES_TAX_ACCOUNT), account_by_number(company, PURCHASE_TAX_ACCOUNT)
		if sales and purchase:
			frappe.get_doc({
				"doctype": "JP Tax Settings",
				"company": company,
				"sales_tax_account": sales,
				"purchase_tax_account": purchase,
				"nondeductible_loss_account": account_by_number(company, MISC_LOSS_ACCOUNT),
			}).insert(ignore_permissions=True)


# 勘定科目テンプレートの改訂で新設した科目（科目番号, 科目名, 隣の科目番号, account_type）。改訂前のテンプレートで作った会社に足す
ADDED_ACCOUNTS = [("8108", "償却債権取立益", "8107", "Indirect Income")]


def ensure_added_accounts(company):
	for number, name, sibling, account_type in ADDED_ACCOUNTS:
		sibling_name = account_by_number(company, sibling)
		if account_by_number(company, number) or not sibling_name:
			continue
		frappe.get_doc({
			"doctype": "Account", "company": company, "account_name": name, "account_number": number,
			"parent_account": frappe.db.get_value("Account", sibling_name, "parent_account"), "account_type": account_type,
		}).insert(ignore_permissions=True)
