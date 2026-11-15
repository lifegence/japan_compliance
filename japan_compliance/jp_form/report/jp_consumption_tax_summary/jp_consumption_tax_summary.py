"""消費税集計表（帳票記載要件 4.7 FORM-60〜68、端数処理方針 4.3(f)）。

集計の元データは、確定済み伝票の税区分スナップショット（jp_tax_breakdown）と、振替伝票・入出金伝票の
明細の税区分。消費税の相殺・振替仕訳（jp_tax_settlement）は集計しない。申告書の作成は行わない。
"""

from collections import OrderedDict
from decimal import ROUND_FLOOR, Decimal

import frappe
from frappe import _
from frappe.utils import flt, getdate

from japan_compliance.jp_tax import masters
from japan_compliance.jp_tax.rounding import allocate

SALES, PURCHASE = "売上", "仕入"
# 取消の反対仕訳の月に符号を反転する金額
SIGNED_AMOUNTS = ("net_amount", "gross_amount", "posted_tax", "computed_tax", "deductible_tax", "nondeductible_tax")
KIND_ORDER = {"通常": 0, "返還等": 1, "貸倒": 2, "貸倒回収": 3}


def execute(filters=None):
	filters = frappe._dict(filters or {})
	settings = masters.get_settings(filters.company)
	if not settings:
		frappe.throw(_("会社 {0} の JP Tax Settings がありません。").format(filters.company))
	rows = collect(filters, settings)
	if filters.view == "申告用の計算":
		return filing_view(rows, settings, filters)
	if filters.view == "免税事業者等の仕入先別累計":
		return supplier_cap_view(filters, settings)
	return category_view(rows)


# ---------------------------------------------------------------- 集計の元データ


def collect(filters, settings):
	"""税区分ごとの明細（1伝票・1区分・1取引種別で1行）。"""
	rows = collect_vouchers(filters, settings)
	for voucher_type, voucher_no, posted, reversed_on in reversed_vouchers(filters):
		in_posted = getdate(filters.from_date) <= posted <= getdate(filters.to_date)
		in_reversed = getdate(filters.from_date) <= reversed_on <= getdate(filters.to_date)
		if in_posted == in_reversed:
			continue
		one = frappe._dict(filters, voucher_filter={"type": voucher_type, "name": voucher_no})
		sign = 1 if in_posted else -1
		for row in collect_vouchers(one, settings):
			for field in SIGNED_AMOUNTS:
				row[field] = sign * flt(row.get(field))
			rows.append(row)
	return rows


def reversed_vouchers(filters):
	"""Immutable Ledger で取り消した伝票。元の計上日と反対仕訳の日付が違えば、元帳はどちらの月にも残る（CLS-31）。

	集計表も元帳に合わせ、計上した月はそのまま、反対仕訳の月にマイナスで集計する。
	"""
	out = []
	for voucher_type, voucher_no, reversed_on in frappe.db.sql(
		"""
		select distinct voucher_type, voucher_no, posting_date from `tabGL Entry`
		where company = %(company)s and is_cancelled = 0 and remarks like 'On cancellation of %%'
			and voucher_type in ('Sales Invoice', 'Purchase Invoice', 'Journal Entry', 'Payment Entry')
		""",
		filters,
	):
		posted = frappe.db.get_value(voucher_type, voucher_no, "posting_date")
		if getdate(posted) != getdate(reversed_on):
			out.append((voucher_type, voucher_no, getdate(posted), getdate(reversed_on)))
	return out


def voucher_scope(filters, alias, voucher_type):
	"""集計対象の条件。voucher_filter があれば取消済みの1伝票、なければ期間内の提出済み伝票。"""
	one = filters.get("voucher_filter")
	if one:
		matches = "1 = 1" if one["type"] == voucher_type else "1 = 0"
		return f"{alias}.name = %(voucher_name)s and {matches}", {"voucher_name": one["name"], "voucher_filter": None}
	return f"{alias}.docstatus = 1 and {alias}.posting_date between %(from_date)s and %(to_date)s", {}


