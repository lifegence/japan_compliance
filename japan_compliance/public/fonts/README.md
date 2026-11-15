# 帳票用日本語フォント（LifegenceJP）

OS に日本語フォントがない環境（Docker のベンチ、Frappe Cloud／Press など）でも帳票の PDF に日本語が
正しく出るよう、アプリに同梱しているフォントです。`@font-face` の定義は `../css/jp_print_fonts.css` にあります。

## 使い方（帳票テンプレート）

- `jp_print_fonts.css` の `@font-face` 2 つを帳票の `<style>` にそのまま書き写し、`font-family` の先頭に
  `"LifegenceJP"` を置く（OS のフォント名は後ろに残す）。フッター（`#footer-html` の中）にも同じ
  `font-family` を当てる（このアプリの帳票では `class="jp-print-footer"`）。
- `<link>` で読み込まないのは、Frappe の wkhtmltopdf がヘッダー・フッター用の HTML に `<style>` しか
  引き継がないため。URL はサイトのルートからのパス（`/assets/japan_compliance/fonts/...`）で書き、
  `format()` は付けない（理由は CSS のコメント参照）。
- 他のアプリ（japan_hr_compliance など）からも同じ書き方で使える（japan_compliance がインストールされていること）。

| ファイル | 内容 |
| --- | --- |
| `LifegenceJP-Regular.ttf` | 標準（font-weight: normal） |
| `LifegenceJP-Bold.ttf` | 太字（font-weight: bold） |
| `OFL.txt` | SIL Open Font License 1.1 の全文と著作権表示 |

## 元のフォント

- Noto Sans CJK JP Regular／Bold、Version 2.004（noto-cjk 2022-01-27 版）
- 取得元：Ubuntu の `fonts-noto-cjk` パッケージ（1:20220127+repack1-1）の
  `/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc`・`NotoSansCJK-Bold.ttc` の JP フェイス
- 著作権：© 2014-2021 Adobe（http://www.adobe.com/）。ライセンス：SIL Open Font License 1.1

## 改変の内容

- **サブセット化**：Shift_JIS で表せる文字（7,491 字）に絞った。
  - cp932（Windows-31J）と JIS（Python の `shift_jis`）の両方のマッピングで、1 バイト・2 バイトの
    全コードを Unicode に変換した文字の和集合。ASCII、半角カナ、JIS X 0208 の非漢字・第1水準・第2水準、
    NEC 特殊文字（①〜⑳・㈱・№・㎡ など）、NEC 選定 IBM 拡張・IBM 拡張を含む。
    〜（U+301C）と ～（U+FF5E）、−（U+2212）と －（U+FF0D）のように両マッピングで対応先が異なる文字は両方入る。
    JIS X 0201 の ¥（U+00A5）も加えた。
  - 除外：外字領域（私用領域 U+E000〜）、制御文字。元フォントに字形のない ≒（U+2252）と ‾（U+203E）は収録されない
    （OS のフォントに任せる）。
  - OpenType の機能は fontTools の既定（カーニング・縦書き用の字形 vert／vrt2 など）＋ fwid／hwid。
    中国語・韓国語の字形を引き込む `locl` は除いた。ヒンティングは除いた。
- **形式の変換**：CFF（OTF）から TrueType（glyf）へ。wkhtmltopdf（patched Qt）の PDF 出力でも
  確実に埋め込まれるように、三次曲線を二次曲線に変換した（許容誤差 1 unit）。
- **名前の変更**：フォント名を `LifegenceJP` とした。

## 名前について（OFL の Reserved Font Name）

OFL 1.1 第3条により、改変版には Reserved Font Name を主たるフォント名として使えません。
noto-cjk の著作権表示は Reserved Font Name 'Source'（Source Han Sans 由来）を定めており、
また「Noto」は Google の商標です。改変版である本フォントはどちらも名前に使わず、
`LifegenceJP` としています（名前テーブルの説明欄にも「Noto の原版ではない」旨を記載）。
著作権表示（name ID 0）とライセンス（name ID 13・14）は元のまま残しています。

本フォントも OFL 1.1 の下で配布します（アプリ本体の GPL-3.0 とは別）。フォント単体での販売はできません。

## 再生成

```sh
python3 scripts/build_fonts.py            # 要 fontTools、元フォントは fonts-noto-cjk
python3 scripts/build_fonts.py --src-dir /path/to/noto
```
