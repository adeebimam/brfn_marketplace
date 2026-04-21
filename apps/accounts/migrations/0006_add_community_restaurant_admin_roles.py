# Generated manually – adds COMMUNITY_GROUP, RESTAURANT, ADMIN role choices

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("accounts", "0005_profile_delivery_address_profile_delivery_postcode"),
    ]

    operations = [
        migrations.AlterField(
            model_name="profile",
            name="role",
            field=models.CharField(
                choices=[
                    ("CUSTOMER", "Customer"),
                    ("PRODUCER", "Producer"),
                    ("COMMUNITY_GROUP", "Community Group"),
                    ("RESTAURANT", "Restaurant"),
                    ("ADMIN", "Admin"),
                ],
                default="CUSTOMER",
                max_length=20,
            ),
        ),
    ]