def collect_vouchers(filters, settings):
	rows = []
	for dt in ("Sales Invoice", "Purchase Invoice"):
		scope, extra = voucher_scope(filters, "p", dt)
		party = "customer" if dt == "Sales Invoice" else "supplier"
		for r in frappe.db.sql(
			f"""
			select b.tax_category, b.tax_rate, b.transaction_kind, b.net_amount * p.conversion_rate as net_amount,
				b.gross_amount * p.conversion_rate as gross_amount, b.posted_tax * p.conversion_rate as posted_tax,
				b.computed_tax * p.conversion_rate as computed_tax, b.deduction_ratio,
				b.deductible_tax * p.conversion_rate as deductible_tax, b.nondeductible_tax * p.conversion_rate as nondeductible_tax,
				p.name as voucher_no, p.{party} as party
			-- 金額は会社通貨（円）に換算する
			from `tabJP Tax Breakdown` b join `tab{dt}` p on p.name = b.parent and b.parenttype = %(dt)s
			where {scope} and p.company = %(company)s
			""",
			{"dt": dt, **filters, **extra},
			as_dict=True,
		):
			# 控除対象額：免税事業者等の仕入は控除割合後の額、それ以外は計上税額
			deductible = r.deductible_tax if r.deduction_ratio or r.nondeductible_tax else r.posted_tax
			rows.append(frappe._dict(r, voucher_type=dt, deductible_tax=flt(deductible) if dt == "Purchase Invoice" else 0))
	rows += collect_journal_lines(filters, settings)
	rows += collect_payment_deductions(filters, settings)
	return rows


def collect_journal_lines(filters, settings):
	scope, extra = voucher_scope(filters, "je", "Journal Entry")
	lines = frappe.db.sql(
		f"""
		select je.name as voucher_no, je.jp_transaction_kind as kind, a.account, a.debit, a.credit, a.jp_tax_category
		from `tabJournal Entry Account` a join `tabJournal Entry` je on je.name = a.parent
		where {scope} and je.company = %(company)s and ifnull(je.jp_tax_settlement, 0) = 0
		order by je.name, a.idx
		""",
		{**filters, **extra},
		as_dict=True,
	)
	tax_accounts = (settings.sales_tax_account, settings.purchase_tax_account)
	by_voucher = OrderedDict()
	for line in lines:
		by_voucher.setdefault(line.voucher_no, []).append(line)
	out = []
	for voucher, vlines in by_voucher.items():
		entries = [
			(line.jp_tax_category, flt(line.debit) - flt(line.credit), line.kind or "通常")
			for line in vlines if line.jp_tax_category and line.account not in tax_accounts
		]
		tax = {
			SALES: sum(flt(line.credit) - flt(line.debit) for line in vlines if line.account == settings.sales_tax_account),
			PURCHASE: sum(flt(line.debit) - flt(line.credit) for line in vlines if line.account == settings.purchase_tax_account),
		}
		out += distribute(voucher, "Journal Entry", entries, tax)
	return out


def collect_payment_deductions(filters, settings):
	scope, extra = voucher_scope(filters, "pe", "Payment Entry")
	lines = frappe.db.sql(
		f"""
		select pe.name as voucher_no, d.account, d.amount, d.jp_tax_category
		from `tabPayment Entry Deduction` d join `tabPayment Entry` pe on pe.name = d.parent
		where {scope} and pe.company = %(company)s
		""",
		{**filters, **extra},
		as_dict=True,
	)
	tax_accounts = (settings.sales_tax_account, settings.purchase_tax_account)
	by_voucher = OrderedDict()
	for line in lines:
		by_voucher.setdefault(line.voucher_no, []).append(line)
	out = []
	for voucher, vlines in by_voucher.items():
		# 控除欄の金額は借方。売上の区分が立つのは対価の返還等（先方負担の振込手数料など）
		entries = [(line.jp_tax_category, flt(line.amount), "返還等" if direction_of(line.jp_tax_category) == SALES else "通常")
			for line in vlines if line.jp_tax_category and line.account not in tax_accounts]
		tax = {
			SALES: -sum(flt(line.amount) for line in vlines if line.account == settings.sales_tax_account),
			PURCHASE: sum(flt(line.amount) for line in vlines if line.account == settings.purchase_tax_account),
		}
		out += distribute(voucher, "Payment Entry", entries, tax)
	return out


def direction_of(code):
	return frappe.get_cached_value("JP Tax Category", code, "direction")


