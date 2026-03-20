import csv
from decimal import Decimal
from collections import defaultdict
from datetime import date, timedelta

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.http import HttpResponse, HttpResponseForbidden
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone

from apps.accounts.models import Profile
from .forms import ProductForm, ProducerOrderStatusForm
from .models import Product, Category, ProducerOrder, ProducerOrderStatusHistory


# -----------------------------
# Customer product browsing
# -----------------------------

def product_list(request):
    products = Product.objects.filter(is_active=True).order_by("-created_at")
    categories = Category.objects.order_by("name")

    selected_category = request.GET.get("category")

    if selected_category:
        products = products.filter(category_id=selected_category)

    context = {
        "products": products,
        "categories": categories,
        "selected_category": selected_category,
    }

    return render(request, "marketplace/product_list.html", context)


def product_detail(request, pk):
    product = get_object_or_404(Product, pk=pk, is_active=True)

    return render(
        request,
        "marketplace/product_detail.html",
        {"product": product}
    )


# -----------------------------
# Producer access check
# -----------------------------

def _require_producer(request):
    if not request.user.is_authenticated:
        return False

    profile, _ = Profile.objects.get_or_create(user=request.user)
    return profile.role == Profile.Role.PRODUCER


# -----------------------------
# TC-012 helper functions
# -----------------------------

def _last_completed_week_range(today=None):
    """
    Return Monday-Sunday for the last completed week.
    """
    if today is None:
        today = timezone.localdate()

    this_monday = today - timedelta(days=today.weekday())
    last_monday = this_monday - timedelta(days=7)
    last_sunday = this_monday - timedelta(days=1)

    return last_monday, last_sunday


def _uk_tax_year_start(today=None):
    """
    UK tax year starts on 6 April.
    """
    if today is None:
        today = timezone.localdate()

    current_year_start = date(today.year, 4, 6)

    if today >= current_year_start:
        return current_year_start

    return date(today.year - 1, 4, 6)


def _anonymise_customer(user):
    first = (user.first_name or "").strip()
    last = (user.last_name or "").strip()

    first_initial = first[0].upper() if first else ""
    last_initial = last[0].upper() if last else ""

    if first_initial or last_initial:
        return f"{first_initial}. {last_initial}.".strip()

    return "Customer"


# -----------------------------
# Producer product management
# -----------------------------

@login_required
def producer_product_list(request):
    if not _require_producer(request):
        return HttpResponseForbidden("Producer access only.")

    products = Product.objects.filter(
        producer=request.user
    ).order_by("-created_at")

    return render(
        request,
        "marketplace/producer_product_list.html",
        {"products": products},
    )


@login_required
def product_create(request):
    if not _require_producer(request):
        return HttpResponseForbidden("Producer access only.")

    if request.method == "POST":
        form = ProductForm(request.POST)

        if form.is_valid():
            product = form.save(commit=False)
            product.producer = request.user
            product.save()

            messages.success(request, "Product created.")
            return redirect("marketplace:producer_product_list")
    else:
        form = ProductForm()

    return render(
        request,
        "marketplace/product_form.html",
        {"form": form, "mode": "create"},
    )


@login_required
def product_update(request, pk):
    if not _require_producer(request):
        return HttpResponseForbidden("Producer access only.")

    product = get_object_or_404(
        Product,
        pk=pk,
        producer=request.user,
    )

    if request.method == "POST":
        form = ProductForm(request.POST, instance=product)

        if form.is_valid():
            form.save()

            messages.success(request, "Product updated.")
            return redirect("marketplace:producer_product_list")
    else:
        form = ProductForm(instance=product)

    return render(
        request,
        "marketplace/product_form.html",
        {"form": form, "mode": "edit"},
    )


@login_required
def product_delete(request, pk):
    if not _require_producer(request):
        return HttpResponseForbidden("Producer access only.")

    product = get_object_or_404(
        Product,
        pk=pk,
        producer=request.user,
    )

    if request.method == "POST":
        product.delete()

        messages.success(request, "Product deleted.")
        return redirect("marketplace:producer_product_list")

    return render(
        request,
        "marketplace/product_confirm_delete.html",
        {"product": product},
    )


