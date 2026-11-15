"""売上・仕入請求書の消費税額の計算（端数処理方針 4.3、税区分定義 適用ルール）。

ERPNext 標準の税計算は明細の税額を税行に累計して通貨精度で丸める（銀行家丸め）。日本の要件は
「伝票 × 税率区分ごとに1回、会社設定の方法で丸める」なので、税行を自前で計算して「Actual」行として与える。

  1. 税行を外して ERPNext に明細の金額を計算させる
  2. 税区分ごとに税額を計算し、Actual 行として設定して ERPNext に合計を計算させる
  3. 内税と、免税事業者等からの仕入（控除不能分を本体に含める場合）は、明細の税抜金額を補正する

確定（Submit）時の計算結果は jp_tax_breakdown と jp_calc_snapshot に残り、確定後は再計算しない。
"""

import json
from collections import OrderedDict

import frappe
from frappe import _
from frappe.utils import cint, flt, getdate, money_in_words, now_datetime

from japan_compliance.jp_tax import masters
from japan_compliance.jp_tax.rounding import allocate, apply_ratio, round_yen, tax_exclusive, tax_inclusive

SALES = "売上"
PURCHASE = "仕入"
MISC_LOSS = "仮払消費税等に含め決算で雑損失"


def direction_of(doc):
	return SALES if doc.doctype == "Sales Invoice" else PURCHASE


def calculate(doc, erpnext_calculate):
	"""calculate_taxes_and_totals の置き換え。日本の設定がない会社では ERPNext 標準のまま。"""
	settings = masters.get_settings(doc.company)
	if not settings or doc.get("is_opening") == "Yes":
		return erpnext_calculate()

	direction = direction_of(doc)
	validate_document(doc)
	set_default_categories(doc, direction)

	doc.taxes_and_charges = None
	# 消費税以外の行（源泉所得税の控除など。B-6 G-07）は残し、消費税の行の後ろに付け直す
	others = [t.as_dict(no_default_fields=True) for t in doc.get("taxes") or [] if not is_consumption_tax_row(t, settings, direction)]
	doc.set("taxes", [])
	# 1回目は税抜の合計しか出ないため、前受金の充当額の検査（税込合計以下）にかからないよう一時的に外す
	advances = [(a, a.allocated_amount) for a in doc.get("advances") or []]
	for a, _amount in advances:
		a.allocated_amount = 0
	erpnext_calculate()
	for a, amount in advances:
		a.allocated_amount = amount

	groups = compute_groups(doc, settings, direction)
	for g in groups:
		if g["posted_tax"]:
			doc.append("taxes", tax_row(doc, settings, direction, g))
	for row in others:
		row.pop("idx", None)
		doc.append("taxes", row)
	erpnext_calculate()

	deltas = {}
	for g in groups:
		for idx, d in zip(g["item_idx"], allocate(g["net_delta"], g["item_weights"])):
			if d:
				deltas[idx] = deltas.get(idx, 0) + d
	apply_net_deltas(doc, deltas)
	normalize_base_amounts(doc)
	write_snapshot(doc, settings, groups)
	if direction == PURCHASE:
		small_amount_notice(doc, settings, groups)


def is_consumption_tax_row(tax, settings, direction):
	account = settings.sales_tax_account if direction == SALES else settings.purchase_tax_account
	return tax.account_head == account


def validate_document(doc):
	if flt(doc.get("discount_amount")) or flt(doc.get("additional_discount_percentage")):
		frappe.throw(_("日本の消費税計算では、伝票全体の値引きは使えません。明細の値引き、または返品・値引の伝票で処理してください。"))


def applied_date(doc):
	"""税率・控除割合の適用日。取引日（引渡日・役務の完了日）があればそれ、なければ計上日（適用ルール #1）。"""
	return getdate(doc.get("jp_transaction_date") or doc.posting_date)


