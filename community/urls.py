from django.urls import path
from . import views

app_name = 'community'

urlpatterns = [
    path("", views.community_feed, name="community"),
    path("recipes/new/", views.create_recipe, name="create_recipe"),
    path("recipes/<int:pk>/", views.recipe_detail, name="recipe_detail"),
    path("recipes/<int:pk>/edit/", views.recipe_edit, name="recipe_edit"),
    path("posts/new/", views.create_post, name="create_post"),
    path("posts/<int:pk>/", views.story_detail, name="story_detail"),
    path("posts/<int:pk>/edit/", views.story_edit, name="story_edit"),
]
