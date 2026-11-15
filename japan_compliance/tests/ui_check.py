"""画面での確認（E-2 を投入したサイトを Chromium で操作し、スクリーンショットと確認結果を残す）。

  pip install playwright && python -m playwright install --with-deps chromium
  JP_UI_BASE=http://localhost:8001 JP_UI_USER=Administrator JP_UI_PASSWORD=... JP_UI_OUT=/tmp/ui python ui_check.py
"""

import json
import os
import sys
import urllib.parse

from playwright.sync_api import sync_playwright

BASE = os.environ.get("JP_UI_BASE", "http://localhost:8001")
OUT = os.environ.get("JP_UI_OUT", "/workspace/ui")
os.makedirs(OUT, exist_ok=True)
results = []


def check(name, ok, detail=""):
	results.append({"name": name, "ok": bool(ok), "detail": detail})
	print(("OK  " if ok else "NG  ") + name + ("：" + detail if detail else ""), flush=True)


def settle(pg, ms=2500):
	pg.wait_for_load_state("load", timeout=60000)
	pg.wait_for_timeout(ms)


def report_url(name, **filters):
	return f"{BASE}/desk/query-report/{urllib.parse.quote(name)}?" + urllib.parse.urlencode(filters)


with sync_playwright() as p:
	browser = p.chromium.launch()
	ctx = browser.new_context(viewport={"width": 1440, "height": 900}, locale="ja-JP")
	page = ctx.new_page()
	page.set_default_timeout(90000)
	page.set_default_navigation_timeout(120000)
	page.goto(f"{BASE}/login")
	page.fill("#login_email", os.environ.get("JP_UI_USER", "Administrator"))
	page.fill("#login_password", os.environ.get("JP_UI_PASSWORD", "admin"))
	page.click("button.btn-login")
	page.wait_for_url("**/desk**", timeout=60000, wait_until="commit")
	page.wait_for_timeout(3000)

	# 1. デスクが日本語か
	page.goto(f"{BASE}/desk/sales-invoice")
	settle(page)
	page.screenshot(path=f"{OUT}/01_sales_invoice_list.png")
	title = page.title()
	check("売上請求書の一覧が日本語で表示される", "売上請求書" in title or "売上請求書" in page.inner_text("body"), title.strip())

	# 2. 混在請求書の画面と、税区分ごとの計算結果
	name = page.evaluate("""async () => (await frappe.db.get_value('Sales Invoice', {remarks: ['like', 'D-110 %']}, 'name')).message.name""")
	page.goto(f"{BASE}/desk/sales-invoice/{name}")
	settle(page)
	page.wait_for_timeout(1500)
	page.screenshot(path=f"{OUT}/02_sales_invoice_form.png", full_page=True)
	body = page.inner_text("body")
	check("請求書の画面に「日本の消費税」の区画と税区分が出る", "日本の消費税" in body and "課税売上 軽減" in body, name)
	check("画面の円の金額に小数が付かない", "¥ 30,000" in body.replace("¥30,000", "¥ 30,000") and "30,000.00" not in body)

	# 3. 適格請求書の印刷プレビュー
	page.goto(f"{BASE}/printview?doctype=Sales%20Invoice&name={name}&format={urllib.parse.quote('適格請求書（JP）')}&no_letterhead=1")
	settle(page)
	page.screenshot(path=f"{OUT}/03_invoice_print.png", full_page=True)
	text = page.inner_text("body")
	check("適格請求書の印刷に記載事項が出る", all(t in text for t in ("登録番号", "8%（軽減）", "※は軽減税率", "御中")))

	# 4. 消費税集計表
	page.goto(report_url("JP Consumption Tax Summary", company="テスト商事A株式会社", from_date="2026-10-01", to_date="2026-10-31", view="区分別集計"))
	settle(page)
	page.wait_for_timeout(1500)
	page.screenshot(path=f"{OUT}/04_tax_summary.png", full_page=True)
	text = page.inner_text("body")
	check("消費税集計表が日本語名で開き、P-NREG-10・S-EXP が出る", "消費税集計表" in text and "P-NREG-10" in text and "350,000" in text)

	# 5. 仕訳帳（JP）と印刷（FORM-04）
	page.goto(report_url("JP Journal Book", company="テスト商事A株式会社", from_date="2026-10-01", to_date="2026-10-31"))
	settle(page)
	page.wait_for_timeout(1500)
	page.screenshot(path=f"{OUT}/05_journal_book.png")
	text = page.inner_text("body")
	check("仕訳帳（JP）に税区分欄「70%控除対象」が出る", "仕訳帳（JP）" in text and "70%控除対象" in text)
	with ctx.expect_page() as popup:
		page.evaluate("frappe.query_report.print_report({orientation: 'Landscape'})")
	printed = popup.value
	printed.wait_for_load_state()
	printed.wait_for_timeout(1500)
	printed.screenshot(path=f"{OUT}/06_journal_book_print.png")
	text = printed.inner_text("body")
	check("仕訳帳（JP）の印刷に会社名・期間・出力日時の見出しが出る",
		all(t in text for t in ("仕訳帳", "テスト商事A株式会社", "期間：2026/10/01 〜 2026/10/31", "出力日時")))
	printed.close()

	# 6. 総勘定元帳（JP）：諸口のリンクから元の仕訳を開く（FORM-31）
	page.goto(report_url("JP General Ledger", company="テスト商事A株式会社", from_date="2026-10-01", to_date="2026-10-31"))
	settle(page)
	page.wait_for_timeout(1500)
	page.screenshot(path=f"{OUT}/07_general_ledger.png")
	link = page.locator("a", has_text="諸口").first
	href = link.get_attribute("href")
	link.click()
	page.wait_for_url("**/desk/**", timeout=60000, wait_until="commit")
	settle(page)
	check("総勘定元帳の「諸口」から元の伝票が開く", "/desk/" in page.url and href in page.url.replace(BASE, ""), href)
	page.screenshot(path=f"{OUT}/08_against_link_opened.png")

	# 7. 月次締め（2026年9月）：締め前チェックの実行
	existing = page.evaluate("""async () => (await frappe.db.get_value('JP Month Close', {company: 'テスト商事A株式会社', period_start: '2026-09-01'}, 'name')).message.name""")
	if existing:
		page.goto(f"{BASE}/desk/jp-month-close/{existing}")
		settle(page)
	else:
		page.goto(f"{BASE}/desk/jp-month-close/new")
		settle(page)
		page.evaluate("""() => { cur_frm.set_value('company', 'テスト商事A株式会社'); cur_frm.set_value('period_start', '2026-09-01'); }""")
		page.wait_for_timeout(800)
		page.keyboard.press("Control+s")
		page.wait_for_url("**/desk/jp-month-close/MC-**", timeout=60000, wait_until="commit")
		settle(page)
	page.get_by_role("button", name="締め前チェック").click()
	page.wait_for_timeout(5000)
	page.screenshot(path=f"{OUT}/09_month_close_checks.png", full_page=True)
	checks = page.evaluate("cur_frm.doc.checks.filter(r => r.result === 'NG').map(r => r.check_code)")
	check("月次締めの画面で締め前チェックが動き、C01・C02・C05・C09 が NG", sorted(checks) == ["C01", "C02", "C05", "C09"], ",".join(checks))
	page.get_by_role("button", name="確定").click()
	page.wait_for_timeout(800)
	yes = page.locator(".modal:visible .btn-primary").first
	if yes.count():
		yes.click()
	page.wait_for_timeout(3000)
	page.screenshot(path=f"{OUT}/10_month_close_rejected.png")
	check("エラーが残る月は確定できない（画面にエラーが出る）", "確定できません" in page.inner_text("body"))

	browser.close()

with open(f"{OUT}/results.json", "w", encoding="utf-8") as f:
	json.dump(results, f, ensure_ascii=False, indent=1)
sys.exit(0 if all(r["ok"] for r in results) else 1)
