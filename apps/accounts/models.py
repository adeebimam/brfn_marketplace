from django.conf import settings
from django.db import models

#Create your models here

class Profile(models.Model):
    class Role(models.TextChoices):
        CUSTOMER = "CUSTOMER", "Customer"
        PRODUCER = "PRODUCER", "Producer"
        

    user = models.OneToOneField(settings.AUTH_USER_MODEL, on_delete=models.CASCADE)
    role = models.CharField(max_length=20, choices=Role.choices, default=Role.CUSTOMER)
    business_name = models.CharField(max_length=255, blank = True)
    contact_first_name = models.CharField(max_length=255, blank=True)
    contact_last_name = models.CharField(max_length=255, blank=True)
    phone = models.CharField(max_length=255, blank=True)
    address = models.CharField(max_length=255,blank=True)
    postcode = models.CharField(max_length=20, blank= True)
    delivery_address = models.TextField(blank=True)
    delivery_postcode = models.CharField(max_length=20, blank=True)
    def __str__(self):
        return f"{self.user.username} ({self.role})"
