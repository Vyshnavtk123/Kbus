from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("transport", "0005_driverregistration_user_fk"),
    ]

    operations = [
        migrations.AddField(
            model_name="bus",
            name="current_stop",
            field=models.CharField(blank=True, max_length=100, null=True),
        ),
    ]
