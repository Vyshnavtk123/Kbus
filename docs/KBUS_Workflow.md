# K-BUS Website — Full Workflow & System Documentation

> Workspace: Django project `Kbus` (Windows)
>
> This document describes **what the website does**, **how each role uses it**, and **how data flows** through the system.
>
> PDF tip: see **“Export to PDF”** at the end.

---

## 1) What this website is

K-BUS is a role-based web app for:

- **Admin**: configure routes, stops, buses, driver accounts, and assign drivers to buses.
- **Passenger**: book a ticket using a bus OTP, see ticket history, and track a bus live on the map.
- **Driver**: start/end trips, send live GPS, and view trip/ticket totals.

The app is a standard Django server-rendered UI (templates) with a few JSON endpoints used by the frontend (maps, live tracking, fare calculation, etc.).

---

## 2) Tech stack & structure

- Backend: **Django** (project: `kbus_project`, app: `transport`)
- Authentication:
  - Web UI uses **Django sessions** (`login_view`, `logout_view`).
  - APIs also expose **DRF Token** and **JWT** endpoints (for programmatic access).
- DB: configured via environment variables in settings (commonly PostgreSQL on Render).
- Maps: **Leaflet** + optional **KML overlay** via `leaflet-omnivore`.
- Static route map: KML is served via a Django view so it works even if static hosting is misconfigured.

Key directories:
- `transport/views.py` — main business logic
- `transport/models.py` — database models
- `transport/urls.py` — app endpoints (mounted under `/transport/`)
- `transport/templates/` — UI pages
- `staticfiles/maps/K-BUS Route.kml` — route KML used on maps

---

## 3) URL routing (important)

The project mounts the app under `/transport/`.

- Base path: `/transport/…`
- Convenience redirects also exist:
  - `/` → `/transport/login/`
  - `/login/` → `/transport/login/`
  - `/logout/` → `/transport/logout/`

### 3.1 Main UI pages

| Role | Page | URL |
|---|---|---|
| Public | Register | `/transport/register/` |
| Public | Login | `/transport/login/` |
| Any logged-in | Role router | `/transport/kbus/` |
| Any logged-in | Logout | `/transport/logout/` |
| Passenger | Passenger portal | `/transport/passenger/` |
| Passenger | Select route/stops | `/transport/select/<otp>/` |
| Passenger | Ticket view | `/transport/ticket/<ticket_id>/` |
| Admin | Admin dashboard | `/transport/admin-dashboard/` |
| Driver | Driver dashboard | `/transport/driver-dashboard/` |

### 3.2 JSON / helper endpoints

| Purpose | URL | Notes |
|---|---|---|
| OTP validate (GET/POST) | `/transport/validate/` and `/transport/validate/<otp>/` | Redirects in UI; GET returns JSON validity |
| Fetch stops by bus OTP | `/transport/get-stops/<otp>/` | Used for tracking + OTP resolution |
| Route stops by route_id | `/transport/get-route-stops/<route_id>/` | Used by passenger select page |
| Fare calculation | `/transport/calculate-fare/` | POST; returns `{fare: …}` |
| Update live GPS | `/transport/update-location/<bus_id>/` | POST; driver sends location |
| Read live GPS + ETA | `/transport/bus-location/<bus_id>/` | GET; passenger/driver reads |
| Serve KML route | `/transport/route-kml/` | Serves “K-BUS Route.kml” |

### 3.3 Admin actions (POST forms)

| Action | URL |
|---|---|
| Create route | `/transport/create-route/` |
| Add stop | `/transport/add-stop/` |
| Register bus | `/transport/register-bus/` |
| Register driver user | `/transport/register-driver/` |
| Assign driver ↔ bus | `/transport/assign-driver-bus/` |

### 3.4 Driver trip APIs (UI links / fetch)

| Action | URL |
|---|---|
| Start trip | `/transport/start-trip/<bus_id>/` |
| End trip | `/transport/end-trip/<bus_id>/` |
| Trip summary JSON | `/transport/driver-trip-summary/<bus_id>/` |
| Trip details JSON | `/transport/driver-trip/<trip_id>/` |

### 3.5 Auth APIs (DRF)

| Auth type | URL |
|---|---|
| DRF token | `/transport/auth/` |
| JWT obtain | `/transport/api/token/` |
| JWT refresh | `/transport/api/token/refresh/` |

---

## 4) Roles & access rules

### 4.1 Roles
Defined on the custom user model `transport.User`:
- `admin`
- `driver`
- `passenger`

### 4.2 Role routing
When a user is authenticated and visits `/transport/kbus/`, the app redirects based on `request.user.role`:
- `admin` → admin dashboard
- `driver` → driver dashboard
- else (passenger) → passenger portal

