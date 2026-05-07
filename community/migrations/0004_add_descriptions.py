from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('community', '0003_add_ingredients_and_other_ingredients'),
    ]

    operations = [
        migrations.AddField(
            model_name='recipe',
            name='description',
            field=models.TextField(blank=True),
        ),
        migrations.AddField(
            model_name='farmstory',
            name='description',
            field=models.TextField(blank=True),
        ),
    ]
