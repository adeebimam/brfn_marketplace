from django.conf import settings
from django.db import models


SEASON_CHOICES = [
    ("SPRING", "Spring"),
    ("SUMMER", "Summer"),
    ("AUTUMN", "Autumn"),
    ("WINTER", "Winter"),
    ("ALL", "All Season"),
]


class Recipe(models.Model):
    producer = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="recipes_created",
    )
    title = models.CharField(max_length=200)
    content = models.TextField(blank=True)
    products = models.ManyToManyField(
        "marketplace.Product",
        related_name="recipes",
        blank=True,
    )
    # New: allow selecting multiple products as "ingredients" (separate semantic name)
    ingredients = models.ManyToManyField(
        "marketplace.Product",
        related_name="recipe_ingredients",
        blank=True,
    )
    # Free-text field to list additional ingredients not supplied by producers
    other_ingredients = models.TextField(blank=True)
    # Short description/teaser shown on the feed pages
    description = models.TextField(blank=True)
    # Optional image file for the recipe (stored under MEDIA). Uses ImageField
    image = models.ImageField(upload_to="community/", null=True, blank=True)
    # Seasonal tag to help group recipes
    season = models.CharField(max_length=10, choices=SEASON_CHOICES, default="ALL")
    published = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return self.title


class FarmStory(models.Model):
    producer = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="stories_created",
    )
    title = models.CharField(max_length=200)
    body = models.TextField(blank=True)
    # Short description/teaser shown on the feed pages
    description = models.TextField(blank=True)
    # Optional image file and season tag for stories
    image = models.ImageField(upload_to="community/", null=True, blank=True)
    season = models.CharField(max_length=10, choices=SEASON_CHOICES, default="ALL")
    published = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return self.title
