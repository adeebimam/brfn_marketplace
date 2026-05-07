from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ("marketplace", "0027_alter_product_surplus_discount_amount"),
    ]

    operations = [
        migrations.CreateModel(
            name="FavouriteProducer",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("customer", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="favourite_producers", to=settings.AUTH_USER_MODEL)),
                ("producer", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="favourited_by_customers", to=settings.AUTH_USER_MODEL)),
            ],
            options={
                "ordering": ["-created_at"],
                "unique_together": {("customer", "producer")},
            },
        ),
        migrations.CreateModel(
            name="SurplusDealNotification",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("message", models.CharField(max_length=255)),
                ("is_read", models.BooleanField(default=False)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("customer", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="surplus_deal_notifications", to=settings.AUTH_USER_MODEL)),
                ("producer", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="sent_surplus_deal_notifications", to=settings.AUTH_USER_MODEL)),
                ("product", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="surplus_notifications", to="marketplace.product")),
            ],
            options={
                "ordering": ["is_read", "-created_at"],
                "unique_together": {("customer", "producer", "product")},
            },
        ),
    ]
