from django.contrib import admin
from django.urls import path, include
from django.shortcuts import redirect
from django.conf import settings
from django.conf.urls.static import static


def home_redirect(request):
    return redirect("marketplace:product_list")


def producer_redirect(request):
    return redirect("marketplace:producer_product_list")


urlpatterns = [
    path("admin/", admin.site.urls),
    path("accounts/", include("apps.accounts.urls")),

    # Root homepage
    path("", home_redirect, name="home"),

    # Producer shortcut
    path("producer/", producer_redirect, name="producer_dashboard"),
    
    # Cart routes
    path("cart/", include("apps.cart.urls")),

    # Marketplace routes
    path("", include("apps.marketplace.urls")),
    
    path("orders/", include("orders.urls")),
    
]

if settings.DEBUG:
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)
