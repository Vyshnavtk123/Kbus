from __future__ import annotations

import json
import math
import re
from decimal import Decimal, ROUND_CEILING
from datetime import timedelta

from django.contrib import messages
from django.contrib.auth import authenticate, get_user_model, login
from django.contrib.auth.decorators import login_required
from django.contrib.staticfiles import finders
from django.db.models import Sum
from django.http import Http404, HttpResponse, JsonResponse
from django.shortcuts import redirect, render
from django.utils import timezone
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_GET

from .models import (
    Bus,
    BusLiveLocation,
    BusTrip,
    DriverBusAssignment,
    DriverRegistration,
    Route,
    Stop,
    Ticket,
)


@require_GET
def kbus_route_kml(request):
    """Serve the route KML from installed static sources.

    This avoids deployment issues where `/static/...` isn't available (e.g. collectstatic
    not running or static hosting misconfigured), which would make map stop markers vanish.
    """

    kml_path = finders.find('maps/K-BUS Route.kml')
    if not kml_path:
        raise Http404('KML not found')

    with open(kml_path, 'rb') as f:
        content = f.read()

    resp = HttpResponse(content, content_type='application/vnd.google-earth.kml+xml; charset=utf-8')
    resp['Cache-Control'] = 'public, max-age=3600'
    return resp


def distance_meters(lat1, lon1, lat2, lon2):
    R = 6371000
    phi1 = math.radians(float(lat1))
    phi2 = math.radians(float(lat2))
    dphi = math.radians(float(lat2) - float(lat1))
    dlambda = math.radians(float(lon2) - float(lon1))

    a = math.sin(dphi / 2) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(dlambda / 2) ** 2
    return R * (2 * math.atan2(math.sqrt(a), math.sqrt(1 - a)))


def _estimate_speed_mps(prev_lat, prev_lng, prev_time, lat, lng, now_time):
    if prev_lat is None or prev_lng is None or prev_time is None:
        return 0.0
    try:
        dt = (now_time - prev_time).total_seconds()
    except Exception:
        return 0.0
    if dt <= 0.5 or dt > 120:
        return 0.0
    d = distance_meters(prev_lat, prev_lng, lat, lng)
    if d < 0:
        return 0.0
    return float(d) / float(dt)


def _route_remaining_distance_m(bus, lat, lng):
    """Returns (next_stop_obj, distance_to_next_m, remaining_path_m).

    remaining_path_m includes: current position -> next stop + subsequent stop-to-stop distances.
    """
    stops = list(Stop.objects.filter(route=bus.route).order_by('order', 'id'))
    if not stops:
        return (None, None, None)

    current = None
    if bus.current_stop:
        for s in stops:
            if s.name == bus.current_stop:
                current = s
                break

    if current is None:
        next_stop = stops[0]
        remaining = distance_meters(lat, lng, next_stop.latitude, next_stop.longitude)
        return (next_stop, remaining, remaining)

    remaining_stops = [s for s in stops if s.order > current.order]
    if not remaining_stops:
        return (None, 0.0, 0.0)

    next_stop = remaining_stops[0]
    dist_to_next = distance_meters(lat, lng, next_stop.latitude, next_stop.longitude)
    path = dist_to_next

    prev = next_stop
    for s in remaining_stops[1:]:
        path += distance_meters(prev.latitude, prev.longitude, s.latitude, s.longitude)
        prev = s

    return (next_stop, dist_to_next, path)


def _route_path_distance_m(route, source_stop: Stop, dest_stop: Stop) -> float:
    stops = list(Stop.objects.filter(route=route).order_by('order', 'id'))
    if not stops:
        return float(distance_meters(source_stop.latitude, source_stop.longitude, dest_stop.latitude, dest_stop.longitude))

    id_to_index = {s.id: i for i, s in enumerate(stops)}
    i_src = id_to_index.get(source_stop.id)
    i_dst = id_to_index.get(dest_stop.id)
    if i_src is None or i_dst is None or i_dst <= i_src:
        return float(distance_meters(source_stop.latitude, source_stop.longitude, dest_stop.latitude, dest_stop.longitude))

    total = 0.0
    for i in range(i_src, i_dst):
        a = stops[i]
        b = stops[i + 1]
        total += float(distance_meters(a.latitude, a.longitude, b.latitude, b.longitude))
    return float(total)


