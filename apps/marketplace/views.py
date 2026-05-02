import csv
import random
from collections import defaultdict
from datetime import date, datetime, timedelta
from decimal import Decimal

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.contrib.auth import get_user_model
from django.db.models import Q
from django.http import Http404, HttpResponse, HttpResponseForbidden
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone

from apps.accounts.models import Profile
from apps.cart.models import Cart, CartItem

from .forms import CheckoutForm, ProductForm, ProducerOrderStatusForm, ReviewForm
from .models import (
    Allergen,
    Category,
    CustomerOrderHistory,
    MONTH_NAMES,
    Order,
    OrderItem,
    Product,
    ProducerOrder,
    ProducerOrderStatusHistory,
    Review,
)
from .services import expire_surplus_deals, update_producer_order_status


COMMISSION_RATE = Decimal("0.05")
TWO_PLACES = Decimal("0.01")


# -----------------------------
# Producer access check
# -----------------------------

def _require_producer(request, verified_only=True):
    if not request.user.is_authenticated:
        return False

    profile, _ = Profile.objects.get_or_create(user=request.user)

    if profile.role != Profile.Role.PRODUCER:
        return False

    if verified_only and not profile.is_verified:
        return False

    return True


def _producer_access_denied_response(request):
    if request.user.is_authenticated:
        profile, _ = Profile.objects.get_or_create(user=request.user)

        if profile.role == Profile.Role.PRODUCER and not profile.is_verified:
            messages.warning(
                request,
                "Your producer account is under review. Producer features will be available once an admin approves your account."
            )
            return redirect("home")

    return HttpResponseForbidden("Producer access only.")


# -----------------------------
# Helpers
# -----------------------------

def _get_product_unit_price(product):
    if product.is_active_surplus_deal and product.discounted_price is not None:
        return product.discounted_price

    return product.price


def _attach_producer_order_context(producer_orders):
    for producer_order in producer_orders:
        order_date = timezone.localtime(producer_order.order.created_at).date()

        if producer_order.delivery_date:
            producer_order.lead_time_days = max(
                (producer_order.delivery_date - order_date).days,
                0,
            )
        else:
            producer_order.lead_time_days = 0

        producer_order.item_summary = ", ".join(
            f"{item.product.name} x{item.quantity}"
            for item in producer_order.items.all()
        )

    return producer_orders


def _compute_financials(gross):
    gross = Decimal(gross).quantize(TWO_PLACES)
    commission = (gross * COMMISSION_RATE).quantize(TWO_PLACES)
    net = (gross - commission).quantize(TWO_PLACES)
    return commission, net


def _has_purchased_product(order_history, product_id):
    for order in order_history:
        for producer, items in order.get("producers", {}).items():
            for item in items:
                if str(item.get("id")) == str(product_id):
                    return True

    return False


def _build_settlement_ref(producer_id, week_start, week_end):
    return (
        f"SET-{producer_id}-"
        f"{week_start.strftime('%Y%m%d')}-"
        f"{week_end.strftime('%Y%m%d')}"
    )


def _last_completed_week_range(today):
    current_week_start = today - timedelta(days=today.weekday())
    week_end = current_week_start - timedelta(days=1)
    week_start = week_end - timedelta(days=6)
    return week_start, week_end


def _uk_tax_year_start(today):
    tax_year_start = date(today.year, 4, 6)

    if today < tax_year_start:
        tax_year_start = date(today.year - 1, 4, 6)

    return tax_year_start


def _anonymise_customer(customer):
    if not customer:
        return "Unknown customer"

    full_name = customer.get_full_name().strip()

    if full_name:
        parts = full_name.split()

        if len(parts) == 1:
            return parts[0]

        return f"{parts[0]} {parts[-1][0]}."

    return customer.username


def _get_customer_order_history(user):
    return [
        record.order_data
        for record in CustomerOrderHistory.objects.filter(customer=user).order_by("-id")
    ]


def _parse_date(value):
    if not value:
        return None

    try:
        return datetime.strptime(value, "%Y-%m-%d").date()
    except ValueError:
        return None


# ----------------------------
# PRODUCT LIST
# ----------------------------

