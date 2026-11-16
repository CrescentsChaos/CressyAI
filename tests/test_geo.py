import unittest

import geo
import geomath
import openmeteo
import openstreetmap as osm
from tests import fake_upstream as fake
from websearch import WebError


def setUpModule():
    global SRV
    SRV = fake.start()


def tearDownModule():
    SRV.shutdown()
    SRV.server_close()


class MathTests(unittest.TestCase):
    def test_distance_and_direction(self):
        d = geomath.haversine_m(48.8566, 2.3522, 41.9028, 12.4964)
        self.assertAlmostEqual(d / 1000, 1105, delta=8)                     # Paris -> Rome is about 1,105 km
        self.assertEqual(geomath.compass(geomath.bearing(48.8566, 2.3522, 41.9028, 12.4964)), "SE")
        self.assertEqual(geomath.compass(0), "N")
        self.assertEqual(geomath.compass(359), "N")

    def test_formatting_and_coords(self):
        self.assertEqual(geomath.fmt_dist(450), "450 m")
        self.assertEqual(geomath.fmt_dist(2300), "2.3 km")
        self.assertEqual(geomath.fmt_dist(1609.344, "imperial"), "1.0 mi")
        self.assertEqual(geomath.parse_coords("23.81, 90.41"), (23.81, 90.41))
        self.assertIsNone(geomath.parse_coords("123.4, 20"))
        self.assertIsNone(geomath.parse_coords("Dhaka"))


class OpenMeteoTests(unittest.TestCase):
    def test_forecast_shape(self):
        fc = openmeteo.forecast(23.8103, 90.4125, "metric", 4)
        self.assertEqual(fc["now"]["text"], "Partly cloudy")
        self.assertEqual(fc["now"]["wind_dir_txt"], "SW")
        self.assertEqual(len(fc["daily"]), 4)
        self.assertEqual(len(fc["hourly"]), 24)
        self.assertEqual(fc["hourly"][0]["time"][11:13], "10")                # starts at the current hour
        self.assertEqual(fc["daily"][1]["text"], "Light rain")
        self.assertEqual(fc["daily"][0]["sunrise"], "06:12")
        self.assertEqual(fc["units"]["temp"], "°C")

    def test_imperial_units_are_requested(self):
        fake.CALLS.clear()
        fc = openmeteo.forecast(23.8103, 90.4125, "imperial", 2)
        q = [c for c in fake.CALLS if c[1] == "/forecast"][-1][2]
        self.assertEqual(q["temperature_unit"], ["fahrenheit"])
        self.assertEqual(q["wind_speed_unit"], ["mph"])
        self.assertEqual(fc["units"]["temp"], "°F")

    def test_coordinates_are_rounded_before_sending(self):
        openmeteo._CACHE.clear()
        fake.CALLS.clear()
        openmeteo.forecast(23.810347, 90.412518, "metric", 3)
        q = [c for c in fake.CALLS if c[1] == "/forecast"][-1][2]
        self.assertEqual((q["latitude"], q["longitude"]), (["23.81"], ["90.41"]))

    def test_forecast_is_cached(self):
        openmeteo.forecast(1.5, 2.5, "metric", 3)
        n = len(fake.CALLS)
        openmeteo.forecast(1.5, 2.5, "metric", 3)
        self.assertEqual(len(fake.CALLS), n)

    def test_air_quality_categories(self):
        a = openmeteo.air_quality(23.81, 90.41)
        self.assertEqual((a["us_aqi"], a["category"]), (152, "Unhealthy"))
        self.assertEqual(openmeteo.aqi_category(30)[0], "Good")
        self.assertEqual(openmeteo.aqi_category(320)[0], "Hazardous")
        self.assertEqual(openmeteo.aqi_category(None), ("", ""))

    def test_climate_check_against_normal(self):
        fc = openmeteo.forecast(23.81, 90.41, "metric", 5)
        c = openmeteo.climate_check(23.81, 90.41, fc["daily"], "metric")
        self.assertEqual(c["years"], "2016-2025")
        self.assertAlmostEqual(c["tmax_diff"], 1.5, places=1)               # forecast 31.5 vs normal 30.0
        self.assertEqual(c["label"], "about normal")                         # mean of +1.5 and +0.5 is under the 1.5 threshold

    def test_climate_threshold_scales_for_fahrenheit(self):
        fc = openmeteo.forecast(23.81, 90.41, "imperial", 3)
        self.assertEqual(openmeteo.climate_check(23.81, 90.41, fc["daily"], "imperial")["label"], "about normal")

    def test_geocode_fallback_source(self):
        r = openmeteo.geocode("Dhaka")
        self.assertEqual((r[0]["name"], r[0]["country_code"]), ("Dhaka", "BD"))
        self.assertEqual(openmeteo.geocode("nowhereville"), [])

    def test_service_down_gives_readable_error(self):
        fake.FAIL.add("forecast")
        try:
            with self.assertRaises(WebError) as cm:
                openmeteo.forecast(5.5, 5.5, "metric", 2)
            self.assertIn("Open-Meteo", str(cm.exception))
        finally:
            fake.FAIL.clear()

    def test_invalid_coordinates(self):
        with self.assertRaises(WebError):
            openmeteo.forecast(200, 0)

    def test_wmo_table_covers_all_documented_codes(self):
        for code in (0, 1, 2, 3, 45, 48, 51, 53, 55, 56, 57, 61, 63, 65, 66, 67, 71, 73, 75, 77, 80, 81, 82, 85, 86, 95, 96, 99):
            self.assertNotEqual(openmeteo.describe(code)[0], "Unknown", code)