def _fare_from_distance_m(distance_m: float) -> Decimal:
    # Pricing rule:
    # - Up to 2.5 km => ₹10
    # - After 2.5 km => +₹1 for each additional started 1 km
    base_km = Decimal('2.5')
    base_fare = Decimal('10')

    try:
        km = (Decimal(str(distance_m)) / Decimal('1000'))
    except Exception:
        return base_fare

    if km <= base_km:
        return base_fare

    extra_km = km - base_km
    # started-km charging: ceil(extra_km)
    extra_units = int(extra_km.to_integral_value(rounding=ROUND_CEILING))
    return (base_fare + Decimal(extra_units)).quantize(Decimal('0.01'))


User = get_user_model()


_OTP_RE = re.compile(r'^[A-Z0-9]{5}$')


def _normalize_bus_otp(value) -> str | None:
    if value is None:
        return None
    otp = str(value).strip().upper()
    if not otp:
        return None
    if not _OTP_RE.fullmatch(otp):
        return None
    return otp


def _to_int(value, *, min_value: int | None = None, max_value: int | None = None):
    try:
        i = int(str(value).strip())
    except Exception:
        return None
    if min_value is not None and i < min_value:
        return None
    if max_value is not None and i > max_value:
        return None
    return i


def _to_float(value, *, min_value: float | None = None, max_value: float | None = None):
    try:
        f = float(str(value).strip())
    except Exception:
        return None
    if min_value is not None and f < min_value:
        return None
    if max_value is not None and f > max_value:
        return None
    return f


def _to_decimal(value, *, min_value: Decimal | None = None):
    try:
        d = Decimal(str(value).strip())
    except Exception:
        return None
    if min_value is not None and d < min_value:
        return None
    return d


def kbus_view(request):
    if not request.user.is_authenticated:
        return redirect('login')

    role = getattr(request.user, 'role', None)
    if role == 'admin':
        return redirect('admin_dashboard')
    if role == 'driver':
        return redirect('driver_dashboard')
    return redirect('passenger')


def register_view(request):
    if request.method == 'POST':
        username = (request.POST.get('username') or '').strip()
        password = request.POST.get('password') or ''
        confirm_password = request.POST.get('confirm_password') or ''
        if not username or not password or not confirm_password:
            return render(request, 'register.html', {'error': 'Username and password are required'})
        if len(password) < 6:
            return render(request, 'register.html', {'error': 'Password must be at least 6 characters'})
        if password != confirm_password:
            return render(request, 'register.html', {'error': 'Passwords do not match'})
        if User.objects.filter(username=username).exists():
            return render(request, 'register.html', {'error': 'Username already exists'})
        User.objects.create_user(username=username, password=password, role='passenger')
        return redirect('login')
    return render(request, 'register.html')


def login_view(request):
    if request.method == 'POST':
        username = (request.POST.get('username') or '').strip()
        password = request.POST.get('password') or ''
        user = authenticate(request, username=username, password=password)
        if user is None:
            return render(request, 'login.html', {'error': 'Invalid credentials'})
        login(request, user)
        if getattr(user, 'role', None) == 'admin':
            return redirect('admin_dashboard')
        if getattr(user, 'role', None) == 'driver':
            return redirect('driver_dashboard')
        return redirect('passenger')
    return render(request, 'login.html')


@login_required
def passenger_otp_view(request):
    tickets = (
        Ticket.objects.filter(user=request.user)
        .select_related('bus', 'bus__route')
        .order_by('-created_at', '-id')
    )
    return render(request, 'passenger_otp.html', {'tickets': tickets, 'now': timezone.now()})


