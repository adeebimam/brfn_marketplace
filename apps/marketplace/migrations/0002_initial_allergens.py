from django.db import migrations

def create_initial_allergens(apps, schema_editor):
    Allergen = apps.get_model('marketplace', 'Allergen')
    allergens = ['Gluten', 'Milk', 'Eggs', 'Peanuts']
    for name in allergens:
        Allergen.objects.get_or_create(name=name)

class Migration(migrations.Migration):
    dependencies = [
        ('marketplace', '0001_initial'),
    ]

    operations = [
        migrations.RunPython(create_initial_allergens),
    ]
