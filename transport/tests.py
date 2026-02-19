from __future__ import annotations

from datetime import timedelta

from django.test import TestCase
from django.utils import timezone

from .models import Bus, BusLiveLocation, BusTrip, DriverBusAssignment, Route, Stop, Ticket, User


def _mk_route_with_stops(*, name: str = 'R1'):
	route = Route.objects.create(name=name, source='A', destination='B')
	# Roughly 1 km per 0.009 degrees latitude.
	# Stop0 -> Stop3 is set to ~2.4km (should fare = 10).
	# Stop0 -> Stop4 is set to ~3.4km (should fare = 11).
	stops = [
		Stop.objects.create(route=route, name='S0', order=0, latitude=0.0000, longitude=0.0),
		Stop.objects.create(route=route, name='S1', order=1, latitude=0.0090, longitude=0.0),
		Stop.objects.create(route=route, name='S2', order=2, latitude=0.0180, longitude=0.0),
		Stop.objects.create(route=route, name='S3', order=3, latitude=0.0216, longitude=0.0),
		Stop.objects.create(route=route, name='S4', order=4, latitude=0.0306, longitude=0.0),
	]
	return route, stops


class KbusSmokeTests(TestCase):
	def setUp(self):
		self.route, self.stops = _mk_route_with_stops()
		self.bus = Bus.objects.create(
			vehicle_number='KL07AB1234',
			operator_type='private',
			operator_name='operator',
			route=self.route,
			base_fare='10.00',
		)

	def test_calculate_fare_rejects_same_stop(self):
		res = self.client.post(
			'/transport/calculate-fare/',
			data={
				'otp': self.bus.otp_code,
				'source': str(self.stops[1].id),
				'destination': str(self.stops[1].id),
			},
		)
		self.assertEqual(res.status_code, 400)
		self.assertIn('error', res.json())

	def test_calculate_fare_km_rule(self):
		# S0 -> S3 ~ 2.4km => 10
		res_25 = self.client.post(
			'/transport/calculate-fare/',
			data={'otp': self.bus.otp_code, 'source': str(self.stops[0].id), 'destination': str(self.stops[3].id)},
		)
		self.assertEqual(res_25.status_code, 200)
		self.assertEqual(res_25.json().get('fare'), 10.0)

		# S0 -> S4 ~ 3.4km => 11 (extra ~0.9km started)
		res_35 = self.client.post(
			'/transport/calculate-fare/',
			data={'otp': self.bus.otp_code, 'source': str(self.stops[0].id), 'destination': str(self.stops[4].id)},
		)
		self.assertEqual(res_35.status_code, 200)
		self.assertEqual(res_35.json().get('fare'), 11.0)

	def test_booking_requires_different_stops(self):
		passenger = User.objects.create_user(username='p1', password='pw', role='passenger')
		self.client.force_login(passenger)

		before = Ticket.objects.count()
		res = self.client.post(
			'/transport/book-ticket/',
			data={'otp': self.bus.otp_code, 'source': str(self.stops[1].id), 'destination': str(self.stops[1].id)},
			follow=False,
		)
		self.assertEqual(res.status_code, 302)
		self.assertEqual(Ticket.objects.count(), before)

	def test_driver_trip_summary_backfills_missing_bustrip(self):
		driver = User.objects.create_user(username='d1', password='pw', role='driver')
		DriverBusAssignment.objects.create(user=driver, bus=self.bus, active=True, start_time=timezone.now())

		# Simulate old logic: trip flag set but no BusTrip row.
		start = timezone.now() - timedelta(minutes=10)
		self.bus.trip_active = True
		self.bus.trip_start_time = start
		self.bus.save(update_fields=['trip_active', 'trip_start_time'])

		passenger = User.objects.create_user(username='p2', password='pw', role='passenger')
		Ticket.objects.create(
			user=passenger,
			bus=self.bus,
			source_stop=self.stops[0].name,
			destination_stop=self.stops[4].name,
			fare='11.00',
			expires_at=timezone.now() + timedelta(minutes=30),
		)

		self.assertEqual(BusTrip.objects.filter(bus=self.bus).count(), 0)

		self.client.force_login(driver)
		res = self.client.get(f'/transport/driver-trip-summary/{self.bus.id}/')
		self.assertEqual(res.status_code, 200)
		payload = res.json()
		self.assertTrue(payload.get('trip_active'))
		self.assertGreaterEqual(len(payload.get('tickets', [])), 1)

		# Backfill should have created an open trip.
		self.assertEqual(BusTrip.objects.filter(bus=self.bus, end_time__isnull=True).count(), 1)

	def test_bus_location_endpoints(self):
		# No location yet
		res = self.client.get(f'/transport/bus-location/{self.bus.id}/')
		self.assertEqual(res.status_code, 404)

		# Update location
		res2 = self.client.post(
			f'/transport/update-location/{self.bus.id}/',
			data={'lat': '0.0', 'lng': '0.0', 'speed': '0'},
		)
		self.assertEqual(res2.status_code, 200)
		self.assertEqual(res2.json().get('status'), 'ok')

		# Now location should exist
		res3 = self.client.get(f'/transport/bus-location/{self.bus.id}/')
		self.assertEqual(res3.status_code, 200)
		self.assertIn('lat', res3.json())
		self.assertTrue(BusLiveLocation.objects.filter(bus=self.bus).exists())

	def test_register_validation_missing_and_duplicate(self):
		before = User.objects.count()

		res = self.client.post('/transport/register/', data={'username': '', 'password': '', 'confirm_password': ''})
		self.assertEqual(res.status_code, 200)
		self.assertEqual(User.objects.count(), before)
		self.assertContains(res, 'required')

		res2 = self.client.post('/transport/register/', data={'username': 'u1', 'password': 'pw1234', 'confirm_password': 'pw1234'})
		self.assertEqual(res2.status_code, 302)
		self.assertTrue(User.objects.filter(username='u1').exists())

		before2 = User.objects.count()
		res3 = self.client.post('/transport/register/', data={'username': 'u1', 'password': 'pw1234', 'confirm_password': 'pw1234'})
		self.assertEqual(res3.status_code, 200)
		self.assertEqual(User.objects.count(), before2)
		self.assertContains(res3, 'already')

	def test_admin_form_validation_stop_and_bus(self):
		admin = User.objects.create_user(username='admin1', password='pw12', role='admin')
		self.client.force_login(admin)

		# Invalid stop (missing route_id)
		before_stops = Stop.objects.count()
		res = self.client.post('/transport/add-stop/', data={'route_id': '', 'name': 'X', 'order': '1', 'latitude': '0', 'longitude': '0'})
		self.assertEqual(res.status_code, 302)
		self.assertEqual(Stop.objects.count(), before_stops)

		# Invalid stop (bad lat)
		res2 = self.client.post('/transport/add-stop/', data={'route_id': str(self.route.id), 'name': 'X', 'order': '1', 'latitude': '999', 'longitude': '0'})
		self.assertEqual(res2.status_code, 302)
		self.assertEqual(Stop.objects.count(), before_stops)

		# Invalid bus (bad base fare)
		before_buses = Bus.objects.count()
		res3 = self.client.post('/transport/register-bus/', data={
			'vehicle_number': 'X1',
			'operator_type': 'private',
			'operator_name': 'op',
			'route_id': str(self.route.id),
			'base_fare': 'abc',
		})
		self.assertEqual(res3.status_code, 302)
		self.assertEqual(Bus.objects.count(), before_buses)

	def test_update_location_rejects_invalid_range(self):
		res = self.client.post(f'/transport/update-location/{self.bus.id}/', data={'lat': '999', 'lng': '0', 'speed': '0'})
		self.assertEqual(res.status_code, 400)
		self.assertIn('error', res.json())

	def test_passenger_portal_shows_tickets(self):
		passenger = User.objects.create_user(username='p3', password='pw', role='passenger')
		ticket = Ticket.objects.create(
			user=passenger,
			bus=self.bus,
			source_stop=self.stops[0].name,
			destination_stop=self.stops[4].name,
			fare='11.00',
			expires_at=timezone.now() + timedelta(minutes=30),
		)

		self.client.force_login(passenger)
		res = self.client.get('/transport/passenger/')
		self.assertEqual(res.status_code, 200)
		self.assertContains(res, f'Ticket #{ticket.id}')
