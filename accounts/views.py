from django.shortcuts import render, redirect
from django.contrib import messages
from django.views.decorators.http import require_http_methods
from django.utils import timezone

from .forms import LoginForm, ChangePasswordForm
from .jwt_utils import issue_access_token, issue_refresh_token, set_auth_cookies, clear_auth_cookies
from .permissions import jwt_login_required
from .audit import log_action
from django.contrib.auth import get_user_model

User = get_user_model()


def _client_ip(request):
    forwarded = request.META.get("HTTP_X_FORWARDED_FOR")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.META.get("REMOTE_ADDR")


@require_http_methods(["GET", "POST"])
def login_view(request):
    if request.user.is_authenticated:
        return redirect("upload")

    form = LoginForm(request.POST or None)
    error = None
    locked_unlock_time = None

    if request.method == "POST" and form.is_valid():
        email    = form.cleaned_data["email"].lower().strip()
        password = form.cleaned_data["password"]
        ip       = _client_ip(request)

        try:
            user = User.objects.get(email=email)
        except User.DoesNotExist:
            log_action(None, action="login_failed", user=None,
                       detail=f"Unknown email attempted login: {email}")
            error = "Invalid email or password."
            user = None

        if user:
            if not user.is_active:
                log_action(request, action="login_failed", user=user,
                           detail="Login attempt on deactivated account")
                error = "Your account has been deactivated. Contact your administrator."

            elif user.is_locked():
                local_unlock_time = timezone.localtime(user.locked_until)
                log_action(request, action="login_failed", user=user,
                        detail="Login attempt while account locked")
                return render(request, "accounts/locked.html",
                            {"unlock_time": local_unlock_time.strftime("%I:%M %p on %d %b %Y")})
        
            elif user.check_password(password):
                user.record_successful_login(ip=ip)
                log_action(request, action="login_success", user=user,
                           detail=f"Successful login from {ip}")

                access_token  = issue_access_token(user)
                refresh_token = issue_refresh_token(user)

                next_url = request.GET.get("next") or ("change_password" if user.must_change_password else "upload")
                response = redirect(next_url)
                set_auth_cookies(response, access_token, refresh_token)
                return response

            else:
                user.record_failed_login()
                remaining = max(0, 5 - user.failed_login_attempts)
                log_action(request, action="login_failed", user=user,
                           detail=f"Incorrect password. {remaining} attempt(s) remaining before lock.")
                if user.is_locked():
                    log_action(request, action="account_locked", user=user,
                               detail="Account locked after 5 failed attempts")
                    return render(request, "accounts/locked.html",
                                  {"unlock_time": user.locked_until.strftime("%I:%M %p on %d %b %Y")})
                error = f"Invalid email or password. {remaining} attempt(s) remaining."

    return render(request, "accounts/login.html", {"form": form, "error": error})


@jwt_login_required
@require_http_methods(["GET", "POST"])
def logout_view(request):
    if request.method == "POST":
        log_action(request, action="logout", user=request.user, detail="User signed out")
        response = redirect("login")
        clear_auth_cookies(response)
        return response
    return render(request, "accounts/logout_confirm.html")


@jwt_login_required
@require_http_methods(["GET", "POST"])
def change_password_view(request):
    form = ChangePasswordForm(user=request.user, data=request.POST or None)

    if request.method == "POST" and form.is_valid():
        user = request.user
        user.set_password(form.cleaned_data["new_password"])
        user.must_change_password = False
        user.token_version += 1   # invalidate all existing refresh tokens (forces re-login everywhere)
        user.save(update_fields=["password", "must_change_password", "token_version"])

        log_action(request, action="password_changed", user=user, detail="User changed their own password")

        messages.success(request, "Password changed. Please log in again with your new password.")
        response = redirect("login")
        clear_auth_cookies(response)
        return response

    return render(request, "accounts/change_password.html", {"form": form})


# from django.shortcuts import render, redirect
# from django.contrib.auth import login, logout, get_user_model
# from django.contrib import messages
# from django.utils import timezone
# from django.views.decorators.http import require_http_methods
# from django.contrib.auth.decorators import login_required

# from .forms import LoginForm, ChangePasswordForm


# User = get_user_model()


# def get_client_ip(request):
#     x_forwarded = request.META.get("HTTP_X_FORWARDED_FOR")
#     if x_forwarded:
#         return x_forwarded.split(",")[0].strip()
#     return request.META.get("REMOTE_ADDR")


# @require_http_methods(["GET", "POST"])
# def login_view(request):
#     if request.user.is_authenticated:
#         return redirect("upload")

#     form = LoginForm(request.POST or None)
#     error = None

#     if request.method == "POST" and form.is_valid():
#         email    = form.cleaned_data["email"].lower().strip()
#         password = form.cleaned_data["password"]

#         try:
#             user = User.objects.get(email=email)
#         except User.DoesNotExist:
#             error = "Invalid email or password."
#         else:
#             if not user.is_active:
#                 error = "Your account has been deactivated. Contact your administrator."
#             elif user.is_locked():
#                 unlock_time = user.locked_until.strftime("%H:%M")
#                 error = f"Account locked due to too many failed attempts. Try after {unlock_time}."
#             else:
#                 if user.check_password(password):
#                     user.record_successful_login(ip=get_client_ip(request))
#                     login(request, user)
#                     if user.must_change_password:
#                         return redirect("change_password")
#                     return redirect(request.GET.get("next", "upload"))
#                 else:
#                     user.record_failed_login()
#                     remaining = max(0, 5 - user.failed_login_attempts)
#                     if user.is_locked():
#                         error = "Account locked for 30 minutes due to too many failed attempts."
#                     else:
#                         error = f"Invalid email or password. {remaining} attempt(s) remaining."

#     return render(request, "accounts/login.html", {"form": form, "error": error})

# @login_required
# @require_http_methods(["GET", "POST"])
# def logout_view(request):
#     if request.method == "POST":
#         logout(request)
#         messages.success(request, "You have been signed out successfully.")
#         return redirect("login")
#     return render(request, "accounts/logout_confirm.html")

# @login_required
# def change_password_view(request):
#     form = ChangePasswordForm(user=request.user, data=request.POST or None)

#     if request.method == "POST" and form.is_valid():
#         request.user.set_password(form.cleaned_data["new_password"])
#         request.user.must_change_password = False
#         request.user.save(update_fields=["password", "must_change_password"])
#         logout(request)
#         messages.success(request, "Password changed. Please log in with your new password.")
#         return redirect("login")

#     return render(request, "accounts/change_password.html", {"form": form})