@login_required
def validate_bus_otp(request, otp=None):
    otp_from_url = (request.method == 'GET' and otp is not None)
    if request.method == 'POST':
        otp = request.POST.get('otp')

    otp = _normalize_bus_otp(otp)

    if otp_from_url and not otp:
        return JsonResponse({'valid': False, 'error': 'Invalid OTP'}, status=400)

    if not otp:
        messages.error(request, 'Enter OTP')
        return redirect('passenger')

    bus = Bus.objects.filter(otp_code=otp).first()
    if not bus:
        if request.method == 'GET' and otp is not None:
            return JsonResponse({'valid': False, 'error': 'Invalid OTP'}, status=400)
        messages.error(request, 'Invalid OTP')
        return redirect('passenger')

    if request.method == 'GET' and otp is not None:
        return JsonResponse({'valid': True, 'otp': otp, 'bus_id': bus.id})

    return redirect('passenger_select', otp=otp)


@login_required
def passenger_select(request, otp):
    otp = _normalize_bus_otp(otp)
    if not otp:
        messages.error(request, 'Invalid OTP')
        return redirect('passenger')

    bus = Bus.objects.filter(otp_code=otp).first()
    if not bus:
        messages.error(request, 'Invalid OTP')
        return redirect('passenger')
    routes = Route.objects.all()
    return render(request, 'passenger_select.html', {'routes': routes, 'otp': otp})


@login_required
def bus_ticket(request):
    if request.method != 'POST':
        return redirect('passenger')

    otp = _normalize_bus_otp(request.POST.get('otp'))
    source = (request.POST.get('source') or '').strip()
    destination = (request.POST.get('destination') or '').strip()

    if not otp:
        messages.error(request, 'Invalid OTP')
        return redirect('passenger')

    if not (source and destination):
        messages.error(request, 'Please select source and destination')
        return redirect('passenger')

    bus = Bus.objects.filter(otp_code=otp).first()
    if not bus:
        messages.error(request, 'Invalid OTP')
        return redirect('passenger')

    stops = Stop.objects.filter(route=bus.route)
    source_id = _to_int(source, min_value=1)
    dest_id = _to_int(destination, min_value=1)
    source_stop = stops.filter(id=source_id).first() if source_id else stops.filter(name=source).order_by('order', 'id').first()
    dest_stop = stops.filter(id=dest_id).first() if dest_id else stops.filter(name=destination).order_by('order', 'id').first()

    if not source_stop or not dest_stop:
        messages.error(request, 'Stop not found')
        return redirect('passenger_select', otp=otp)

    if source_stop.id == dest_stop.id:
        messages.error(request, 'Source and destination cannot be the same stop')
        return redirect('passenger_select', otp=otp)

    if dest_stop.order <= source_stop.order:
        messages.error(request, 'Invalid stop selection')
        return redirect('passenger_select', otp=otp)

    distance_m = _route_path_distance_m(bus.route, source_stop, dest_stop)
    fare = _fare_from_distance_m(distance_m)
    ticket = Ticket.objects.create(
        user=request.user,
        bus=bus,
        source_stop=source_stop.name,
        destination_stop=dest_stop.name,
        fare=fare,
        expires_at=timezone.now() + timedelta(minutes=30),
    )

    return redirect('ticket_view', ticket_id=ticket.id)


def _require_admin(request):
    if getattr(request.user, 'role', None) != 'admin':
        messages.error(request, 'Admin only')
        return False
    return True


@login_required
def admin_dashboard(request):
    if not _require_admin(request):
        return redirect('login')
    routes = Route.objects.all()
    stops = Stop.objects.all()
    buses = Bus.objects.all()
    users = User.objects.all()
    drivers = User.objects.filter(role='driver').order_by('username')
    tickets = Ticket.objects.all()
    return render(request, 'admin_dashboard.html', {
        'routes': routes,
        'stops': stops,
        'buses': buses,
        'users': users,
        'drivers': drivers,
        'tickets': tickets,
    })


@login_required
def create_route(request):
    if not _require_admin(request):
        return redirect('admin_dashboard')
    if request.method == 'POST':
        name = (request.POST.get('name') or '').strip()
        source = (request.POST.get('source') or '').strip()
        destination = (request.POST.get('destination') or '').strip()
        if not (name and source and destination):
            messages.error(request, 'All route fields are required')
            return redirect('admin_dashboard')

        Route.objects.create(name=name, source=source, destination=destination)
        messages.success(request, 'Route registered successfully')
    return redirect('admin_dashboard')


