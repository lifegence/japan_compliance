"""標準 DocType に追加する項目（税区分定義「ERPNext実装対応」、インボイス要件 6章、C-1・C-2）。"""

from frappe.custom.doctype.custom_field.custom_field import create_custom_fields

# 帳簿のみ保存の特例の金額基準と住所（INV-46。Q&A 問110）
SPECIAL_CASE_LIMIT = 30000  # 1 回の取引（伝票）の税込金額
# 3 万円以上は特例の対象外（請求書等の保存が必要）
INVOICE_REQUIRED_FROM_LIMIT = ("公共交通機関特例", "自動販売機・自動サービス機特例")
# 3 万円以上は相手方の住所又は所在地の記載が必要
ADDRESS_REQUIRED_FROM_LIMIT = ("入場券等回収特例",)
# 業法で帳簿に相手方の住所の記載が義務付けられる取引だけ住所が必要（古物商・質屋・宅建業者。再生資源事業者の消費者等からの仕入は不要）
ADDRESS_REQUIRED_BY_LAW = ("古物等の購入特例",)
ADDRESS_DESCRIPTION = "入場券等回収特例で税込 3 万円以上、古物等の購入特例で業法の義務がある取引は必須"
SPECIAL_CASES = "\n公共交通機関特例\n出張旅費等特例\n通勤手当\n自動販売機・自動サービス機特例\n入場券等回収特例\n古物等の購入特例\n郵便切手類特例"


def _law_field(insert_after):
	return dict(fieldname="jp_address_required_by_law", fieldtype="Check", label="業法で相手方の住所の記載が義務付けられる取引",
		insert_after=insert_after, depends_on="eval:doc.jp_special_case=='古物等の購入特例'",
		description="古物商・質屋・宅建業者の業法で、帳簿に相手方の住所の記載が必要な取引")


def _invoice_fields(insert_after, purchase):
	fields = [
		dict(fieldname="jp_tax_section", fieldtype="Section Break", label="日本の消費税", insert_after=insert_after, collapsible=1),
		dict(fieldname="jp_transaction_date", fieldtype="Date", label="取引日（引渡日・役務の完了日）", insert_after="jp_tax_section",
			description="税率・控除割合の適用日。空欄なら計上日"),
		dict(fieldname="jp_tax_inclusive", fieldtype="Check", label="内税（単価は税込）", insert_after="jp_transaction_date"),
	]
	last = "jp_tax_inclusive"
	if purchase:
		fields += [
			dict(fieldname="jp_stated_tax_std", fieldtype="Currency", label="請求書記載の消費税額（10%）", insert_after=last,
				options="currency", description="請求書等積上げ。空欄なら自動計算"),
			dict(fieldname="jp_stated_tax_red", fieldtype="Currency", label="請求書記載の消費税額（軽減8%）", insert_after="jp_stated_tax_std",
				options="currency"),
			dict(fieldname="jp_special_case", fieldtype="Select", label="帳簿のみ保存の特例", options=SPECIAL_CASES, insert_after="jp_stated_tax_red"),
			_law_field("jp_special_case"),
			dict(fieldname="jp_counterparty_address", fieldtype="Small Text", label="相手方の住所又は所在地", insert_after="jp_address_required_by_law",
				depends_on="jp_special_case", description=ADDRESS_DESCRIPTION),
		]
		last = "jp_counterparty_address"
	fields += [
		dict(fieldname="jp_tax_cb", fieldtype="Column Break", insert_after=last),
		dict(fieldname="jp_tax_breakdown", fieldtype="Table", label="税区分ごとの計算結果", options="JP Tax Breakdown",
			insert_after="jp_tax_cb", read_only=1, no_copy=1),
		dict(fieldname="jp_calc_snapshot", fieldtype="Small Text", label="計算時の設定", insert_after="jp_tax_breakdown",
			read_only=1, no_copy=1, hidden=1),
	]
	return fields


def _item_fields(insert_after):
	return [
		dict(fieldname="jp_tax_category", fieldtype="Link", label="税区分", options="JP Tax Category", insert_after=insert_after,
			in_list_view=1, columns=2),
		dict(fieldname="jp_tax_reason", fieldtype="Data", label="税区分の選択理由", insert_after="jp_tax_category",
			description="旧税率（経過措置）など理由が必要な区分のみ"),
	]


