# アーキテクチャ方針

## 1. 層構造

```
Frappe Framework (MIT)
  └─ ERPNext (GPLv3)
       ├─ japan_compliance     ← 本アプリ。会計・税務・法定帳簿
       ├─ japan_banking        ← 全銀・入金消込（予定）
       ├─ hrms
       │    └─ japan_hr_compliance ← 人事労務・社会保険・給与・源泉（別リポジトリ）
       └─ japan_connectors     ← 国内会計ソフト・e-Tax 連携（予定）
```

## 2. 依存の向きに関する原則

**`japan_*` は特定企業の固有アプリに依存しない。**

| 向き | 可否 | 理由 |
| :--- | :-: | :--- |
| 固有アプリ → `japan_compliance` | 可 | AI 機能や帳票基盤が制度アプリを利用する |
| `japan_compliance` → 固有アプリ | **不可** | 誰でも単独で導入できる状態を保つ |

## 3. モジュール境界

制度に由来するものと、個別企業の運用に由来するものを分ける。

| 区分 | 置き場所 |
| :--- | :--- |
| 日本の法令・会計基準に由来するもの | `japan_compliance` および `japan_*` 各アプリ |
| 個別企業の運用・ワークフローに由来するもの | 各社の固有アプリ（例: 稟議・押印申請） |

## 4. 命名

- リポジトリ・アプリのモジュール名とも `japan_compliance`（アンダースコア）

`required_apps` は Frappe 本体がアプリ名で、Press と bench が GitHub のリポジトリ名で照合するため、
両者を一致させる（frappe/hrms、frappe/erpnext と同じ形）。

## 5. ライセンス

GPL-3.0-or-later。ERPNext が GPLv3 であり、本アプリはその DocType に依存する派生物と
なるため。
