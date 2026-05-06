from django.urls import path
from . import views

app_name = "marketplace"

urlpatterns = [
    # Customer browsing
    path("products/", views.product_list, name="product_list"),
    path("products/<int:pk>/", views.product_detail, name="product_detail"),

    # Producer products
    path("producer/products/", views.producer_product_list, name="producer_product_list"),
    path("producer/products/new/", views.product_create, name="product_create"),
    path("producer/products/<int:pk>/edit/", views.product_update, name="product_update"),
    path("producer/products/<int:pk>/delete/", views.product_delete, name="product_delete"),
    path("products/<int:product_id>/reviews/create/", views.create_review, name="create_review"),
    # Checkout flow
    path("checkout/", views.checkout, name="checkout"),
    path("payment/", views.payment, name="payment"),

    # Allergen test
    path("allergen-test/", views.allergen_test, name="allergen_test"),

    # TC-009 Producer orders
    path("producer/orders/", views.producer_order_list, name="producer_order_list"),
    path("producer/orders/<int:pk>/", views.producer_order_detail, name="producer_order_detail"),

    # TC-010 Update order status
    path("producer/orders/<int:pk>/update-status/", views.producer_order_update_status, name="producer_order_update_status"),

    # TC-012 Producer payments
    path("producer/payments/", views.producer_payments, name="producer_payments"),
    path("producer/payments/download/", views.download_payments_csv, name="download_payments_csv"),


     # TC21 - CUSTOMER ORDER HISTORY
    # -----------------------------
    path("orders/", views.order_history, name="order_history"),
    path("orders/<str:order_id>/", views.order_detail, name="order_detail"),
    path("orders/<str:order_id>/reorder/", views.reorder, name="reorder"),
    path("orders/<str:order_id>/receipt/", views.download_receipt, name="download_receipt"),

    path("producer/orders/", views.producer_order_management, name="producer_order_management"),

    path("orders/<str:order_id>/purchase-review/", views.create_purchase_review, name="create_purchase_review"),



    path("products/suggestions/", views.product_search_suggestions, name="product_search_suggestions"),

    path("orders/<str:order_id>/purchase-review/", views.create_purchase_review, name="create_purchase_review"),



]
