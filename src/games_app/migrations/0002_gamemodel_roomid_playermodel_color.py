# Generated by Django 5.1.3 on 2024-11-23 20:24

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("games_app", "0001_initial"),
    ]

    operations = [
        migrations.AddField(
            model_name="gamemodel",
            name="roomId",
            field=models.CharField(default=0, max_length=64),
            preserve_default=False,
        ),
        migrations.AddField(
            model_name="playermodel",
            name="color",
            field=models.CharField(default=0, max_length=64),
            preserve_default=False,
        ),
    ]