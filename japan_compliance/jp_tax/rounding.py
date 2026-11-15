"""消費税額の端数処理（端数処理方針 4.1 #3・#4）。

Decimal で計算し、浮動小数点の誤差を持ち込まない。返品など負の金額は、絶対値を丸めてから符号を戻す
（「切捨て」が負の方向に 1 円ずれないようにするため）。
"""

from decimal import ROUND_CEILING, ROUND_FLOOR, ROUND_HALF_UP, Decimal

METHODS = {"切捨て": ROUND_FLOOR, "四捨五入": ROUND_HALF_UP, "切上げ": ROUND_CEILING}


def to_decimal(value) -> Decimal:
	return Decimal(str(value or 0))


def round_yen(value, method: str) -> int:
	"""value（Decimal または数値）を 1 円単位に丸める。"""
	if method not in METHODS:
		raise ValueError(f"未知の端数処理: {method}")
	v = to_decimal(value)
	sign = -1 if v < 0 else 1
	return sign * int(abs(v).quantize(Decimal(1), rounding=METHODS[method]))


def tax_exclusive(net, rate_percent, method: str) -> int:
	"""外税：税抜合計 × 税率。"""
	return round_yen(to_decimal(net) * to_decimal(rate_percent) / 100, method)


def tax_inclusive(gross, rate_percent, method: str) -> int:
	"""内税（割戻し）：税込合計 × 税率 /（100 ＋ 税率）（Q&A 問57）。"""
	r = to_decimal(rate_percent)
	return round_yen(to_decimal(gross) * r / (100 + r), method)


def apply_ratio(amount, ratio_percent, method: str) -> int:
	"""仮払消費税相当額 × 控除割合（Q&A 問130）。"""
	return round_yen(to_decimal(amount) * to_decimal(ratio_percent) / 100, method)


def allocate(total: int, weights: list) -> list:
	"""total（整数円）を weights の比で按分する。端数は絶対値の最も大きい行に寄せる（端数処理方針 4.2）。"""
	if not weights:
		return []
	wsum = sum(to_decimal(w) for w in weights)
	if wsum == 0:
		shares = [0] * len(weights)
		shares[0] = total
		return shares
	shares = [int((to_decimal(total) * to_decimal(w) / wsum).to_integral_value(rounding=ROUND_FLOOR)) for w in weights]
	diff = total - sum(shares)
	largest = max(range(len(weights)), key=lambda i: abs(to_decimal(weights[i])))
	shares[largest] += diff
	return shares
