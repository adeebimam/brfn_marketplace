import csv
import random
from collections import defaultdict
from datetime import date, timedelta
from decimal import Decimal

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.db.models import Q
from django.http import HttpResponse, HttpResponseForbidden
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone

from apps.accounts.models import Profile
from .forms import CheckoutForm, ProductForm, ProducerOrderStatusForm
from .models import (
    Allergen, Category, Product, MONTH_NAMES,
    ProducerOrder, ProducerOrderStatusHistory,
)


# -----------------------------
# Producer access check
# -----------------------------

def _require_producer(request):
    if not request.user.is_authenticated:
        return False

    profile, _ = Profile.objects.get_or_create(user=request.user)
    return profile.role == "PRODUCER"


# ----------------------------
# PRODUCT LIST
# ----------------------------

def product_list(request):
    # Only show products where stock > 0 AND is_active (not marked "Not available")
    products = (
        Product.objects.filter(is_active=True, stock_quantity__gt=0)
        .select_related("category", "producer")
        .prefetch_related("allergens")
    )
    categories = Category.objects.order_by("name")
    allergens = Allergen.objects.order_by("name")

    selected_category = request.GET.get("category", "").strip()
    selected_season = request.GET.get("season", "").strip()
    query = request.GET.get("q", "").strip()
    allergen_filter = request.GET.get("allergen_filter", "").strip()

    if selected_category:
        products = products.filter(category_id=selected_category)

    if selected_season:
        products = products.filter(season=selected_season)

    if query:
        products = products.filter(
            Q(name__icontains=query) |
            Q(description__icontains=query) |
            Q(allergens__name__icontains=query) |
            Q(other_allergen_info__icontains=query) |
            Q(producer__username__icontains=query)
        ).distinct()

    if allergen_filter == "with":
        products = products.filter(
            Q(allergens__isnull=False) | ~Q(other_allergen_info="")
        ).distinct()
    elif allergen_filter == "without":
        products = products.exclude(
            allergens__isnull=False
        ).filter(other_allergen_info="")
    elif allergen_filter.startswith("specific_"):
        allergen_id = allergen_filter.split("_")[1]
        products = products.filter(allergens__id=allergen_id)

    # Auto-hide out-of-season products (date-based filtering)
    today = date.today()
    current_month = today.month
    # Keep products that are year-round (ALL or no months set) OR currently in season
    products = [p for p in products if p.is_in_season(today)]

    # Annotate each product with season status for template use
    for p in products:
        p.in_season_now = p.is_in_season(today)

    context = {
        "products": products,
        "categories": categories,
        "allergens": allergens,
        "selected_category": selected_category,
        "selected_season": selected_season,
        "seasons": Product.SEASON_CHOICES,
        "query": query,
        "allergen_filter": allergen_filter,
        "current_month": current_month,
    }
    return render(request, "marketplace/product_list.html", context)


# ----------------------------
# PRODUCT DETAIL
# ----------------------------

def product_detail(request, pk):
    # Only show products that are active (formerly `in_season`)
    product = get_object_or_404(
        Product.objects.select_related("category", "producer").prefetch_related("allergens"),
        pk=pk,
        is_active=True,
    )
    # If the product is not available, out of stock, or out of season — only the producer can view
    if (not product.is_active or product.stock_quantity <= 0 or not product.is_in_season()):
        if request.user != product.producer:
            from django.http import Http404
            raise Http404("This product is not currently available.")

    return render(request, "marketplace/product_detail.html", {
        "product": product,
        "is_in_season": product.is_in_season(),
    })


# -----------------------------
# TC-012 helper functions
# -----------------------------

COMMISSION_RATE = Decimal("0.05")
TWO_PLACES = Decimal("0.01")


def _compute_financials(gross):
    """Return (commission, net) for a given gross amount."""
    gross = gross.quantize(TWO_PLACES)
    commission = (gross * COMMISSION_RATE).quantize(TWO_PLACES)
    net = (gross - commission).quantize(TWO_PLACES)
    return commission, net


def _build_settlement_ref(producer_id, week_start, week_end):
    """Return a deterministic settlement reference string."""
    return (
        f"SET-{producer_id}-"
        f"{week_start.strftime('%Y%m%d')}-"
        f"{week_end.strftime('%Y%m%d')}"
    )


def _last_completed_week_range(today=None):
    if today is None:
        today = timezone.localdate()

    this_monday = today - timedelta(days=today.weekday())
    last_monday = this_monday - timedelta(days=7)
    last_sunday = this_monday - timedelta(days=1)

    return last_monday, last_sunday


