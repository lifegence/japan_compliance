# Japan Compliance

ERPNext 向けの日本会計ローカライズアプリ。日本の会計実務・消費税・インボイス制度・
法定帳簿および決算に対応する。

ERPNext のインド税制対応で事実上の標準となっている
[`india_compliance`](https://github.com/resilient-tech/india-compliance)（Resilient Tech）と
同じ位置づけの、日本版コンプライアンスアプリである。

## 対象範囲

| モジュール | 領域ID | 内容 | 状態 |
| :--- | :--- | :--- | :--- |
| JP Core | JP-CORE | 日本式勘定科目・会計基盤 | 対応 |
| JP Tax | JP-TAX | 消費税・税区分・端数処理 | 対応 |
| JP Invoice | JP-INVOICE | インボイス制度・適格請求書 | 対応 |
| JP Form | JP-FORM | 日本式請求書・帳簿・財務帳票 | 対応 |
| JP Close | JP-CLOSE | 月次・年次決算・期間ロック | 対応 |
| JP Audit | JP-AUDIT | 監査証跡・訂正削除履歴 | 対応 |
| （予定） | JP-EBOOK | 電子帳簿保存法・証憑管理 | 将来の版で対応予定 |
| （予定） | JP-ASSET | 固定資産・減価償却（日本税法） | 将来の版で対応予定 |

機能の一覧は [CHANGELOG.md](CHANGELOG.md) を参照。

本アプリの対象外（別アプリ）:

| アプリ | 領域ID | 内容 |
| :--- | :--- | :--- |
| [`japan_hr_compliance`](https://github.com/lifegence/japan_hr_compliance) | JP-WHT ほか | 人事労務（雇用契約・社会保険・休暇・給与・源泉所得税）。HRMS に依存 |
| `japan_banking` | JP-BANK | 全銀フォーマット・入金消込・手形・でんさい |
| `japan_connectors` | JP-INTEGRATION / JP-TAXFILE | 国内会計ソフト連携・e-Tax / eLTAX |
| `frappe_japanese_translations` | — | 翻訳（フレームワーク層・公開済） |
| `frappe-i18n-patches` | — | カレンダー・日付書式（フレームワーク層・公開済） |

## 設計方針

1. ERPNext Core を直接改修せず、本アプリ側で完結させる
2. 税率・税区分・控除率は Effective From / To を持つマスタとして管理し、コードへハードコードしない
3. 帳票は ERPNext 標準の Print Format（Jinja）で実装する
4. **`japan_compliance` は特定企業の固有アプリに依存しない**

依存の向きについては [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) を参照。

## 必要環境

- Frappe Framework v16
- ERPNext v16
- 日本語フォントは不要（帳票用のフォント LifegenceJP を同梱。PDF 生成時に wkhtmltopdf／Chrome がサイトの `/assets/` から読み込むため、サーバーから自サイトの URL に接続できること）

v15 では検証していません。

## インストール

```bash
bench get-app https://github.com/lifegence/japan_compliance
bench --site <site> install-app japan_compliance
```

インストールすると、サイト全体の次の設定を変更します。

- Immutable Ledger を有効にする（取消の反対仕訳を取消日に計上する）
- 行単位の税額の丸めを無効にする
- 円の表示を小数なしにする

## テスト

```bash
bench --site <site> set-config allow_tests true
bench --site <site> run-tests --app japan_compliance
```

PDF を確認するテストがあるため、`bench serve` を起動した状態で実行してください。

## 状態

1.0.0 です。公認会計士による検証（108 項目：差異なし 106、対象外 2）を完了しています。

## 免責

- 本アプリおよび付属の文書は、法的・税務上・会計上の助言ではありません。
- 本アプリの計算結果・帳票・チェック結果の正確性、完全性、特定の目的への適合性は保証しません
  （GPL-3.0 第15条・第16条のとおり、無保証で提供します）。
- 税率・経過措置などのマスタは作成時点の法令に基づきます。法令の改正や個別の事情には対応していない場合があります。
- 会計処理と申告の内容は利用者の責任で判断し、必要に応じて税理士・公認会計士に確認してください。
- 本アプリの利用により生じた損害について、作者および貢献者は責任を負いません。

## コード中の ID について

コードやテストのコメントにある ID（`INV-46`、`FORM-31`、`C-1`、`B-6`、`TS-07-04`、`E-2`、`L-30` など）は、
開発時の要件定義・テスト計画の番号です。法令上の番号ではありません。

## 貢献・脆弱性の報告

[CONTRIBUTING.md](CONTRIBUTING.md) と [SECURITY.md](SECURITY.md) を参照してください。

## ライセンス

GPL-3.0-or-later。ERPNext（GPLv3）に依存するため。

同梱フォント（`japan_compliance/public/fonts/LifegenceJP-*.ttf`）は Noto Sans CJK JP の改変版で、
SIL Open Font License 1.1 です（[OFL.txt](japan_compliance/public/fonts/OFL.txt)、[README](japan_compliance/public/fonts/README.md)）。
