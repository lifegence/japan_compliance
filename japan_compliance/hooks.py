app_name = "japan_compliance"
app_title = "Japan Compliance"
app_publisher = "Lifegence Corporation"
app_description = (
    "Japanese accounting, consumption tax, qualified invoice and "
    "statutory bookkeeping compliance for ERPNext"
)
app_email = "contact@lifegence.com"
app_license = "GPL-3.0-or-later"

# ERPNext の会計 DocType（Account / Sales Invoice / Journal Entry /
# Tax Template / Period Closing Voucher）に依存するため必須とする。
required_apps = ["frappe/erpnext"]

# --- 依存の向きに関する原則 -------------------------------------------------
# japan_compliance は特定企業の固有アプリに依存しない。
# 固有アプリから本アプリを参照するのは可。逆向きの依存を追加しないこと。
# 詳細は docs/ARCHITECTURE.md を参照。
# ---------------------------------------------------------------------------

after_install = "japan_compliance.install.after_install"
after_migrate = "japan_compliance.install.after_migrate"
before_uninstall = "japan_compliance.install.before_uninstall"

# 日本式勘定科目テンプレートを ERPNext の選択肢に加える
try:
    from japan_compliance.jp_core.chart import patch_erpnext

    patch_erpnext()
except ImportError:
    pass

fixtures = []

override_whitelisted_methods = {
    "erpnext.accounts.doctype.account.chart_of_accounts.chart_of_accounts.get_charts_for_country":
        "japan_compliance.jp_core.chart.get_charts_for_country",
}

jinja = {
    "methods": [
        "japan_compliance.jp_form.formatting.jp_yen",
        "japan_compliance.jp_form.formatting.jp_date",
        "japan_compliance.jp_form.formatting.jp_rate_label",
        "japan_compliance.jp_form.formatting.is_reduced",
    ]
}

# 税額計算（伝票 × 税率区分ごとに1回の端数処理）を日本の方式に置き換える
override_doctype_class = {
    "Sales Invoice": "japan_compliance.jp_tax.overrides.JPSalesInvoice",
    "Purchase Invoice": "japan_compliance.jp_tax.overrides.JPPurchaseInvoice",
}

_deny_submitted_delete = {"on_trash": "japan_compliance.jp_audit.guards.deny_submitted_delete"}

doc_events = {
    "Company": {
        "before_insert": "japan_compliance.jp_core.chart.ensure_patched",
        "after_insert": "japan_compliance.jp_core.company.after_company_insert",
        "on_update": "japan_compliance.jp_core.company.company_on_update",
        "validate": "japan_compliance.jp_close.doctype.jp_month_close.jp_month_close.company_validate",
    },
    "Customer": {"validate": "japan_compliance.jp_invoice.registration.party_validate"},
    "Supplier": {"validate": "japan_compliance.jp_invoice.registration.party_validate"},
    "Accounts Settings": {"validate": "japan_compliance.jp_audit.guards.deny_row_wise_rounding"},
    "Version": {"on_trash": "japan_compliance.jp_audit.guards.deny_version_delete"},
    "Transaction Deletion Record": {"before_insert": "japan_compliance.jp_audit.guards.deny_transaction_deletion"},
    "Sales Invoice": {
        **_deny_submitted_delete,
        "before_validate": "japan_compliance.jp_close.close.amended_posting_date",
        "validate": "japan_compliance.jp_invoice.invoice.sales_invoice_validate",
        "on_submit": "japan_compliance.jp_invoice.invoice.attach_invoice_copy",
    },
    "File": {"on_trash": "japan_compliance.jp_invoice.invoice.deny_invoice_copy_delete"},
    "Purchase Invoice": {
        **_deny_submitted_delete,
        "before_validate": "japan_compliance.jp_close.close.amended_posting_date",
        "validate": [
            "japan_compliance.jp_tax.journal.validate_special_case",
            "japan_compliance.jp_audit.permissions.enforce_workflow_edit",
        ],
    },
    "Journal Entry": {
        **_deny_submitted_delete,
        "before_validate": "japan_compliance.jp_close.close.amended_posting_date",
        "validate": [
            "japan_compliance.jp_tax.journal.journal_entry_validate",
            "japan_compliance.jp_audit.permissions.enforce_workflow_edit",
        ],
    },
    "Payment Entry": {**_deny_submitted_delete, "validate": "japan_compliance.jp_tax.journal.payment_entry_validate"},
    "Period Closing Voucher": _deny_submitted_delete,
}

scheduler_events = {
    "daily": [
        "japan_compliance.install.protect_versions",
        "japan_compliance.jp_close.close.notify_overdue_close",
    ],
}