@login_required
def add_stop(request):
    if not _require_admin(request):
        return redirect('admin_dashboard')
    if request.method == 'POST':
        route_id_raw = request.POST.get('route_id')
        name = (request.POST.get('name') or '').strip()
        order_raw = request.POST.get('order')
        lat_raw = request.POST.get('latitude')
        lon_raw = request.POST.get('longitude')

        route_id = _to_int(route_id_raw, min_value=1)
        order = _to_int(order_raw, min_value=0)
        lat = _to_float(lat_raw, min_value=-90.0, max_value=90.0)
        lon = _to_float(lon_raw, min_value=-180.0, max_value=180.0)

        if not route_id:
            messages.error(request, 'Select a valid route')
            return redirect('admin_dashboard')
        if not name:
            messages.error(request, 'Stop name is required')
            return redirect('admin_dashboard')
        if order is None:
            messages.error(request, 'Stop order must be a valid number')
            return redirect('admin_dashboard')
        if lat is None or lon is None:
            messages.error(request, 'Select a valid stop location on the map')
            return redirect('admin_dashboard')

        route = Route.objects.filter(id=route_id).first()
        if not route:
            messages.error(request, 'Route not found')
            return redirect('admin_dashboard')

        Stop.objects.create(route=route, name=name, order=order, latitude=lat, longitude=lon)
        messages.success(request, 'Stop Added successfully')
    return redirect('admin_dashboard')


@csrf_exempt
@login_required
def register_bus(request):
    if not _require_admin(request):
        return redirect('admin_dashboard')
    if request.method == 'POST':
        vehicle_number = (request.POST.get('vehicle_number') or '').strip()
        operator_type = (request.POST.get('operator_type') or '').strip()
        operator_name = (request.POST.get('operator_name') or '').strip()
        route_id = _to_int(request.POST.get('route_id'), min_value=1)
        base_fare = _to_decimal(request.POST.get('base_fare'), min_value=Decimal('0.01'))

        if not vehicle_number:
            messages.error(request, 'Vehicle number is required')
            return redirect('admin_dashboard')
        if not route_id:
            messages.error(request, 'Select a valid route')
            return redirect('admin_dashboard')
        if not operator_type:
            messages.error(request, 'Operator type is required')
            return redirect('admin_dashboard')
        if not operator_name:
            messages.error(request, 'Operator name is required')
            return redirect('admin_dashboard')
        if base_fare is None:
            messages.error(request, 'Base fare must be a valid number')
            return redirect('admin_dashboard')

        route = Route.objects.filter(id=route_id).first()
        if not route:
            messages.error(request, 'Route not found')
            return redirect('admin_dashboard')

        Bus.objects.create(
            vehicle_number=vehicle_number,
            operator_type=operator_type,
            operator_name=operator_name,
            route=route,
            base_fare=base_fare,
        )
        messages.success(request, 'Bus Registered successfully')
    return redirect('admin_dashboard')


@login_required
def register_driver(request):
    if not _require_admin(request):
        return redirect('admin_dashboard')
    if request.method != 'POST':
        return redirect('admin_dashboard')

    username = (request.POST.get('username') or '').strip()
    password = request.POST.get('password') or ''
    if not (username and password):
        messages.error(request, 'Missing fields')
        return redirect('admin_dashboard')

    if len(password) < 4:
        messages.error(request, 'Password must be at least 4 characters')
        return redirect('admin_dashboard')

    if User.objects.filter(username=username).exists():
        messages.error(request, 'Username already exists')
        return redirect('admin_dashboard')

    User.objects.create_user(username=username, password=password, role='driver')
    messages.success(request, 'Driver registered successfully. Assign a bus next.')
    return redirect('admin_dashboard')


