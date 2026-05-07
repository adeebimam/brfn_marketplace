from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ("community", "0001_initial"),
    ]

    operations = [
        migrations.AddField(
            model_name="recipe",
            name="producer",
            field=models.ForeignKey(
                related_name="recipes_created",
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                to="%s" % ("auth.user",),
                blank=True,
            ),
        ),
        migrations.AddField(
            model_name="farmstory",
            name="producer",
            field=models.ForeignKey(
                related_name="stories_created",
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                to="%s" % ("auth.user",),
                blank=True,
            ),
        ),
    ]
