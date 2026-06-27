from django.contrib import admin
from .models import Account, Statement, Transaction


@admin.register(Account)
class AccountAdmin(admin.ModelAdmin):
    list_display = ("cd_name", "bank_name", "account_number", "is_cd_account")
    search_fields = ("cd_name", "bank_name", "account_number")


@admin.register(Statement)
class StatementAdmin(admin.ModelAdmin):
    list_display = ("original_filename", "account", "file_type", "rows_extracted", "uploaded_at")
    list_filter = ("file_type", "account")

@admin.register(Transaction)
class TransactionAdmin(admin.ModelAdmin):
    list_display = (
        "txn_date", "txn_mode", "counterparty_name",
        "narration_raw", "debit", "credit", "balance",
        "balance_type", "reference", "statement",
    )
    list_filter = ("statement__account", "statement", "txn_mode", "balance_type")
    search_fields = ("narration_raw", "reference", "counterparty_name")