"""端数処理の単体テスト（端数処理方針 5章の計算例）。"""

import unittest

from japan_compliance.jp_invoice.registration import check_digit, is_valid
from japan_compliance.jp_tax.rounding import allocate, apply_ratio, round_yen, tax_exclusive, tax_inclusive


class TestRounding(unittest.TestCase):
	def test_5_1_exclusive_three_lines(self):
		self.assertEqual(tax_exclusive(4358, 10, "切捨て"), 435)
		self.assertEqual(tax_exclusive(4358, 10, "四捨五入"), 436)
		self.assertEqual(tax_exclusive(4358, 10, "切上げ"), 436)

	def test_5_2_mixed_rates(self):
		self.assertEqual(tax_exclusive(5225, 10, "切捨て"), 522)
		self.assertEqual(tax_exclusive(5225, 10, "四捨五入"), 523)
		self.assertEqual(tax_exclusive(2499, 8, "切捨て"), 199)
		self.assertEqual(tax_exclusive(2499, 8, "四捨五入"), 200)

	def test_5_3_half_boundary_is_not_bankers(self):
		self.assertEqual([tax_exclusive(n, 10, "四捨五入") for n in (1005, 1015, 1025)], [101, 102, 103])
		self.assertEqual([tax_exclusive(n, 10, "切捨て") for n in (1005, 1015, 1025)], [100, 101, 102])

	def test_5_4_inclusive(self):
		self.assertEqual(tax_inclusive(1000, 10, "切捨て"), 90)
		self.assertEqual(tax_inclusive(1000, 10, "四捨五入"), 91)
		self.assertEqual(tax_inclusive(10999, 10, "切捨て"), 999)
		self.assertEqual(tax_inclusive(10999, 10, "四捨五入"), 1000)
		self.assertEqual(tax_inclusive(1080, 8, "切捨て"), 80)
		self.assertEqual(tax_inclusive(3218, 8, "切捨て"), 238)

	def test_5_6_nonregistered_ratio(self):
		self.assertEqual(tax_inclusive(55000, 10, "切捨て"), 5000)
		self.assertEqual(tax_inclusive(33333, 10, "切捨て"), 3030)
		for ratio, a, b in ((80, 4000, 2424), (70, 3500, 2121), (50, 2500, 1515), (30, 1500, 909), (0, 0, 0)):
			self.assertEqual(apply_ratio(5000, ratio, "切捨て"), a)
			self.assertEqual(apply_ratio(3030, ratio, "切捨て"), b)

	def test_negative_amounts_round_on_absolute_value(self):
		self.assertEqual(round_yen(-99.9, "切捨て"), -99)
		self.assertEqual(tax_exclusive(-999, 10, "切捨て"), -99)

	def test_allocate_remainder_to_largest(self):
		self.assertEqual(allocate(-3500, [55000]), [-3500])
		self.assertEqual(sum(allocate(100, [1, 1, 1])), 100)
		self.assertEqual(allocate(10, [1000, 3000]), [2, 8])

	def test_registration_check_digit(self):
		self.assertEqual(check_digit("000000000009"), 9)
		self.assertTrue(is_valid("T9000000000009"))
		self.assertFalse(is_valid("T9000000000001"))
		self.assertFalse(is_valid("1234567890123"))
