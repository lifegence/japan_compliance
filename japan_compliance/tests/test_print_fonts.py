"""帳票 PDF に同梱フォント（LifegenceJP）が埋め込まれ、日本語が文字として取り出せることのテスト。

フォントは /assets/japan_compliance/fonts/ から読み込まれるため、wkhtmltopdf がサイトの URL
（host_name）に接続できる必要がある（CI は bench serve を起動して host_name を設定している）。
"""

import io
import os
import re

import frappe
from pypdf import PdfReader

from japan_compliance.tests.test_invoice_tax import JPTaxTestCase, make_si

APP_DIR = os.path.dirname(os.path.dirname(__file__))
FONT_DIR = os.path.join(APP_DIR, "public", "fonts")
FONT_CSS = os.path.join(APP_DIR, "public", "css", "jp_print_fonts.css")
TEMPLATES = [
	"jp_form/print_templates/qualified_invoice.html",
	"jp_form/report/jp_journal_book/jp_journal_book.html",
	"jp_form/report/jp_general_ledger/jp_general_ledger.html",
	"jp_form/report/jp_consumption_tax_summary/jp_consumption_tax_summary.html",
]
FONT_FACE = re.compile(r"@font-face\s*\{[^}]*\}")


def read(path):
	with open(os.path.join(APP_DIR, path), encoding="utf-8") as f:
		return f.read()


def font_faces(text):
	return [" ".join(m.split()) for m in FONT_FACE.findall(text)]


def pdf_fonts(pdf):
	"""PDF に埋め込まれたフォント名（サブセット接頭辞 ABCDEF+ を除く）。"""
	names = set()
	for page in PdfReader(io.BytesIO(pdf)).pages:
		fonts = (page.get("/Resources") or {}).get("/Font") or {}
		for ref in fonts.values():
			font = ref.get_object()
			for f in [font, *[d.get_object() for d in font.get("/DescendantFonts") or []]]:
				names.add(str(f.get("/BaseFont", "")).lstrip("/").split("+", 1)[-1])
	return names


def compact(text):
	return "".join(text.split())


class TestBundledFont(JPTaxTestCase):
	def test_font_files_are_bundled(self):
		for name in ("LifegenceJP-Regular.ttf", "LifegenceJP-Bold.ttf", "OFL.txt", "README.md"):
			self.assertTrue(os.path.exists(os.path.join(FONT_DIR, name)), name)

	def test_templates_copy_font_faces_from_css(self):
		expected = font_faces(read(FONT_CSS))
		self.assertEqual(len(expected), 2)
		for path in TEMPLATES:
			html = read(path)
			self.assertEqual(font_faces(html), expected, path)
			self.assertIn('font-family: "LifegenceJP", "Noto Sans CJK JP"', html, path)
			self.assertIn('class="jp-print-footer"', html, path)

	def test_qualified_invoice_pdf_embeds_bundled_font(self):
		from frappe.utils.pdf import get_pdf

		si = make_si(items=[("ITEM-X", 6, 5000), ("ITEM-Y", 10, 2000)], submit=True)
		html = frappe.get_print("Sales Invoice", si.name, print_format="適格請求書（JP）", no_letterhead=1)
		pdf = get_pdf(html)

		fonts = pdf_fonts(pdf)
		self.assertIn("LifegenceJP-Regular", fonts)
		self.assertIn("LifegenceJP-Bold", fonts)  # 見出し（h1）は太字のフェイス

		text = compact("".join(page.extract_text() for page in PdfReader(io.BytesIO(pdf)).pages))
		for expected in ("請求書", "テスト得意先A株式会社御中", "登録番号T8000000000001", "食品Y※", "8%（軽減）", "30,000"):
			self.assertIn(expected, text)

	def test_report_footer_uses_bundled_font(self):
		"""帳票（レポート）のフッターはヘッダー・フッター用の別 HTML になるが、<style> の @font-face は引き継がれる。"""
		from frappe.utils.pdf import get_pdf

		template = read("jp_form/report/jp_journal_book/jp_journal_book.html")
		style = template[template.index("<style>"):template.index("</style>") + len("</style>")]
		rows = "".join(f"<tr><td>{i}</td><td>売上高</td></tr>" for i in range(200))
		html = (style + '<div class="jp-report"><h2>仕訳帳</h2><table>' + rows + "</table>"
			'<div id="footer-html" class="visible-pdf"><div class="jp-print-footer">仕訳帳　<span class="page"></span> / <span class="topage"></span></div></div></div>')
		pdf = get_pdf(html)
		reader = PdfReader(io.BytesIO(pdf))
		n = len(reader.pages)
		self.assertGreater(n, 1)
		self.assertIn("LifegenceJP-Regular", pdf_fonts(pdf))
		self.assertIn("売上高", compact(reader.pages[0].extract_text()))
		self.assertIn(f"仕訳帳1/{n}", compact(reader.pages[0].extract_text()))
		self.assertIn(f"仕訳帳{n}/{n}", compact(reader.pages[-1].extract_text()))