### 4.3 Admin-only enforcement
Admin pages/actions require:
- user is logged in
- `user.role == 'admin'`

Non-admin requests get redirected back to login/admin dashboard and may get an error message.

---

## 5) Data model (database)

From `transport/models.py`:

### 5.1 Core entities

- **User**
  - Extends Django `AbstractUser`
  - Field: `role`

- **Route**
  - `name`, `source`, `destination`

- **Stop**
  - `route` (FK)
  - `name`, `order`, `latitude`, `longitude`

- **Bus**
  - `vehicle_number`
  - `operator_type`, `operator_name`
  - `route` (FK)
  - `base_fare`
  - `otp_code` (5 chars, auto-generated)
  - `status`
  - `current_stop`
  - `trip_active` (bool)
  - `trip_start_time`

- **Ticket**
  - `user` (FK)
  - `bus` (FK)
  - `source_stop`, `destination_stop`
  - `fare`
  - `created_at`
  - `expires_at` (typically 30 minutes after booking)

### 5.2 Live tracking

- **BusLiveLocation**
  - `bus` (FK)
  - `latitude`, `longitude`, `speed`
  - `updated_at` (auto)

### 5.3 Driver ↔ Bus mapping
Two models exist for compatibility:

- **DriverRegistration** (older mapping)
  - One-to-one driver → bus OTP

- **DriverBusAssignment** (current mapping)
  - driver ↔ bus assignment
  - shift-like: `active`, `start_time`, `end_time`

### 5.4 Trip history

- **BusTrip**
  - `bus` (FK)
  - `start_time`, `end_time`

---

## 6) End-to-end workflows (by role)

### 6.1 Passenger workflow

#### A) Register & Login
1. Passenger opens `/transport/register/` and creates an account.
2. Passenger logs in at `/transport/login/`.
3. After login, passenger is redirected to the passenger portal `/transport/passenger/`.

#### B) Book a bus using OTP
1. Passenger enters OTP on the Passenger Portal.
2. App validates OTP:
   - POST to `/transport/validate/`
   - If valid, redirect to `/transport/select/<otp>/`

#### C) Select route, source, destination
1. Passenger Select page loads available routes.
2. On route selection, frontend loads stops:
   - GET `/transport/get-route-stops/<route_id>/`
3. When source/destination chosen, frontend requests fare:
   - POST `/transport/calculate-fare/` with `{otp, source, destination}`
4. Passenger submits booking:
   - POST `/transport/book-ticket/`
5. Server creates `Ticket` and redirects to:
   - `/transport/ticket/<ticket_id>/`

#### D) View ticket history
1. Passenger portal shows tickets fetched server-side:
   - `Ticket.objects.filter(user=request.user)`
2. Ticket cards link to `/transport/ticket/<ticket_id>/`.
3. Tickets can be “expired” (based on `expires_at < now`).

#### E) Live bus tracking
1. Passenger enters OTP in tracking section.
2. Frontend resolves OTP → bus id:
   - GET `/transport/get-stops/<otp>/` returns `bus_id`
3. Frontend polls bus location every ~5s:
   - GET `/transport/bus-location/<bus_id>/`
4. Map marker updates, and UI shows:
   - current stop
   - next stop
   - speed
   - ETA (computed server-side)

#### Passenger map layers
- Base: OpenStreetMap tiles
- Overlay: KML route layer loaded from `/transport/route-kml/`

---

### 6.2 Driver workflow

#### A) Login and bus association
1. Driver logs in at `/transport/login/`.
2. Redirects to `/transport/driver-dashboard/`.
3. The dashboard determines the driver’s bus using:
   - Active `DriverBusAssignment`
   - Fallbacks:
     - `DriverRegistration.bus_otp`
     - `Bus.operator_name == user.username`

If a fallback mapping is used, the system auto-creates a `DriverBusAssignment` for backward compatibility.

#### B) Start trip
1. Driver clicks Start Trip:
   - GET `/transport/start-trip/<bus_id>/`
2. Server:
   - closes any open `BusTrip` rows
   - creates a new `BusTrip`
   - sets `Bus.trip_active = True` and `Bus.trip_start_time`

#### C) Send GPS updates (live)
1. When trip is active, frontend uses browser geolocation.
2. It POSTs location updates to:
   - `/transport/update-location/<bus_id>/`
3. Server stores/updates `BusLiveLocation` and attempts to infer current stop when near a stop.

#### D) End trip
1. Driver clicks End Trip:
   - GET `/transport/end-trip/<bus_id>/`
2. Server:
   - sets open trip end time
   - sets `Bus.trip_active = False`

