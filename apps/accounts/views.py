import logging
import csv
from decimal import Decimal
from django.contrib import messages
from django.contrib.auth import authenticate, login, logout
from django.contrib.auth.decorators import login_required, user_passes_test
from django.contrib.auth import authenticate, login, logout
from django.contrib.auth.models import User
from django.shortcuts import redirect, render
from django.http import HttpResponseForbidden, HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from .forms import CustomerRegisterForm, ProducerRegisterForm
from .models import Profile
from apps.marketplace.models import Order, ProducerOrder, Product
from django.utils.dateparse import parse_date
from datetime import timedelta, date
from django.utils import timezone
from apps.message.models import MessageThread, Message
from apps.marketplace.models import CommissionLog
from reportlab.lib.pagesizes import A4, landscape
from reportlab.lib import colors
from reportlab.lib.units import cm
from reportlab.platypus import SimpleDocTemplate, Table, TableStyle, Paragraph, Spacer
from reportlab.lib.styles import getSampleStyleSheet
import io



security_logger = logging.getLogger("security")


def producer_register_view(request):
    if request.method == "POST":
        form = ProducerRegisterForm(request.POST)
        if form.is_valid():
            user = form.save()

            profile = user.profile
            profile.role = Profile.Role.PRODUCER
            profile.is_verified = False
            profile.verification_status = Profile.VerificationStatus.PENDING
            profile.verification_notes = ""
            profile.save(update_fields=[
                "role",
                "is_verified",
                "verification_status",
                "verification_notes",
            ])

            messages.success(
                request,
                "Your Producer account is under review. You will be able to access Producer features once an admin approves your account."
            )
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
        verification_status__in=[
            Profile.VerificationStatus.PENDING,
            Profile.VerificationStatus.DECLINED,
                ],
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

    producers = (
        Profile.objects
        .filter(
            role=Profile.Role.PRODUCER,
            verification_status__in=[
                Profile.VerificationStatus.PENDING,
                Profile.VerificationStatus.DECLINED,
            ],
        )
        .select_related("user")
        .order_by("-id")
    )

    return render(request, "accounts/admin_producer_approvals.html", {
        "producers": producers,
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
    profile.verification_status = Profile.VerificationStatus.APPROVED
    profile.verification_notes = "Approved by admin"
    profile.save(update_fields=[
        "is_verified",
        "verification_status",
        "verification_notes",
    ])

    thread = MessageThread.objects.create(
        subject="Producer account approved",
        created_by=request.user,
    )

    thread.participants.add(request.user, profile.user)

    Message.objects.create(
        thread=thread,
        sender=request.user,
        body="Your producer account has been approved. You can now access producer features.",
    )

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

    if request.method == "POST":
        note = request.POST.get("verification_notes", "").strip()

        if not note:
            messages.error(request, "Please provide a reason for declining this producer.")
            return redirect("accounts:admin_producer_approvals")

        profile.is_verified = False
        profile.verification_status = Profile.VerificationStatus.DECLINED
        profile.verification_notes = note
        profile.save(update_fields=[
            "is_verified",
            "verification_status",
            "verification_notes",
        ])

        thread = MessageThread.objects.create(
            sender=request.user,
            receiver=profile.user,
            subject="Producer account declined - more information required",
        )

        Message.objects.create(
            thread=thread,
            sender=request.user,
            body=(
                "Your producer account has been declined for now.\n\n"
                f"Reason:\n{note}\n\n"
                "Please reply to this message with any extra information or evidence. "
                "The admin can review your response and approve your account later."
            ),
        )

        messages.warning(
            request,
            f"{profile.user.username} has been declined and a message has been sent."
        )

    return redirect("accounts:admin_producer_approvals")


@login_required
def producer_approvals(request):
    if request.user.profile.role != Profile.Role.ADMIN:
        return HttpResponseForbidden("Admin only")

    if request.method == "POST":
        profile_id = request.POST.get("profile_id")
        action = request.POST.get("action")
        note = request.POST.get("verification_notes", "").strip()

        profile = get_object_or_404(
            Profile,
            id=profile_id,
            role=Profile.Role.PRODUCER
        )

        if action == "approve":
            profile.is_verified = True
            profile.verification_status = Profile.VerificationStatus.APPROVED
            profile.verification_notes = "Approved by admin"
            profile.save(update_fields=[
                "is_verified",
                "verification_status",
                "verification_notes",
            ])

            thread = MessageThread.objects.create(
                subject = "Producer account approved",
                created_by = request.user,
                
            )

            thread.participants.add(request.user, profile.user)

            Message.objects.create(
                thread=thread,
                sender=request.user,
                body="Your producer account has been approved. You can now access producer features.",
            )

            messages.success(request, f"{profile.user.username} has been approved.")

        elif action == "reject":
            if not note:
                messages.error(request, "Please provide a reason for declining this producer.")
                return redirect("accounts:admin_producer_approvals")

            profile.is_verified = False
            profile.verification_status = Profile.VerificationStatus.DECLINED
            profile.verification_notes = note
            profile.save(update_fields=[
                "is_verified",
                "verification_status",
                "verification_notes",
            ])

            thread = MessageThread.objects.create(
                subject="Producer account declined - more information required",
                created_by = request.user,
            )
            thread.participants.add(request.user, profile.user)

            Message.objects.create(
                thread=thread,
                sender=request.user,
                body=(
                    "Your producer account has been declined for now.\n\n"
                    f"Reason:\n{note}\n\n"
                    "Please reply to this message with any extra information or evidence. "
                    "The admin can review your response and approve your account later."
                ),
            )

            messages.warning(
                request,
                f"{profile.user.username} has been declined and a message has been sent."
            )

        return redirect("accounts:admin_producer_approvals")

    producers = Profile.objects.filter(
        role=Profile.Role.PRODUCER,
        verification_status__in=[
            Profile.VerificationStatus.PENDING,
            Profile.VerificationStatus.DECLINED,
        ],
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
                                Order.Status.DELIVERED,
                                Order.Status.CANCELLED,
                                ],

           Order.Status.DELIVERED: [
                                    Order.Status.DELIVERED,
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

    # Additional filters
    producer_filter = request.GET.get("producer", "").strip()
    status_filter = request.GET.get("status", "").strip()

    orders = Order.objects.filter(
        created_at__date__range=[start_date, end_date]
    ).prefetch_related(
        "producer_orders",
        "producer_orders__items",
        "producer_orders__producer",
    )

    if producer_filter:
        orders = orders.filter(
            producer_orders__producer__username__icontains=producer_filter
        ).distinct()

    if status_filter:
        orders = orders.filter(status=status_filter)

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
            po_commission = (po_total * Decimal("0.05")).quantize(Decimal("0.01"))
            po_payment = (po_total * Decimal("0.95")).quantize(Decimal("0.01"))
            producers.append({
                "producer": po.producer,
                "total": po_total,
                "commission": po_commission,
                "payment": po_payment,
            })

        report_data.append({
            "order": order,
            "order_total": order_total,
            "commission": commission,
            "producer_payment": producer_payment,
            "producers": producers,
        })
    
    # Monthly summary 
    from collections import defaultdict
    monthly_summary = defaultdict(lambda: {
        "order_count": 0,
        "total_order_value": Decimal("0.00"),
        "total_commission": Decimal("0.00"),
        "total_producer_payment": Decimal("0.00"),
    })

    all_orders_ytd = Order.objects.filter(
        created_at__year=timezone.now().year
    ).prefetch_related("producer_orders")

    for o in all_orders_ytd:
        month_key = o.created_at.strftime("%Y-%m")
        month_label = o.created_at.strftime("%B %Y")
        o_total = o.total_amount or Decimal("0.00")
        o_commission = (o_total * Decimal("0.05")).quantize(Decimal("0.01"))
        o_producer_payment = (o_total - o_commission).quantize(Decimal("0.01"))
        monthly_summary[month_key]["label"] = month_label
        monthly_summary[month_key]["order_count"] += 1
        monthly_summary[month_key]["total_order_value"] += o_total
        monthly_summary[month_key]["total_commission"] += o_commission
        monthly_summary[month_key]["total_producer_payment"] += o_producer_payment

    monthly_summary = dict(sorted(monthly_summary.items(), reverse=True))

    # Year-to-date totals
    ytd_order_value = sum(
        (m["total_order_value"] for m in monthly_summary.values()), Decimal("0.00")
    ).quantize(Decimal("0.01"))
    ytd_commission = sum(
        (m["total_commission"] for m in monthly_summary.values()), Decimal("0.00")
    ).quantize(Decimal("0.01"))
    ytd_producer_payment = sum(
        (m["total_producer_payment"] for m in monthly_summary.values()), Decimal("0.00")
    ).quantize(Decimal("0.01"))
    audit_logs = CommissionLog.objects.filter(
        order__created_at__date__range=[start_date, end_date]
    ).select_related("order", "producer", "producer_order").order_by("-calculated_at")

    if producer_filter:
        audit_logs = audit_logs.filter(
            producer__username__icontains=producer_filter
        )
    if status_filter:
        audit_logs = audit_logs.filter(order__status=status_filter)

    context = {
        "orders": report_data,
        "total_order_value": total_order_value,
        "total_commission": total_commission,
        "total_producer_payment": total_producer_payment,
        "start_date": start_date,
        "end_date": end_date,
        "producer_filter": producer_filter,
        "status_filter": status_filter,
        "order_statuses": Order.Status.choices,
        "monthly_summary": monthly_summary,      
        "ytd_order_value": ytd_order_value,      
        "ytd_commission": ytd_commission,         
        "ytd_producer_payment": ytd_producer_payment,  
        "current_year": timezone.now().year, 
        "monthly_summary": monthly_summary,
        "ytd_order_value": ytd_order_value,
        "ytd_commission": ytd_commission,
        "ytd_producer_payment": ytd_producer_payment,
        "current_year": timezone.now().year, 
        "audit_logs": audit_logs,   
    }

    return render(request, "accounts/admin_financial_report.html", context)

def is_admin(user):
    return (
        user.is_authenticated
        and hasattr(user, "profile")
        and user.profile.role == "ADMIN"
    )

@login_required
@user_passes_test(is_admin)
def export_financial_report_csv(request):
    start_date = request.GET.get("start_date")
    end_date = request.GET.get("end_date")

    orders = Order.objects.prefetch_related(
        "producer_orders",
        "producer_orders__producer",
    ).order_by("-created_at")

    if start_date:
        orders = orders.filter(created_at__date__gte=start_date)
    if end_date:
        orders = orders.filter(created_at__date__lte=end_date)

    response = HttpResponse(content_type="text/csv")
    response["Content-Disposition"] = 'attachment; filename="financial_report.csv"'
    writer = csv.writer(response)

    writer.writerow([
        "Order ID",
        "Order Date",
        "Status",
        "Customer",
        "Order Total (£)",
        "Commission 5% (£)",
        "Total Producer Payment 95% (£)",
        "Producer",
        "Producer Subtotal (£)",
        "Producer Commission (£)",
        "Producer Payment (£)",
    ])

    for order in orders:
        total = order.total_amount or Decimal("0.00")
        commission = (total * Decimal("0.05")).quantize(Decimal("0.01"))
        producer_payment = (total - commission).quantize(Decimal("0.01"))
        producer_orders = list(order.producer_orders.all())

        if producer_orders:
            for i, po in enumerate(producer_orders):
                po_total = po.total_value
                po_commission = (po_total * Decimal("0.05")).quantize(Decimal("0.01"))
                po_payment = (po_total * Decimal("0.95")).quantize(Decimal("0.01"))
                writer.writerow([
                    f"BRFN-{order.id}" if i == 0 else "",
                    order.created_at.strftime("%Y-%m-%d") if i == 0 else "",
                    order.get_status_display() if i == 0 else "",
                    order.customer.username if i == 0 else "",
                    total if i == 0 else "",
                    commission if i == 0 else "",
                    producer_payment if i == 0 else "",
                    po.producer.username,
                    po_total,
                    po_commission,
                    po_payment,
                ])
        else:
            writer.writerow([
                f"BRFN-{order.id}",
                order.created_at.strftime("%Y-%m-%d"),
                order.get_status_display(),
                order.customer.username,
                total,
                commission,
                producer_payment,
                "", "", "", "",
            ])

    return response

@login_required
@user_passes_test(is_admin)
def export_financial_report_pdf(request):
    
    start_date = request.GET.get("start_date")
    end_date = request.GET.get("end_date")

    orders = Order.objects.prefetch_related(
        "producer_orders",
        "producer_orders__producer",
    ).order_by("-created_at")

    if start_date:
        orders = orders.filter(created_at__date__gte=start_date)
    if end_date:
        orders = orders.filter(created_at__date__lte=end_date)

    buffer = io.BytesIO()
    doc = SimpleDocTemplate(
        buffer,
        pagesize=landscape(A4),
        rightMargin=1*cm,
        leftMargin=1*cm,
        topMargin=1*cm,
        bottomMargin=1*cm,
    )

    styles = getSampleStyleSheet()
    elements = []

    # Title
    elements.append(Paragraph("BRFN Marketplace — Financial Report", styles["Title"]))
    if start_date and end_date:
        elements.append(Paragraph(f"Period: {start_date} to {end_date}", styles["Normal"]))
    elements.append(Spacer(1, 0.5*cm))

    # Summary totals
    total_value = Decimal("0.00")
    total_commission = Decimal("0.00")
    total_payment = Decimal("0.00")
    for order in orders:
        t = order.total_amount or Decimal("0.00")
        c = (t * Decimal("0.05")).quantize(Decimal("0.01"))
        p = (t - c).quantize(Decimal("0.01"))
        total_value += t
        total_commission += c
        total_payment += p

    summary_data = [
        ["Total Order Value", "Total Commission (5%)", "Total Producer Payment (95%)", "Total Orders"],
        [f"£{total_value.quantize(Decimal('0.01'))}", f"£{total_commission.quantize(Decimal('0.01'))}", f"£{total_payment.quantize(Decimal('0.01'))}", str(orders.count())],
    ]
    summary_table = Table(summary_data, colWidths=[7*cm, 6*cm, 7*cm, 4*cm])
    summary_table.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#087d73")),
        ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
        ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
        ("ALIGN", (0, 0), (-1, -1), "CENTER"),
        ("GRID", (0, 0), (-1, -1), 0.5, colors.grey),
        ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#f8fafc")]),
        ("FONTSIZE", (0, 0), (-1, -1), 10),
        ("PADDING", (0, 0), (-1, -1), 6),
    ]))
    elements.append(summary_table)
    elements.append(Spacer(1, 0.5*cm))

    # Order breakdown table
    elements.append(Paragraph("Order Breakdown", styles["Heading2"]))
    table_data = [["Order ID", "Date", "Status", "Customer", "Producer", "Order Total", "Commission (5%)", "Payment (95%)"]]

    for order in orders:
        o_total = order.total_amount or Decimal("0.00")
        o_commission = (o_total * Decimal("0.05")).quantize(Decimal("0.01"))
        producer_orders = list(order.producer_orders.all())

        if producer_orders:
            for i, po in enumerate(producer_orders):
                po_total = po.total_value
                po_commission = (po_total * Decimal("0.05")).quantize(Decimal("0.01"))
                po_payment = (po_total * Decimal("0.95")).quantize(Decimal("0.01"))
                table_data.append([
                    f"BRFN-{order.id}" if i == 0 else "",
                    order.created_at.strftime("%Y-%m-%d") if i == 0 else "",
                    order.get_status_display() if i == 0 else "",
                    order.customer.username if i == 0 else "",
                    po.producer.username,
                    f"£{o_total}" if i == 0 else "",
                    f"£{po_commission}",
                    f"£{po_payment}",
                ])
        else:
            table_data.append([
                f"BRFN-{order.id}",
                order.created_at.strftime("%Y-%m-%d"),
                order.get_status_display(),
                order.customer.username,
                "—",
                f"£{o_total}",
                f"£{o_commission}",
                f"£{(o_total - o_commission).quantize(Decimal('0.01'))}",
            ])

    col_widths = [3*cm, 2.5*cm, 2.5*cm, 3*cm, 3*cm, 3*cm, 3*cm, 3*cm]
    breakdown_table = Table(table_data, colWidths=col_widths, repeatRows=1)
    breakdown_table.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#087d73")),
        ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
        ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
        ("ALIGN", (0, 0), (-1, -1), "CENTER"),
        ("GRID", (0, 0), (-1, -1), 0.5, colors.grey),
        ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#f8fafc")]),
        ("FONTSIZE", (0, 0), (-1, -1), 8),
        ("PADDING", (0, 0), (-1, -1), 4),
    ]))
    elements.append(breakdown_table)

    doc.build(elements)
    buffer.seek(0)

    response = HttpResponse(buffer, content_type="application/pdf")
    response["Content-Disposition"] = 'attachment; filename="financial_report.pdf"'
    return response


