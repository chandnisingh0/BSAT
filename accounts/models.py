from django.contrib.auth.models import AbstractBaseUser, BaseUserManager, PermissionsMixin
from django.db import models
from django.utils import timezone

class UserManager(BaseUserManager):
    def create_user(self, email, password=None, **extra):
        if not email:
            raise ValueError("Email is required")
        email = self.normalize_email(email)
        user = self.model(email=email, **extra)
        user.set_password(password)
        user.save(using=self._db)
        return user

    def create_superuser(self, email, password=None, **extra):
        extra.setdefault("is_staff", True)
        extra.setdefault("is_superuser", True)
        extra.setdefault("role", "admin")
        extra.setdefault("must_change_password", False)
        return self.create_user(email, password, **extra)

class User(AbstractBaseUser, PermissionsMixin):
    ROLE_CHOICES = [
        ("admin",    "System Administrator"),
        ("reviewer", "Reviewer"),
        ("analyst",  "Analyst"),
    ]

    email     = models.EmailField(unique=True)
    full_name = models.CharField(max_length=150)
    role      = models.CharField(max_length=20, choices=ROLE_CHOICES, default="analyst")

    is_active = models.BooleanField(default=True)
    is_staff  = models.BooleanField(default=False)

    must_change_password   = models.BooleanField(default=True)
    failed_login_attempts  = models.IntegerField(default=0)
    locked_until            = models.DateTimeField(null=True, blank=True)
    last_login_ip            = models.GenericIPAddressField(null=True, blank=True)

    token_version = models.IntegerField(default=0)

    created_at = models.DateTimeField(auto_now_add=True)
    created_by = models.ForeignKey(
        "self", null=True, blank=True,
        on_delete=models.SET_NULL, related_name="created_users"
    )

    groups = models.ManyToManyField(
        "auth.Group", blank=True, related_name="accounts_user_set", verbose_name="groups"
    )
    user_permissions = models.ManyToManyField(
        "auth.Permission", blank=True, related_name="accounts_user_set", verbose_name="user permissions"
    )

    USERNAME_FIELD  = "email"
    REQUIRED_FIELDS = ["full_name"]

    objects = UserManager()

    def is_locked(self) -> bool:
        return bool(self.locked_until and timezone.now() < self.locked_until)

    def record_failed_login(self):
        self.failed_login_attempts += 1
        if self.failed_login_attempts >= 5:
            self.locked_until = timezone.now() + timezone.timedelta(minutes=30)
        self.save(update_fields=["failed_login_attempts", "locked_until"])

    def record_successful_login(self, ip=None):
        self.failed_login_attempts = 0
        self.locked_until = None
        self.last_login_ip = ip
        self.save(update_fields=["failed_login_attempts", "locked_until", "last_login_ip"])

    def is_admin(self) -> bool:
        return self.role == "admin"

    def is_reviewer(self) -> bool:
        return self.role == "reviewer"

    def is_analyst(self) -> bool:
        return self.role == "analyst"

    def __str__(self):
        return f"{self.full_name} <{self.email}> [{self.role}]"


class Engagement(models.Model):
    """
    One Engagement = one CIRP case / one Corporate Debtor under resolution.
    All Accounts, Statements, and Transactions belong to exactly one Engagement.
    """
    STATUS_CHOICES = [
        ("active",   "Active"),
        ("on_hold",  "On Hold"),
        ("closed",   "Closed"),
    ]

    name            = models.CharField(max_length=255)
    cd_name         = models.CharField("Corporate Debtor name", max_length=255)
    cirp_number     = models.CharField("CIRP / Case number", max_length=100, blank=True)
    icd_date        = models.DateField("Insolvency Commencement Date", null=True, blank=True)
    status          = models.CharField(max_length=20, choices=STATUS_CHOICES, default="active")
    created_at      = models.DateTimeField(auto_now_add=True)
    created_by      = models.ForeignKey(
        User, null=True, blank=True, on_delete=models.SET_NULL, related_name="engagements_created"
    )

    def __str__(self):
        return self.name


