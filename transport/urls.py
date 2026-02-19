from django.urls import path
from .views import *

from rest_framework.authtoken.views import obtain_auth_token

from rest_framework_simplejwt.views import (
    TokenObtainPairView,
    TokenRefreshView,
)

urlpatterns = [
    # path('register/', register_user),
    path('validate/', validate_bus_otp, name='validate'),
    path('validate/<str:otp>/', validate_bus_otp, name='validate_otp'),
    path('book-ticket/', bus_ticket, name='book_ticket'),
    path('create-route/', create_route),
    path('add-stop/', add_stop),
    path('register-bus/', register_bus),
    path('register-driver/', register_driver, name='register_driver'),
    path('assign-driver-bus/', assign_driver_bus, name='assign_driver_bus'),
    path('get-stops/<str:otp>/', get_stops_by_otp),
    path('calculate-fare/', calculate_fare),
    path('my-tickets/<int:user_id>/', my_tickets),
    path('register/', register_view, name='register'),
    path('login/', login_view, name='login'),
    path('kbus/', kbus_view, name='kbus'),
    path('select/<str:otp>/', passenger_select, name='passenger_select'),


    path('admin-dashboard/', admin_dashboard, name='admin_dashboard'),
    path('passenger/', passenger_otp_view, name='passenger'),
    path('ticket/<int:ticket_id>/', ticket_view, name='ticket_view'),
    path('get-route-stops/<int:route_id>/', get_route_stops),


    path('update-location/<int:bus_id>/', update_bus_location, name='update_bus_location'),
    path('bus-location/<int:bus_id>/', get_bus_location, name='get_bus_location'),

    path('driver-dashboard/', driver_dashboard, name='driver_dashboard'),
    path('driver-trip-summary/<int:bus_id>/', driver_trip_summary, name='driver_trip_summary'),
    path('driver-trip/<int:trip_id>/', driver_trip_details, name='driver_trip_details'),
    path('start-trip/<int:bus_id>/', start_trip, name='start_trip'),
    path('end-trip/<int:bus_id>/', end_trip, name='end_trip'),







    path('auth/', obtain_auth_token, name='auth'),

    path('api/token/', TokenObtainPairView.as_view(), name='token_obtain_pair'),
    path('api/token/refresh/', TokenRefreshView.as_view(), name='token_refresh'),
]
