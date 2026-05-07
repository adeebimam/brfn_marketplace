from django.urls import path
from . import views

app_name = "marketplace"

urlpatterns = [
    # Customer browsing
    path("products/", views.product_list, name="product_list"),
    path("products/<int:pk>/", views.product_detail, name="product_detail"),
    path("products/suggestions/", views.product_search_suggestions, name="product_search_suggestions"),

    # Reviews
    path("products/<int:product_id>/reviews/create/", views.create_review, name="create_review"),

    # Producer products
    path("producer/products/", views.producer_product_list, name="producer_product_list"),
    path("producer/products/new/", views.product_create, name="product_create"),
    path("producer/products/<int:pk>/edit/", views.product_update, name="product_update"),
    path("producer/products/<int:pk>/delete/", views.product_delete, name="product_delete"),

    # Allergen test
    path("allergen-test/", views.allergen_test, name="allergen_test"),

    # Producer orders
    path("producer/orders/", views.producer_order_list, name="producer_order_list"),
    path("producer/orders/<int:pk>/", views.producer_order_detail, name="producer_order_detail"),
    path("producer/orders/<int:pk>/update-status/", views.producer_order_update_status, name="producer_order_update_status"),
    path("producer/order-management/", views.producer_order_management, name="producer_order_management"),

    # Producer payments
    path("producer/payments/", views.producer_payments, name="producer_payments"),
    path("producer/payments/download/", views.download_payments_csv, name="download_payments_csv"),

    # Stock notifications
    path("producer/stock-alerts/", views.stock_notifications, name="stock_notifications"),

    # Surplus deals
    path("surplus-deals/", views.surplus_deals, name="surplus_deals"),

    # Customer order history
    path("orders/", views.order_history, name="order_history"),
    path("orders/<str:order_id>/", views.order_detail, name="order_detail"),
    path("orders/<str:order_id>/reorder/", views.reorder, name="reorder"),
    path("orders/<str:order_id>/receipt/", views.download_receipt, name="download_receipt"),
    path("orders/<str:order_id>/purchase-review/", views.create_purchase_review, name="create_purchase_review"),
    path("orders/<str:order_id>/refund/", views.request_refund, name="request_refund"),

    # Refunds
    path("refunds/", views.refund_list, name="refund_list"),
    path("refunds/<int:refund_id>/respond/", views.producer_refund_response, name="producer_refund_response"),
    path("refunds/<int:refund_id>/decision/", views.admin_refund_decision, name="admin_refund_decision"),

    # Recurring orders
    path("recurring/setup/", views.recurring_order_setup, name="recurring_order_setup"),
    path("recurring/", views.recurring_order_list, name="recurring_order_list"),
    path("recurring/<int:pk>/", views.recurring_order_detail, name="recurring_order_detail"),
    path("recurring/<int:pk>/pause/", views.recurring_order_pause, name="recurring_order_pause"),
    path("recurring/<int:pk>/cancel/", views.recurring_order_cancel, name="recurring_order_cancel"),
    path("recurring/instance/<int:instance_pk>/modify/", views.recurring_order_modify_instance, name="recurring_order_modify_instance"),
    path("recurring/instance/<int:instance_pk>/skip/", views.recurring_order_skip_instance, name="recurring_order_skip_instance"),
    path("recurring/notifications/", views.recurring_notifications, name="recurring_notifications"),
    path("recurring/notifications/count/", views.recurring_notifications_count, name="recurring_notifications_count"),
]