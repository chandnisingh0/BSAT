from django.shortcuts import redirect

EXEMPT_PATHS = ["/login/", "/logout/", "/change-password/"]


class ForcePasswordChangeMiddleware:
    """
    Runs after JWTAuthenticationMiddleware. If the authenticated user's JWT
    payload (or live DB record) says must_change_password=True, force them
    to /change-password/ before anything else.
    """
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        user = getattr(request, "user", None)
        if user and getattr(user, "is_authenticated", False):
            if user.must_change_password and request.path not in EXEMPT_PATHS:
                return redirect("change_password")
        return self.get_response(request)