def set_default_categories(doc, direction):
	"""明細の税区分が空なら、品目・取引先から既定を決める（適用ルール #3・#4、INV 登録区分）。"""
	if direction == SALES:
		party_class = frappe.db.get_value("Customer", doc.customer, "jp_tax_class") if doc.customer else None
		for item in doc.items:
			if item.get("jp_tax_category"):
				continue
			explicit, reduced = item_defaults(item.item_code, "jp_sales_tax_category")
			if explicit:
				item.jp_tax_category = explicit
			elif party_class == "輸出（国外）":
				item.jp_tax_category = "S-EXP"
			else:
				item.jp_tax_category = "S-TAX-8R" if reduced else "S-TAX-10"
		return

	status = registration_status(doc.supplier, applied_date(doc)) if doc.supplier else None
	# 帳簿のみ保存の特例（交通機関・出張旅費・通勤手当など）は請求書がなくても控除できるため、登録番号の有無を問わない（INV-46）
	nonreg = status in ("非登録", "不明") and not doc.get("jp_special_case")
	for item in doc.items:
		if item.get("jp_tax_category"):
			continue
		explicit, reduced = item_defaults(item.item_code, "jp_purchase_tax_category")
		code = explicit or ("P-TAX-8R" if reduced else "P-TAX-10")
		if nonreg and code.startswith("P-TAX-"):
			code = code.replace("P-TAX-", "P-NREG-")
		item.jp_tax_category = code
	if status == "不明" and nonreg:
		frappe.msgprint(
			_("仕入先 {0} の登録区分が「不明」のため、免税事業者等からの仕入（経過措置）として計算しました。登録番号を確認してください。").format(doc.supplier),
			title=_("登録区分の確認"), indicator="orange",
		)


def registration_status(supplier, on_date):
	"""仕入日時点の登録区分。登録の有効期間の外なら非登録として扱う（INV-07）。"""
	status, start, end = frappe.db.get_value("Supplier", supplier,
		["jp_registration_status", "jp_registration_from", "jp_registration_to"]) or (None, None, None)
	if status == "登録" and ((start and getdate(on_date) < getdate(start)) or (end and getdate(on_date) > getdate(end))):
		return "非登録"
	return status


def item_defaults(item_code, field):
	if not item_code:
		return None, False
	values = frappe.get_cached_value("Item", item_code, [field, "jp_reduced_rate"], as_dict=True) or {}
	return values.get(field), cint(values.get("jp_reduced_rate"))


def compute_groups(doc, settings, direction):
	"""税区分ごとに税額を計算する。返り値は税区分ごとの dict のリスト。"""
	inclusive = cint(doc.get("jp_tax_inclusive"))
	on_date = applied_date(doc)
	rounding = settings.sales_rounding if direction == SALES else settings.purchase_rounding
	buckets = OrderedDict()
	for item in doc.items:
		buckets.setdefault(item.jp_tax_category, []).append(item)

	supplier_status = None
	if direction == PURCHASE and doc.supplier and not doc.get("jp_special_case"):
		supplier_status = registration_status(doc.supplier, on_date)

	groups = []
	for code, items in buckets.items():
		cat = masters.get_category(code)
		check_category(doc, cat, items, direction, supplier_status)
		base = sum(flt(i.net_amount) for i in items)
		g = {
			"tax_category": code, "category_name": cat.category_name, "rate_code": cat.rate_code or "",
			"tax_rate": 0, "applied_date": on_date, "inclusion": "内税" if inclusive else "外税",
			"transaction_kind": "返還等" if cint(doc.get("is_return")) else "通常", "rounding_method": rounding,
			"computed_tax": 0, "stated_tax": 0, "posted_tax": 0, "deduction_rule": "", "deduction_ratio": 0,
			"deductible_tax": 0, "nondeductible_tax": 0, "net_delta": 0,
			"item_idx": [i.idx for i in items], "item_weights": [flt(i.net_amount) for i in items],
			"display_label": cat.category_name,
		}
		taxable = cat.tax_type == "課税" and cat.rate_code and cat.rate_code != "ZERO"
		if not taxable:
			g.update(net_amount=base, gross_amount=base)
			groups.append(g)
			continue

		rate = flt(masters.get_rate(cat.rate_code, on_date).rate)
		g["tax_rate"] = rate
		computed = tax_inclusive(base, rate, rounding) if inclusive else tax_exclusive(base, rate, rounding)
		gross = base if inclusive else base + computed
		g["computed_tax"] = computed
		posted = computed

		if cat.is_nonregistered:
			rule = masters.get_deduction_rule(on_date)
			deductible = apply_ratio(computed, rule.value, settings.purchase_rounding)
			g.update(deduction_rule=rule.rule_code, deduction_ratio=flt(rule.value), deductible_tax=deductible,
				nondeductible_tax=computed - deductible, display_label=f"{cint(rule.value)}%控除対象")
			posted = computed if settings.nondeductible_method == MISC_LOSS else deductible
		elif direction == PURCHASE and settings.purchase_calc_method == "請求書等積上げ":
			stated = stated_tax_for(doc, cat.rate_code)
			if stated:
				stated = -abs(stated) if base < 0 else abs(stated)
				g["stated_tax"] = stated
				posted = stated
				if abs(stated - computed) > 1:
					frappe.msgprint(
						_("請求書記載の消費税額（{0}）が計算値（{1}）と {2} 円異なります。記載額で計上しますが、請求書の内容を確認してください。").format(
							stated, computed, abs(stated - computed)),
						title=_("消費税額の確認"), indicator="orange",
					)

		# 税込対価：内税は入力額、外税は税抜＋税額（免税事業者等は相当額、請求書記載額があればその額）
		if not cat.is_nonregistered:
			gross = base if inclusive else base + posted
		tax_in_gross = computed if cat.is_nonregistered else posted
		g["posted_tax"] = posted
		g["gross_amount"] = gross
		g["net_amount"] = gross - tax_in_gross
		# 明細に計上する金額は 税込対価 − 計上税額。内税は計上税額を差し引き、外税で控除不能分を本体に含める場合は加える
		g["net_delta"] = (gross - posted) - base
		groups.append(g)
	return groups