class EngagementAssignment(models.Model):
    """
    Join table — which users can access which engagements.
    Cross-engagement access is impossible without a row here.
    """
    user        = models.ForeignKey(User, on_delete=models.CASCADE, related_name="engagement_assignments")
    engagement  = models.ForeignKey(Engagement, on_delete=models.CASCADE, related_name="assignments")
    assigned_at = models.DateTimeField(auto_now_add=True)
    assigned_by = models.ForeignKey(
        User, null=True, blank=True, on_delete=models.SET_NULL, related_name="assignments_made"
    )

    class Meta:
        unique_together = ("user", "engagement")

    def __str__(self):
        return f"{self.user.email} → {self.engagement.name}"


class AuditLog(models.Model):
    """
    Append-only. No view, model, or admin action may UPDATE or DELETE rows here.
    """
    ACTION_CHOICES = [
        ("login_success",   "Login success"),
        ("login_failed",     "Login failed"),
        ("logout",           "Logout"),
        ("account_locked",   "Account locked"),
        ("password_changed", "Password changed"),
        ("password_reset",   "Password reset (admin-triggered)"),
        ("user_created",     "User created"),
        ("user_deactivated", "User deactivated"),
        ("user_reactivated", "User reactivated"),
        ("upload",           "Statement upload"),
        ("transaction_view", "Transaction view"),
        ("transaction_edit", "Transaction correction"),
        ("export",           "Export"),
        ("report_generated", "Report generated"),
        ("engagement_created",   "Engagement created"),
        ("engagement_assigned",  "Engagement assignment"),
        ("engagement_unassigned","Engagement unassignment"),
        ("role_changed",     "Role changed"),
        ("access_denied",    "Access denied"),
    ]

    user         = models.ForeignKey(User, null=True, blank=True, on_delete=models.SET_NULL, related_name="audit_entries")
    user_email_snapshot = models.CharField(max_length=255, blank=True)  # survives even if user is later deleted
    action       = models.CharField(max_length=40, choices=ACTION_CHOICES)
    detail       = models.TextField(blank=True)
    engagement   = models.ForeignKey(Engagement, null=True, blank=True, on_delete=models.SET_NULL, related_name="audit_entries")
    ip_address   = models.GenericIPAddressField(null=True, blank=True)
    timestamp    = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-timestamp"]
        indexes = [
            models.Index(fields=["user", "timestamp"]),
            models.Index(fields=["action", "timestamp"]),
        ]

    def save(self, *args, **kwargs):
        # Enforce append-only at the model layer: block any update to an existing row.
        if self.pk is not None:
            raise PermissionError("AuditLog entries are append-only and cannot be modified.")
        if self.user and not self.user_email_snapshot:
            self.user_email_snapshot = self.user.email
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise PermissionError("AuditLog entries cannot be deleted.")

    def __str__(self):
        return f"[{self.timestamp:%Y-%m-%d %H:%M}] {self.user_email_snapshot} — {self.action}"


class RoleChangeLog(models.Model):
    """
    Append-only. Records every change to a user's role or engagement assignment.
    """
    CHANGE_TYPE = [
        ("role",                 "Role change"),
        ("engagement_assigned",  "Engagement assigned"),
        ("engagement_unassigned","Engagement unassigned"),
    ]

    target_user  = models.ForeignKey(User, on_delete=models.CASCADE, related_name="role_change_entries")
    change_type  = models.CharField(max_length=30, choices=CHANGE_TYPE)
    prior_value  = models.CharField(max_length=255, blank=True)
    new_value    = models.CharField(max_length=255, blank=True)
    changed_by   = models.ForeignKey(User, null=True, blank=True, on_delete=models.SET_NULL, related_name="role_changes_made")
    timestamp    = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-timestamp"]

    def save(self, *args, **kwargs):
        if self.pk is not None:
            raise PermissionError("RoleChangeLog entries are append-only and cannot be modified.")
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise PermissionError("RoleChangeLog entries cannot be deleted.")

    def __str__(self):
        return f"{self.target_user.email}: {self.change_type} ({self.prior_value} → {self.new_value})"