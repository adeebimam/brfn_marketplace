from django.shortcuts import render
from apps.marketplace.models import Product
from .models import Recipe, FarmStory
from .forms import FarmStoryForm, RecipeForm
from django.shortcuts import redirect, get_object_or_404
from django.db.models import Q
from django.contrib import messages
from django.http import Http404


def community_feed(request):
    # Show published items to everyone. If a user is authenticated show their own drafts too.
    if request.user.is_authenticated:
        recipes = Recipe.objects.filter(Q(published=True) | Q(producer=request.user)).order_by("-created_at")[:6]
        stories = FarmStory.objects.filter(Q(published=True) | Q(producer=request.user)).order_by("-created_at")[:10]
    else:
        recipes = Recipe.objects.filter(published=True).order_by("-created_at")[:6]
        stories = FarmStory.objects.filter(published=True).order_by("-created_at")[:10]

    context = {
        "recipes": recipes,
        "stories": stories,
    }

    return render(
        request,
        "community/community_feed.html",
        context
    )


def create_recipe(request):
    # Render and process a Recipe create form
    if request.method == "POST":
        form = RecipeForm(request.POST, request.FILES, user=request.user)
        if form.is_valid():
            recipe = form.save(commit=False)
            # respect the published flag from the form; do not auto-publish
            if request.user.is_authenticated:
                recipe.producer = request.user
            # if description blank, auto-fill from the first ~100 words of content
            if not recipe.description or not recipe.description.strip():
                words = recipe.content.split()
                recipe.description = " ".join(words[:100])
            recipe.save()
            form.save_m2m()
            return redirect("community:community")
        else:
            messages.error(request, "There were errors saving the recipe — please check the form and try again.")
            print("RecipeForm invalid (create):", form.errors)
    else:
        # sensible default so the form is valid by default when opened
        form = RecipeForm(user=request.user, initial={"season": "ALL"})

    return render(request, "community/create_recipe.html", {"form": form})


def recipe_detail(request, pk):
    recipe = get_object_or_404(Recipe, pk=pk)
    # Only allow access to unpublished recipes for the producer or staff
    if not recipe.published:
        if not request.user.is_authenticated or (request.user != recipe.producer and not request.user.is_staff):
            raise Http404()
    # Split other_ingredients into cleaned lines for template iteration
    other_lines = []
    if recipe.other_ingredients:
        # split on newlines and commas; prefer line breaks but also support comma-separated lists
        # First, split on newlines
        for line in recipe.other_ingredients.splitlines():
            # further split comma-separated items on the same line
            parts = [p.strip() for p in line.split(',') if p.strip()]
            for p in parts:
                other_lines.append(p)

    return render(request, "community/recipe_detail.html", {"recipe": recipe, "other_ingredient_lines": other_lines})


def story_detail(request, pk):
    story = get_object_or_404(FarmStory, pk=pk)
    # Only allow access to unpublished stories for the producer or staff
    if not story.published:
        if not request.user.is_authenticated or (request.user != story.producer and not request.user.is_staff):
            raise Http404()
    return render(request, "community/story_detail.html", {"story": story})


def recipe_edit(request, pk):
    recipe = get_object_or_404(Recipe, pk=pk)
    # Permission: only producer or staff can edit
    if not request.user.is_authenticated or (request.user != recipe.producer and not request.user.is_staff):
        raise Http404()

    if request.method == "POST":
        form = RecipeForm(request.POST, request.FILES, instance=recipe, user=request.user)
        if form.is_valid():
            r = form.save(commit=False)
            # respect published flag from form; keep producer
            if request.user.is_authenticated:
                r.producer = recipe.producer or request.user
            # auto-fill description if empty
            if not r.description or not r.description.strip():
                words = r.content.split()
                r.description = " ".join(words[:100])
            r.save()
            form.save_m2m()
            return redirect("community:recipe_detail", pk=r.pk)
        else:
            messages.error(request, "There were errors saving the recipe — please check the form and try again.")
            print("RecipeForm invalid (edit):", form.errors)
    else:
        form = RecipeForm(instance=recipe, user=request.user)

    return render(request, "community/create_recipe.html", {"form": form, "editing": True, "object": recipe})


def story_edit(request, pk):
    story = get_object_or_404(FarmStory, pk=pk)
    if not request.user.is_authenticated or (request.user != story.producer and not request.user.is_staff):
        raise Http404()

    if request.method == "POST":
        form = FarmStoryForm(request.POST, request.FILES, instance=story)
        if form.is_valid():
            s = form.save(commit=False)
            if request.user.is_authenticated:
                s.producer = story.producer or request.user
            # auto-fill description if empty
            if not s.description or not s.description.strip():
                words = s.body.split()
                s.description = " ".join(words[:100])
            s.save()
            return redirect("community:story_detail", pk=s.pk)
        else:
            messages.error(request, "There were errors saving the post — please check the form and try again.")
            print("FarmStoryForm invalid (edit):", form.errors)
    else:
        form = FarmStoryForm(instance=story)

    return render(request, "community/create_post.html", {"form": form, "editing": True, "object": story})


def create_post(request):
    # Render and process a simple FarmStory create form
    if request.method == "POST":
        form = FarmStoryForm(request.POST, request.FILES)
        if form.is_valid():
            story = form.save(commit=False)
            if request.user.is_authenticated:
                story.producer = request.user
            # auto-fill description from body when blank
            if not story.description or not story.description.strip():
                words = story.body.split()
                story.description = " ".join(words[:100])
            story.save()
            return redirect("community:community")
        else:
            messages.error(request, "There were errors saving the post — please check the form and try again.")
            print("FarmStoryForm invalid (create):", form.errors)
    else:
        # default season to ALL for a smoother create flow
        form = FarmStoryForm(initial={"season": "ALL"})

    return render(request, "community/create_post.html", {"form": form})