@login_required
def assign_driver_bus(request):
    if not _require_admin(request):
        return redirect('admin_dashboard')
    if request.method != 'POST':
        return redirect('admin_dashboard')

    driver_id = _to_int(request.POST.get('driver_id'), min_value=1)
    bus_id = _to_int(request.POST.get('bus_id'), min_value=1)
    if not driver_id or not bus_id:
        messages.error(request, 'Select a driver and a bus')
        return redirect('admin_dashboard')

    driver = User.objects.filter(id=driver_id, role='driver').first()
    if not driver:
        messages.error(request, 'Driver not found')
        return redirect('admin_dashboard')

    bus = Bus.objects.filter(id=bus_id).first()
    if not bus:
        messages.error(request, 'Bus not found')
        return redirect('admin_dashboard')

    now = timezone.now()
    DriverBusAssignment.objects.filter(user=driver, active=True, end_time__isnull=True).update(active=False, end_time=now)
    DriverBusAssignment.objects.create(user=driver, bus=bus, active=True, start_time=now)

    messages.success(request, f'Assigned {driver.username} to {bus.vehicle_number}')
    return redirect('admin_dashboard')


def get_stops_by_otp(request, otp):
    try:
        otp = _normalize_bus_otp(otp)
        if not otp:
            return JsonResponse({'error': 'Invalid OTP'}, status=400)

        bus = Bus.objects.get(otp_code=otp)
        stops = Stop.objects.filter(route=bus.route).order_by('order')

        stop_list = []
        for s in stops:
            stop_list.append({
                'name': s.name,
                'latitude': s.latitude,
                'longitude': s.longitude,
                'order': s.order,
            })

        return JsonResponse({
            'bus_id': bus.id,
            'bus_number': bus.vehicle_number,
            'route_name': bus.route.name,
            'stops': stop_list,
        })
    except Bus.DoesNotExist:
        return JsonResponse({'error': 'Invalid OTP'}, status=400)


@csrf_exempt
def calculate_fare(request):
    if request.method != 'POST':
        return JsonResponse({'error': 'Method not allowed'}, status=405)

    otp = _normalize_bus_otp(request.POST.get('otp'))
    source = (request.POST.get('source') or '').strip()
    destination = (request.POST.get('destination') or '').strip()

    if not otp:
        return JsonResponse({'error': 'Invalid OTP'}, status=400)

    if not (source and destination):
        return JsonResponse({'error': 'Missing fields'}, status=400)

    bus = Bus.objects.filter(otp_code=otp).first()
    if not bus:
        return JsonResponse({'error': 'Invalid OTP'}, status=400)

    stops = Stop.objects.filter(route=bus.route)

    source_id = _to_int(source, min_value=1)
    dest_id = _to_int(destination, min_value=1)
    source_stop = stops.filter(id=source_id).first() if source_id else stops.filter(name=source).order_by('order', 'id').first()
    dest_stop = stops.filter(id=dest_id).first() if dest_id else stops.filter(name=destination).order_by('order', 'id').first()

    if not source_stop or not dest_stop:
        return JsonResponse({'error': 'Stop not found'}, status=400)

    if source_stop.id == dest_stop.id:
        return JsonResponse({'error': 'Source and destination cannot be the same stop'}, status=400)

    if dest_stop.order <= source_stop.order:
        return JsonResponse({'error': 'Invalid stop selection'}, status=400)

    distance_m = _route_path_distance_m(bus.route, source_stop, dest_stop)
    fare = _fare_from_distance_m(distance_m)
    return JsonResponse({'fare': float(fare)})


@login_required
def my_tickets(request, user_id):
    if getattr(request.user, 'role', None) != 'admin' and request.user.id != user_id:
        return JsonResponse({'error': 'Forbidden'}, status=403)

    tickets = Ticket.objects.filter(user_id=user_id)
    data = []
    for t in tickets:
        data.append({
            'ticket_id': t.id,
            'bus': t.bus.vehicle_number,
            'source': t.source_stop,
            'destination': t.destination_stop,
            'fare': float(t.fare),
            'created_at': t.created_at,
        })
    return JsonResponse({'tickets': data})


