from django.shortcuts import redirect
from django.contrib import messages
from functools import wraps

from .models import Profile


def verified_producer_required(view_func):
    @wraps(view_func)
    def _wrapped_view(request, *args, **kwargs):

        if not request.user.is_authenticated:
            return redirect("accounts:login")

        profile = request.user.profile

        if profile.role != Profile.Role.PRODUCER:
            messages.error(request, "You are not authorised to access this page.")
            return redirect("home")

        if not profile.is_verified:
            messages.warning(
                request,
                "Your account is under review. You will gain access once approved."
            )
            return redirect("home")

        return view_func(request, *args, **kwargs)

    return _wrapped_view