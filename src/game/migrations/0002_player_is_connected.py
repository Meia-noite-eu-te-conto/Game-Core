# Generated by Django 5.1.2 on 2024-11-17 21:03

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("game", "0001_initial"),
    ]

    operations = [
        migrations.AddField(
            model_name="player",
            name="is_connected",
            field=models.BooleanField(default=False),
        ),
    ]