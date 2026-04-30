import logging
from decimal import Decimal
from django.contrib import messages
from django.contrib.auth import authenticate, login, logout
from django.contrib.auth.decorators import login_required
from django.shortcuts import redirect, render
from django.http import HttpResponseForbidden
from django.shortcuts import get_object_or_404, redirect, render
from .forms import CustomerRegisterForm, ProducerRegisterForm
from .models import Profile
from apps.marketplace.models import Order, ProducerOrder, Product
from django.utils.dateparse import parse_date
from datetime import timedelta
from django.utils import timezone


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
            return redirect("accounts:admin_dashboard")
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

def _require_admin(request):
    return (
        request.user.is_authenticated
        and hasattr(request.user, "profile")
        and request.user.profile.role == Profile.Role.ADMIN
    )


@login_required
def admin_dashboard(request):
    if not _require_admin(request):
        return HttpResponseForbidden("Admin access only.")

    context = {
        "pending_producers_count": Profile.objects.filter(
            role=Profile.Role.PRODUCER,
            is_verified=False,
        ).count(),
        "total_orders": Order.objects.count(),
        "pending_orders": ProducerOrder.objects.filter(
            status=ProducerOrder.Status.PENDING,
        ).count(),
        "total_products": Product.objects.count(),
        "total_accounts": Profile.objects.count(),
    }

    return render(request, "accounts/admin_dashboard.html", context)


@login_required
def admin_producer_approvals(request):
    if not _require_admin(request):
        return HttpResponseForbidden("Admin access only.")

    pending_producers = (
        Profile.objects
        .filter(role=Profile.Role.PRODUCER, is_verified=False)
        .select_related("user")
        .order_by("-id")
    )

    return render(request, "accounts/admin_producer_approvals.html", {
        "pending_producers": pending_producers,
    })


@login_required
def admin_approve_producer(request, profile_id):
    if not _require_admin(request):
        return HttpResponseForbidden("Admin access only.")

    profile = get_object_or_404(
        Profile,
        id=profile_id,
        role=Profile.Role.PRODUCER,
    )

    profile.is_verified = True
    profile.verification_notes = "Approved by admin"
    profile.save(update_fields=["is_verified", "verification_notes"])

    messages.success(request, f"{profile.user.username} has been approved.")
    return redirect("accounts:admin_producer_approvals")


@login_required
def admin_reject_producer(request, profile_id):
    if not _require_admin(request):
        return HttpResponseForbidden("Admin access only.")

    profile = get_object_or_404(
        Profile,
        id=profile_id,
        role=Profile.Role.PRODUCER,
    )

    note = request.POST.get("verification_notes", "Rejected by admin")
    profile.verification_notes = note
    profile.save(update_fields=["verification_notes"])

    messages.warning(request, f"{profile.user.username} has been rejected.")
    return redirect("accounts:admin_producer_approvals")

@login_required
def producer_approvals(request):
    if request.user.profile.role != "ADMIN":
        return HttpResponseForbidden("Admin only")

    if request.method == "POST":
        profile_id = request.POST.get("profile_id")
        action = request.POST.get("action")

        profile = get_object_or_404(Profile, id=profile_id, role=Profile.Role.PRODUCER)

        if action == "approve":
            profile.is_verified = True
            profile.verification_notes = "Approved by admin"
            profile.save(update_fields=["is_verified", "verification_notes"])

        elif action == "reject":
            profile.is_verified = False
            profile.verification_notes = "Rejected by admin"
            profile.save(update_fields=["is_verified", "verification_notes"])

        return redirect("accounts:admin_producer_approvals")

    producers = Profile.objects.filter(
        role=Profile.Role.PRODUCER,
        is_verified=False
    ).select_related("user")

    return render(request, "accounts/admin_producer_approvals.html", {
        "producers": producers
    })

@login_required
def admin_accounts(request):
    if request.user.profile.role != Profile.Role.ADMIN:
        return redirect("home")

    selected_role = request.GET.get("role", "")

    profiles = Profile.objects.select_related("user").all().order_by(
        "role",
        "user__username"
    )

    if selected_role:
        profiles = profiles.filter(role=selected_role)

    return render(request, "accounts/admin_accounts.html", {
        "profiles": profiles,
        "selected_role": selected_role,
        "role_choices": Profile.Role.choices,
    })

@login_required
def account_detail(request, profile_id):
    profile = get_object_or_404(Profile.objects.select_related("user"), id=profile_id)

    is_admin = request.user.profile.role == Profile.Role.ADMIN

    if not is_admin and profile.user != request.user:
        messages.error(request, "You do not have permission to edit this account.")
        return redirect("accounts:profile")

    if request.method == "POST":
        user = profile.user

        user.email = request.POST.get("email", user.email)
        user.save()

        profile.contact_first_name = request.POST.get("contact_first_name", profile.contact_first_name)
        profile.contact_last_name = request.POST.get("contact_last_name", profile.contact_last_name)
        profile.business_name = request.POST.get("business_name", profile.business_name)
        profile.address = request.POST.get("address", profile.address)
        profile.postcode = request.POST.get("postcode", profile.postcode)
        profile.phone = request.POST.get("phone", profile.phone)

        if is_admin:
            profile.role = request.POST.get("role", profile.role)
            profile.is_verified = request.POST.get("is_verified") == "on"
            profile.verification_notes = request.POST.get("verification_notes", profile.verification_notes)
        if profile.role == Profile.Role.CUSTOMER:
            profile.business_name = ""
        else:
            profile.business_name = request.POST.get(
                "business_name",
                profile.business_name
            )

        profile.save()

        messages.success(request, "Account updated successfully.")

        if is_admin:
            return redirect("accounts:admin_accounts")

        return redirect("accounts:profile")

    return render(request, "accounts/account_detail.html", {
        "profile": profile,
        "role_choices": Profile.Role.choices,
        "is_admin": is_admin,
    })


