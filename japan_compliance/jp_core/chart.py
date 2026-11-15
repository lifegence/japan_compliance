"""日本式勘定科目テンプレート（勘定科目一覧）を ERPNext の科目テンプレートとして選べるようにする。

ERPNext は科目テンプレートを erpnext 内のフォルダからしか読まないため、get_chart と
get_charts_for_country を包んで本アプリのテンプレートを加える（hooks で読み込み時に適用）。
"""

import json
import os
from functools import lru_cache, wraps

import frappe

DATA = os.path.join(os.path.dirname(__file__), "data")


@lru_cache
def load_chart():
	with open(os.path.join(DATA, "jp_chart_of_accounts.json"), encoding="utf-8") as f:
		return json.load(f)


@lru_cache
def account_tax_categories():
	with open(os.path.join(DATA, "jp_account_tax_category.json"), encoding="utf-8") as f:
		return json.load(f)


def chart_name():
	return load_chart()["name"]


@frappe.whitelist()
def get_charts_for_country(country, with_standard=False):
	"""セットアップウィザード・会社画面の科目テンプレートの選択肢（override_whitelisted_methods）。"""
	patch_erpnext()
	from erpnext.accounts.doctype.account.chart_of_accounts import chart_of_accounts as coa

	return coa.get_charts_for_country(country, with_standard)


def ensure_patched(doc=None, method=None):
	"""会社の作成時（科目の作成より前）に必ず差し替えておく。フックがキャッシュから読まれる場合に備える。"""
	patch_erpnext()


def patch_erpnext():
	from erpnext.accounts.doctype.account.chart_of_accounts import chart_of_accounts as coa

	if getattr(coa, "_japan_compliance_patched", False):
		return
	original_get_chart = coa.get_chart
	original_for_country = coa.get_charts_for_country

	@wraps(original_get_chart)
	def get_chart(chart_template, existing_company=None):
		if chart_template == chart_name() and not existing_company:
			return load_chart()["tree"]
		return original_get_chart(chart_template, existing_company)

	@wraps(original_for_country)
	def get_charts_for_country(country, with_standard=False):
		charts = original_for_country(country, with_standard)
		if country in ("Japan", "日本") and chart_name() not in charts:
			charts = [chart_name(), *charts]
		return charts

	coa.get_chart = get_chart
	coa.get_charts_for_country = get_charts_for_country
	coa._japan_compliance_patched = True