def distribute(voucher, voucher_type, entries, tax):
	"""振替伝票・入出金伝票の明細を区分ごとの行にし、同じ伝票の仮受・仮払の額を課税区分に按分する。"""
	rows = []
	for code, debit_minus_credit, kind in entries:
		cat = masters.get_category(code)
		net = -debit_minus_credit if cat.direction == SALES else debit_minus_credit
		rate = rate_for(cat)
		rows.append(frappe._dict(tax_category=code, tax_rate=rate, transaction_kind=kind, net_amount=net, posted_tax=0,
			computed_tax=0, deduction_ratio=0, deductible_tax=0, nondeductible_tax=0, voucher_no=voucher,
			voucher_type=voucher_type, party=None, direction=cat.direction))
	for direction in (SALES, PURCHASE):
		taxable = [r for r in rows if r.direction == direction and r.tax_rate]
		amount = int(round(tax[direction]))
		if not taxable or not amount:
			continue
		for r, share in zip(taxable, allocate(amount, [r.net_amount for r in taxable])):
			r.posted_tax = r.computed_tax = share
			if direction == PURCHASE:
				r.deductible_tax = share
	for r in rows:
		r.gross_amount = r.net_amount + r.posted_tax
	return rows


def rate_for(cat):
	if cat.tax_type != "課税" or not cat.rate_code or cat.rate_code == "ZERO":
		return 0
	return flt(masters.get_rate(cat.rate_code, getdate()).rate)


# ---------------------------------------------------------------- 区分別集計（FORM-60・62）


def category_view(rows):
	groups = OrderedDict()
	for r in sorted(rows, key=lambda r: (r.tax_category, KIND_ORDER.get(r.transaction_kind, 9), -flt(r.deduction_ratio))):
		key = (r.tax_category, flt(r.tax_rate), r.transaction_kind, flt(r.deduction_ratio))
		g = groups.setdefault(key, frappe._dict(
			tax_category=r.tax_category, category_name=masters.get_category(r.tax_category).category_name,
			tax_rate=flt(r.tax_rate), transaction_kind=r.transaction_kind, deduction_ratio=flt(r.deduction_ratio) or None,
			net_amount=0, tax_amount=0, gross_amount=0, deductible_tax=0, nondeductible_tax=0, count=0,
		))
		g.net_amount += flt(r.net_amount)
		g.tax_amount += flt(r.posted_tax)
		g.gross_amount += flt(r.gross_amount)
		g.deductible_tax += flt(r.deductible_tax)
		g.nondeductible_tax += flt(r.nondeductible_tax)
		g.count += 1
	columns = [
		col("tax_category", "区分", "Link", 110, "JP Tax Category"), col("category_name", "区分名", "Data", 200),
		col("tax_rate", "税率 %", "Float", 70), col("transaction_kind", "取引種別", "Data", 80),
		col("deduction_ratio", "控除割合 %", "Float", 90), col("net_amount", "税抜金額", "Currency", 120),
		col("tax_amount", "消費税額（計上額）", "Currency", 130), col("gross_amount", "税込金額", "Currency", 120),
		col("deductible_tax", "控除対象額", "Currency", 110), col("nondeductible_tax", "控除不能額", "Currency", 110),
		col("count", "件数", "Int", 60),
	]
	return columns, list(groups.values())


# ---------------------------------------------------------------- 申告用の計算（FORM-61・63・65・67）


