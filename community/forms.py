from django import forms
from apps.marketplace.models import Product
from .models import FarmStory


class FarmStoryForm(forms.ModelForm):
    class Meta:
        model = FarmStory
        fields = ["title", "description", "body", "season", "image", "published"]
        widgets = {
            "title": forms.TextInput(attrs={"class": "form-control", "placeholder": "Post title"}),
            "description": forms.Textarea(attrs={"class": "form-control", "rows": 2, "placeholder": "Short description (max ~100 words)"}),
            "body": forms.Textarea(attrs={"class": "form-control", "rows": 6, "placeholder": "Write something..."}),
            "published": forms.CheckboxInput(attrs={"class": "form-check-input"}),
        }


from .models import Recipe


class RecipeForm(forms.ModelForm):
    def __init__(self, *args, **kwargs):
        # pop a passed-in user and use it to limit the ingredients queryset
        user = kwargs.pop("user", None)
        super().__init__(*args, **kwargs)

        if "ingredients" in self.fields:
            try:
                if user and user.is_authenticated:
                    # show only products owned by the current user (producer)
                    self.fields["ingredients"].queryset = Product.objects.filter(producer=user)
                else:
                    # no user or not authenticated -> empty choices
                    self.fields["ingredients"].queryset = Product.objects.none()
            except Exception:
                # defensive fallback to none if Product model isn't available for some reason
                self.fields["ingredients"].queryset = Product.objects.none()
    class Meta:
        model = Recipe
        fields = ["title", "description", "content", "season", "image", "published", "ingredients", "other_ingredients"]
        widgets = {
            "title": forms.TextInput(attrs={"class": "form-control", "placeholder": "Recipe title"}),
            "description": forms.Textarea(attrs={"class": "form-control", "rows": 2, "placeholder": "Short description (max ~100 words)"}),
            "content": forms.Textarea(attrs={"class": "form-control", "rows": 6, "placeholder": "Recipe content"}),
            "published": forms.CheckboxInput(attrs={"class": "form-check-input"}),
            # render ingredients as checkboxes for better multi-select UX
            "ingredients": forms.CheckboxSelectMultiple(attrs={"class": "form-check-input"}),
            "other_ingredients": forms.Textarea(attrs={"class": "form-control", "rows": 3, "placeholder": "Other ingredients (comma separated or free text)"}),
        }