#### E) Trip history and totals
The driver dashboard can fetch:
- Trip summary: `/transport/driver-trip-summary/<bus_id>/`
- Trip details: `/transport/driver-trip/<trip_id>/`

Totals are computed by summing fares of tickets created during the trip window.

#### Driver map layers
- Base: OpenStreetMap tiles
- Overlay: KML route layer loaded from `/transport/route-kml/`
- Marker:
  - Shows live bus location (from `/transport/bus-location/<bus_id>/`)
  - Also shows driver’s own reported location during active trip

---

### 6.3 Admin workflow

Admin uses `/transport/admin-dashboard/` and can:

#### A) Create route
- POST `/transport/create-route/` with:
  - `name`, `source`, `destination`

#### B) Add stops
- POST `/transport/add-stop/` with:
  - `route_id`, `name`, `order`, `latitude`, `longitude`

Stops define the ordered path and are used for:
- fare computation
- next stop / ETA calculation
- auto current-stop inference

#### C) Register bus
- POST `/transport/register-bus/` with:
  - `vehicle_number`, `operator_type`, `operator_name`, `route_id`, `base_fare`

#### D) Register driver user
- POST `/transport/register-driver/` with:
  - `username`, `password`

Creates `User(role='driver')`.

#### E) Assign driver to bus
- POST `/transport/assign-driver-bus/` with:
  - `driver_id`, `bus_id`

This:
- deactivates any previous active assignment
- creates a new active assignment row

---

## 7) Fare & ETA logic (key rules)

### 7.1 OTP
- OTP is 5 characters: `A–Z` and `0–9`.
- Used to find a `Bus` (`Bus.otp_code`).

### 7.2 Fare calculation
Fare is derived from distance (based on route stops order):

- Up to 2.5 km: ₹10
- Beyond 2.5 km: +₹1 for **each additional started 1 km**

In other words:

$$\text{fare} = 10 + \lceil (\text{km} - 2.5) \rceil\quad \text{for km} > 2.5$$

Stops are used to compute path distance stop-to-stop (not straight-line), whenever possible.

### 7.3 ETA
ETA is computed when:
- there is a next stop
- speed is at least ~0.5 m/s

Remaining distance is estimated from:
- current GPS position → next stop
- then next stop → subsequent stops

---

## 8) Static route KML (“your map”)

The KML file is served by a Django view:
- `/transport/route-kml/`

This view uses Django static file finders to locate:
- `maps/K-BUS Route.kml`

So the KML must be inside a place Django can find via static finders (e.g. in `staticfiles/maps/…` after `collectstatic`, or in an app static directory).

---

## 9) Deployment notes (Render)

### 9.1 Common environment variables
These are referenced in settings:

- `SECRET_KEY`
- Database variables (as currently configured):
  - `ENGINE`, `NAME`, `USER`, `PASSWORD`, `HOST`

Recommended / supported extras:
- `DEBUG` (default false)
- `ALLOWED_HOSTS` (comma-separated)
- `CSRF_TRUSTED_ORIGINS` (comma-separated, include `https://…`)

### 9.2 “Page not loading” checklist
If any page won’t load on Render:

1. Check Render logs for `DisallowedHost`:
   - Fix with `ALLOWED_HOSTS`.
2. If login POST fails:
   - Ensure `CSRF_TRUSTED_ORIGINS` contains your Render URL.
3. If map KML doesn’t appear:
   - Open `/transport/route-kml/` directly and confirm it returns KML.
4. If 500 errors:
   - confirm all env vars are set.

---

## 10) Export to PDF

### Option A (VS Code, easiest)
1. Open this file in VS Code: `docs/KBUS_Workflow.md`
2. Open Markdown Preview (`Ctrl+Shift+V`)
3. In the preview, use the browser/preview print option → **Print to PDF**

### Option B (Markdown PDF extension)
1. Install extension: “Markdown PDF”
2. Right-click the file → “Export (pdf)”

---

## 11) Quick “workflow cheat sheet” (1-page)

- User opens site → redirected to `/transport/login/`
- Login success → redirects by role:
  - admin → `/transport/admin-dashboard/`
  - driver → `/transport/driver-dashboard/`
  - passenger → `/transport/passenger/`

Passenger:
- Enter OTP → `/transport/validate/` → `/transport/select/<otp>/` → `/transport/book-ticket/` → `/transport/ticket/<id>/`
- Track bus → OTP → `/transport/get-stops/<otp>/` → poll `/transport/bus-location/<bus_id>/`

Driver:
- Start trip → `/transport/start-trip/<bus_id>/`
- GPS sends → `/transport/update-location/<bus_id>/`
- End trip → `/transport/end-trip/<bus_id>/`

Admin:
- Create route/stops/bus/driver + assign via dashboard forms.
