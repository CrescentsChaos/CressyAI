"""/api/tools, /api/tools/select and /api/agent of the real FastAPI app: real places & weather code talking to the fake upstream,
with a scripted stand-in for the language model (no model, no internet). Run: python -m unittest tests.test_server_tools -v"""
import json
import sys
import tempfile
import types
import unittest

from tests import fake_upstream as fake

HDR = {"X-Cressy": "1"}


class FakeLlama:
    def __init__(self):
        self.turns, self.seen, self.n_tokens = [], [], 0

    def n_ctx(self):
        return 8192

    def tokenize(self, b, add_bos=False):
        return list(b)

    def create_chat_completion(self, messages, stream=True, temperature=0.7, **kw):
        self.seen.append({"messages": messages, "temperature": temperature})
        text = self.turns.pop(0)
        self.n_tokens = len(text)
        for i in range(0, len(text), 5):
            yield {"choices": [{"delta": {"content": text[i:i + 5]}, "finish_reason": None}]}
        yield {"choices": [{"delta": {}, "finish_reason": "stop"}]}


def setUpModule():
    global SRV, CLIENT, server, LLM
    SRV = fake.start()
    m = types.ModuleType("llama_cpp")
    m.Llama = object
    sys.modules.setdefault("llama_cpp", m)
    d = tempfile.mkdtemp()
    sys.argv = ["server.py", "--no-vision", "--no-image", "--no-reasoner", "--no-kb", "--data-dir", d, "--models-dir", d, "--root", d, "--osm-contact", "tests@example.org"]
    import server as server_mod
    server = server_mod
    from fastapi.testclient import TestClient
    CLIENT = TestClient(server.app, base_url="http://127.0.0.1")
    fake.start()
    LLM = FakeLlama()
    server.llm = LLM
    server.load_coder = lambda: None
    (server.ROOT / "notes.txt").write_text("alpha beta\n", "utf-8")


def tearDownModule():
    SRV.shutdown()
    SRV.server_close()


def call(name, **a):
    return "<tool_call>" + json.dumps({"name": name, "arguments": a}) + "</tool_call>"


def agent(turns, text, **extra):
    LLM.turns, LLM.seen = list(turns), []
    body = {"messages": [{"role": "system", "content": "sys"}, {"role": "user", "content": text}], "text": text, "temperature": 0.7, **extra}
    r = CLIENT.post("/api/agent", json=body, headers=HDR)
    assert r.status_code == 200, r.text
    return [json.loads(line[6:]) for line in r.text.split("\n\n") if line.startswith("data: ")]


def results(evs):
    return [e for e in evs if e["type"] == "tool_result"]


def answer(evs):
    return "".join(e["text"] for e in evs if e["type"] == "token")


class RouteTests(unittest.TestCase):
    def test_caps_and_catalog(self):
        self.assertTrue(CLIENT.get("/api/caps").json()["tools"])
        t = CLIENT.get("/api/tools").json()
        by = {x["name"]: x for x in t["tools"]}
        self.assertTrue(t["enabled"] and by["weather"]["available"] and by["calculator"]["available"] and by["read_file"]["available"])
        self.assertFalse(by["knowledge_search"]["available"])                      # --no-kb
        self.assertIn("knowledge base", by["knowledge_search"]["why"])

    def test_post_routes_need_the_custom_header(self):
        for path in ("/api/tools/select", "/api/agent"):
            self.assertEqual(CLIENT.post(path, json={"text": "x", "messages": []}).status_code, 403, path)

    def test_select(self):
        s = CLIENT.post("/api/tools/select", json={"text": "What's the weather in Paris?"}, headers=HDR).json()
        self.assertTrue(s["use"])
        self.assertEqual(s["seeds"][0]["name"], "weather")
        self.assertFalse(CLIENT.post("/api/tools/select", json={"text": "thanks!"}, headers=HDR).json()["use"])
        s = CLIENT.post("/api/tools/select", json={"text": "hello there friend", "force": True}, headers=HDR).json()
        self.assertTrue(s["use"] and "calculator" in s["tools"])
        s = CLIENT.post("/api/tools/select", json={"text": "weather in Paris", "disabled": ["weather", "geocode"]}, headers=HDR).json()
        self.assertNotIn("weather", s["tools"])

    def test_agent_validates_the_body(self):
        for body in ({"messages": []}, {"messages": "x"}, {"messages": [{"role": "tool", "content": "x"}]}, {"messages": [{"role": "user", "content": 5}]}):
            self.assertEqual(CLIENT.post("/api/agent", json=body, headers=HDR).status_code, 400, body)