@login_required
def ticket_view(request, ticket_id):
    ticket = Ticket.objects.filter(id=ticket_id).select_related('user', 'bus', 'bus__route').first()
    if not ticket:
        return JsonResponse({'error': 'Ticket not found'}, status=404)
    if getattr(request.user, 'role', None) != 'admin' and ticket.user_id != request.user.id:
        return JsonResponse({'error': 'Forbidden'}, status=403)
    return render(request, 'ticket.html', {'ticket': ticket, 'now': timezone.now()})


def get_route_stops(request, route_id):
    stops = Stop.objects.filter(route_id=route_id).order_by('order')
    data = [{'id': s.id, 'name': s.name} for s in stops]
    return JsonResponse({'stops': data})


@csrf_exempt
def update_bus_location(request, bus_id):
    if request.method != 'POST':
        return JsonResponse({'error': 'Method not allowed'}, status=405)

    if request.content_type and 'application/json' in request.content_type:
        try:
            payload = json.loads(request.body.decode('utf-8') or '{}')
        except Exception:
            return JsonResponse({'error': 'Invalid JSON'}, status=400)
    else:
        payload = request.POST

    lat = payload.get('lat') or payload.get('latitude')
    lng = payload.get('lng') or payload.get('longitude')
    speed = payload.get('speed', 0)

    if lat is None or lng is None:
        return JsonResponse({'error': 'Missing latitude/longitude'}, status=400)

    bus = Bus.objects.filter(id=bus_id).first()
    if not bus:
        return JsonResponse({'error': 'Bus not found'}, status=404)

    try:
        lat_f = float(lat)
        lng_f = float(lng)
        speed_f = float(speed or 0)
    except (TypeError, ValueError):
        return JsonResponse({'error': 'Invalid latitude/longitude/speed'}, status=400)

    if not (-90.0 <= lat_f <= 90.0 and -180.0 <= lng_f <= 180.0):
        return JsonResponse({'error': 'Invalid latitude/longitude range'}, status=400)
    if speed_f < 0:
        speed_f = 0.0

    loc = BusLiveLocation.objects.filter(bus=bus).order_by('-updated_at', '-id').first()
    prev_lat = loc.latitude if loc else None
    prev_lng = loc.longitude if loc else None
    prev_time = loc.updated_at if loc else None

    if loc:
        loc.latitude = lat_f
        loc.longitude = lng_f
        if speed_f <= 0 and prev_time is not None:
            speed_f = _estimate_speed_mps(prev_lat, prev_lng, prev_time, lat_f, lng_f, timezone.now())
        loc.speed = speed_f
        loc.save(update_fields=['latitude', 'longitude', 'speed', 'updated_at'])
    else:
        if speed_f <= 0:
            speed_f = 0.0
        BusLiveLocation.objects.create(bus=bus, latitude=lat_f, longitude=lng_f, speed=speed_f)

    stops = Stop.objects.filter(route=bus.route)
    nearest_stop = None
    nearest_dist = None
    for stop in stops:
        dist = distance_meters(lat_f, lng_f, stop.latitude, stop.longitude)
        if nearest_dist is None or dist < nearest_dist:
            nearest_dist = dist
            nearest_stop = stop

    if nearest_stop is not None and nearest_dist is not None and nearest_dist < 50:
        if bus.current_stop != nearest_stop.name:
            bus.current_stop = nearest_stop.name
            bus.save(update_fields=['current_stop'])

    return JsonResponse({'status': 'ok'})


def get_bus_location(request, bus_id):
    bus = Bus.objects.filter(id=bus_id).first()
    if not bus:
        return JsonResponse({'error': 'Bus not found'}, status=404)

    location = BusLiveLocation.objects.filter(bus_id=bus_id).order_by('-updated_at', '-id').first()
    if not location:
        return JsonResponse({'error': 'location not available'}, status=404)

    eta_seconds = None
    eta_minutes = None
    next_stop = None

    next_stop_obj, dist_to_next_m, remaining_path_m = _route_remaining_distance_m(
        bus, float(location.latitude), float(location.longitude)
    )
    if next_stop_obj:
        next_stop = next_stop_obj.name

    speed_mps = float(location.speed or 0)
    if next_stop_obj and dist_to_next_m is not None and speed_mps >= 0.5:
        eta_seconds = int(dist_to_next_m / speed_mps)
        eta_minutes = int(round(eta_seconds / 60))

    return JsonResponse({
        'lat': float(location.latitude),
        'lng': float(location.longitude),
        'speed': float(location.speed),
        'time': location.updated_at.isoformat(),
        'current_stop': bus.current_stop,
        'next_stop': next_stop,
        'eta_seconds': eta_seconds,
        'eta_minutes': eta_minutes,
    })


