"""A local stand-in for Open-Meteo, Nominatim and Overpass, so the tests never touch the internet.
The JSON shapes follow the public API documentation of each service."""
import datetime as dt
import json
import threading
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import openmeteo
import openstreetmap

PLACES = {"dhaka": ("Dhaka", 23.8103, 90.4125, "Bangladesh", "BD", "Dhaka Division"),
          "paris": ("Paris", 48.8566, 2.3522, "France", "FR", "Île-de-France"),
          "rome": ("Rome", 41.9028, 12.4964, "Italy", "IT", "Lazio"),
          "kyoto": ("Kyoto", 35.0116, 135.7681, "Japan", "JP", "Kyoto Prefecture")}
CALLS = []
FAIL = set()          # service names that should answer 503, e.g. {"overpass"}


def _forecast(q):
    days = int(q.get("forecast_days", ["7"])[0])
    imp = q.get("temperature_unit") == ["fahrenheit"]
    k = 1.8 if imp else 1.0
    f = (lambda c: round(c * k + (32 if imp else 0), 1))
    now = dt.datetime(2026, 11, 14, 10, 30)
    day0 = now.date()
    hours = [(dt.datetime.combine(day0, dt.time()) + dt.timedelta(hours=i)) for i in range(days * 24)]
    return {"latitude": float(q["latitude"][0]), "longitude": float(q["longitude"][0]), "elevation": 8.0, "timezone": "Asia/Dhaka",
            "timezone_abbreviation": "GMT+6", "utc_offset_seconds": 21600,
            "current_units": {"temperature_2m": "°F" if imp else "°C", "wind_speed_10m": "mp/h" if imp else "km/h", "precipitation": "inch" if imp else "mm", "pressure_msl": "hPa"},
            "current": {"time": now.strftime("%Y-%m-%dT%H:%M"), "temperature_2m": f(28.4), "apparent_temperature": f(31.2), "relative_humidity_2m": 62, "is_day": 1,
                        "precipitation": 0.0, "weather_code": 2, "cloud_cover": 40, "pressure_msl": 1009.3, "wind_speed_10m": 12.3, "wind_direction_10m": 225, "wind_gusts_10m": 22.0},
            "hourly": {"time": [h.strftime("%Y-%m-%dT%H:%M") for h in hours], "temperature_2m": [f(24 + (h.hour % 12)) for h in hours],
                       "precipitation_probability": [min(100, h.hour * 4) for h in hours], "weather_code": [3 if h.hour > 15 else 2 for h in hours],
                       "is_day": [1 if 6 <= h.hour < 18 else 0 for h in hours]},
            "daily": {"time": [(day0 + dt.timedelta(days=i)).isoformat() for i in range(days)], "weather_code": [2, 61, 3, 95, 0, 1, 2, 3][:days] + [2] * max(0, days - 8),
                      "temperature_2m_max": [f(31.5)] * days, "temperature_2m_min": [f(22.5)] * days,
                      "sunrise": [f"{(day0 + dt.timedelta(days=i)).isoformat()}T06:12" for i in range(days)],
                      "sunset": [f"{(day0 + dt.timedelta(days=i)).isoformat()}T17:21" for i in range(days)],
                      "uv_index_max": [7.1] * days, "precipitation_sum": [0.0, 4.2] + [1.0] * max(0, days - 2), "precipitation_probability_max": [20, 80] + [40] * max(0, days - 2),
                      "wind_speed_10m_max": [18.0] * days}}


def _archive(q):
    imp = q.get("temperature_unit") == ["fahrenheit"]
    d0, d1 = dt.date.fromisoformat(q["start_date"][0]), dt.date.fromisoformat(q["end_date"][0])
    days = [d0 + dt.timedelta(days=i) for i in range((d1 - d0).days + 1)]
    f = (lambda c: round(c * 1.8 + 32, 1) if imp else c)
    return {"daily": {"time": [d.isoformat() for d in days], "temperature_2m_max": [f(30.0)] * len(days), "temperature_2m_min": [f(22.0)] * len(days)}}


def _search(q):
    key = q["q"][0].lower().split(",")[0].strip()
    if key not in PLACES:
        return []
    n, lat, lon, country, cc, state = PLACES[key]
    return [{"place_id": 1, "osm_type": "relation", "osm_id": 184, "lat": str(lat), "lon": str(lon), "category": "boundary", "type": "administrative",
             "name": n, "display_name": f"{n}, {state}, {country}", "importance": 0.8, "boundingbox": [str(lat - .1), str(lat + .1), str(lon - .1), str(lon + .1)],
             "address": {"city": n, "state": state, "country": country, "country_code": cc.lower()},
             "extratags": {"wikipedia": f"en:{n}", "population": "10000000", "website": f"https://{n.lower()}.example"}}]