def filing_view(rows, settings, filters):
	def total(field, direction, kind=None, rate_code=None, cats=None):
		s = 0
		for r in rows:
			cat = masters.get_category(r.tax_category)
			if cat.direction != direction or (kind and r.transaction_kind != kind):
				continue
			if rate_code and cat.rate_code != rate_code:
				continue
			if cats and r.tax_category not in cats:
				continue
			if not cats and not rate_code and not r.tax_rate:
				continue
			s += flt(r.get(field))
		return s

	out = []

	def line(label, std=None, red=None, note="", bold=False):
		values = [v for v in (std, red) if v is not None]
		out.append(frappe._dict(item=label, std=std, red=red, total=sum(values) if values else None, note=note, bold=bold))

	sales_gross = {rc: total("gross_amount", SALES, "通常", rc) for rc in ("STD10", "RED8")}
	sales_tax = {rc: total("posted_tax", SALES, "通常", rc) for rc in ("STD10", "RED8")}
	line("【売上】" + settings.sales_calc_method, bold=True)
	if settings.sales_calc_method == "割戻し計算":
		base = {rc: floor_thousand(dec(sales_gross[rc]) * 100 / (110 if rc == "STD10" else 108)) for rc in sales_gross}
		line("税込売上合計", sales_gross["STD10"], sales_gross["RED8"])
		line("課税標準額（千円未満切捨て）", base["STD10"], base["RED8"])
		line("消費税額（国税 7.8%／6.24%）", floor_yen(dec(base["STD10"]) * dec("0.078")), floor_yen(dec(base["RED8"]) * dec("0.0624")))
	else:
		line("適格請求書記載の消費税額の合計", sales_tax["STD10"], sales_tax["RED8"])
		line("売上税額 × 78/100（国税分）", floor_yen(dec(sales_tax["STD10"]) * dec("0.78")), floor_yen(dec(sales_tax["RED8"]) * dec("0.78")))
	line("売上対価の返還等に係る消費税額", total("posted_tax", SALES, "返還等", "STD10"), total("posted_tax", SALES, "返還等", "RED8"))
	line("貸倒れに係る消費税額", total("posted_tax", SALES, "貸倒", "STD10"), total("posted_tax", SALES, "貸倒", "RED8"))
	# 回収した貸倒債権に含まれる消費税額は、回収した課税期間の売上の税額に加算する（消費税法39条3項、B-6 S-11）
	recovered = {rc: total("posted_tax", SALES, "貸倒回収", rc) for rc in ("STD10", "RED8")}
	line("貸倒回収に係る消費税額（売上の税額に加算）", recovered["STD10"], recovered["RED8"])
	line("同上の国税分（× 78/100）", floor_yen(dec(recovered["STD10"]) * dec("0.78")), floor_yen(dec(recovered["RED8"]) * dec("0.78")))

	line("【仕入】" + settings.purchase_calc_method, bold=True)
	if settings.purchase_calc_method == "割戻し計算":
		gross = {rc: total("gross_amount", PURCHASE, None, rc) for rc in ("STD10", "RED8")}
		line("税込課税仕入れの合計", gross["STD10"], gross["RED8"])
		line("仕入税額 × 7.8/110・6.24/108（国税分）", floor_yen(dec(gross["STD10"]) * dec("7.8") / 110), floor_yen(dec(gross["RED8"]) * dec("6.24") / 108))
	else:
		ded = {rc: total("deductible_tax", PURCHASE, None, rc) for rc in ("STD10", "RED8")}
		line("仮払消費税等（控除対象額）の合計", ded["STD10"], ded["RED8"])
		line("仕入税額 × 78/100（国税分）", floor_yen(dec(ded["STD10"]) * dec("0.78")), floor_yen(dec(ded["RED8"]) * dec("0.78")))
	for ratio in sorted({flt(r.deduction_ratio) for r in rows if r.deduction_ratio or r.nondeductible_tax}, reverse=True):
		sub = [r for r in rows if flt(r.deduction_ratio) == ratio and (r.deduction_ratio or r.nondeductible_tax)]
		line(f"経過措置 {ratio:g}%：税込課税仕入れ／控除対象額／控除不能額",
			sum(flt(r.gross_amount) for r in sub), None,
			f"控除対象 {sum(flt(r.deductible_tax) for r in sub):,.0f}／控除不能 {sum(flt(r.nondeductible_tax) for r in sub):,.0f}")

	# 課税売上割合（参考値・FORM-65）
	# 売上の対価の額は、返還等を差し引いた額。貸倒れと貸倒回収は対価の額を変えない
	def sales_net(cats):
		return sum(flt(r.net_amount) for r in rows
			if r.tax_category in cats and r.transaction_kind not in ("貸倒", "貸倒回収"))

	taxable = sales_net(["S-TAX-10", "S-TAX-8R", "S-TAX-8", "S-TAX-5"])
	export = sales_net(["S-EXP", "S-EXM-EXP"])
	exempt = sales_net(["S-EXM"])
	securities = sales_net(["S-SEC"]) * 0.05
	numerator, denominator = taxable + export, taxable + export + exempt + securities
	ratio = numerator / denominator * 100 if denominator else 0
	line("【課税売上割合（参考値）】", bold=True)
	line("分子（課税売上＋免税売上）", numerator)
	line("分母（分子＋非課税売上＋有価証券譲渡の5%）", denominator)
	line("課税売上割合 %", round(ratio, 2), note="95%未満のため全額控除の前提が崩れます" if denominator and ratio < 95 else "")

	# 元帳との差額（FORM-67）
	line("【元帳との差額】", bold=True)
	for label, account, direction, field in (
		("仮受消費税等", settings.sales_tax_account, SALES, "posted_tax"),
		("仮払消費税等", settings.purchase_tax_account, PURCHASE, "posted_tax"),
	):
		gl = gl_balance(account, filters, direction)
		tax = sum(flt(r.get(field)) for r in rows if masters.get_category(r.tax_category).direction == direction)
		line(f"{label}：元帳／集計表／差額", gl, tax, f"差額 {gl - tax:,.0f}")

	columns = [col("item", "項目", "Data", 340), col("std", "10%", "Currency", 140), col("red", "軽減8%", "Currency", 140),
		col("total", "合計", "Currency", 140), col("note", "備考", "Data", 260)]
	return columns, out