@login_required
def user_settings(request):
    profile = request.user.profile

    if request.method == "POST":
        action = request.POST.get("action")

        if action == "change_password":
            current_password = request.POST.get("current_password")
            new_password1 = request.POST.get("new_password1")
            new_password2 = request.POST.get("new_password2")

            if not request.user.check_password(current_password):
                messages.error(request, "Current password is incorrect.")
            elif new_password1 != new_password2:
                messages.error(request, "New passwords do not match.")
            elif len(new_password1) < 8:
                messages.error(request, "Password must be at least 8 characters.")
            else:
                request.user.set_password(new_password1)
                request.user.save()
                from django.contrib.auth import update_session_auth_hash
                update_session_auth_hash(request, request.user)
                messages.success(request, "Password changed successfully.")

        elif action == "change_address":
            delivery_address = request.POST.get("delivery_address", "").strip()
            delivery_postcode = request.POST.get("delivery_postcode", "").strip()
            if not delivery_address:
                messages.error(request, "Delivery address cannot be empty.")
            else:
                profile.delivery_address = delivery_address
                profile.delivery_postcode = delivery_postcode
                profile.save()
                messages.success(request, "Delivery address updated successfully.")

        elif action == "font_size":
            font_size = request.POST.get("font_size", "medium")
            if font_size not in ["small", "medium", "large"]:
                font_size = "medium"
            request.session["font_size"] = font_size
            messages.success(request, f"Font size set to {font_size}.")

        return redirect("accounts:settings")

    font_size = request.session.get("font_size", "medium")

    return render(request, "accounts/settings.html", {
        "profile": profile,
        "font_size": font_size,
    })

