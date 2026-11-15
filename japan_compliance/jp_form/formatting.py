"""日本式帳票の表記（帳票記載要件 FORM-01〜03）。Jinja から使う。"""

from frappe.utils import flt, getdate


def jp_yen(value, blank_zero=False):
	"""円・桁区切り・小数なし。マイナスは「△」（FORM-01・02）。"""
	v = round(flt(value))
	if blank_zero and not v:
		return ""
	return f"△{abs(v):,}" if v < 0 else f"{v:,}"


def jp_date(value):
	"""西暦 yyyy/mm/dd（FORM-03）。"""
	return getdate(value).strftime("%Y/%m/%d") if value else ""


def jp_rate_label(row):
	"""税率別内訳の見出し。軽減税率は「8%（軽減）」（INV-12）。"""
	if not flt(row.tax_rate):
		return row.display_label or row.tax_category
	label = f"{flt(row.tax_rate):g}%"
	if (row.rate_code or "") == "RED8":
		label += "（軽減）"
	return label


def is_reduced(item):
	return (item.get("jp_tax_category") or "").endswith("-8R")