def gl_balance(account, filters, direction):
	"""期間中の仕訳による増減（相殺・振替仕訳を除く）。仮受は貸方、仮払は借方をプラス。"""
	sign = "credit - debit" if direction == SALES else "debit - credit"
	value = frappe.db.sql(
		f"""
		select sum(gle.{sign.split(' - ')[0]}) - sum(gle.{sign.split(' - ')[1]}) from `tabGL Entry` gle
		left join `tabJournal Entry` je on gle.voucher_type = 'Journal Entry' and je.name = gle.voucher_no
		where gle.account = %(account)s and gle.is_cancelled = 0 and gle.company = %(company)s
			and gle.posting_date between %(from_date)s and %(to_date)s and ifnull(je.jp_tax_settlement, 0) = 0
		""",
		{"account": account, **filters},
	)[0][0]
	return flt(value)


def dec(value):
	return Decimal(str(value or 0))


def floor_thousand(value):
	return int(Decimal(value).quantize(Decimal(1), rounding=ROUND_FLOOR)) // 1000 * 1000


def floor_yen(value):
	return int(Decimal(str(value)).quantize(Decimal(1), rounding=ROUND_FLOOR))


# ---------------------------------------------------------------- 仕入先別累計（FORM-64）


def supplier_cap_view(filters, settings):
	"""免税事業者等からの課税仕入れの仕入先別累計と上限超過額。集計単位は課税期間（JP Tax Settings）。"""
	start = settings.tax_period_start or filters.from_date
	end = settings.tax_period_end or filters.to_date
	cap = masters.get_purchase_cap(start)
	data = frappe.db.sql(
		"""
		select p.supplier, sum(b.gross_amount * p.conversion_rate) as gross, count(distinct p.name) as count
		from `tabJP Tax Breakdown` b join `tabPurchase Invoice` p on p.name = b.parent and b.parenttype = 'Purchase Invoice'
		join `tabJP Tax Category` c on c.name = b.tax_category
		where p.docstatus = 1 and p.company = %(company)s and p.posting_date between %(start)s and %(end)s
			and c.is_nonregistered = 1
		group by p.supplier order by gross desc
		""",
		{"company": filters.company, "start": start, "end": end},
		as_dict=True,
	)
	limit = flt(cap.value) if cap else 0
	for row in data:
		row.period = f"{start} 〜 {end}"
		row.cap_rule = cap.rule_code if cap else ""
		row.cap = limit or None
		row.over = max(flt(row.gross) - limit, 0) if limit else 0
	columns = [col("supplier", "仕入先", "Link", 220, "Supplier"), col("period", "課税期間", "Data", 190),
		col("gross", "税込累計（返品・値引後）", "Currency", 170), col("cap_rule", "上限ルール", "Data", 100),
		col("cap", "上限", "Currency", 140), col("over", "上限超過額", "Currency", 140), col("count", "伝票数", "Int", 70)]
	return columns, data


def col(fieldname, label, fieldtype, width, options=None):
	c = {"fieldname": fieldname, "label": label, "fieldtype": fieldtype, "width": width}
	if options:
		c["options"] = options
	return c
