from django.urls import path
from . import views

app_name = "message"

urlpatterns = [
    path("inbox/", views.inbox, name="inbox"),
    path("new/", views.start_message, name="start_message"),
    path("thread/<int:thread_id>/", views.thread_detail, name="thread_detail"),
]