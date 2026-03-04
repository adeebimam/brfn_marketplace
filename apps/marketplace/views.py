from django.shortcuts import render, redirect
from datetime import date, timedelta
from .forms import CheckoutForm
import random


# ---------------------------
# PRODUCT PAGES
# ---------------------------

def product_list(request):
    return render(request, "products/product_list.html")


def product_detail(request, pk):
    return render(request, "products/product_detail.html")


# ---------------------------
# PRODUCER PAGES
# ---------------------------

def producer_product_list(request):
    return render(request, "producer/product_list.html")


def product_create(request):
    return render(request, "producer/product_form.html")


def product_update(request, pk):
    return render(request, "producer/product_form.html")


def product_delete(request, pk):
    return render(request, "producer/product_confirm_delete.html")


# ---------------------------
# CHECKOUT PAGE
# ---------------------------

def checkout(request):

    products = [
        {"name": "Organic Carrots", "price": 10},
        {"name": "Fresh Potatoes", "price": 10},
    ]

    subtotal = sum(p["price"] for p in products)

    commission = round(subtotal * 0.05, 2)
    producer_payment = round(subtotal * 0.95, 2)

    total = subtotal + commission

    if request.method == "POST":

        form = CheckoutForm(request.POST)

        if form.is_valid():

            delivery_address = form.cleaned_data["delivery_address"]
            delivery_date = form.cleaned_data["delivery_date"]
            payment_method = form.cleaned_data["payment_method"]

            # enforce 48-hour rule
            minimum_date = date.today() + timedelta(days=2)

            if delivery_date < minimum_date:

                form.add_error(
                    "delivery_date",
                    "Delivery must be at least 48 hours from now."
                )

            else:

                request.session["order"] = {
                    "address": delivery_address,
                    "date": str(delivery_date),
                    "payment": payment_method,
                    "products": products,
                    "subtotal": subtotal,
                    "commission": commission,
                    "producer_payment": producer_payment,
                    "total": total
                }

                return redirect("marketplace:payment")

    else:

        initial = {}

        if request.user.is_authenticated:
            initial["delivery_address"] = request.user.email

        form = CheckoutForm(initial=initial)

    return render(request, "checkout.html", {
        "form": form,
        "products": products,
        "subtotal": subtotal,
        "commission": commission,
        "total": total
    })


# ---------------------------
# PAYMENT PAGE
# ---------------------------

def payment(request):

    order = request.session.get("order")

    if not order:
        return redirect("marketplace:checkout")

    if request.method == "POST":

        order_number = "ORD-" + str(random.randint(10000, 99999))

        # simulate producer notification
        print("NEW ORDER RECEIVED")
        print("Order Number:", order_number)
        print("Total:", order["total"])

        return render(request, "confirmation.html", {
            "order_number": order_number,
            "address": order["address"],
            "date": order["date"],
            "payment": order["payment"],
            "subtotal": order["subtotal"],
            "commission": order["commission"],
            "total": order["total"],
        })

    return render(request, "payment.html", order)