import logging

from django.contrib import messages
from django.contrib.auth import authenticate, login, logout
from django.contrib.auth.decorators import login_required
from django.shortcuts import redirect, render

from .forms import CustomerRegisterForm, ProducerRegisterForm
from .models import Profile

security_logger = logging.getLogger("security")


def producer_register_view(request):
    if request.method == "POST":
        form = ProducerRegisterForm(request.POST)
        if form.is_valid():
            form.save()  # Form will handle user + profile creation

            messages.success(request, "Your Producer account is under review. You will be able to access Producer features once an admin approves your account. ")
            return redirect("accounts:login")
    else:
        form = ProducerRegisterForm()

    return render(request, "accounts/producer_register.html", {"form": form})

def register_view(request):
    if request.method == "POST":
        form = CustomerRegisterForm(request.POST)
        if form.is_valid():
            user = form.save()
            messages.success(request, "Account created. Please log in.")
            return redirect("accounts:login")
    else:
        form = CustomerRegisterForm()

    return render(request, "accounts/register.html", {"form": form})


def login_view(request):
    if request.method == "POST":
        username = request.POST.get("username", "").strip()
        password = request.POST.get("password", "")

        user = authenticate(request, username=username, password=password)

        if user is None:
            # Log failed login attempt
            security_logger.warning(
                "Failed login attempt for '%s' from IP %s",
                username,
                _get_client_ip(request),
            )
            messages.error(request, "Invalid username or password.")
            return render(request, "accounts/login.html")

        login(request, user)

        # Log successful login
        security_logger.info(
            "Successful login for user '%s' (id=%s) from IP %s",
            user.username,
            user.pk,
            _get_client_ip(request),
        )

        # "Remember me" — keep session alive for 2 weeks
        # Otherwise session expires when browser closes (default)
        if request.POST.get("remember_me"):
            request.session.set_expiry(1209600)  # 2 weeks in seconds
        else:
            request.session.set_expiry(0)  # Expire on browser close

        # Ensure profile exists (handles admin/superuser too)
        profile, _ = Profile.objects.get_or_create(user=user)

        # Role-based redirect
        if profile.role == Profile.Role.PRODUCER:
            return redirect("marketplace:producer_product_list")
        elif profile.role == Profile.Role.ADMIN:
            return redirect("/admin/")
        # CUSTOMER, COMMUNITY_GROUP, RESTAURANT → product catalogue
        return redirect("marketplace:product_list")

    return render(request, "accounts/login.html")


@login_required
def profile_view(request):
    profile, _ = Profile.objects.get_or_create(user=request.user)
    return render(request, "accounts/profile.html", {"profile": profile})


def logout_view(request):
    user = request.user
    security_logger.info(
        "User '%s' (id=%s) logged out",
        user.username if user.is_authenticated else "anonymous",
        user.pk if user.is_authenticated else "N/A",
    )
    logout(request)
    return redirect("accounts:login")


def axes_lockout_view(request, credentials=None, *args, **kwargs):
    """Called by django-axes when an account is locked out."""
    locked_username = ""
    if credentials:
        locked_username = credentials.get("username", "")
    elif request.method == "POST":
        locked_username = request.POST.get("username", "")
    security_logger.warning(
        "Account '%s' locked out due to repeated failed login attempts from IP %s",
        locked_username,
        _get_client_ip(request),
    )
    messages.error(
        request,
        "Too many failed login attempts. Please try again later.",
    )
    return render(request, "accounts/login.html", status=403)


def _get_client_ip(request):
    """Extract client IP, respecting X-Forwarded-For behind a proxy."""
    x_forwarded = request.META.get("HTTP_X_FORWARDED_FOR")
    if x_forwarded:
        return x_forwarded.split(",")[0].strip()
    return request.META.get("REMOTE_ADDR", "unknown")
