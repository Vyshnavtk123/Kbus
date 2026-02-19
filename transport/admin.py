from django.contrib import admin
from .models import User, Route, Stop, Bus, Ticket, BusLiveLocation, DriverRegistration, DriverBusAssignment

admin.site.register(User)
admin.site.register(Route)
admin.site.register(Stop)
admin.site.register(Bus)
admin.site.register(Ticket)
admin.site.register(BusLiveLocation)
admin.site.register(DriverRegistration)
admin.site.register(DriverBusAssignment)


# Register your models here.
