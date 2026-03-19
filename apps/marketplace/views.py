from datetime import timedelta
from django.utils import timezone
from decimal import Decimal
from django.http import HttpResponse, HttpResponseForbidden
import csv


from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.http import HttpResponseForbidden
from django.shortcuts import get_object_or_404, redirect, render

from apps.accounts.models import Profile
from .forms import ProductForm
from .models import Product, Category, ProducerOrder


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

    # Filter by status
    status = request.GET.get("status", "")
    if status:
        orders = orders.filter(status=status)

    # Sort by delivery date
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

@login_required
def producer_payments(request):

    if not _require_producer(request):
        return HttpResponseForbidden("Producer access only.")

    producer = request.user

    delivered_orders = (
        ProducerOrder.objects
        .filter(
            producer=producer,
            status=ProducerOrder.Status.DELIVERED
        )
        .select_related("order", "order__customer")
    )

    gross_total = sum(
        (order.total_value for order in delivered_orders),Decimal("0.00")
    )

    commission = (gross_total * Decimal("0.05")).quantize(Decimal("0.01"))
    net_payment = (gross_total * Decimal("0.95")).quantize(Decimal("0.01"))

    context = {
        "orders": delivered_orders,
        "gross_total": gross_total,
        "commission": commission,
        "net_payment": net_payment,
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

    orders = ProducerOrder.objects.filter(
        producer=producer,
        status=ProducerOrder.Status.DELIVERED
    )

    response = HttpResponse(content_type="text/csv")
    response["Content-Disposition"] = 'attachment; filename="payments_report.csv"'

    writer = csv.writer(response)
    writer.writerow(["Order ID", "Customer", "Delivery Date", "Total"])

    for order in orders:
        writer.writerow([
            order.order.id,
            f"{order.order.customer.first_name} {order.order.customer.last_name}",
            order.delivery_date,
            order.total_value,
        ])

    return response