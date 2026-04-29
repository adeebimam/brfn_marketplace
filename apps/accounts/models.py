from django.conf import settings
from django.db import models


class Profile(models.Model):
    class Role(models.TextChoices):
        CUSTOMER = "CUSTOMER", "Customer"
        PRODUCER = "PRODUCER", "Producer"
        COMMUNITY_GROUP = "COMMUNITY_GROUP", "Community Group"
        RESTAURANT = "RESTAURANT", "Restaurant / Café"
        ADMIN = "ADMIN", "Admin"

    class BusinessType(models.TextChoices):
        RESTAURANT = "RESTAURANT", "Restaurant"
        CAFE = "CAFE", "Café"
        BISTRO = "BISTRO", "Bistro"
        TAKEAWAY = "TAKEAWAY", "Takeaway"
        PUB = "PUB", "Pub / Bar"
        OTHER = "OTHER", "Other"

    class VerificationStatus(models.TextChoices):
        PENDING = "PENDING", "Pending"
        APPROVED = "APPROVED", "Approved"
        REJECTED = "REJECTED", "Rejected"

    user = models.OneToOneField(settings.AUTH_USER_MODEL, on_delete=models.CASCADE)

    role = models.CharField(max_length=20, choices=Role.choices, default=Role.CUSTOMER)

    business_type = models.CharField(
        max_length=20,
        choices=BusinessType.choices,
        blank=True,
        help_text="Sub-type for Restaurant / Café accounts.",
    )

    business_name = models.CharField(max_length=255, blank=True)
    contact_first_name = models.CharField(max_length=255, blank=True)
    contact_last_name = models.CharField(max_length=255, blank=True)
    phone = models.CharField(max_length=255, blank=True)
    address = models.CharField(max_length=255, blank=True)
    postcode = models.CharField(max_length=20, blank=True)
    delivery_address = models.TextField(blank=True)
    delivery_postcode = models.CharField(max_length=20, blank=True)

    verification_status = models.CharField(
        max_length=20,
        choices=VerificationStatus.choices,
        default=VerificationStatus.APPROVED,
    )
    is_verified = models.BooleanField(default=True)
    verification_notes = models.TextField(blank=True)

    def __str__(self):
        return f"{self.user.username} ({self.display_role})"

    @property
    def display_role(self):
        """Return a user-friendly label, using business_type for Restaurant/Café accounts."""
        if self.role == self.Role.RESTAURANT and self.business_type:
            return self.get_business_type_display()
        return self.get_role_display()