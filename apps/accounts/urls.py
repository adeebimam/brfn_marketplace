from django.urls import path
from . import views

app_name = "accounts"

urlpatterns = [
    path("register/", views.register_view, name="register"),
    path("login/", views.login_view, name="login"),
    path("logout/", views.logout_view, name="logout"),
    path("register/producer/", views.producer_register_view, name="producer_register"),
    path("profile/", views.profile_view, name="profile"),

    path("admin-dashboard/", views.admin_dashboard, name="admin_dashboard"),
    path("account/<int:profile_id>/delete/", views.delete_account, name = "delete_account"),

    path("admin-producer-approvals/", views.producer_approvals, name="admin_producer_approvals"),
    path("admin/producers/<int:profile_id>/approve/", views.admin_approve_producer, name="admin_approve_producer"),
    path("admin/producers/<int:profile_id>/reject/", views.admin_reject_producer, name="admin_reject_producer"),

    path("admin-accounts/", views.admin_accounts, name="admin_accounts"),
    path("account/<int:profile_id>/", views.account_detail, name="account_detail"),

    path("admin-total-orders/", views.admin_total_orders, name="admin_total_orders"),
    path("admin-total-orders/<int:order_id>/", views.admin_order_detail, name="admin_order_detail"),
    path("admin-total-products/", views.admin_total_products, name="admin_total_products"),

    path("admin-total-products/<int:product_id>/", views.admin_product_detail, name="admin_product_detail"),
    path("admin-financials/",views.admin_financial_report, name="admin_financial_report"),
    
]