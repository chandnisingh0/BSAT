"""
Authorization decorators built on top of request.user (set by JWTAuthenticationMiddleware).

@jwt_login_required   — must be authenticated via valid JWT.
@role_required(*roles) — must be authenticated AND have one of the given roles.
@engagement_required   — view must receive an `engagement_id` kwarg; user must have
                          an EngagementAssignment row for it (admins bypass this check).
"""
from functools import wraps
from django.shortcuts import redirect
from django.http import HttpResponseForbidden
from django.contrib.auth.models import AnonymousUser

from .models import EngagementAssignment
from .audit import log_action


def jwt_login_required(view_func):
    @wraps(view_func)
    def wrapper(request, *args, **kwargs):
        if isinstance(request.user, AnonymousUser) or not request.user.is_authenticated:
            return redirect(f"/login/?next={request.path}")
        return view_func(request, *args, **kwargs)
    return wrapper


def role_required(*allowed_roles):
    def decorator(view_func):
        @wraps(view_func)
        @jwt_login_required
        def wrapper(request, *args, **kwargs):
            if request.user.role not in allowed_roles:
                log_action(
                    request, action="access_denied",
                    detail=f"Role '{request.user.role}' attempted to access "
                           f"{request.path} (requires one of {allowed_roles})"
                )
                return HttpResponseForbidden("You do not have permission to access this page.")
            return view_func(request, *args, **kwargs)
        return wrapper
    return decorator


def engagement_required(view_func):
    """
    Use on views that take engagement_id (or statement_id -> resolves to engagement)
    as a kwarg. System Administrators bypass the assignment check; all other roles
    must have an explicit EngagementAssignment.
    """
    @wraps(view_func)
    @jwt_login_required
    def wrapper(request, *args, **kwargs):
        engagement_id = kwargs.get("engagement_id")
        if engagement_id is None:
            # Try to resolve via statement_id if that's what the view uses
            statement_id = kwargs.get("statement_id")
            if statement_id is not None:
                from statements.models import Statement
                stmt = Statement.objects.filter(id=statement_id).select_related("account__engagement").first()
                if stmt and stmt.account and stmt.account.engagement_id:
                    engagement_id = stmt.account.engagement_id

        if request.user.is_admin():
            return view_func(request, *args, **kwargs)

        if engagement_id is None:
            return HttpResponseForbidden("This resource is not linked to an engagement.")

        has_access = EngagementAssignment.objects.filter(
            user=request.user, engagement_id=engagement_id
        ).exists()

        if not has_access:
            log_action(
                request, action="access_denied",
                detail=f"User attempted to access engagement_id={engagement_id} without assignment"
            )
            return HttpResponseForbidden("You are not assigned to this engagement.")

        return view_func(request, *args, **kwargs)
    return wrapper