def _overpass(body):
    ql = urllib.parse.parse_qs(body)["data"][0]
    els = [{"type": "node", "id": 11, "lat": 23.8150, "lon": 90.4125, "tags": {"name": "City Pharmacy", "amenity": "pharmacy", "opening_hours": "Mo-Su 08:00-23:00", "addr:street": "Road 5", "addr:housenumber": "12", "phone": "+880 1"}},
           {"type": "way", "id": 12, "center": {"lat": 23.8200, "lon": 90.4200}, "tags": {"name": "Health Plus", "amenity": "pharmacy"}},
           {"type": "node", "id": 13, "lat": 23.8110, "lon": 90.4130, "tags": {"amenity": "pharmacy"}},          # unnamed: dropped for pharmacies
           {"type": "node", "id": 14, "lat": 23.8150, "lon": 90.4125, "tags": {"name": "City Pharmacy", "amenity": "pharmacy"}}]   # duplicate
    if "toilets" in ql:
        els = [{"type": "node", "id": 20, "lat": 23.8110, "lon": 90.4130, "tags": {"amenity": "toilets"}}]
    return {"elements": els, "ql": ql}


class H(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def reply(self, code, obj):
        b = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(b)))
        self.end_headers()
        self.wfile.write(b)

    def route(self, body=""):
        u = urllib.parse.urlparse(self.path)
        q = urllib.parse.parse_qs(u.query)
        CALLS.append((self.command, u.path, q, self.headers.get("User-Agent", "")))
        svc = u.path.split("/")[1]
        if svc in FAIL:
            return self.reply(503, {"error": True, "reason": "down"})
        if svc == "forecast":
            return self.reply(200, _forecast(q))
        if svc == "air":
            return self.reply(200, {"current_units": {"pm2_5": "μg/m³"}, "current": {"time": "2026-11-14T10:00", "us_aqi": 152, "european_aqi": 90, "pm2_5": 62.1, "pm10": 98.0, "ozone": 40, "nitrogen_dioxide": 20, "sulphur_dioxide": 5, "carbon_monoxide": 400, "uv_index": 6.5}})
        if svc == "archive":
            return self.reply(200, _archive(q))
        if svc == "geocode":
            key = q["name"][0].lower()
            return self.reply(200, {"results": [{"name": PLACES[key][0], "latitude": PLACES[key][1], "longitude": PLACES[key][2], "country": PLACES[key][3], "country_code": PLACES[key][4], "admin1": PLACES[key][5], "timezone": "UTC"}]} if key in PLACES else {})
        if svc == "nominatim" and u.path.endswith("/search"):
            return self.reply(200, _search(q))
        if svc == "nominatim" and u.path.endswith("/reverse"):
            return self.reply(200, {"osm_type": "way", "osm_id": 5, "lat": q["lat"][0], "lon": q["lon"][0], "category": "highway", "type": "residential", "name": "Road 5",
                                    "display_name": "Road 5, Gulshan, Dhaka, Bangladesh", "address": {"road": "Road 5", "suburb": "Gulshan", "city": "Dhaka", "country": "Bangladesh", "country_code": "bd"}})
        if svc == "nominatim" and u.path.endswith("/status.php"):
            return self.reply(200, {"status": 0, "message": "OK"})
        if svc == "overpass":
            return self.reply(200, _overpass(body))
        self.reply(404, {"error": True})

    def do_GET(self):
        self.route()

    def do_POST(self):
        n = int(self.headers.get("Content-Length", 0))
        self.route(self.rfile.read(n).decode())


def start():
    srv = ThreadingHTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{srv.server_address[1]}"
    openmeteo.FORECAST_URL, openmeteo.AIR_URL = base + "/forecast", base + "/air"
    openmeteo.ARCHIVE_URL, openmeteo.GEOCODE_URL = base + "/archive", base + "/geocode"
    openstreetmap.configure("tests@example.org", base + "/nominatim", base + "/overpass")
    openstreetmap.MIN_GAP = 0.0
    for m in (openmeteo, openstreetmap):
        m._CACHE.clear()
    CALLS.clear()
    FAIL.clear()
    return srv
