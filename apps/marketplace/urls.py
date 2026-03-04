from django.urls import path
from . import views

app_name = "marketplace"

urlpatterns = [
    # Customer browsing
    path("products/", views.product_list, name="product_list"),
    path("products/<int:pk>/", views.product_detail, name="product_detail"),

    # Producer CRUD
    path("producer/products/", views.producer_product_list, name="producer_product_list"),
    path("producer/products/new/", views.product_create, name="product_create"),
    path("producer/products/<int:pk>/edit/", views.product_update, name="product_update"),
    path("producer/products/<int:pk>/delete/", views.product_delete, name="product_delete"),

    # Checkout flow
    path("checkout/", views.checkout, name="checkout"),
    path("payment/", views.payment, name="payment"),
]