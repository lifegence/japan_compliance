"""適格請求書発行事業者の登録番号の形式検証（インボイス要件 INV-01・INV-05）。

登録番号は「T」＋13桁。法人は法人番号と同じ番号で、先頭1桁がチェックデジット。
個人事業者の番号も同じ桁数・同じ計算で付番される。
"""

import re

import frappe
from frappe import _

PATTERN = re.compile(r"^T\d{13}$")


def check_digit(base12: str) -> int:
	"""法人番号のチェックデジット：9 −（Σ 各桁 × 重み）mod 9。重みは下位から 1,2,1,2,…。"""
	total = sum(int(d) * (1 if i % 2 == 0 else 2) for i, d in enumerate(reversed(base12)))
	return 9 - total % 9


def is_valid(number: str) -> bool:
	number = (number or "").strip()
	if not PATTERN.match(number):
		return False
	return int(number[1]) == check_digit(number[2:])


def validate_registration_number(number):
	number = (number or "").strip()
	if not PATTERN.match(number):
		frappe.throw(_("登録番号は「T」と13桁の数字で入力してください（例：T1234567890123）。"))
	if int(number[1]) != check_digit(number[2:]):
		frappe.throw(_("登録番号 {0} のチェックデジットが一致しません。番号を確認してください。").format(number))


def party_validate(doc, method=None):
	"""得意先・仕入先の登録番号と登録区分（INV-05・06）。"""
	if doc.get("jp_registration_number"):
		doc.jp_registration_number = doc.jp_registration_number.strip().upper()
		validate_registration_number(doc.jp_registration_number)
		if doc.jp_registration_status == "不明":
			doc.jp_registration_status = "登録"
	elif doc.get("jp_registration_status") == "登録":
		frappe.throw(_("登録区分を「登録」にする場合は登録番号を入力してください。"))
