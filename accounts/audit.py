"""
Central audit logging helper. Every call here is one append-only AuditLog row.
Call this from views — never write to AuditLog directly elsewhere, so all
logging stays consistent.
"""
from .models import AuditLog


def _client_ip(request) -> str | None:
    if request is None:
        return None
    forwarded = request.META.get("HTTP_X_FORWARDED_FOR")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.META.get("REMOTE_ADDR")


def log_action(request, action: str, detail: str = "", user=None, engagement=None):
    """
    request can be None (e.g. background jobs) — pass user explicitly in that case.
    """
    actor = user or (getattr(request, "user", None) if request else None)
    if actor is not None and not getattr(actor, "is_authenticated", True):
        actor = None  # AnonymousUser -> None, but failed-login attempts still log via user=None + detail

    AuditLog.objects.create(
        user=actor if actor and getattr(actor, "pk", None) else None,
        action=action,
        detail=detail,
        engagement=engagement,
        ip_address=_client_ip(request),
    )