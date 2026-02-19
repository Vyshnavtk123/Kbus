from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("transport", "0006_bus_current_stop"),
    ]

    operations = [
        migrations.AddField(
            model_name="bus",
            name="trip_active",
            field=models.BooleanField(default=False),
        ),
        migrations.AddField(
            model_name="bus",
            name="trip_start_time",
            field=models.DateTimeField(blank=True, null=True),
        ),
    ]
