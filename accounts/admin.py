from django.contrib import admin
from django.contrib.auth.admin import UserAdmin as BaseUserAdmin
from .models import User, Engagement, EngagementAssignment, AuditLog, RoleChangeLog


@admin.register(User)
class UserAdmin(BaseUserAdmin):
    list_display = ("email", "full_name", "role", "is_active",
                     "must_change_password", "failed_login_attempts", "last_login_ip")
    list_filter  = ("role", "is_active", "must_change_password")
    search_fields = ("email", "full_name")
    ordering = ("email",)
    readonly_fields = ("last_login", "last_login_ip", "failed_login_attempts",
                       "locked_until", "created_at", "token_version")

    fieldsets = (
        ("Identity", {"fields": ("email", "full_name", "password")}),
        ("Role",     {"fields": ("role", "is_active", "is_staff", "is_superuser")}),
        ("Security", {"fields": ("must_change_password", "failed_login_attempts",
                                  "locked_until", "last_login_ip", "token_version")}),
        ("Audit",    {"fields": ("created_by", "created_at", "last_login")}),
    )
    add_fieldsets = (
        (None, {"fields": ("email", "full_name", "role", "password1", "password2")}),
    )


@admin.register(Engagement)
class EngagementAdmin(admin.ModelAdmin):
    list_display = ("name", "cd_name", "cirp_number", "status", "created_at", "created_by")
    list_filter  = ("status",)
    search_fields = ("name", "cd_name", "cirp_number")


@admin.register(EngagementAssignment)
class EngagementAssignmentAdmin(admin.ModelAdmin):
    list_display = ("user", "engagement", "assigned_at", "assigned_by")
    list_filter  = ("engagement",)


@admin.register(AuditLog)
class AuditLogAdmin(admin.ModelAdmin):
    """View-only — append-only enforced at model layer, but also lock it
    down here so admins can't even attempt edits/deletes through the UI."""
    list_display  = ("timestamp", "user_email_snapshot", "action", "detail", "engagement", "ip_address")
    list_filter   = ("action", "engagement")
    search_fields = ("user_email_snapshot", "detail")
    ordering      = ("-timestamp",)

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(RoleChangeLog)
class RoleChangeLogAdmin(admin.ModelAdmin):
    list_display = ("timestamp", "target_user", "change_type", "prior_value", "new_value", "changed_by")
    list_filter  = ("change_type",)
    ordering     = ("-timestamp",)

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False

# from django.contrib import admin
# from django.contrib.auth.admin import UserAdmin as BaseUserAdmin
# from .models import User


# @admin.register(User)
# class UserAdmin(BaseUserAdmin):
#     list_display  = ("email", "full_name", "role", "is_active",
#                      "must_change_password", "failed_login_attempts", "last_login_ip")
#     list_filter   = ("role", "is_active", "must_change_password")
#     search_fields = ("email", "full_name")
#     ordering      = ("email",)
#     readonly_fields = ("last_login", "last_login_ip", "failed_login_attempts",
#                        "locked_until", "created_at")

#     fieldsets = (
#         ("Identity",  {"fields": ("email", "full_name", "password")}),
#         ("Role",      {"fields": ("role", "is_active", "is_staff", "is_superuser")}),
#         ("Security",  {"fields": ("must_change_password", "failed_login_attempts",
#                                   "locked_until", "last_login_ip")}),
#         ("Audit",     {"fields": ("created_by", "created_at", "last_login")}),
#     )
#     add_fieldsets = (
#         (None, {"fields": ("email", "full_name", "role", "password1", "password2")}),
#     )