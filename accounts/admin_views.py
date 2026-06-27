"""
System Administrator screens — user management, password resets, engagement
creation and assignment. All views are gated by @role_required("admin").
Every state-changing action logs to AuditLog; role/assignment changes also
log to RoleChangeLog with prior_value/new_value.
"""
import secrets
import string

from django.shortcuts import render, redirect, get_object_or_404
from django.contrib import messages
from django.views.decorators.http import require_http_methods
from django.core.paginator import Paginator

from .models import User, Engagement, EngagementAssignment, AuditLog, RoleChangeLog
from .permissions import role_required
from .audit import log_action
from .forms import AdminCreateUserForm, EngagementForm
from statements.models import Account
from statements.forms import AccountForm 

def _generate_temp_password(length=14) -> str:
    alphabet = string.ascii_letters + string.digits + "!@#$%^&*"
    return "".join(secrets.choice(alphabet) for _ in range(length))


# ── User management ────────────────────────────────────────────────────────

@role_required("admin")
@require_http_methods(["GET", "POST"])
def user_list_view(request):
    users = User.objects.all().order_by("-created_at")
    form = AdminCreateUserForm(request.POST or None)
    
    show_user_modal = False
    temp_password = None
    created_user_obj = None

    if request.method == "POST":
        if form.is_valid():
            temp_password = _generate_temp_password()
            user = User(
                email=form.cleaned_data["email"].lower().strip(),
                full_name=form.cleaned_data["full_name"],
                role=form.cleaned_data["role"],
                must_change_password=True,
                created_by=request.user,
            )
            user.set_password(temp_password)
            user.save()

            log_action(
                request, 
                action="user_created", 
                user=request.user,
                detail=f"Created user {user.email} with role '{user.role}'"
            )
            RoleChangeLog.objects.create(
                target_user=user, change_type="role",
                prior_value="", new_value=user.role,
                changed_by=request.user,
            )

            # Keep these in session context to display on page reload modal frame
            request.session['new_user_pwd'] = temp_password
            request.session['new_user_email'] = user.email
            
            messages.success(request, f"User {user.email} created successfully.")
            return redirect("admin_user_list")
        else:
            show_user_modal = True

    # Check if a temporary password needs to be shown instantly via session pop
    pop_pwd = request.session.pop('new_user_pwd', None)
    pop_email = request.session.pop('new_user_email', None)

    context = {
        "users": users,
        "form": form,
        "show_user_modal": show_user_modal,
        "display_temp_pwd": pop_pwd,
        "display_user_email": pop_email,
    }
    return render(request, "accounts/admin/user_list.html", context)

@role_required("admin")
@require_http_methods(["GET", "POST"])
def user_create_view(request):
    form = AdminCreateUserForm(request.POST or None)
    temp_password = None

    if request.method == "POST" and form.is_valid():
        temp_password = _generate_temp_password()
        user = User(
            email=form.cleaned_data["email"].lower().strip(),
            full_name=form.cleaned_data["full_name"],
            role=form.cleaned_data["role"],
            must_change_password=True,
            created_by=request.user,
        )
        user.set_password(temp_password)
        user.save()

        log_action(request, action="user_created", user=request.user,
                   detail=f"Created user {user.email} with role '{user.role}'")
        RoleChangeLog.objects.create(
            target_user=user, change_type="role",
            prior_value="", new_value=user.role,
            changed_by=request.user,
        )

        messages.success(request,
            f"User {user.email} created. Temporary password (shown once): {temp_password}")
        return render(request, "accounts/admin/user_created.html", {
            "user_obj": user, "temp_password": temp_password,
        })

    return render(request, "accounts/admin/user_form.html", {"form": form, "mode": "create"})


