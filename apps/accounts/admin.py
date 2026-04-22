from django.contrib import admin
from .models import Profile


@admin.register(Profile)
class ProfileAdmin(admin.ModelAdmin):
    list_display = (
        "user",
        "role",
        "business_name",
        "verification_status",
        "is_verified",
    )
    list_filter = (
        "role",
        "verification_status",
        "is_verified",
        "business_type",
    )
    search_fields = (
        "user__username",
        "user__email",
        "business_name",
        "contact_first_name",
        "contact_last_name",
    )