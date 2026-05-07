from django.urls import path
from . import views

app_name = "accounts"

urlpatterns = [
    path("register/", views.register_view, name="register"),
    path("login/", views.login_view, name="login"),
    path("logout/", views.logout_view, name="logout"),
    path("register/producer/",views.producer_register_view, name="producer_register"),
    path("profile/", views.profile_view, name="profile"),
    path("producer/<str:username>/", views.producer_detail, name="producer_detail"),
]