@role_required("admin")
def user_detail_view(request, user_id):
    target = get_object_or_404(User, id=user_id)

    if request.method == "POST":
        new_role = request.POST.get("role")
        if new_role and new_role != target.role and new_role in dict(User.ROLE_CHOICES):
            prior_role = target.role
            target.role = new_role
            target.save(update_fields=["role"])

            RoleChangeLog.objects.create(
                target_user=target, change_type="role",
                prior_value=prior_role, new_value=new_role,
                changed_by=request.user,
            )
            log_action(request, action="role_changed", user=request.user,
                       detail=f"Changed role of {target.email} from '{prior_role}' to '{new_role}'")
            messages.success(request, f"Role updated to {target.get_role_display()}.")
            return redirect("admin_user_detail", user_id=target.id)

    assignments = EngagementAssignment.objects.filter(user=target).select_related("engagement")
    role_history = RoleChangeLog.objects.filter(target_user=target).order_by("-timestamp")[:20]
    recent_activity = AuditLog.objects.filter(user=target).order_by("-timestamp")[:20]
    reset_temp_pwd = request.session.pop('reset_temp_pwd', None)

    return render(request, "accounts/admin/user_detail.html", {
        "target": target,
        "assignments": assignments,
        "role_history": role_history,
        "recent_activity": recent_activity,
        "all_roles": User.ROLE_CHOICES,
        "reset_temp_pwd": reset_temp_pwd,
    })

@role_required("admin")
@require_http_methods(["POST"])
def admin_reset_password_view(request, user_id):
    target = get_object_or_404(User, id=user_id)
    temp_password = _generate_temp_password()
    
    target.set_password(temp_password)
    target.must_change_password = True
    target.token_version += 1
    target.save(update_fields=["password", "must_change_password", "token_version"])

    log_action(
        request, 
        action="password_reset", 
        user=request.user,
        detail=f"Admin reset password for {target.email}"
    )

    request.session['reset_temp_pwd'] = temp_password    
    messages.success(request, f"Password reset successfully for {target.email}.")
    return redirect("admin_user_detail", user_id=target.id)

@role_required("admin")
@require_http_methods(["POST"])
def user_deactivate_view(request, user_id):
    target = get_object_or_404(User, id=user_id)
    if target.id == request.user.id:
        messages.error(request, "You cannot deactivate your own account.")
        return redirect("admin_user_detail", user_id=user_id)

    target.is_active = False
    target.token_version += 1   # kill active sessions
    target.save(update_fields=["is_active", "token_version"])

    log_action(request, action="user_deactivated", user=request.user,
               detail=f"Deactivated user {target.email}")
    messages.success(request, f"{target.email} has been deactivated.")
    return redirect("admin_user_detail", user_id=user_id)


@role_required("admin")
@require_http_methods(["POST"])
def user_reactivate_view(request, user_id):
    target = get_object_or_404(User, id=user_id)
    target.is_active = True
    target.failed_login_attempts = 0
    target.locked_until = None
    target.save(update_fields=["is_active", "failed_login_attempts", "locked_until"])

    log_action(request, action="user_reactivated", user=request.user,
               detail=f"Reactivated user {target.email}")
    messages.success(request, f"{target.email} has been reactivated.")
    return redirect("admin_user_detail", user_id=user_id)


# ── Engagement management ──────────────────────────────────────────────────
@role_required("admin")
@require_http_methods(["GET", "POST"])
def engagement_list_view(request):
    # engagements = Engagement.objects.all().order_by("-created_at")
    engagements = Engagement.objects.prefetch_related(
            "assignments"
        ).order_by("-created_at")
    for engagement in engagements:
        engagement.selected_user_ids = list(
            engagement.assignments.values_list(
                "user_id", flat=True
            )
        )
    
    form = EngagementForm(request.POST or None)
    show_modal = False

    if request.method == "POST":
        if form.is_valid():
            engagement = form.save(commit=False)
            engagement.created_by = request.user
            engagement.save()

            log_action(
                request, 
                action="engagement_created", 
                user=request.user, 
                engagement=engagement,
                detail=f"Created engagement '{engagement.name}'"
            )
            messages.success(request, f"Engagement '{engagement.name}' created.")
            return redirect("admin_engagement_list")
        else:
            show_modal = True

    context = {
        "engagements": engagements,
        "all_users": User.objects.filter(is_active=True).order_by("full_name"),
        "form": form,
        "show_modal": show_modal,
        "account_form": AccountForm(),
    }
    return render(request, "accounts/admin/engagement_list.html", context)

@role_required("admin")
@require_http_methods(["GET", "POST"])
def engagement_create_view(request):
    form = EngagementForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        engagement = form.save(commit=False)
        engagement.created_by = request.user
        engagement.save()

        log_action(request, action="engagement_created", user=request.user, engagement=engagement,
                   detail=f"Created engagement '{engagement.name}'")
        messages.success(request, f"Engagement '{engagement.name}' created.")
        return redirect("admin_engagement_list")

    return render(request, "accounts/admin/engagement_form.html", {"form": form})

