from django import forms
from django.contrib.auth.password_validation import validate_password
from .models import User, Engagement


class LoginForm(forms.Form):
    email = forms.EmailField(widget=forms.EmailInput(attrs={
        "placeholder": "Email address", "autofocus": True, "autocomplete": "email",
    }))
    password = forms.CharField(widget=forms.PasswordInput(attrs={
        "placeholder": "Password", "autocomplete": "current-password",
    }))


class ChangePasswordForm(forms.Form):
    current_password = forms.CharField(widget=forms.PasswordInput(attrs={"placeholder": "Current password"}))
    new_password      = forms.CharField(widget=forms.PasswordInput(attrs={"placeholder": "New password"}))
    confirm_password  = forms.CharField(widget=forms.PasswordInput(attrs={"placeholder": "Confirm new password"}))

    def __init__(self, user, *args, **kwargs):
        self.user = user
        super().__init__(*args, **kwargs)

    def clean_current_password(self):
        pwd = self.cleaned_data.get("current_password")
        if not self.user.check_password(pwd):
            raise forms.ValidationError("Current password is incorrect.")
        return pwd

    def clean(self):
        cleaned = super().clean()
        p1, p2 = cleaned.get("new_password"), cleaned.get("confirm_password")
        if p1 and p2 and p1 != p2:
            raise forms.ValidationError("New passwords do not match.")
        if p1:
            validate_password(p1, self.user)
        return cleaned
    


class AdminCreateUserForm(forms.Form):
    email     = forms.EmailField(widget=forms.EmailInput(attrs={"placeholder": "user@company.com"}))
    full_name = forms.CharField(max_length=150, widget=forms.TextInput(attrs={"placeholder": "Full name"}))
    role      = forms.ChoiceField(choices=User.ROLE_CHOICES)

    def clean_email(self):
        email = self.cleaned_data["email"].lower().strip()
        if User.objects.filter(email=email).exists():
            raise forms.ValidationError("A user with this email already exists.")
        return email


class EngagementForm(forms.ModelForm):
    class Meta:
        model = Engagement
        fields = ["name", "cd_name", "cirp_number", "icd_date", "status"]
        widgets = {
            "name":        forms.TextInput(attrs={"placeholder": "e.g. Nirmal Lifestyle Ltd — CIRP"}),
            "cd_name":     forms.TextInput(attrs={"placeholder": "Corporate Debtor legal name"}),
            "cirp_number": forms.TextInput(attrs={"placeholder": "Case / CIRP number (optional)"}),
            "icd_date":    forms.DateInput(attrs={"type": "date"}),
        }