def _driver_can_access_bus(user, bus):
    if getattr(user, 'role', None) == 'admin':
        return True

    # New mapping (Option B)
    if DriverBusAssignment.objects.filter(user=user, bus=bus, active=True, end_time__isnull=True).exists():
        return True

    # Backward-compat: if old mapping exists, allow and auto-create assignment
    reg = DriverRegistration.objects.filter(user=user).first()
    if reg and reg.bus_otp == bus.otp_code:
        DriverBusAssignment.objects.create(user=user, bus=bus, active=True, start_time=timezone.now())
        return True

    # Backward-compat: operator_name based linkage
    if bus.operator_name == user.username:
        DriverBusAssignment.objects.create(user=user, bus=bus, active=True, start_time=timezone.now())
        return True

    return False


@login_required
def driver_dashboard(request):
    bus = None
    assignment = DriverBusAssignment.objects.filter(
        user=request.user,
        active=True,
        end_time__isnull=True,
    ).select_related('bus').order_by('-start_time', '-id').first()
    if assignment:
        bus = assignment.bus

    # Backward-compat fallbacks
    if not bus:
        reg = DriverRegistration.objects.filter(user=request.user).order_by('-created_at', '-id').first()
        if reg:
            bus = Bus.objects.filter(otp_code=reg.bus_otp).first()
            if bus:
                DriverBusAssignment.objects.create(user=request.user, bus=bus, active=True, start_time=timezone.now())
        if not bus:
            bus = Bus.objects.filter(operator_name=request.user.username).first()
            if bus:
                DriverBusAssignment.objects.create(user=request.user, bus=bus, active=True, start_time=timezone.now())

    trips = []
    active_trip = None
    if bus:
        # Backward-compat: older code toggled Bus.trip_active/trip_start_time without
        # creating a BusTrip row. Create one lazily so ticket history works.
        if bus.trip_active:
            active_trip = BusTrip.objects.filter(bus=bus, end_time__isnull=True).order_by('-start_time', '-id').first()
            if not active_trip:
                start_time = bus.trip_start_time or timezone.now()
                if bus.trip_start_time is None:
                    bus.trip_start_time = start_time
                    bus.save(update_fields=['trip_start_time'])
                active_trip = BusTrip.objects.create(bus=bus, start_time=start_time)

        trips = BusTrip.objects.filter(bus=bus).order_by('-start_time', '-id')
        if active_trip is None:
            active_trip = BusTrip.objects.filter(bus=bus, end_time__isnull=True).order_by('-start_time', '-id').first()

    tickets = Ticket.objects.none()
    total_amount = 0
    if bus and bus.trip_active and active_trip:
        tickets = Ticket.objects.filter(bus=bus, created_at__gte=active_trip.start_time).order_by('-created_at')
        total_amount = tickets.aggregate(Sum('fare'))['fare__sum'] or 0

    return render(request, 'driver_dashboard.html', {
        'bus': bus,
        'trips': trips,
        'active_trip': active_trip,
        'tickets': tickets,
        'total_amount': total_amount,
    })


@login_required
def start_trip(request, bus_id):
    bus = Bus.objects.filter(id=bus_id).first()
    if not bus:
        messages.error(request, 'Bus not found')
        return redirect('driver_dashboard')

    if not _driver_can_access_bus(request.user, bus):
        messages.error(request, 'You are not allowed to start this trip')
        return redirect('driver_dashboard')

    open_trip = BusTrip.objects.filter(bus=bus, end_time__isnull=True).order_by('-start_time', '-id').first()
    if open_trip:
        open_trip.end_time = timezone.now()
        open_trip.save(update_fields=['end_time'])

    trip = BusTrip.objects.create(bus=bus, start_time=timezone.now())
    bus.trip_active = True
    bus.trip_start_time = trip.start_time
    bus.save(update_fields=['trip_active', 'trip_start_time'])
    return redirect('driver_dashboard')