class OsmTests(unittest.TestCase):
    def test_search_normalises(self):
        r = osm.search("Dhaka")[0]
        self.assertEqual((r["name"], r["country_code"], r["lat"]), ("Dhaka", "BD", 23.8103))
        self.assertEqual(r["wikipedia"], "https://en.wikipedia.org/wiki/Dhaka")
        self.assertEqual(r["osm_url"], "https://www.openstreetmap.org/relation/184")
        self.assertEqual(len(r["bbox"]), 4)

    def test_identifying_user_agent(self):
        osm._CACHE.clear()
        fake.CALLS.clear()
        osm.search("Paris")
        self.assertIn("tests@example.org", [c for c in fake.CALLS if c[1].endswith("/search")][-1][3])

    def test_coordinates_go_to_reverse(self):
        r = osm.search("23.8103, 90.4125")[0]
        self.assertEqual(r["name"], "Road 5")
        self.assertIn("Gulshan", r["address"])

    def test_nearby_sorts_dedupes_and_drops_unnamed(self):
        items = osm.nearby(23.8103, 90.4125, "pharmacy", 1000, 10)
        self.assertEqual([p["name"] for p in items], ["City Pharmacy", "Health Plus"])
        self.assertLess(items[0]["distance_m"], items[1]["distance_m"])
        self.assertEqual(items[0]["compass"], "N")
        self.assertEqual(items[0]["address"], "12 Road 5")
        self.assertTrue(items[0]["osm_url"].endswith("/node/11"))
        self.assertTrue(items[1]["osm_url"].endswith("/way/12"))            # ways use their centre point

    def test_unnamed_ok_for_toilets(self):
        items = osm.nearby(23.8103, 90.4125, "toilets", 500, 5)
        self.assertEqual(items[0]["name"], "Unnamed toilet")

    def test_overpass_query_is_built_from_fixed_filters(self):
        osm._CACHE.clear()
        fake.CALLS.clear()
        osm.nearby(23.8103, 90.4125, "pharmacy", 800, 5)
        ql = fake.CALLS[-1][1], osm._ql(23.8103, 90.4125, "pharmacy", 800, 300)
        self.assertIn('nwr(around:800,23.81030,90.41250)["amenity"="pharmacy"]', ql[1])
        with self.assertRaises(WebError):
            osm.nearby(1, 1, 'pharmacy"];out;', 100)                        # unknown category: never reaches the query

    def test_radius_is_clamped(self):
        self.assertIn("around:10000", osm._ql(1, 1, "cafe", 10000, 5))
        osm._CACHE.clear()
        fake.CALLS.clear()
        osm.nearby(2, 2, "cafe", 999999, 5)
        self.assertTrue(fake.CALLS)

    def test_service_down_message(self):
        fake.FAIL.add("overpass")
        try:
            osm._CACHE.clear()
            with self.assertRaises(WebError) as cm:
                osm.nearby(3, 3, "cafe", 500, 5)
            self.assertIn("busy or down", str(cm.exception))
        finally:
            fake.FAIL.clear()

    def test_kind_words(self):
        for text, kind in [("any pharmacies near me", "pharmacy"), ("a good cup of coffee", None), ("coffee shops in Rome", "cafe"), ("coffee beans", None), ("nearest ATM", "atm"),
                           ("where is a mosque", "worship"), ("EV charging station", "charging"), ("public toilet", "toilets")]:
            self.assertEqual(osm.kind_from_text(text), kind, text)

    def test_between_and_gpx(self):
        d = osm.between({"lat": 48.8566, "lon": 2.3522}, {"lat": 41.9028, "lon": 12.4964})
        self.assertEqual(d["compass"], "SE")
        self.assertAlmostEqual(d["km"], 1105, delta=3)
        g = osm.gpx([{"lat": 1, "lon": 2, "name": "A & B <x>", "address": "x"}])
        self.assertIn("A &amp; B &lt;x&gt;", g)
        self.assertIn('lat="1.000000"', g)

    def test_configure_rejects_bad_urls(self):
        with self.assertRaises(ValueError):
            osm.configure("", "file:///etc/passwd")


