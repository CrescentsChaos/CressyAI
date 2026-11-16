"""The /api/geo/* routes of the real FastAPI app, talking to the fake upstream (no internet, no model needed)."""
import sys
import tempfile
import types
import unittest

from tests import fake_upstream as fake

HDR = {"X-Cressy": "1"}


def setUpModule():
    global SRV, CLIENT, server
    SRV = fake.start()
    m = types.ModuleType("llama_cpp")
    m.Llama = object
    sys.modules.setdefault("llama_cpp", m)
    d = tempfile.mkdtemp()
    sys.argv = ["server.py", "--no-vision", "--no-image", "--no-reasoner", "--no-kb", "--data-dir", d, "--models-dir", d, "--root", d,
                "--osm-contact", "tests@example.org"]
    import server as server_mod
    server = server_mod
    from fastapi.testclient import TestClient
    CLIENT = TestClient(server.app, base_url="http://127.0.0.1")
    fake.start()        # the app's import re-ran configure(); point the services back at the fake


def tearDownModule():
    SRV.shutdown()
    SRV.server_close()


def post(path, body, headers=HDR):
    return CLIENT.post(path, json=body, headers=headers)


class RouteTests(unittest.TestCase):
    def test_caps_advertise_the_feature(self):
        c = CLIENT.get("/api/caps").json()
        self.assertTrue(c["geo"] and c["weather"] and c["osm"])

    def test_status(self):
        r = CLIENT.get("/api/geo/status?force=1").json()
        self.assertTrue(r["enabled"])
        self.assertTrue(r["weather"]["ok"] and r["osm"]["ok"])
        self.assertTrue(r["contact"])
        self.assertIn("pharmacy", [k["id"] for k in r["kinds"]])

    def test_post_routes_need_the_custom_header(self):
        for path, body in [("/api/geo/search", {"query": "Dhaka"}), ("/api/geo/context", {"cmd": "weather", "args": "Dhaka"}),
                           ("/api/geo/reverse", {"lat": 1, "lon": 1}), ("/api/geo/gpx", {"points": [{"lat": 1, "lon": 1}]})]:
            self.assertEqual(post(path, body, headers={}).status_code, 403, path)

    def test_search(self):
        r = post("/api/geo/search", {"query": "Dhaka"}).json()
        self.assertEqual((r["source"], r["results"][0]["name"]), ("openstreetmap", "Dhaka"))
        self.assertEqual(post("/api/geo/search", {"query": ""}).status_code, 400)

    def test_search_falls_back_when_osm_is_down(self):
        fake.FAIL.add("nominatim")
        try:
            r = post("/api/geo/search", {"query": "Paris"}).json()
            self.assertEqual(r["source"], "open-meteo")
        finally:
            fake.FAIL.clear()

    def test_reverse(self):
        r = post("/api/geo/reverse", {"lat": 23.81, "lon": 90.41}).json()
        self.assertIn("Gulshan", r["place"]["address"])
        self.assertEqual(post("/api/geo/reverse", {"lat": 95, "lon": 0}).status_code, 400)
        self.assertEqual(post("/api/geo/reverse", {"lat": "x", "lon": 0}).status_code, 400)

    def test_context_weather(self):
        r = post("/api/geo/context", {"cmd": "weather", "args": "Dhaka 3 days", "units": "metric", "days": 7}).json()
        self.assertTrue(r["used"])
        self.assertEqual(len(r["card"]["weather"]["daily"]), 3)
        self.assertIn("<live-data", r["text"])
        self.assertIn("seconds", r)

    def test_context_not_a_geo_question(self):
        self.assertEqual(post("/api/geo/context", {"message": "how do I reverse a list in python"}).json()["used"], False)

    def test_context_needs_a_place_is_not_an_error(self):
        r = post("/api/geo/context", {"message": "will it rain tomorrow?"})
        self.assertEqual(r.status_code, 200)
        self.assertEqual((r.json()["used"], r.json()["need_place"]), (False, True))
        self.assertIn("save yours", r.json()["why"])

    def test_context_unknown_place_is_404_with_a_readable_message(self):
        r = post("/api/geo/context", {"cmd": "weather", "args": "Qwertyville"})
        self.assertEqual(r.status_code, 404)
        self.assertIn("could not find", r.json()["detail"])

    def test_context_upstream_down_is_502(self):
        fake.FAIL.add("forecast")
        server.openmeteo._CACHE.clear()
        try:
            r = post("/api/geo/context", {"cmd": "weather", "args": "Paris"})
            self.assertEqual(r.status_code, 502)
            self.assertIn("Open-Meteo", r.json()["detail"])
        finally:
            fake.FAIL.clear()

    def test_context_sanitises_inputs(self):
        r = post("/api/geo/context", {"cmd": "near", "args": "pharmacy in Dhaka", "radius": "99999999", "default": {"lat": "abc"}})
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.json()["card"]["radius"], 10000)
        self.assertEqual(post("/api/geo/context", {"message": "x" * 5000}).status_code, 400)
        self.assertEqual(post("/api/geo/context", {"cmd": "weather", "args": "Dhaka", "default": [1, 2]}).status_code, 200)

    def test_gpx(self):
        r = post("/api/geo/gpx", {"name": "Pharmacies", "points": [{"name": "A", "lat": 1.5, "lon": 2.5}, {"name": "bad", "lat": "x"}]})
        self.assertEqual(r.status_code, 200)
        self.assertIn("application/gpx+xml", r.headers["content-type"])
        self.assertEqual(r.text.count("<wpt"), 1)
        self.assertEqual(post("/api/geo/gpx", {"points": []}).status_code, 400)

    def test_switching_features_off(self):
        old = (server.WEATHER_ON, server.OSM_ON, server.GEO_ON)
        try:
            server.OSM_ON = False
            self.assertEqual(post("/api/geo/reverse", {"lat": 1, "lon": 1}).status_code, 503)
            r = post("/api/geo/context", {"cmd": "near", "args": "cafe in Dhaka"})
            self.assertEqual(r.status_code, 502)
            self.assertIn("turned off", r.json()["detail"])
            server.WEATHER_ON = server.OSM_ON = server.GEO_ON = False
            self.assertEqual(post("/api/geo/search", {"query": "Dhaka"}).status_code, 503)
            self.assertFalse(CLIENT.get("/api/geo/status").json()["enabled"])
        finally:
            server.WEATHER_ON, server.OSM_ON, server.GEO_ON = old


if __name__ == "__main__":
    unittest.main()
