from django.db import models
from django.contrib.auth.models import AbstractUser
from django.utils import timezone
import random
import string

# ---------- USER ----------
class User(AbstractUser):
    ROLE_CHOICES = (
        ('admin', 'Admin'),
        ('passenger', 'Passenger'),
        ('driver', 'Driver'),
    )
    role = models.CharField(max_length=20, choices=ROLE_CHOICES)


# ---------- ROUTE ----------
class Route(models.Model):
    name = models.CharField(max_length=100)
    source = models.CharField(max_length=100)
    destination = models.CharField(max_length=100)

    def __str__(self):
        return self.name


# ---------- STOP ----------
class Stop(models.Model):
    route = models.ForeignKey(Route, on_delete=models.CASCADE)
    name = models.CharField(max_length=100)
    order = models.IntegerField()
    latitude = models.FloatField()
    longitude = models.FloatField()

    def __str__(self):
        return self.name


# ---------- BUS ----------
def generate_otp():
    return ''.join(random.choices(string.ascii_uppercase + string.digits, k=5))

class Bus(models.Model):
    vehicle_number = models.CharField(max_length=20)
    operator_type = models.CharField(max_length=20)
    operator_name = models.CharField(max_length=100)
    route = models.ForeignKey(Route, on_delete=models.CASCADE)
    base_fare = models.DecimalField(max_digits=6, decimal_places=2)
    otp_code = models.CharField(max_length=5, default=generate_otp)
    status = models.CharField(max_length=20, default="active")
    current_stop = models.CharField(max_length=100, null=True, blank=True)
    trip_active = models.BooleanField(default=False)
    trip_start_time = models.DateTimeField(null=True, blank=True)

    def __str__(self):
        return self.vehicle_number


# ---------- TICKET ----------
class Ticket(models.Model):
    user = models.ForeignKey(User, on_delete=models.CASCADE)
    bus = models.ForeignKey(Bus, on_delete=models.CASCADE)
    source_stop = models.CharField(max_length=100)
    destination_stop = models.CharField(max_length=100)
    fare = models.DecimalField(max_digits=6, decimal_places=2)
    created_at = models.DateTimeField(auto_now_add=True)
    expires_at = models.DateTimeField(null=True, blank=True)


# ---------- LIVE TRACKING ----------
class BusLiveLocation(models.Model):
    bus = models.ForeignKey(Bus, on_delete=models.CASCADE)
    latitude = models.FloatField()
    longitude = models.FloatField()
    speed = models.FloatField()
    updated_at = models.DateTimeField(auto_now=True)


# ---------- DRIVER REGISTRATION (simple mapping) ----------
class DriverRegistration(models.Model):
    user = models.OneToOneField(User, on_delete=models.CASCADE)
    bus_otp = models.CharField(max_length=5)
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"{self.user.username} -> {self.bus_otp}"


# ---------- DRIVER BUS ASSIGNMENT (shift-based mapping) ----------
class DriverBusAssignment(models.Model):
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name='bus_assignments')
    bus = models.ForeignKey(Bus, on_delete=models.CASCADE, related_name='driver_assignments')
    active = models.BooleanField(default=True)
    start_time = models.DateTimeField(default=timezone.now)
    end_time = models.DateTimeField(null=True, blank=True)

    def __str__(self):
        status = 'active' if self.active and self.end_time is None else 'inactive'
        return f"{self.user.username} -> {self.bus.vehicle_number} ({status})"


# ---------- BUS TRIPS (history) ----------
class BusTrip(models.Model):
    bus = models.ForeignKey(Bus, on_delete=models.CASCADE)
    start_time = models.DateTimeField()
    end_time = models.DateTimeField(null=True, blank=True)

    def __str__(self):
        return f"{self.bus.vehicle_number} {self.start_time}"


# Create your models here.