class IntentTests(unittest.TestCase):
    def kind(self, text, have_default=False):
        r = geo.parse_message(text, have_default)
        return r and r["kind"]

    def test_positive(self):
        for text, kind in [("What's the weather in Dhaka tomorrow?", "weather"), ("will it rain in new york city this weekend?", "weather"),
                           ("how hot is it outside today", "weather"), ("what's the air quality in Delhi?", "weather"),
                           ("pharmacies near Gulshan 2, Dhaka?", "nearby"), ("nearest cafe", "nearby"), ("restaurants in Lisbon", "nearby"),
                           ("how far is Paris from Rome?", "distance"), ("distance between Dhaka and Chittagong", "distance"),
                           ("plan a 4 day trip to Kyoto", "trip"), ("is it unusually hot in Lisbon today", "climate"), ("where is the Eiffel Tower", "where")]:
            self.assertEqual(self.kind(text, True), kind, text)

    def test_negative_never_looks_up_code_or_writing(self):
        for text in ["set temperature to 0.7 for the llm", "write a poem about rain in Paris", "where is the bug in my code", "```py\nweather = get('rain')\n```",
                     "why does my function return None", "what is the best way to learn Python", "explain how wind turbines work", "the temperature = 0.7 in my sampler",
                     "where is Python installed on windows", "refactor this class", "/weather Dhaka", "summarize the history of Rome", "what is a cafe",
                     "my api returns 429 when the rain sensor fires", "hello", "tell me about coffee in Italy", "tell me about parks in Japan", "what is a pharmacy"]:
            self.assertIsNone(geo.parse_message(text, True), text)

    def test_place_phrase_stops_at_time_words(self):
        self.assertEqual(geo.phrase_after("weather in Cox's Bazar tomorrow morning"), "Cox's Bazar")
        self.assertEqual(geo.phrase_after("forecast for the next 5 days in Paris, France please"), "Paris, France")
        self.assertEqual(geo.phrase_after("rain in St. Louis today"), "St. Louis")
        self.assertEqual(geo.phrase_after("cafes near Sector 7, Uttara"), "Sector 7, Uttara")
        self.assertEqual(geo.phrase_after("weather for 5 days"), "")

    def test_here_means_saved_place(self):
        r = geo.parse_message("any pharmacy near me?", True)
        self.assertEqual((r["kind"], r["place"]), ("nearby", ""))

    def test_days_and_when(self):
        self.assertEqual(geo.parse_message("weather in Rome for the next 10 days")["days"], 10)
        self.assertEqual(geo.parse_message("weather in Rome tomorrow")["when"], "tomorrow")

    def test_commands(self):
        c = geo.parse_command("weather", "Dhaka 5 days")
        self.assertEqual((c["kind"], c["place"], c["days"]), ("weather", "Dhaka", 5))
        c = geo.parse_command("near", "pharmacy near Gulshan, Dhaka")
        self.assertEqual((c["kind"], c["thing"], c["place"]), ("nearby", "pharmacy", "Gulshan, Dhaka"))
        c = geo.parse_command("near", "kind:toilets @23.81,90.41")
        self.assertEqual((c["thing"], c["place"], c["coords"]), ("toilets", "", (23.81, 90.41)))
        c = geo.parse_command("distance", "Paris to Rome")
        self.assertEqual((c["place"], c["place2"]), ("Paris", "Rome"))
        c = geo.parse_command("weather", "Dhaka @23.8103,90.4125")
        self.assertEqual((c["place"], c["coords"]), ("Dhaka", (23.8103, 90.4125)))
        self.assertEqual(geo.parse_command("aqi", "")["focus"], "aqi")
        for bad in (("near", ""), ("distance", "Paris"), ("fly", "x")):
            with self.assertRaises(WebError):
                geo.parse_command(*bad)