def stated_tax_for(doc, rate_code):
	field = {"STD10": "jp_stated_tax_std", "RED8": "jp_stated_tax_red"}.get(rate_code)
	return flt(doc.get(field)) if field else 0


def check_category(doc, cat, items, direction, supplier_status):
	if not cat.enabled:
		frappe.throw(_("税区分 {0} は無効です。旧税率など管理者が有効化した区分のみ使えます。").format(cat.name))
	if cat.direction != direction:
		frappe.throw(_("税区分 {0} は{1}用です。{2}の伝票では使えません。").format(cat.name, cat.direction, direction))
	if cat.requires_reason:
		for item in items:
			if not item.get("jp_tax_reason"):
				frappe.throw(_("行 {0}：税区分 {1} を使う場合は理由を入力してください。").format(item.idx, cat.name))
	if direction == PURCHASE and supplier_status in ("非登録", "不明") and cat.name.startswith("P-TAX-"):
		frappe.msgprint(
			_("登録番号のない仕入先に、通常の課税仕入の税区分（{0}）が使われています。少額特例などの理由がない場合は P-NREG-* を使ってください。").format(cat.name),
			title=_("税区分の確認"), indicator="orange",
		)


def tax_row(doc, settings, direction, g):
	account = settings.sales_tax_account if direction == SALES else settings.purchase_tax_account
	row = {
		"charge_type": "Actual",
		"account_head": account,
		"description": f"消費税 {g['display_label']}" if g["deduction_rule"] else f"消費税 {g['category_name']}",
		"tax_amount": g["posted_tax"],
		"cost_center": doc.get("cost_center") or frappe.get_cached_value("Company", doc.company, "cost_center"),
	}
	if direction == PURCHASE:
		row.update(category="Total", add_deduct_tax="Add")
	return row


def apply_net_deltas(doc, deltas):
	"""明細の税抜金額を補正し、伝票の合計を合わせ直す（総額は税込合計のまま）。"""
	if not deltas:
		return
	conv = flt(doc.conversion_rate) or 1
	total = 0
	for item in doc.items:
		d = deltas.get(item.idx)
		if not d:
			continue
		item.net_amount = flt(item.net_amount) + d
		item.base_net_amount = flt(item.net_amount * conv, item.precision("base_net_amount"))
		if flt(item.qty):
			item.net_rate = item.net_amount / flt(item.qty)
			item.base_net_rate = item.base_net_amount / flt(item.qty)
		total += d

	doc.net_total = flt(doc.net_total) + total
	doc.base_net_total = flt(doc.net_total * conv, doc.precision("base_net_total"))
	prev = None
	for tax in doc.get("taxes"):
		amount = flt(tax.tax_amount_after_discount_amount)
		if tax.get("add_deduct_tax") == "Deduct":
			amount = -amount
		tax.total = flt((prev.total if prev else doc.net_total) + amount, tax.precision("total"))
		tax.base_total = flt(tax.total * conv, tax.precision("base_total"))
		prev = tax
	doc.grand_total = flt(prev.total if prev else doc.net_total, doc.precision("grand_total"))
	doc.base_grand_total = flt(doc.grand_total * conv, doc.precision("base_grand_total"))
	if doc.meta.get_field("rounded_total") and not doc.is_rounded_total_disabled():
		from erpnext.controllers.taxes_and_totals import round_based_on_smallest_currency_fraction

		doc.rounded_total = round_based_on_smallest_currency_fraction(doc.grand_total, doc.currency, doc.precision("rounded_total"))
		doc.rounding_adjustment = flt(doc.rounded_total - doc.grand_total, doc.precision("rounding_adjustment"))
		doc.base_rounded_total = flt(doc.rounded_total * conv, doc.precision("base_rounded_total"))
		doc.base_rounding_adjustment = flt(doc.rounding_adjustment * conv, doc.precision("base_rounding_adjustment"))
	if doc.meta.get_field("outstanding_amount") and doc.docstatus == 0:
		doc.outstanding_amount = flt(doc.outstanding_amount) + total
	for field, amount_field in (("in_words", "rounded_total"), ("base_in_words", "base_rounded_total")):
		if doc.meta.get_field(field):
			currency = doc.currency if field == "in_words" else frappe.get_cached_value("Company", doc.company, "default_currency")
			doc.set(field, money_in_words(doc.get(amount_field) or doc.get(amount_field.replace("rounded", "grand")), currency))