class AgentTests(unittest.TestCase):
    def setUp(self):
        fake.FAIL.clear()
        for m in (server.openmeteo, server.openstreetmap):
            m._CACHE.clear()

    def test_intent_seed_runs_first_and_the_model_answers_from_it(self):
        evs = agent(["Dhaka is 28.4 °C and partly cloudy."], "What's the weather in Dhaka?")
        self.assertEqual([e["type"] for e in evs][:4], ["plan", "tool_start", "tool_result", "token"])
        r = results(evs)[0]
        self.assertEqual((r["name"], r["status"], r["via"]), ("weather", "ok", "intent"))
        self.assertIn("28", r["preview"])
        self.assertEqual(LLM.seen[0]["temperature"], 0.7)                            # results are in already: normal temperature
        self.assertIn("TOOL RESULTS", LLM.seen[0]["messages"][-1]["content"])
        self.assertEqual(answer(evs), "Dhaka is 28.4 °C and partly cloudy.")
        self.assertEqual(evs[-1]["type"], "done")
        self.assertEqual(evs[-1]["tools"], {"calls": 1, "ok": 1, "failed": 0})
        self.assertFalse([e for e in evs if e["type"] == "grounding"])               # 28.4 is in the tool output

    def test_model_chains_tools_across_rounds(self):
        evs = agent([call("geocode", query="Kyoto") + call("distance", **{"from": "Paris", "to": "Rome"}), call("weather", lat=35.0116, lon=135.7681, days=2), "Done."],
                    "Compare two things for me", force=True)
        rs = results(evs)
        self.assertEqual({r["name"] for r in rs}, {"geocode", "distance", "weather"})
        self.assertTrue(all(r["status"] == "ok" for r in rs))
        dist = next(r for r in rs if r["name"] == "distance")
        self.assertIn("Straight-line distance", dist["preview"])
        self.assertEqual(LLM.seen[0]["temperature"], 0.3)                            # choosing tools: low temperature
        self.assertIn("lat 35.01160", LLM.seen[1]["messages"][-1]["content"])        # round two saw round one
        self.assertEqual(evs[-1]["tools"]["calls"], 3)

    def test_failures_are_visible_to_the_model_and_the_card(self):
        fake.FAIL.add("forecast")
        evs = agent(["I could not get the weather right now."], "weather in Dhaka please")
        r = results(evs)[0]
        self.assertEqual(r["status"], "error")
        self.assertIn("ERROR", LLM.seen[0]["messages"][-1]["content"])
        self.assertEqual(evs[-1]["tools"]["failed"], 1)

    def test_unknown_place_is_an_error_not_a_guess(self):
        evs = agent([call("weather", place="Atlantis"), "No such place."], "weather in Atlantis", force=True)
        r = results(evs)[0]
        self.assertEqual(r["status"], "error")
        self.assertIn("Atlantis", r["notes"][0])

    def test_nearby_and_verification_of_places(self):
        evs = agent([call("nearby_places", kind="pharmacies", place="Dhaka", radius=2000), "City Pharmacy is the closest."], "pharmacy near Dhaka?")
        r = [x for x in results(evs) if x["via"] == "model"][0]
        self.assertEqual(r["status"], "ok")
        self.assertIn("City Pharmacy", LLM.seen[-1]["messages"][-1]["content"])

    def test_files_stay_inside_root(self):
        evs = agent([call("read_file", path="notes.txt") + call("read_file", path="../../etc/passwd"), "ok"], "read notes.txt", force=True)
        by = {r["args"]["path"]: r for r in results(evs)}      # parallel calls finish in any order
        ok, bad = by["notes.txt"], by["../../etc/passwd"]
        self.assertEqual((ok["status"], bad["status"]), ("ok", "error"))
        self.assertIn("outside", bad["notes"][0])
        self.assertIn("alpha beta", ok["preview"])

    def test_invented_numbers_are_flagged(self):
        evs = agent(["It is 28.4 °C, with a 73% chance of snow and 123456 visitors."], "weather in Dhaka")
        g = [e for e in evs if e["type"] == "grounding"]
        self.assertEqual(g[0]["unsupported"], ["123456"])

    def test_model_errors_are_reported(self):
        LLM.turns = []                                                                  # nothing scripted: the fake model raises
        r = CLIENT.post("/api/agent", json={"messages": [{"role": "user", "content": "2+2"}], "text": "2+2", "force": True}, headers=HDR)
        last = json.loads([line for line in r.text.split("\n\n") if line.startswith("data: ")][-1][6:])
        self.assertEqual(last["type"], "error")
        self.assertTrue(server.LOCK.acquire(blocking=False))                           # the model lock was released
        server.LOCK.release()


if __name__ == "__main__":
    unittest.main()
