from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


def forwards(apps, schema_editor):
    DriverRegistration = apps.get_model("transport", "DriverRegistration")
    User = apps.get_model("transport", "User")

    for reg in DriverRegistration.objects.all():
        # Only old rows (from migration 0004) have driver_name/role fields
        driver_name = getattr(reg, "driver_name", None)
        if not driver_name:
            continue

        user = User.objects.filter(username=driver_name).first()
        if not user:
            # Historical model instances in migrations don't have helpers like
            # set_unusable_password()/set_password(). Use an unusable password
            # marker directly (Django treats passwords starting with '!' as unusable).
            user = User.objects.create(username=driver_name, role="driver", password="!")

        reg.user = user
        reg.save(update_fields=["user"])


class Migration(migrations.Migration):

    dependencies = [
        ("transport", "0004_driver_registration"),
    ]

    operations = [
        migrations.AddField(
            model_name="driverregistration",
            name="user",
            field=models.OneToOneField(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.CASCADE,
                to=settings.AUTH_USER_MODEL,
            ),
        ),
        migrations.RunPython(forwards, migrations.RunPython.noop),
        migrations.RemoveField(
            model_name="driverregistration",
            name="driver_name",
        ),
        migrations.RemoveField(
            model_name="driverregistration",
            name="role",
        ),
        migrations.AlterField(
            model_name="driverregistration",
            name="user",
            field=models.OneToOneField(
                on_delete=django.db.models.deletion.CASCADE,
                to=settings.AUTH_USER_MODEL,
            ),
        ),
    ]
