from django import forms
from .models import Account, Statement


class UploadForm(forms.Form):
    account = forms.ModelChoiceField(
        queryset=Account.objects.all(),
        help_text="Which Corporate Debtor account is this statement for?",
    )
    file = forms.FileField(
        help_text="Upload CSV, XLSX, PDF, image (jpg/png) or RPT file.",
    )

class AccountForm(forms.ModelForm):
    class Meta:
        model = Account
        fields = ["cd_name", "bank_name", "account_number", "is_cd_account"]
        widgets = {
            "cd_name": forms.TextInput(attrs={"placeholder": "Corporate Debtor name"}),
            "bank_name": forms.TextInput(attrs={"placeholder": "e.g. Punjab National Bank"}),
            "account_number": forms.TextInput(attrs={"placeholder": "Account number"}),
        }