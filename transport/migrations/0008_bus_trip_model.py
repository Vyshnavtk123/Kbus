from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ("transport", "0007_bus_trip_fields"),
    ]

    operations = [
        migrations.CreateModel(
            name="BusTrip",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("start_time", models.DateTimeField()),
                ("end_time", models.DateTimeField(blank=True, null=True)),
                ("bus", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, to="transport.bus")),
            ],
        ),
    ]