CUSTOM_FIELDS = {
	"Item": [
		dict(fieldname="jp_reduced_rate", fieldtype="Check", label="軽減税率対象", insert_after="item_group"),
		dict(fieldname="jp_sales_tax_category", fieldtype="Link", label="売上の既定税区分", options="JP Tax Category", insert_after="jp_reduced_rate"),
		dict(fieldname="jp_purchase_tax_category", fieldtype="Link", label="仕入の既定税区分", options="JP Tax Category",
			insert_after="jp_sales_tax_category"),
	],
	"Customer": [
		dict(fieldname="jp_honorific", fieldtype="Select", label="宛名の敬称", options="御中\n様", default="御中", insert_after="customer_name"),
		dict(fieldname="jp_tax_class", fieldtype="Select", label="消費税の取引区分", options="国内課税\n輸出（国外）", default="国内課税",
			insert_after="customer_group"),
		dict(fieldname="jp_registration_number", fieldtype="Data", label="適格請求書発行事業者の登録番号", insert_after="jp_tax_class",
			description="T＋13桁", in_list_view=1),
		dict(fieldname="jp_registration_status", fieldtype="Select", label="登録区分", options="不明\n登録\n非登録", default="不明",
			insert_after="jp_registration_number", in_standard_filter=1),
	],
	"Supplier": [
		dict(fieldname="jp_registration_number", fieldtype="Data", label="適格請求書発行事業者の登録番号", insert_after="supplier_group",
			description="T＋13桁"),
		dict(fieldname="jp_registration_status", fieldtype="Select", label="登録区分", options="不明\n登録\n非登録", default="不明",
			insert_after="jp_registration_number", in_standard_filter=1),
		dict(fieldname="jp_registration_from", fieldtype="Date", label="登録日", insert_after="jp_registration_status"),
		dict(fieldname="jp_registration_to", fieldtype="Date", label="登録の取消・失効日", insert_after="jp_registration_from",
			description="この日より後の仕入は非登録として扱う"),
	],
	"Account": [
		dict(fieldname="jp_tax_category", fieldtype="Link", label="既定の税区分", options="JP Tax Category", insert_after="account_type"),
	],
	"Sales Invoice": _invoice_fields("taxes_and_charges", purchase=False) + [
		dict(fieldname="jp_issuer_registration_number", fieldtype="Data", label="発行者の登録番号（発行時点）", insert_after="jp_tax_inclusive",
			read_only=1, no_copy=1),
		dict(fieldname="jp_original_period", fieldtype="Data", label="基となった取引の時期（返還インボイス）", insert_after="jp_issuer_registration_number",
			description="複数の請求書にまたがる返品・値引のとき。例：2026年9月〜10月分", depends_on="is_return"),
	],
	"Purchase Invoice": _invoice_fields("taxes_and_charges", purchase=True),
	"Sales Invoice Item": _item_fields("item_tax_template"),
	"Purchase Invoice Item": _item_fields("item_tax_template"),
	"Journal Entry": [
		dict(fieldname="jp_section", fieldtype="Section Break", label="日本の会計", insert_after="user_remark", collapsible=1),
		dict(fieldname="jp_transaction_date", fieldtype="Date", label="取引日", insert_after="jp_section"),
		dict(fieldname="jp_counterparty", fieldtype="Data", label="相手方（帳簿に記載）", insert_after="jp_transaction_date",
			description="取引先を指定しない経費など。仕訳帳の取引先に出力"),
		dict(fieldname="jp_special_case", fieldtype="Select", label="帳簿のみ保存の特例", options=SPECIAL_CASES, insert_after="jp_counterparty"),
		_law_field("jp_special_case"),
		dict(fieldname="jp_counterparty_address", fieldtype="Small Text", label="相手方の住所又は所在地", insert_after="jp_address_required_by_law",
			depends_on="jp_special_case", description=ADDRESS_DESCRIPTION),
		dict(fieldname="jp_transaction_kind", fieldtype="Select", label="取引種別（消費税）", options="通常\n返還等\n貸倒\n貸倒回収",
			default="通常", insert_after="jp_counterparty_address"),
		dict(fieldname="jp_tax_settlement", fieldtype="Check", label="消費税の相殺・振替仕訳", insert_after="jp_transaction_kind",
			description="消費税集計表と月次締めの税額チェックから除く"),
		dict(fieldname="jp_nondeductible_period", fieldtype="Data", label="控除不能分の振替の対象期間", insert_after="jp_tax_settlement",
			read_only=1, no_copy=1, hidden=1),
		dict(fieldname="jp_cb", fieldtype="Column Break", insert_after="jp_nondeductible_period"),
		dict(fieldname="jp_correction_of_type", fieldtype="Link", label="訂正元の伝票種別", options="DocType", insert_after="jp_cb"),
		dict(fieldname="jp_correction_of", fieldtype="Dynamic Link", label="訂正元の伝票", options="jp_correction_of_type",
			insert_after="jp_correction_of_type"),
		dict(fieldname="jp_correction_reason", fieldtype="Small Text", label="訂正の理由", insert_after="jp_correction_of"),
	],
	"Journal Entry Account": [
		dict(fieldname="jp_tax_category", fieldtype="Link", label="税区分", options="JP Tax Category", insert_after="account",
			in_list_view=1, columns=2),
	],
	"Payment Entry Deduction": [
		dict(fieldname="jp_tax_category", fieldtype="Link", label="税区分", options="JP Tax Category", insert_after="account"),
	],
}


def setup():
	create_custom_fields(CUSTOM_FIELDS, update=True)