# -----------------------------
# TC-009 Producer Order List
# -----------------------------

@login_required
def producer_order_list(request):
    if not _require_producer(request):
        return HttpResponseForbidden("Producer access only.")

    orders = (
        ProducerOrder.objects
        .filter(producer=request.user)
        .select_related("order", "order__customer")
        .prefetch_related("items", "items__product")
    )

    status = request.GET.get("status", "")
    if status:
        orders = orders.filter(status=status)

    sort = request.GET.get("sort", "delivery_asc")

    if sort == "delivery_desc":
        orders = orders.order_by("-delivery_date")
    else:
        orders = orders.order_by("delivery_date")

    return render(request, "marketplace/producer_order_list.html", {
        "orders": orders,
        "selected_status": status,
        "sort": sort,
        "status_choices": ProducerOrder.Status.choices,
    })


# -----------------------------
# TC-009 Order Detail Page
# -----------------------------

@login_required
def producer_order_detail(request, pk):
    if not _require_producer(request):
        return HttpResponseForbidden("Producer access only.")

    po = get_object_or_404(
        ProducerOrder.objects
        .select_related("order", "order__customer")
        .prefetch_related("items", "items__product"),
        pk=pk,
        producer=request.user,
    )

    return render(
        request,
        "marketplace/producer_order_detail.html",
        {"po": po},
    )


# -----------------------------
# TC-012 Producer Payment Settlement
# -----------------------------

@login_required
def producer_payments(request):
    if not _require_producer(request):
        return HttpResponseForbidden("Producer access only.")

    producer = request.user
    today = timezone.localdate()

    week_start, week_end = _last_completed_week_range(today)
    tax_year_start = _uk_tax_year_start(today)

    all_delivered_orders = (
        ProducerOrder.objects
        .filter(
            producer=producer,
            status=ProducerOrder.Status.DELIVERED
        )
        .select_related("order", "order__customer")
        .prefetch_related("items", "items__product")
        .order_by("-delivery_date", "-id")
    )

    weekly_orders = all_delivered_orders.filter(
        delivery_date__range=(week_start, week_end)
    ).order_by("-delivery_date", "-id")

    gross_total = sum(
        (order.total_value for order in weekly_orders),
        Decimal("0.00")
    ).quantize(Decimal("0.01"))

    commission = (gross_total * Decimal("0.05")).quantize(Decimal("0.01"))
    net_payment = (gross_total * Decimal("0.95")).quantize(Decimal("0.01"))

    tax_year_orders = all_delivered_orders.filter(
        delivery_date__gte=tax_year_start
    )

    tax_year_total = sum(
        (order.total_value for order in tax_year_orders),
        Decimal("0.00")
    ).quantize(Decimal("0.01"))

    settlement_reference = (
        f"SET-{producer.id}-"
        f"{week_start.strftime('%Y%m%d')}-"
        f"{week_end.strftime('%Y%m%d')}"
    )

    history_map = defaultdict(list)

    for order in all_delivered_orders:
        order_week_start = order.delivery_date - timedelta(days=order.delivery_date.weekday())
        order_week_end = order_week_start + timedelta(days=6)
        history_map[(order_week_start, order_week_end)].append(order)

    historical_records = []

    for (hist_start, hist_end), orders_in_week in sorted(history_map.items(), reverse=True):
        if hist_start == week_start and hist_end == week_end:
            continue

        hist_gross = sum(
            (o.total_value for o in orders_in_week),
            Decimal("0.00")
        ).quantize(Decimal("0.01"))

        hist_commission = (hist_gross * Decimal("0.05")).quantize(Decimal("0.01"))
        hist_net = (hist_gross * Decimal("0.95")).quantize(Decimal("0.01"))

        historical_records.append({
            "week_start": hist_start,
            "week_end": hist_end,
            "gross": hist_gross,
            "commission": hist_commission,
            "net": hist_net,
            "order_count": len(orders_in_week),
        })

    context = {
        "orders": weekly_orders,
        "gross_total": gross_total,
        "commission": commission,
        "net_payment": net_payment,
        "payment_status": "Pending Bank Transfer",
        "settlement_reference": settlement_reference,
        "tax_year_total": tax_year_total,
        "week_start": week_start,
        "week_end": week_end,
        "historical_records": historical_records,
    }

    return render(
        request,
        "marketplace/producer_payments.html",
        context
    )