@login_required
def delete_account(request, profile_id):
    profile = get_object_or_404(Profile.objects.select_related("user"), id=profile_id)

    is_admin = request.user.profile.role == Profile.Role.ADMIN

    if not is_admin:
        messages.error(request, "Only admins can delete accounts.")
        return redirect("accounts:profile")

    if request.method == "POST":
        user = profile.user
        user.delete()
        messages.success(request, "Account deleted successfully.")
        return redirect("accounts:admin_accounts")

    return render(request, "accounts/account_confirm_delete.html", {
        "profile": profile,
    })

@login_required
def admin_total_orders(request):
    if request.user.profile.role != Profile.Role.ADMIN:
        return redirect("home")

    selected_status = request.GET.get("status", "")

    orders = Order.objects.prefetch_related(
        "producer_orders",
        "producer_orders__producer",
        "producer_orders__producer__profile",
    ).select_related(
        "customer",
        "customer__profile",
    ).order_by("-created_at")

    if selected_status:
        orders = orders.filter(status=selected_status)

    for order in orders:
        order.admin_total = sum(
            producer_order.total_value
            for producer_order in order.producer_orders.all()
        )

    context = {
        "orders": orders,
        "selected_status": selected_status,
        "status_choices": Order.Status.choices,
    }

    return render(request, "accounts/admin_total_orders.html", context)

@login_required
def admin_order_detail(request, order_id):
    if request.user.profile.role != Profile.Role.ADMIN:
        return redirect("home")

    order = get_object_or_404(
        Order.objects.select_related(
            "customer",
            "customer__profile",
        ).prefetch_related(
            "producer_orders",
            "producer_orders__producer",
            "producer_orders__producer__profile",
            "producer_orders__items",
            "producer_orders__items__product",
        ),
        id=order_id,
    )

    allowed_next_statuses = {
            Order.Status.PENDING: [
                                    Order.Status.PENDING,
                                    Order.Status.CONFIRMED,
                                    Order.Status.CANCELLED,
                                 ],
            Order.Status.CONFIRMED: [
                                    Order.Status.CONFIRMED,
                                    Order.Status.READY,
                                    Order.Status.CANCELLED,
                                    ],
            Order.Status.READY: [
                                Order.Status.READY,
                                Order.Status.COMPLETED,
                                Order.Status.CANCELLED,
                                ],

            Order.Status.COMPLETED: [
                                    Order.Status.COMPLETED,
                                    Order.Status.CANCELLED,
                                    ],
            Order.Status.CANCELLED: [],
    }

    if request.method == "POST":
        new_status = request.POST.get("status")

        if new_status in allowed_next_statuses.get(order.status, []):
            order.status = new_status
            order.save(update_fields=["status"])
            messages.success(request, "Order status updated successfully.")
        else:
            messages.error(request, "Invalid status change.")

        return redirect("accounts:admin_order_detail", order_id=order.id)

    return render(request, "accounts/admin_order_detail.html", {
        "order": order,
        "allowed_next_statuses": allowed_next_statuses.get(order.status, []),
    })

@login_required
def admin_total_products(request):
    if request.user.profile.role != Profile.Role.ADMIN:
        return redirect("home")

    products = Product.objects.select_related(
        "producer",
        "producer__profile",
        "category",
    ).prefetch_related("allergens").order_by("-id")

    return render(request, "accounts/admin_total_products.html", {
        "products": products,
    })


@login_required
def admin_product_detail(request, product_id):
    if request.user.profile.role != Profile.Role.ADMIN:
        return redirect("home")

    product = get_object_or_404(
        Product.objects.select_related(
            "producer",
            "producer__profile",
            "category",
        ).prefetch_related("allergens"),
        id=product_id,
    )

    return render(request, "accounts/admin_product_detail.html", {
        "product": product,
    })



@login_required
def admin_financial_report(request):
    if request.user.profile.role != Profile.Role.ADMIN:
        return redirect("home")

    # Default: last 14 days
    end_date = request.GET.get("end_date")
    start_date = request.GET.get("start_date")

    if end_date:
        end_date = parse_date(end_date)
    else:
        end_date = timezone.now().date()

    if start_date:
        start_date = parse_date(start_date)
    else:
        start_date = end_date - timedelta(days=14)

    orders = Order.objects.filter(
        created_at__date__range=[start_date, end_date]
    ).prefetch_related(
        "producer_orders",
        "producer_orders__items",
        "producer_orders__producer",
    )

    total_order_value = Decimal("0.00")
    total_commission = Decimal("0.00")
    total_producer_payment = Decimal("0.00")

    report_data = []

    for order in orders:
        order_total = order.total_amount or Decimal("0.00")
        commission = (order_total * Decimal("0.05")).quantize(Decimal("0.01"))
        producer_payment = (order_total - commission).quantize(Decimal("0.01"))

        total_order_value += order_total
        total_commission += commission
        total_producer_payment += producer_payment

        producers = []

        for po in order.producer_orders.all():
            po_total = po.total_value
            po_payment = (po_total * Decimal("0.95")).quantize(Decimal("0.01"))

            producers.append({
                "producer": po.producer,
                "total": po_total,
                "payment": po_payment,
            })

        report_data.append({
            "order": order,
            "order_total": order_total,
            "commission": commission,
            "producer_payment": producer_payment,
            "producers": producers,
        })

    context = {
        "orders": report_data,
        "total_order_value": total_order_value,
        "total_commission": total_commission,
        "total_producer_payment": total_producer_payment,
        "start_date": start_date,
        "end_date": end_date,
    }

    return render(request, "accounts/admin_financial_report.html", context)