def _uk_tax_year_start(today=None):
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

    products = Product.objects.filter(producer=request.user).select_related("category").order_by("-created_at")
    return render(request, "marketplace/producer_product_list.html", {"products": products})


# ----------------------------
# PRODUCT CREATE
# ----------------------------

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
            form.save_m2m()

            messages.success(request, "Product created.")
            return redirect("marketplace:producer_product_list")
    else:
        form = ProductForm()

    return render(
        request,
        "marketplace/product_form.html",
        {"form": form, "mode": "create"},
    )


# ----------------------------
# PRODUCT UPDATE
# ----------------------------

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
            product = form.save(commit=False)
            product.producer = request.user
            product.save()
            form.save_m2m()

            messages.success(request, "Product updated.")
            return redirect("marketplace:producer_product_list")
    else:
        form = ProductForm(instance=product)

    return render(
        request,
        "marketplace/product_form.html",
        {"form": form, "mode": "edit"},
    )


# ----------------------------
# PRODUCT DELETE
# ----------------------------

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


# ----------------------------
# CHECKOUT (MULTI PRODUCER)
# ----------------------------

def checkout(request):
    cart = request.session.get("cart", {})
    producers = defaultdict(list)
    subtotal = Decimal("0.00")

    for product_id, qty in cart.items():
        try:
            product = Product.objects.select_related("producer").get(id=int(product_id))
        except Product.DoesNotExist:
            continue

        qty = int(qty)
        line_total = product.price * qty
        subtotal += line_total

        lead_time = getattr(product.producer, "lead_time", 2)

        producers[product.producer.username].append({
            "name": product.name,
            "price": float(product.price),
            "qty": qty,
            "total": float(line_total),
            "lead_time": lead_time,
        })

    commission = subtotal * Decimal("0.05")
    total = subtotal + commission

    if request.method == "POST":
        form = CheckoutForm(request.POST)
        if form.is_valid():
            delivery_address = form.cleaned_data["delivery_address"]
            delivery_date = form.cleaned_data["delivery_date"]
            payment_method = form.cleaned_data["payment_method"]

            request.session["order"] = {
                "address": delivery_address,
                "date": str(delivery_date),
                "payment": payment_method,
                "subtotal": float(subtotal),
                "commission": float(commission),
                "total": float(total),
                "producers": dict(producers),
            }

            return redirect("marketplace:payment")
    else:
        initial = {}
        if request.user.is_authenticated:
            initial["delivery_address"] = request.user.email or request.user.username

        form = CheckoutForm(initial=initial)

    return render(request, "checkout.html", {
        "form": form,
        "producers": dict(producers),
        "subtotal": subtotal,
        "commission": commission,
        "total": total,
    })


# ----------------------------
# PAYMENT
# ----------------------------

def payment(request):
    order = request.session.get("order")

    if not order:
        return redirect("marketplace:product_list")

    if request.method == "POST":
        order_number = "ORD-" + str(random.randint(10000, 99999))
        print("NEW ORDER RECEIVED")
        print("Order Number:", order_number)

        for producer, items in order["producers"].items():
            print(f"\nNotification for producer: {producer}")
            for item in items:
                print(f"- {item['name']} x{item['qty']} (£{item['total']})")

        if "order" in request.session:
            del request.session["order"]

        return render(request, "confirmation.html", {
            "order_number": order_number,
            "address": order["address"],
            "date": order["date"],
            "payment": order["payment"],
            "subtotal": order["subtotal"],
            "commission": order["commission"],
            "total": order["total"],
            "producers": order["producers"],
        })

    return render(request, "payment.html", {
        "order": order,
    })


# ----------------------------
# ALLERGEN TEST
# ----------------------------

def allergen_test(request):
    form = ProductForm()
    return render(request, "marketplace/allergen_test.html", {"form": form})


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
    ).quantize(TWO_PLACES)

    commission, net_payment = _compute_financials(gross_total)

    tax_year_orders = all_delivered_orders.filter(
        delivery_date__gte=tax_year_start
    )

    tax_year_total = sum(
        (order.total_value for order in tax_year_orders),
        Decimal("0.00")
    ).quantize(TWO_PLACES)

    settlement_reference = _build_settlement_ref(producer.id, week_start, week_end)

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
        ).quantize(TWO_PLACES)

        hist_commission, hist_net = _compute_financials(hist_gross)

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

    settlement_reference = _build_settlement_ref(producer.id, week_start, week_end)

    for order in orders:
        items_sold = ", ".join(
            f"{item.product.name} x{item.quantity}"
            for item in order.items.all()
        )

        gross = order.total_value.quantize(TWO_PLACES)
        commission, net = _compute_financials(gross)

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