@login_required
def download_payments_csv(request):
    if not _require_producer(request):
        return HttpResponseForbidden("Producer access only.")

    producer = request.user
    today = timezone.localdate()
    week_start, week_end = _last_completed_week_range(today)

    orders = (
        ProducerOrder.objects
        .filter(
            producer=producer,
            status=ProducerOrder.Status.DELIVERED,
            delivery_date__range=(week_start, week_end)
        )
        .select_related("order", "order__customer")
        .prefetch_related("items", "items__product")
        .order_by("-delivery_date", "-id")
    )

    response = HttpResponse(content_type="text/csv")
    filename = f"payments_report_{week_start}_{week_end}.csv"
    response["Content-Disposition"] = f'attachment; filename="{filename}"'

    writer = csv.writer(response)
    writer.writerow([
        "Settlement Reference",
        "Order Number",
        "Customer",
        "Delivery Date",
        "Items Sold",
        "Gross Amount",
        "Commission (5%)",
        "Net Payment",
        "Status",
    ])

    settlement_reference = (
        f"SET-{producer.id}-"
        f"{week_start.strftime('%Y%m%d')}-"
        f"{week_end.strftime('%Y%m%d')}"
    )

    for order in orders:
        items_sold = ", ".join(
            f"{item.product.name} x{item.quantity}"
            for item in order.items.all()
        )

        gross = order.total_value.quantize(Decimal("0.01"))
        commission = (gross * Decimal("0.05")).quantize(Decimal("0.01"))
        net = (gross * Decimal("0.95")).quantize(Decimal("0.01"))

        writer.writerow([
            settlement_reference,
            order.order.id,
            _anonymise_customer(order.order.customer),
            order.delivery_date,
            items_sold,
            gross,
            commission,
            net,
            order.get_status_display(),
        ])

    return response

@login_required
def producer_order_update_status(request, pk):
    if not _require_producer(request):
        return HttpResponseForbidden("Producer access only.")

    po = get_object_or_404(
        ProducerOrder.objects.select_related("order", "order__customer"),
        pk=pk,
        producer=request.user,
    )

    allowed_transitions = {
        ProducerOrder.Status.PENDING: [ProducerOrder.Status.CONFIRMED],
        ProducerOrder.Status.CONFIRMED: [ProducerOrder.Status.READY],
        ProducerOrder.Status.READY: [ProducerOrder.Status.DELIVERED],
        ProducerOrder.Status.DELIVERED: [],
    }

    if request.method == "POST":
        form = ProducerOrderStatusForm(request.POST)

        if form.is_valid():
            new_status = form.cleaned_data["status"]
            note = form.cleaned_data["note"]

            if new_status not in allowed_transitions.get(po.status, []):
                messages.error(request, "Invalid status progression.")
                return redirect("marketplace:producer_order_detail", pk=po.pk)

            old_status = po.status
            po.status = new_status
            po.save()

            ProducerOrderStatusHistory.objects.create(
                producer_order=po,
                old_status=old_status,
                new_status=new_status,
                note=note,
                changed_by=request.user,
            )

            messages.success(request, "Order status updated successfully.")
            return redirect("marketplace:producer_order_detail", pk=po.pk)
    else:
        form = ProducerOrderStatusForm(initial={"status": po.status})

    return render(
        request,
        "marketplace/producer_order_update_status.html",
        {
            "po": po,
            "form": form,
        },
    )