def small_amount_notice(doc, settings, groups):
	"""少額特例に該当し得る仕入で、適格請求書がなくても控除できる旨を表示する（INV-47）。表示のみ。"""
	if not settings.small_amount_special or cint(doc.get("is_return")):
		return
	rule = frappe.db.get_value("JP Tax Rule Period", "SMALL-1", ["value", "valid_from", "valid_to"], as_dict=True)
	on_date = applied_date(doc)
	if not rule or (rule.valid_from and on_date < getdate(rule.valid_from)) or (rule.valid_to and on_date > getdate(rule.valid_to)):
		return
	if not any(g["rate_code"] for g in groups) or flt(doc.grand_total) >= flt(rule.value):
		return
	message = _("少額特例（Q&A 問111）の対象となり得ます。適格請求書がなくても帳簿の保存のみで仕入税額控除できます。一回の取引の金額かどうかはご確認ください。")
	if any(masters.get_category(g["tax_category"]).is_nonregistered for g in groups):
		message += _("<br>免税事業者等からの仕入でも、通常の課税仕入の税区分（P-TAX-*）を選べます。")
	frappe.msgprint(message, title=_("少額特例"), indicator="blue")


def normalize_base_amounts(doc):
	"""外貨建て伝票の明細の円換算額を 1 円単位にそろえ、端数は金額の最も大きい明細に寄せる。

	明細ごとに銭まで換算すると、仕訳で円に丸めたときに借方・貸方が 1 円ずれ、提出できなくなる
	（ERPNext は請求書の貸借差を 0.5 までしか許さない）。端数処理方針 4.2 と同じ寄せ方にする。
	"""
	conv = flt(doc.conversion_rate) or 1
	company_currency = frappe.get_cached_value("Company", doc.company, "default_currency")
	if conv == 1 or doc.currency == company_currency:
		return
	precision = doc.precision("base_net_total")
	if precision:
		return
	total = round(flt(doc.base_net_total))
	amounts = [flt(i.base_net_amount) for i in doc.items]
	shares = [round_yen(a, "四捨五入") for a in amounts]
	if shares:
		shares[max(range(len(amounts)), key=lambda i: abs(amounts[i]))] += total - sum(shares)
	for item, amount in zip(doc.items, shares):
		item.base_net_amount = item.base_amount = amount
		if flt(item.qty):
			item.base_net_rate = item.base_rate = amount / flt(item.qty)
	doc.base_net_total = doc.base_total = total


BREAKDOWN_FIELDS = (
	"tax_category", "display_label", "rate_code", "tax_rate", "applied_date", "transaction_kind", "inclusion",
	"rounding_method", "net_amount", "gross_amount", "computed_tax", "stated_tax", "posted_tax", "deduction_rule",
	"deduction_ratio", "deductible_tax", "nondeductible_tax",
)


def write_snapshot(doc, settings, groups):
	"""計算結果を伝票に保存する（端数処理方針 4.4、適用ルール #7）。"""
	doc.set("jp_tax_breakdown", [])
	for g in groups:
		doc.append("jp_tax_breakdown", {k: g.get(k) for k in BREAKDOWN_FIELDS})
	doc.jp_calc_snapshot = json.dumps({
		"sales_calc_method": settings.sales_calc_method,
		"purchase_calc_method": settings.purchase_calc_method,
		"sales_rounding": settings.sales_rounding,
		"purchase_rounding": settings.purchase_rounding,
		"nondeductible_method": settings.nondeductible_method,
		"inclusion": "内税" if cint(doc.get("jp_tax_inclusive")) else "外税",
		"applied_date": str(applied_date(doc)),
		"calculated_at": str(now_datetime()),
	}, ensure_ascii=False)
