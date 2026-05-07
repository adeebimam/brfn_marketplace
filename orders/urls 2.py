from django.urls import path
from . import views

app_name = "orders"

urlpatterns = [
    path("checkout/", views.checkout, name="checkout"),
    path("payment/", views.payment, name="payment"),
    path("history/", views.order_history, name="history"),
    path("<int:pk>/", views.order_detail, name="detail"),
]