def product_list(request):
    expire_surplus_deals()

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
            Q(name__icontains=query)
            | Q(description__icontains=query)
            | Q(allergens__name__icontains=query)
            | Q(other_allergen_info__icontains=query)
            | Q(producer__username__icontains=query)
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

    today = date.today()
    current_month = today.month

    products = [product for product in products if product.is_in_season(today)]

    for product in products:
        product.in_season_now = product.is_in_season(today)

    allergen_choices = [
        {"value": f"specific_{allergen.id}", "name": allergen.name}
        for allergen in allergens
    ]

    context = {
        "products": products,
        "categories": categories,
        "allergens": allergens,
        "allergen_choices": allergen_choices,
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
    expire_surplus_deals()

    product = get_object_or_404(
        Product.objects.select_related("category", "producer").prefetch_related("allergens"),
        pk=pk,
        is_active=True,
    )

    if not product.is_active or product.stock_quantity <= 0 or not product.is_in_season():
        if request.user != product.producer:
            raise Http404("This product is not currently available.")

    reviews = Review.objects.filter(product=product).order_by("-id")

    average_rating = None

    if reviews.exists():
        average_rating = round(sum(review.rating for review in reviews) / reviews.count(), 1)

    return render(
        request,
        "marketplace/product_detail.html",
        {
            "product": product,
            "is_in_season": product.is_in_season(),
            "reviews": reviews,
            "average_rating": average_rating,
        },
    )


# ----------------------------
# SURPLUS DEALS
# ----------------------------

def surplus_deals(request):
    expire_surplus_deals()

    now = timezone.now()

    products = (
        Product.objects.filter(
            is_active=True,
            is_surplus=True,
            surplus_stock_quantity__gt=0,
            surplus_expires_at__gt=now,
            stock_quantity__gt=0,
        )
        .select_related("category", "producer")
        .prefetch_related("allergens")
        .order_by("surplus_expires_at")
    )

    today = date.today()
    products = [product for product in products if product.is_in_season(today)]

    for product in products:
        product.in_season_now = product.is_in_season(today)

    return render(
        request,
        "marketplace/surplus_deals.html",
        {"products": products},
    )


# -----------------------------
# REVIEWS
# -----------------------------

@login_required
def create_review(request, product_id):
    product = get_object_or_404(Product, id=product_id)

    order_history = _get_customer_order_history(request.user)
    has_purchased = _has_purchased_product(order_history, product_id)

    if not has_purchased:
        messages.error(request, "You can only review products you have purchased.")
        return redirect("marketplace:product_detail", pk=product_id)

    existing_review = Review.objects.filter(
        product=product,
        customer=request.user,
    ).first()

    if existing_review:
        messages.error(request, "You have already reviewed this product.")
        return redirect("marketplace:product_detail", pk=product_id)

    if request.method == "POST":
        form = ReviewForm(request.POST)

        if form.is_valid():
            review = form.save(commit=False)
            review.product = product
            review.customer = request.user
            review.verified_purchase = True
            review.save()

            messages.success(request, "Review submitted successfully.")
            return redirect("marketplace:product_detail", pk=product_id)
    else:
        form = ReviewForm()

    return render(
        request,
        "marketplace/review_form.html",
        {
            "form": form,
            "product": product,
        },
    )


# -----------------------------
# PRODUCER PRODUCT MANAGEMENT
# -----------------------------

@login_required
def producer_product_list(request):
    if not _require_producer(request):
        return _producer_access_denied_response(request)

    products = (
        Product.objects
        .filter(producer=request.user)
        .select_related("category")
        .order_by("-created_at")
    )

    return render(
        request,
        "marketplace/producer_product_list.html",
        {"products": products},
    )


@login_required
def product_create(request):
    if not _require_producer(request):
        return _producer_access_denied_response(request)

    if request.method == "POST":
        form = ProductForm(request.POST, request.FILES)

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


@login_required
def product_update(request, pk):
    if not _require_producer(request):
        return _producer_access_denied_response(request)

    product = get_object_or_404(
        Product,
        pk=pk,
        producer=request.user,
    )

    if request.method == "POST":
        form = ProductForm(request.POST, request.FILES, instance=product)

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


@login_required
def product_delete(request, pk):
    if not _require_producer(request):
        return _producer_access_denied_response(request)

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
# CHECKOUT
# ----------------------------

@login_required
def checkout(request):
    cart, _ = Cart.objects.get_or_create(user=request.user)

    cart_items = (
        CartItem.objects
        .select_related("product", "product__producer")
        .filter(cart=cart)
    )

    producers = defaultdict(list)
    subtotal = Decimal("0.00")

    for cart_item in cart_items:
        product = cart_item.product

        if not product.is_active or product.stock_quantity <= 0:
            continue

        qty = int(cart_item.quantity)
        unit_price = _get_product_unit_price(product)
        line_total = unit_price * qty
        subtotal += line_total

        lead_time = getattr(product.producer, "lead_time", None)

        if lead_time is None and hasattr(product.producer, "profile"):
            lead_time = getattr(product.producer.profile, "lead_time", 2)

        if lead_time is None:
            lead_time = 2

        producers[product.producer.username].append({
            "name": product.name,
            "price": float(unit_price),
            "qty": qty,
            "total": float(line_total),
            "lead_time": lead_time,
            "id": product.id,
        })

    commission = (subtotal * COMMISSION_RATE).quantize(TWO_PLACES)
    total = (subtotal + commission).quantize(TWO_PLACES)

    if request.method == "POST":
        form = CheckoutForm(request.POST)

        if form.is_valid():
            delivery_address = form.cleaned_data["delivery_address"]
            delivery_date = request.POST.get("delivery_1")
            payment_method = request.POST.get("payment_method")

            if not delivery_date:
                messages.error(request, "Please select a delivery date.")
                return render(
                    request,
                    "cart/checkout.html",
                    {
                        "form": form,
                        "producers": dict(producers),
                        "subtotal": subtotal,
                        "commission": commission,
                        "total": total,
                        "cart_items": cart_items,
                    },
                )

            if not payment_method:
                messages.error(request, "Please select a payment method.")
                return render(
                    request,
                    "cart/checkout.html",
                    {
                        "form": form,
                        "producers": dict(producers),
                        "subtotal": subtotal,
                        "commission": commission,
                        "total": total,
                        "cart_items": cart_items,
                    },
                )

            request.session["order"] = {
                "address": delivery_address,
                "date": str(delivery_date),
                "payment": payment_method,
                "subtotal": round(float(subtotal), 2),
                "commission": round(float(commission), 2),
                "total": round(float(total), 2),
                "producers": dict(producers),
            }

            request.session.modified = True

            return redirect("marketplace:payment")

        messages.error(request, "Please check the checkout form and try again.")

    else:
        initial = {
            "delivery_address": request.user.email or request.user.username,
        }

        form = CheckoutForm(initial=initial)

    return render(
        request,
        "cart/checkout.html",
        {
            "form": form,
            "producers": dict(producers),
            "subtotal": float(subtotal),
            "commission": float(commission),
            "total": float(total),
            "cart_items": cart_items,
        },
    )


# ----------------------------
# PAYMENT
# ----------------------------

@login_required
def payment(request):
    order = request.session.get("order")

    if not order:
        return redirect("marketplace:product_list")

    debug_info = []

    if request.method == "POST":
        card_number = request.POST.get("card_number", "").strip()
        expiry = request.POST.get("expiry", "").strip()
        cvc = request.POST.get("cvc", "").strip()

        if len(expiry) != 5 or expiry[2] != "/":
            messages.error(request, "Enter expiry date in MM/YY format.")
            return render(request, "orders/payment.html", {"order": order})

        month_part, year_part = expiry.split("/")

        if not month_part.isdigit() or not year_part.isdigit():
            messages.error(request, "Enter expiry date in MM/YY format.")
            return render(request, "orders/payment.html", {"order": order})

        month = int(month_part)
        year = int(year_part)

        if month < 1 or month > 12:
            messages.error(request, "Enter a valid expiry month.")
            return render(request, "orders/payment.html", {"order": order})

        today = timezone.localdate()
        current_month = today.month
        current_year = today.year % 100

        if year < current_year or (year == current_year and month < current_month):
            messages.error(request, "Card expiry date cannot be in the past.")
            return render(request, "orders/payment.html", {"order": order})

        order_number = "ORD-" + str(random.randint(10000, 99999))
        delivery_date_obj = _parse_date(order.get("date"))

        try:
            User = get_user_model()
            customer = request.user

            if not customer.is_authenticated:
                raise Exception("User is not authenticated.")

            db_order = Order.objects.create(
                customer=customer,
                delivery_address=order["address"],
                delivery_postcode="",
                special_instructions="",
            )

            debug_info.append(f"Created Order: {db_order}")

            for producer_username, items in order["producers"].items():
                producer = User.objects.get(username=producer_username)

                producer_order = ProducerOrder.objects.create(
                    order=db_order,
                    producer=producer,
                    delivery_date=delivery_date_obj,
                    status=ProducerOrder.Status.PENDING,
                    total_value=Decimal("0.00"),
                )

                total_value = Decimal("0.00")

                for item in items:
                    product = Product.objects.get(id=item["id"], producer=producer)

                    quantity = int(item["qty"])
                    unit_price = Decimal(str(item["price"]))

                    OrderItem.objects.create(
                        producer_order=producer_order,
                        product=product,
                        quantity=quantity,
                        unit_price=unit_price,
                    )

                    total_value += unit_price * quantity

                    product.stock_quantity = max(0, product.stock_quantity - quantity)
                    product.save()

                producer_order.total_value = total_value.quantize(TWO_PLACES)
                producer_order.save()

        except Exception as error:
            import traceback

            error_message = f"Order creation failed: {error}"
            print(error_message)
            print(traceback.format_exc())

            return render(
                request,
                "orders/payment.html",
                {
                    "order": order,
                    "error_message": error_message,
                    "debug_info": debug_info,
                },
            )

        order_history = request.session.get("order_history", [])

        order_data = {
            "order_number": order_number,
            "address": order["address"],
            "order_date": timezone.now().strftime("%Y-%m-%d"),
            "delivery_date": order["date"],
            "payment": order["payment"],
            "subtotal": order["subtotal"],
            "commission": order["commission"],
            "total": order["total"],
            "producers": order["producers"],
        }

        order_history.append(order_data)

        request.session["order_history"] = order_history
        request.session.modified = True

        CustomerOrderHistory.objects.create(
            customer=request.user,
            order_number=order_number,
            order_data=order_data,
        )

        cart, _ = Cart.objects.get_or_create(user=request.user)
        CartItem.objects.filter(cart=cart).delete()

        request.session.pop("order", None)
        request.session.pop("cart", None)

        return render(
            request,
            "orders/confirmation.html",
            {
                "order_number": order_number,
                "address": order["address"],
                "date": order["date"],
                "payment": order["payment"],
                "subtotal": order["subtotal"],
                "commission": order["commission"],
                "total": order["total"],
                "producers": order["producers"],
            },
        )

    return render(
        request,
        "orders/payment.html",
        {"order": order},
    )


# ----------------------------
# ALLERGEN TEST
# ----------------------------

def allergen_test(request):
    form = ProductForm()
    return render(request, "marketplace/allergen_test.html", {"form": form})


# -----------------------------
# PRODUCER ORDERS
# -----------------------------

@login_required
def producer_order_list(request):
    if not _require_producer(request):
        return _producer_access_denied_response(request)

    orders = (
        ProducerOrder.objects
        .filter(producer=request.user)
        .select_related("order", "order__customer")
        .prefetch_related("items", "items__product")
    )

    status = request.GET.get("status", "")

    if status:
        orders = orders.filter(status=status)

    sort = request.GET.get("sort", "newest")

    if sort == "oldest":
        orders = orders.order_by("order__created_at", "id")
    else:
        orders = orders.order_by("-order__created_at", "-id")

    orders = list(orders)
    _attach_producer_order_context(orders)

    return render(
        request,
        "marketplace/producer_order_list.html",
        {
            "orders": orders,
            "selected_status": status,
            "sort": sort,
            "status_choices": ProducerOrder.Status.choices,
        },
    )


@login_required
def producer_order_detail(request, pk):
    if not _require_producer(request):
        return _producer_access_denied_response(request)

    producer_order = get_object_or_404(
        ProducerOrder.objects
        .select_related("order", "order__customer")
        .prefetch_related("items", "items__product"),
        pk=pk,
        producer=request.user,
    )

    _attach_producer_order_context([producer_order])

    return render(
        request,
        "marketplace/producer_order_detail.html",
        {"po": producer_order},
    )


@login_required
def producer_order_update_status(request, pk):
    if not _require_producer(request):
        return _producer_access_denied_response(request)

    producer_order = get_object_or_404(
        ProducerOrder.objects.select_related("order", "order__customer"),
        pk=pk,
        producer=request.user,
    )

    allowed_transitions = {
        ProducerOrder.Status.PENDING: [
            ProducerOrder.Status.CONFIRMED,
            ProducerOrder.Status.CANCELLED,
        ],
        ProducerOrder.Status.CONFIRMED: [
            ProducerOrder.Status.READY,
            ProducerOrder.Status.CANCELLED,
        ],
        ProducerOrder.Status.READY: [
            ProducerOrder.Status.DELIVERED,
        ],
        ProducerOrder.Status.DELIVERED: [],
        ProducerOrder.Status.CANCELLED: [],
    }

    next_statuses = allowed_transitions.get(producer_order.status, [])
    status_choices = [
        (status, ProducerOrder.Status(status).label)
        for status in next_statuses
    ]

    if request.method == "POST":
        form = ProducerOrderStatusForm(request.POST, status_choices=status_choices)

        if form.is_valid():
            new_status = form.cleaned_data["status"]
            note = form.cleaned_data["note"]

            try:
                update_producer_order_status(
                    producer_order=producer_order,
                    new_status=new_status,
                    changed_by=request.user,
                    note=note,
                )
            except ValueError:
                messages.error(request, "Invalid status progression.")
                return redirect("marketplace:producer_order_detail", pk=producer_order.pk)

            messages.success(request, "Order status updated successfully.")
            return redirect("marketplace:producer_order_detail", pk=producer_order.pk)
    else:
        form = ProducerOrderStatusForm(
            initial={"status": producer_order.status},
            status_choices=status_choices,
        )

    return render(
        request,
        "marketplace/producer_order_update_status.html",
        {
            "po": producer_order,
            "form": form,
        },
    )


@login_required
def producer_order_management(request):
    if not _require_producer(request):
        return _producer_access_denied_response(request)

    current_orders = (
        ProducerOrder.objects
        .filter(
            producer=request.user,
            status__in=[
                ProducerOrder.Status.PENDING,
                ProducerOrder.Status.CONFIRMED,
                ProducerOrder.Status.READY,
            ],
        )
        .select_related("order", "order__customer")
        .prefetch_related("items", "items__product", "status_history")
        .order_by("delivery_date", "-id")
    )

    order_history = (
        ProducerOrder.objects
        .filter(
            producer=request.user,
            status=ProducerOrder.Status.DELIVERED,
        )
        .select_related("order", "order__customer")
        .prefetch_related("items", "items__product", "status_history")
        .order_by("-delivery_date", "-id")
    )

    return render(
        request,
        "marketplace/order_management.html",
        {
            "current_orders": current_orders,
            "order_history": order_history,
        },
    )


# -----------------------------
# PRODUCER PAYMENTS
# -----------------------------

@login_required
def producer_payments(request):
    if not _require_producer(request):
        return _producer_access_denied_response(request)

    producer = request.user
    today = timezone.localdate()

    week_start, week_end = _last_completed_week_range(today)
    tax_year_start = _uk_tax_year_start(today)

    all_delivered_orders = (
        ProducerOrder.objects
        .filter(
            producer=producer,
            status=ProducerOrder.Status.DELIVERED,
        )
        .select_related("order", "order__customer")
        .prefetch_related("items", "items__product")
        .order_by("-delivery_date", "-id")
    )

    weekly_orders = all_delivered_orders.filter(
        delivery_date__range=(week_start, week_end)
    ).order_by("-delivery_date", "-id")

    gross_total = sum(
        (order.total_value or Decimal("0.00") for order in weekly_orders),
        Decimal("0.00"),
    ).quantize(TWO_PLACES)

    commission, net_payment = _compute_financials(gross_total)

    tax_year_orders = all_delivered_orders.filter(
        delivery_date__gte=tax_year_start
    )

    tax_year_total = sum(
        (order.total_value or Decimal("0.00") for order in tax_year_orders),
        Decimal("0.00"),
    ).quantize(TWO_PLACES)

    settlement_reference = _build_settlement_ref(producer.id, week_start, week_end)

    history_map = defaultdict(list)

    for order in all_delivered_orders:
        if not order.delivery_date:
            continue

        order_week_start = order.delivery_date - timedelta(days=order.delivery_date.weekday())
        order_week_end = order_week_start + timedelta(days=6)
        history_map[(order_week_start, order_week_end)].append(order)

    historical_records = []

    for (hist_start, hist_end), orders_in_week in sorted(history_map.items(), reverse=True):
        if hist_start == week_start and hist_end == week_end:
            continue

        hist_gross = sum(
            (order.total_value or Decimal("0.00") for order in orders_in_week),
            Decimal("0.00"),
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
        context,
    )


@login_required
def download_payments_csv(request):
    if not _require_producer(request):
        return _producer_access_denied_response(request)

    producer = request.user
    today = timezone.localdate()
    week_start, week_end = _last_completed_week_range(today)

    orders = (
        ProducerOrder.objects
        .filter(
            producer=producer,
            status=ProducerOrder.Status.DELIVERED,
            delivery_date__range=(week_start, week_end),
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

        gross = (order.total_value or Decimal("0.00")).quantize(TWO_PLACES)
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


# -----------------------------
# CUSTOMER ORDER HISTORY
# -----------------------------

@login_required
def order_history(request):
    orders = _get_customer_order_history(request.user)

    start = request.GET.get("start", "").strip()
    end = request.GET.get("end", "").strip()
    producer = request.GET.get("producer", "").strip()

    start_date = _parse_date(start)
    end_date = _parse_date(end)

    if start and not start_date:
        messages.error(request, "Invalid start date.")

    if end and not end_date:
        messages.error(request, "Invalid end date.")

    if start_date and end_date and end_date < start_date:
        messages.error(request, "End date cannot be before start date.")
    elif start_date or end_date:
        filtered_orders = []

        for order in orders:
            raw_order_date = order.get("order_date") or order.get("date")
            order_date = _parse_date(raw_order_date)

            if not order_date:
                continue

            if start_date and order_date < start_date:
                continue

            if end_date and order_date > end_date:
                continue

            filtered_orders.append(order)

        orders = filtered_orders

    if producer:
        orders = [
            order for order in orders
            if producer.lower() in [
                producer_name.lower()
                for producer_name in order.get("producers", {}).keys()
            ]
        ]

    return render(
        request,
        "orders/history.html",
        {
            "orders": orders,
            "start": start,
            "end": end,
            "producer": producer,
        },
    )


@login_required
def order_detail(request, order_id):
    orders = _get_customer_order_history(request.user)

    order = next(
        (
            order for order in orders
            if str(order.get("order_number")) == str(order_id)
        ),
        None,
    )

    if not order:
        messages.error(request, "Order not found.")
        return redirect("marketplace:order_history")

    return render(
        request,
        "orders/order_detail.html",
        {"order": order},
    )


@login_required
def reorder(request, order_id):
    orders = _get_customer_order_history(request.user)

    order = next(
        (
            order for order in orders
            if str(order.get("order_number")) == str(order_id)
        ),
        None,
    )

    if not order:
        messages.error(request, "Order not found.")
        return redirect("marketplace:order_history")

    cart, _ = Cart.objects.get_or_create(user=request.user)

    unavailable_items = []
    price_changed_items = []

    for producer, items in order.get("producers", {}).items():
        for item in items:
            try:
                product = Product.objects.get(id=item["id"], is_active=True)
            except Product.DoesNotExist:
                product = None

            if not product or product.stock_quantity <= 0:
                unavailable_items.append(item["name"])
                continue

            old_price = Decimal(str(item.get("price", product.price)))
            new_price = product.price

            if old_price != new_price:
                price_changed_items.append(
                    f"{product.name}: was £{old_price}, now £{new_price}"
                )

            cart_item, created = CartItem.objects.get_or_create(
                cart=cart,
                product=product,
            )

            qty = int(item.get("qty", 1))

            if created:
                cart_item.quantity = qty
            else:
                cart_item.quantity += qty

            cart_item.save()

    if price_changed_items:
        messages.warning(
            request,
            "Price changes detected: " + "; ".join(price_changed_items),
        )

    if unavailable_items:
        messages.error(
            request,
            "Some items unavailable: " + ", ".join(unavailable_items),
        )

    messages.success(request, "Items added to cart with latest prices.")

    return redirect("cart:detail")


@login_required
def download_receipt(request, order_id):
    orders = _get_customer_order_history(request.user)

    order = next(
        (
            order for order in orders
            if str(order.get("order_number")) == str(order_id)
        ),
        None,
    )

    if not order:
        messages.error(request, "Receipt not found.")
        return redirect("marketplace:order_history")

    content = f"Order {order['order_number']} - Total £{order['total']}"

    response = HttpResponse(content, content_type="text/plain")
    response["Content-Disposition"] = f'attachment; filename="receipt_{order_id}.txt"'

    return response