@role_required("admin")
@require_http_methods(["GET", "POST"])
def engagement_assign_view(request, engagement_id):
    engagement = get_object_or_404(Engagement, id=engagement_id)
    assigned_user_ids = set(
        EngagementAssignment.objects.filter(engagement=engagement).values_list("user_id", flat=True)
    )

    if request.method == "POST":
        selected_ids = set(int(x) for x in request.POST.getlist("user_ids"))

        to_add = selected_ids - assigned_user_ids
        to_remove = assigned_user_ids - selected_ids

        for uid in to_add:
            user = User.objects.get(id=uid)
            EngagementAssignment.objects.create(
                user=user, engagement=engagement, assigned_by=request.user
            )
            RoleChangeLog.objects.create(
                target_user=user, change_type="engagement_assigned",
                prior_value="", new_value=engagement.name,
                changed_by=request.user,
            )
            log_action(request, action="engagement_assigned", user=request.user, engagement=engagement,
                       detail=f"Assigned {user.email} to engagement '{engagement.name}'")

        for uid in to_remove:
            user = User.objects.get(id=uid)
            EngagementAssignment.objects.filter(user=user, engagement=engagement).delete()
            RoleChangeLog.objects.create(
                target_user=user, change_type="engagement_unassigned",
                prior_value=engagement.name, new_value="",
                changed_by=request.user,
            )
            log_action(request, action="engagement_unassigned", user=request.user, engagement=engagement,
                       detail=f"Removed {user.email} from engagement '{engagement.name}'")

        messages.success(request, "Engagement assignments updated.")
        return redirect("admin_engagement_list")
    return redirect("admin_engagement_list")

# ── Audit log viewer ────────────────────────────────────────────────────────

@role_required("admin", "reviewer")
def audit_log_view(request):
    entries = AuditLog.objects.select_related("user", "engagement").order_by("-timestamp")

    action_filter = request.GET.get("action", "").strip()
    user_filter   = request.GET.get("user", "").strip()

    if action_filter:
        entries = entries.filter(action=action_filter)
    if user_filter:
        entries = entries.filter(user_email_snapshot__icontains=user_filter)

    paginator = Paginator(entries, 100)
    page = paginator.get_page(request.GET.get("page", 1))

    return render(request, "accounts/admin/audit_log.html", {
        "entries": page,
        "action_choices": AuditLog.ACTION_CHOICES,
    })

@role_required("admin")
@require_http_methods(["GET", "POST"])
def engagement_add_account_view(request, engagement_id):
    engagement = get_object_or_404(Engagement, id=engagement_id)
    form = AccountForm(request.POST or None)

    if request.method == "POST":
        if form.is_valid():
            account = form.save(commit=False)
            account.engagement = engagement
            account.save()

            log_action(
                request, 
                action="engagement_created", 
                user=request.user, 
                engagement=engagement,
                detail=f"Added bank account {account.bank_name} ({account.account_number}) to '{engagement.name}'"
            )
            messages.success(request, f"Account '{account}' added to {engagement.name}.")
            return redirect("admin_engagement_list")
        else:
            # Construct a clear validation error string to display as a message banner
            error_msg = " ".join([f"{field}: {err[0]}" for field, err in form.errors.items()])
            messages.error(request, f"Failed to add account: {error_msg}")
            return redirect("admin_engagement_list")

    # Fallback view (if hit via standard GET request URL)
    return redirect("admin_engagement_list")

# @role_required("admin")
# @require_http_methods(["GET", "POST"])
# def engagement_add_account_view(request, engagement_id):
#     engagement = get_object_or_404(Engagement, id=engagement_id)
#     form = AccountForm(request.POST or None)

#     if request.method == "POST" and form.is_valid():
#         account = form.save(commit=False)
#         account.engagement = engagement
#         account.save()

#         log_action(request, action="engagement_created", user=request.user, engagement=engagement,
#                    detail=f"Added bank account {account.bank_name} ({account.account_number}) to '{engagement.name}'")
#         messages.success(request, f"Account '{account}' added to {engagement.name}.")
#         return redirect("admin_engagement_list")

#     return render(request, "accounts/admin/engagement_add_account.html", {
#         "form": form, "engagement": engagement,
#     })  