class HandleTests(unittest.TestCase):
    base = {"units": "metric", "days": 5, "radius": 1000}

    def run_(self, **kw):
        return geo.handle({**self.base, **kw})

    def test_nothing_to_do(self):
        self.assertEqual(self.run_(message="how do I sort a list"), {"used": False})

    def test_weather_message(self):
        r = self.run_(message="What's the weather in Dhaka tomorrow?", climate=True)
        self.assertTrue(r["used"])
        self.assertEqual(r["kind"], "weather")
        t = r["text"]
        self.assertIn("<live-data", t)
        self.assertIn("Dhaka", t)
        self.assertIn("US AQI 152 (Unhealthy)", t)
        self.assertIn("Light rain", t)
        self.assertIn("Compared with normal (2016-2025", t)
        self.assertIn("The question is about: tomorrow", t)
        self.assertEqual(r["card"]["weather"]["now"]["temp"], 28.4)
        self.assertEqual(r["sources"], ["Open-Meteo"])

    def test_weather_with_saved_place_and_no_name(self):
        r = self.run_(message="should I bring an umbrella today?", default={"name": "Home", "lat": 23.81, "lon": 90.41})
        self.assertEqual(r["card"]["place"]["name"], "Home")

    def test_need_place(self):
        with self.assertRaises(geo.NeedPlace):
            self.run_(message="will it rain tomorrow?")

    def test_unknown_place(self):
        with self.assertRaises(WebError) as cm:
            self.run_(cmd="weather", args="Qwertyville")
        self.assertIn("could not find", str(cm.exception))

    def test_nearby_command(self):
        r = self.run_(cmd="near", args="pharmacy in Dhaka")
        self.assertIn("1. City Pharmacy", r["text"])
        self.assertIn("hours Mo-Su 08:00-23:00", r["text"])
        self.assertIn("may be missing, closed or moved", r["text"])
        self.assertEqual(r["card"]["items"][0]["name"], "City Pharmacy")
        self.assertEqual(r["sources"], ["OpenStreetMap"])

    def test_distance(self):
        r = self.run_(cmd="distance", args="Paris to Rome")
        self.assertIn("Straight-line distance: 110", r["text"])
        self.assertIn("not a driving or walking route", r["text"])

    def test_where_includes_local_time(self):
        r = self.run_(cmd="where", args="Kyoto")
        self.assertIn("Coordinates: 35.01160, 135.76810", r["text"])
        self.assertIn("Local time: 10:30", r["text"])
        self.assertEqual(sorted(r["sources"]), ["Open-Meteo", "OpenStreetMap"])

    def test_trip_combines_both_services(self):
        r = self.run_(cmd="trip", args="Dhaka 3 days")
        self.assertEqual(r["kind"], "trip")
        self.assertIn("Daily outlook:", r["text"])
        self.assertIn("Trip length: 3 day(s)", r["text"])
        self.assertEqual(r["card"]["days"], 3)

    def test_explicit_coordinates_skip_name_lookup(self):
        fake.CALLS.clear()
        r = self.run_(cmd="weather", args="My spot @23.8103,90.4125")
        self.assertEqual(r["card"]["place"]["name"], "My spot")
        self.assertFalse([c for c in fake.CALLS if "nominatim" in c[1]])

    def test_osm_down_falls_back_to_open_meteo_geocoder(self):
        fake.FAIL.add("nominatim")
        osm._CACHE.clear()
        try:
            r = self.run_(cmd="weather", args="Rome")
            self.assertEqual(r["card"]["place"]["source"], "open-meteo")
        finally:
            fake.FAIL.clear()

    def test_partial_failure_is_reported_not_fatal(self):
        fake.FAIL.add("air")
        openmeteo._CACHE.clear()
        try:
            r = self.run_(cmd="weather", args="Paris")
            self.assertTrue(r["used"])
            self.assertIsNone(r["card"]["air"])
            self.assertIn("Air quality unavailable", r["text"])
        finally:
            fake.FAIL.clear()

    def test_services_can_be_switched_off(self):
        with self.assertRaises(WebError):
            geo.handle({**self.base, "cmd": "weather", "args": "Dhaka"}, weather_ok=False)
        with self.assertRaises(WebError):
            geo.handle({**self.base, "cmd": "near", "args": "cafe in Dhaka"}, osm_ok=False)
        r = geo.handle({**self.base, "cmd": "where", "args": "Dhaka"}, weather_ok=False)       # place lookup still works without weather
        self.assertEqual(r["sources"], ["OpenStreetMap"])

    def test_imperial(self):
        r = self.run_(cmd="weather", args="Dhaka", units="imperial")
        self.assertIn("°F", r["text"])
        self.assertIn("mp/h", r["text"])


if __name__ == "__main__":
    unittest.main()
