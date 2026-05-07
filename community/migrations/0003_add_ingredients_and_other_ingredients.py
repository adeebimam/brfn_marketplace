# Generated migration to add ingredients M2M and other_ingredients field
from django.db import migrations, models


def copy_products_to_ingredients(apps, schema_editor):
    Recipe = apps.get_model('community', 'Recipe')
    Product = apps.get_model('marketplace', 'Product')
    for recipe in Recipe.objects.all():
        # copy existing products relations into ingredients
        for p in recipe.products.all():
            recipe.ingredients.add(p)


def clear_ingredients(apps, schema_editor):
    Recipe = apps.get_model('community', 'Recipe')
    for recipe in Recipe.objects.all():
        recipe.ingredients.clear()


class Migration(migrations.Migration):

    dependencies = [
        ('community', '0002_add_producer_fields'),
        ('marketplace', '0001_initial'),
    ]

    operations = [
        migrations.AddField(
            model_name='recipe',
            name='ingredients',
            field=models.ManyToManyField(blank=True, related_name='recipe_ingredients', to='marketplace.Product'),
        ),
        migrations.AddField(
            model_name='recipe',
            name='other_ingredients',
            field=models.TextField(blank=True),
        ),
        migrations.RunPython(copy_products_to_ingredients, reverse_code=clear_ingredients),
    ]
