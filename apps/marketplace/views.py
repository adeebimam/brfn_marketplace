from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.http import HttpResponseForbidden
from django.shortcuts import get_object_or_404, redirect, render

from apps.accounts.models import Profile
from .forms import ProductForm
from .models import Product, Category

#Create your views here

def product_list(request):
    products = Product.objects.filter(in_season=True).order_by("-created_at")
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
    product = get_object_or_404(Product, pk=pk, in_season=True)
    return render(request, "marketplace/product_detail.html", {"product": product})


def _require_producer(request):
    if not request.user.is_authenticated:
        return False
    profile, _ = Profile.objects.get_or_create(user=request.user)
    return profile.role == Profile.Role.PRODUCER


@login_required
def producer_product_list(request):
    if not _require_producer(request):
        return HttpResponseForbidden("Producer access only.")

    products = Product.objects.filter(producer=request.user).order_by("-created_at")
    return render(request, "marketplace/producer_product_list.html", {"products": products})


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

    return render(request, "marketplace/product_form.html", {"form": form, "mode": "create"})


@login_required
def product_update(request, pk):
    if not _require_producer(request):
        return HttpResponseForbidden("Producer access only.")

    product = get_object_or_404(Product, pk=pk, producer=request.user)

    if request.method == "POST":
        form = ProductForm(request.POST, instance=product)
        if form.is_valid():
            form.save()
            messages.success(request, "Product updated.")
            return redirect("marketplace:producer_product_list")
    else:
        form = ProductForm(instance=product)

    return render(request, "marketplace/product_form.html", {"form": form, "mode": "edit"})


@login_required
def product_delete(request, pk):
    if not _require_producer(request):
        return HttpResponseForbidden("Producer access only.")

    product = get_object_or_404(Product, pk=pk, producer=request.user)

    if request.method == "POST":
        product.delete()
        messages.success(request, "Product deleted.")
        return redirect("marketplace:producer_product_list")

    return render(request, "marketplace/product_confirm_delete.html", {"product": product})
