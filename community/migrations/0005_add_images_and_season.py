from django.db import migrations, models

class Migration(migrations.Migration):

    dependencies = [
        ('community', '0004_add_descriptions'),
    ]

    operations = [
        migrations.AddField(
            model_name='recipe',
            name='image',
            field=models.URLField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name='recipe',
            name='season',
            field=models.CharField(default='ALL', max_length=10, choices=[('SPRING', 'Spring'), ('SUMMER', 'Summer'), ('AUTUMN', 'Autumn'), ('WINTER', 'Winter'), ('ALL', 'All Season')]),
        ),
        migrations.AddField(
            model_name='farmstory',
            name='image',
            field=models.URLField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name='farmstory',
            name='season',
            field=models.CharField(default='ALL', max_length=10, choices=[('SPRING', 'Spring'), ('SUMMER', 'Summer'), ('AUTUMN', 'Autumn'), ('WINTER', 'Winter'), ('ALL', 'All Season')]),
        ),
    ]
