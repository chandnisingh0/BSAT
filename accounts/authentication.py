"""
JWT-based authentication middleware.

Runs after Django's AuthenticationMiddleware in MIDDLEWARE, and overrides
request.user with the JWT-derived identity. Django's session auth is NOT used
for authentication state — only for CSRF and messages framework support.

Flow per request:
  1. Read access token cookie. Valid + not expired -> set request.user, done.
  2. Access token expired/missing -> try refresh token cookie.
       Valid refresh token -> mint new access token, set cookie on response,
       set request.user, continue.
  3. No valid token at all -> request.user = AnonymousUser. Views with
     @login_required (or our own @jwt_required) will redirect to /login/.
"""
from django.contrib.auth.models import AnonymousUser
from django.contrib.auth import get_user_model
from django.conf import settings
from django.utils.deprecation import MiddlewareMixin

from .jwt_utils import (
    verify_access_token, verify_refresh_token,
    issue_access_token, issue_refresh_token,
    set_auth_cookies, TokenError,
)

User = get_user_model()


class JWTAuthenticationMiddleware(MiddlewareMixin):
    def process_request(self, request):
        request.user = AnonymousUser()
        request._jwt_refreshed = False  # flag so process_response knows to set new cookies

        access_token = request.COOKIES.get(settings.JWT_ACCESS_COOKIE_NAME)
        refresh_token = request.COOKIES.get(settings.JWT_REFRESH_COOKIE_NAME)

        if access_token:
            try:
                payload = verify_access_token(access_token)
                user = User.objects.filter(id=payload["sub"], is_active=True).first()
                if user and not user.is_locked():
                    request.user = user
                    return
            except TokenError:
                pass  # fall through to refresh attempt

        if refresh_token:
            # Need the user first to check token_version
            try:
                unverified = __import__("jwt").decode(
                    refresh_token, options={"verify_signature": False}
                )
                user = User.objects.filter(id=unverified.get("sub"), is_active=True).first()
                if user and not user.is_locked():
                    verify_refresh_token(refresh_token, user)  # raises if invalid/revoked
                    request.user = user
                    request._jwt_refreshed = True
                    request._jwt_new_access = issue_access_token(user)
                    request._jwt_new_refresh = issue_refresh_token(user)
            except TokenError:
                pass
            except Exception:
                pass

    def process_response(self, request, response):
        if getattr(request, "_jwt_refreshed", False):
            set_auth_cookies(response, request._jwt_new_access, request._jwt_new_refresh)
        return response