def forgot_password(request):
    if request.method == "POST":
        email = request.POST.get("email", "").strip()
        user = User.objects.filter(email=email).first()
        if user:
            request.session["password_reset_user_id"] = user.id
            return redirect("accounts:reset_password")
        else:
            messages.error(request, "No account found with that email address.")
    return render(request, "accounts/forgot_password.html")


def reset_password(request):
    user_id = request.session.get("password_reset_user_id")
    if not user_id:
        messages.error(request, "Invalid or expired reset session.")
        return redirect("accounts:forgot_password")

    try:
        user = User.objects.get(pk=user_id)
    except User.DoesNotExist:
        messages.error(request, "User not found.")
        return redirect("accounts:forgot_password")

    if request.method == "POST":
        new_password1 = request.POST.get("new_password1", "")
        new_password2 = request.POST.get("new_password2", "")

        if new_password1 != new_password2:
            messages.error(request, "Passwords do not match.")
        elif len(new_password1) < 8:
            messages.error(request, "Password must be at least 8 characters.")
        else:
            user.set_password(new_password1)
            user.save()
            del request.session["password_reset_user_id"]
            messages.success(request, "Password reset successfully. Please log in.")
            return redirect("accounts:login")

    return render(request, "accounts/reset_password.html", {"user": user})