@login_required
def end_trip(request, bus_id):
    bus = Bus.objects.filter(id=bus_id).first()
    if not bus:
        messages.error(request, 'Bus not found')
        return redirect('driver_dashboard')

    if not _driver_can_access_bus(request.user, bus):
        messages.error(request, 'You are not allowed to end this trip')
        return redirect('driver_dashboard')

    open_trip = BusTrip.objects.filter(bus=bus, end_time__isnull=True).order_by('-start_time', '-id').first()
    now = timezone.now()
    if open_trip:
        open_trip.end_time = now
        open_trip.save(update_fields=['end_time'])
    else:
        # Backward-compat: if the trip was started using the old flag-only logic,
        # create the trip record on end so it appears in history.
        if bus.trip_active:
            start_time = bus.trip_start_time or now
            if bus.trip_start_time is None:
                bus.trip_start_time = start_time
                bus.save(update_fields=['trip_start_time'])
            BusTrip.objects.create(bus=bus, start_time=start_time, end_time=now)

    bus.trip_active = False
    bus.save(update_fields=['trip_active'])
    return redirect('driver_dashboard')


@login_required
def driver_trip_details(request, trip_id):
    trip = BusTrip.objects.select_related('bus').filter(id=trip_id).first()
    if not trip:
        return JsonResponse({'error': 'Trip not found'}, status=404)

    bus = trip.bus
    if not _driver_can_access_bus(request.user, bus):
        return JsonResponse({'error': 'Forbidden'}, status=403)

    end_time = trip.end_time or timezone.now()
    tickets_qs = Ticket.objects.filter(
        bus=bus,
        created_at__gte=trip.start_time,
        created_at__lte=end_time,
    ).order_by('-created_at')
    total_amount = tickets_qs.aggregate(Sum('fare'))['fare__sum'] or 0

    tickets = []
    for t in tickets_qs[:500]:
        tickets.append({
            'id': t.id,
            'source_stop': t.source_stop,
            'destination_stop': t.destination_stop,
            'fare': float(t.fare),
            'created_at': t.created_at.isoformat(),
        })

    return JsonResponse({
        'trip_id': trip.id,
        'start_time': trip.start_time.isoformat(),
        'end_time': trip.end_time.isoformat() if trip.end_time else None,
        'tickets': tickets,
        'total_amount': float(total_amount),
    })


@login_required
def driver_trip_summary(request, bus_id):
    bus = Bus.objects.filter(id=bus_id).first()
    if not bus:
        return JsonResponse({'error': 'Bus not found'}, status=404)

    if not _driver_can_access_bus(request.user, bus):
        return JsonResponse({'error': 'Forbidden'}, status=403)

    active_trip = BusTrip.objects.filter(bus=bus, end_time__isnull=True).order_by('-start_time', '-id').first()
    if bus.trip_active and not active_trip:
        # Backward-compat: trip flags set but no BusTrip row exists.
        start_time = bus.trip_start_time or timezone.now()
        if bus.trip_start_time is None:
            bus.trip_start_time = start_time
            bus.save(update_fields=['trip_start_time'])
        active_trip = BusTrip.objects.create(bus=bus, start_time=start_time)

    if not bus.trip_active or not active_trip:
        return JsonResponse({'trip_active': False, 'tickets': [], 'total_amount': 0})

    tickets_qs = Ticket.objects.filter(bus=bus, created_at__gte=active_trip.start_time).order_by('-created_at')
    total_amount = tickets_qs.aggregate(Sum('fare'))['fare__sum'] or 0

    tickets = []
    for t in tickets_qs[:200]:
        tickets.append({
            'id': t.id,
            'source_stop': t.source_stop,
            'destination_stop': t.destination_stop,
            'fare': float(t.fare),
            'created_at': t.created_at.isoformat(),
        })

    return JsonResponse({'trip_active': True, 'tickets': tickets, 'total_amount': float(total_amount)})

