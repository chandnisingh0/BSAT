"""
JWT issuing, verification, and refresh-token rotation.

Access token  — short-lived (default 8h), carries user identity + role.
Refresh token — longer-lived (default 7d), used only to mint new access tokens.
                 Carries a `token_version` claim that must match User.token_version,
                 so bumping token_version on the user instantly invalidates all
                 outstanding refresh tokens (used on password change, admin deactivation).

Tokens are stored in HttpOnly, Secure, SameSite=Strict cookies — never in localStorage,
to avoid XSS-based token theft.
"""
import jwt
from datetime import datetime, timedelta, timezone as dt_timezone
from django.conf import settings


class TokenError(Exception):
    pass


def _now():
    return datetime.now(dt_timezone.utc)


def issue_access_token(user) -> str:
    payload = {
        "type": "access",
        "sub": str(user.id),
        "email": user.email,
        "role": user.role,
        "must_change_password": user.must_change_password,
        "iat": _now(),
        "exp": _now() + timedelta(hours=settings.JWT_ACCESS_TOKEN_EXPIRY_HOURS),
    }
    return jwt.encode(payload, settings.JWT_SECRET_KEY, algorithm=settings.JWT_ALGORITHM)


def issue_refresh_token(user) -> str:
    payload = {
        "type": "refresh",
        "sub": str(user.id),
        "token_version": user.token_version,
        "iat": _now(),
        "exp": _now() + timedelta(days=settings.JWT_REFRESH_TOKEN_EXPIRY_DAYS),
    }
    return jwt.encode(payload, settings.JWT_SECRET_KEY, algorithm=settings.JWT_ALGORITHM)


def decode_token(token: str) -> dict:
    try:
        payload = jwt.decode(token, settings.JWT_SECRET_KEY, algorithms=[settings.JWT_ALGORITHM])
    except jwt.ExpiredSignatureError:
        raise TokenError("expired")
    except jwt.InvalidTokenError:
        raise TokenError("invalid")
    return payload


def verify_access_token(token: str) -> dict:
    payload = decode_token(token)
    if payload.get("type") != "access":
        raise TokenError("wrong_type")
    return payload


def verify_refresh_token(token: str, user) -> dict:
    payload = decode_token(token)
    if payload.get("type") != "refresh":
        raise TokenError("wrong_type")
    if payload.get("token_version") != user.token_version:
        raise TokenError("revoked")   # password changed / admin deactivated since this token was issued
    return payload


def set_auth_cookies(response, access_token: str, refresh_token: str):
    response.set_cookie(
        settings.JWT_ACCESS_COOKIE_NAME, access_token,
        httponly=True, secure=settings.JWT_COOKIE_SECURE,
        samesite=settings.JWT_COOKIE_SAMESITE,
        max_age=settings.JWT_ACCESS_TOKEN_EXPIRY_HOURS * 3600,
    )
    response.set_cookie(
        settings.JWT_REFRESH_COOKIE_NAME, refresh_token,
        httponly=True, secure=settings.JWT_COOKIE_SECURE,
        samesite=settings.JWT_COOKIE_SAMESITE,
        max_age=settings.JWT_REFRESH_TOKEN_EXPIRY_DAYS * 86400,
    )
    return response


def clear_auth_cookies(response):
    response.delete_cookie(settings.JWT_ACCESS_COOKIE_NAME)
    response.delete_cookie(settings.JWT_REFRESH_COOKIE_NAME)
    return response