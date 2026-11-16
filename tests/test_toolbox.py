"""Tool orchestration: schema checks, selection, the call format, execution + verification, the multi-step loop.
Run: python -m unittest tests.test_toolbox -v     (no model, no network: every backend is a fake)"""
import json
import threading
import time
import unittest
from types import SimpleNamespace as NS

import geo
import toolbox as tb


class WebError(Exception):
    pass


class NotFound(WebError):
    pass


class NeedPlace(WebError):
    pass


def make_ctx(**over):
    calls = {"search": [], "wiki": [], "fc": []}

    def research(base, q, **kw):
        calls["search"].append(q)
        if "boom" in q:
            raise WebError("SearXNG is not reachable.")
        if "nothing" in q:
            return {"results": [], "read": 0}
        res = [{"title": f"{q} - result {i}", "url": f"https://example.org/{q.replace(' ', '-')}/{i}", "domain": "example.org", "snippet": f"about {q} {i}",
                "text": f"Full text about {q}. The price is 1234.5 dollars." if i == 1 else ""} for i in (1, 2)]
        if "unrelated" in q:
            res = [{"title": "Cats", "url": "https://cats.example/1", "domain": "cats.example", "snippet": "fluffy", "text": ""}]
        return {"results": res, "read": sum(1 for r in res if r.get("text"))}

    def lookup(q, n=3, read=1, lang="en", max_chars=3000):
        calls["wiki"].append(q)
        return {"results": [{"title": q.title(), "url": "https://en.wikipedia.org/wiki/" + q.replace(" ", "_"), "domain": "en.wikipedia.org", "snippet": f"{q} is a thing.",
                             "text": f"{q.title()} is a thing with population 9876.", "disambiguation": False}], "read": 1}

    def resolve(spec, default=None, coords=None, lang="en", osm_ok=True):
        if coords:
            return {"name": spec or "pin", "lat": coords[0], "lon": coords[1], "country": "X"}
        if "atlantis" in spec.lower():
            raise NotFound("I could not find a place called Atlantis.")
        return {"name": spec.title(), "lat": 23.8, "lon": 90.4, "country": "Bangladesh"}

    def forecast(lat, lon, units="metric", days=5):
        calls["fc"].append((lat, lon, units, days))
        return {"now": {"temp": 31.0 if lat < 80 else 400.0, "text": "Sunny"}, "daily": [{}] * days, "units": {"temp": "°C"}}

    geo_f = NS(NotFound=NotFound, NeedPlace=NeedPlace, resolve=resolve, weather_text=lambda *a: f"Weather for {a[0]['name']}: 31 °C, sunny.", wrap=lambda b, s: b + "\n(" + ", ".join(s) + ")",
               nearby_text=lambda p, k, items, r, u: f"{len(items)} {k} near {p['name']}", distance_text=lambda a, b, d, u: f"{a['name']} to {b['name']}: {d['km']} km",
               parse_message=geo.parse_message)
    osm = NS(KINDS={"pharmacy": 1, "cafe": 1}, kind_from_text=lambda t: "pharmacy" if "pharm" in t else None,
             search=lambda q, n=3, lang="en": [{"name": q.title(), "display_name": q.title() + ", X", "lat": 1.5, "lon": 2.5, "country": "X", "kind": "place"}] if "atlantis" not in q else [],
             nearby=lambda lat, lon, kind, radius, limit: [{"name": "P1"}] if lat < 50 else [],
             between=lambda a, b, u="metric": {"km": 5.0 if a["name"] != b["name"] else 0.0})
    kw = dict(web=True, wiki=True, weather=True, osm=True, inat=False, searx="http://x", mem=None, files=True,
              websearch=NS(WebError=WebError, research=research, read_pages=lambda urls, **k: [{"ok": True, "url": urls[0], "title": "T", "text": "page text " * 50}]),
              wikipedia=NS(lookup=lookup), geo=geo_f, openmeteo=NS(forecast=forecast, air_quality=lambda *a: None, geocode=lambda q, n=3, l="en": []), openstreetmap=osm,
              read_file=lambda p, n: ("", "no such file", False) if p == "nope.py" else ("print('hi')", None, False), search_files=lambda q: [])
    kw.update(over)
    c = tb.Ctx(**kw)
    c.calls = calls
    return c


def script(*turns):
    it, seen = iter(turns), []

    def llm(msgs, low_temp=False):
        seen.append(([m["content"] for m in msgs], low_temp))
        text = next(it)
        for i in range(0, len(text), 7):
            yield ("tok", text[i:i + 7])
        yield ("end", {"finish_reason": "stop", "completion_tokens": len(text)})
    return llm, seen


def call(name, **args):
    return tb.TAG_OPEN + json.dumps({"name": name, "arguments": args}) + tb.TAG_CLOSE


def go(llm, ctx, sel, **kw):
    msgs = [{"role": "system", "content": "Today is Sunday."}, {"role": "user", "content": "q"}]
    return list(tb.orchestrate(msgs, "q", sel, ctx, llm, **kw))


def sel_of(*names, seeds=()):
    return {"use": True, "tools": list(names), "intents": ["x"], "seeds": list(seeds), "why": "test"}


class SchemaTests(unittest.TestCase):
    def test_coerce_clamp_and_drop(self):
        clean, errs, notes = tb.validate(tb.TOOLS["weather"], {"place": " Dhaka ", "days": "30", "bogus": 1})
        self.assertEqual((clean["place"], clean["days"], errs), ("Dhaka", 16, []))
        self.assertTrue(any("used 16" in n for n in notes) and any("bogus" in n for n in notes))

    def test_required_and_types(self):
        self.assertIn("missing required argument 'query'", tb.validate(tb.TOOLS["web_search"], {})[1][0])
        self.assertTrue(tb.validate(tb.TOOLS["web_search"], {"query": "x", "n": "many"})[1])
        self.assertTrue(tb.validate(tb.TOOLS["web_search"], [1])[1])
        self.assertEqual(tb.validate(tb.TOOLS["calculator"], '{"expression": "1+1"}')[0], {"expression": "1+1"})
        self.assertTrue(tb.validate(tb.TOOLS["weather"], {"days": True})[1])          # bool is not a number


class CalculatorTests(unittest.TestCase):
    def test_values(self):
        for e, v in [("2+2*3", 8), ("(1+2)^3", 27), ("15% of 80", 12), ("1,234 x 2", 2468), ("sqrt(16)+pi*0", 4), ("10/4", 2.5), ("7 // 2", 3), ("factorial(5)", 120)]:
            self.assertAlmostEqual(tb.calc(e), v, msg=e)

    def test_refuses_anything_else(self):
        for e in ["__import__('os').system('x')", "open('f')", "1/0", "2**99999", "a+1", "lambda: 1", "[1,2]", "9" * 300, "sqrt(-1)", "1 +"]:
            with self.assertRaises(tb.ToolError, msg=e):
                tb.calc(e)


class FormatTests(unittest.TestCase):
    def test_parse_variants(self):
        one = call("weather", place="Dhaka")
        self.assertEqual(tb.parse_calls(one)[0], {"name": "weather", "arguments": {"place": "Dhaka"}})
        self.assertEqual(len(tb.parse_calls(one + "\n" + call("calculator", expression="1+1"))), 2)
        self.assertEqual(tb.parse_calls(tb.TAG_OPEN + '{"name":"calculator","arguments":{"expression":"1+1"}}')[0]["name"], "calculator")    # no closing tag
        self.assertEqual(tb.parse_calls('{"tool_calls":[{"function":{"name":"weather","arguments":"{\\"place\\":\\"X\\"}"}}]}', {"weather"})[0]["arguments"], {"place": "X"})
        self.assertEqual(tb.parse_calls('```json\n{"name":"weather","arguments":{"place":"X"}}\n```', {"weather"})[0]["name"], "weather")

    def test_bare_json_must_name_a_known_tool(self):
        self.assertEqual(tb.parse_calls('{"name": "Bob", "arguments": {}}', {"weather"}), [])
        self.assertEqual(tb.parse_calls('Sure! The answer is 4.', {"weather"}), [])

    def test_sniffer_streams_plain_answers_untouched(self):
        s, out = tb.Sniffer(True), ""
        for ch in "Hello <b> world, 2 < 3.":
            out += s.feed(ch)
        tail, calls, bad = s.finish()
        self.assertEqual((out + tail, calls, bad), ("Hello <b> world, 2 < 3.", [], False))

    def test_sniffer_holds_a_tool_call_even_when_split(self):
        text = call("weather", place="Dhaka")
        s, out = tb.Sniffer(True), ""
        for i in range(0, len(text), 3):
            out += s.feed(text[i:i + 3])
        tail, calls, bad = s.finish()
        self.assertEqual((out, tail, bad, calls[0]["name"]), ("", "", False, "weather"))

    def test_sniffer_think_then_answer_and_think_then_call(self):
        s = tb.Sniffer(True)
        out = "".join(s.feed(x) for x in ["<think>hmm", " ok</think>\n", "The answer."]) + s.finish()[0]
        self.assertEqual(out, "<think>hmm ok</think>\nThe answer.")
        s = tb.Sniffer(True)
        out = "".join(s.feed(x) for x in ["<think>hmm</think>", call("calculator", expression="1+1")])
        tail, calls, _ = s.finish()
        self.assertEqual((out, tail, calls[0]["name"]), ("", "", "calculator"))

    def test_sniffer_json_and_code_fences(self):
        s = tb.Sniffer(True, {"weather"})
        self.assertEqual("".join(s.feed(x) for x in ['{"name":"weather",', '"arguments":{"place":"X"}}']), "")
        self.assertEqual(s.finish()[1][0]["name"], "weather")
        s = tb.Sniffer(True, {"weather"})
        out = "".join(s.feed(x) for x in ["```py", "thon\nprint(1)\n```"]) + s.finish()[0]
        self.assertEqual(out, "```python\nprint(1)\n```")
        s = tb.Sniffer(True, {"weather"})                       # JSON that is not a tool call is shown
        out = "".join(s.feed(x) for x in ['{"name":"Bob"}']) + s.finish()[0]
        self.assertEqual(out, '{"name":"Bob"}')

    def test_sniffer_off_never_holds(self):
        s = tb.Sniffer(False)
        self.assertEqual(s.feed(call("weather", place="X")), call("weather", place="X"))

    def test_malformed(self):
        s = tb.Sniffer(True)
        s.feed(tb.TAG_OPEN + "{not json")
        self.assertEqual(s.finish()[1:], ([], True))


class SelectTests(unittest.TestCase):
    def setUp(self):
        self.c = make_ctx()

    def test_weather_intent_and_seed(self):
        s = tb.select("What's the weather in Dhaka tomorrow?", self.c)
        self.assertTrue(s["use"])
        self.assertEqual(s["tools"][:2], ["weather", "geocode"])
        self.assertEqual(s["seeds"][0]["name"], "weather")
        self.assertIn("dhaka", s["seeds"][0]["arguments"]["place"].lower())

    def test_places_intent(self):
        s = tb.select("any pharmacy near Gulshan?", self.c)
        self.assertIn("nearby_places", s["tools"])
        self.assertEqual(s["seeds"][0]["name"], "nearby_places")

    def test_small_talk_code_and_rewrites_use_no_tools(self):
        for t in ["thanks!", "rewrite that shorter", "what is 2+2 in ```py\nprint(2+2)```", ""]:
            self.assertFalse(tb.select(t, self.c)["use"], t)

    def test_url_and_hints(self):
        s = tb.select("summarise https://example.org/a please", self.c)
        self.assertEqual([x["name"] for x in s["seeds"]], ["read_url"])
        s = tb.select("tell me about ocelots", self.c, hints={"wiki": True, "web": True, "q": "ocelots"})
        self.assertEqual([x["name"] for x in s["seeds"]], ["web_search", "wikipedia"])

    def test_math_seed_only_for_a_bare_expression(self):
        self.assertEqual(tb.select("15% of 80", self.c)["seeds"][0]["arguments"]["expression"], "15% of 80")
        s = tb.select("why is 2+2=4 true in modular arithmetic?", self.c)
        self.assertEqual(s["seeds"], [])

    def test_unavailable_and_disabled_tools_are_left_out(self):
        c = make_ctx(web=False, weather=False)
        s = tb.select("latest news about weather in Paris", c)
        self.assertNotIn("web_search", s["tools"])
        self.assertNotIn("weather", s["tools"])
        s = tb.select("latest news", self.c, disabled=("web_search", "read_url", "wikipedia"))
        self.assertFalse(s["use"])

    def test_knowledge_tool_needs_documents(self):
        self.assertFalse(tb.select("what does my document say?", self.c)["use"])
        c = make_ctx(kb=NS(docs={"a": {"enabled": True}}))
        self.assertEqual(tb.select("what does my document say?", c)["tools"], ["knowledge_search"])

    def test_cap(self):
        s = tb.select("latest news near me about birds, weather and 2+2 and who is Ada", self.c)
        self.assertLessEqual(len(s["tools"]), 6)


class ExecuteTests(unittest.TestCase):
    def setUp(self):
        self.c = make_ctx()
        self.run = tb.Run(self.c)

    def ex(self, name, **a):
        return tb.execute({"name": name, "arguments": a}, self.c, self.run)

    def test_ok_and_sources_numbered_across_calls(self):
        a, b = self.ex("web_search", query="cats food"), self.ex("web_search", query="dogs food")
        self.assertEqual((a["status"], a["sources"], b["sources"]), ("ok", [1, 2], [3, 4]))
        self.assertIn("[3]", b["text"])
        self.assertEqual(len(self.run.sources), 4)

    def test_error_empty_partial(self):
        self.assertEqual(self.ex("web_search", query="boom")["status"], "error")
        e = self.ex("web_search", query="nothing here")
        self.assertEqual(e["status"], "empty")
        self.assertIn("rephrase", e["notes"][0])
        p = self.ex("web_search", query="unrelated topic")
        self.assertEqual(p["status"], "partial")

    def test_invalid_unknown_unavailable_duplicate(self):
        self.assertEqual(self.ex("web_search")["status"], "invalid")
        u = self.ex("teleport", where="mars")
        self.assertEqual(u["status"], "invalid")
        self.assertIn("use one of", u["notes"][0])
        self.c.web = False
        self.assertIn("not available", self.ex("web_search", query="x")["notes"][0])
        self.c.web = True
        first, second = self.ex("calculator", expression="2+2"), self.ex("calculator", expression="2+2")
        self.assertEqual((first["status"], second["status"], second["dup_of"]), ("ok", "duplicate", first["id"]))

    def test_verification_catches_bad_data(self):
        self.assertEqual(self.ex("weather", place="Dhaka")["status"], "ok")
        w = self.ex("weather", lat=89.0, lon=0.0, days=2)             # fake service answers 400 °C up there
        self.assertEqual(w["status"], "partial")
        self.assertIn("implausible", w["notes"][0])
        self.assertEqual(self.ex("weather", place="Atlantis")["status"], "error")
        self.assertEqual(self.ex("distance", **{"from": "A", "to": "A"})["status"], "partial")
        self.assertEqual(self.ex("nearby_places", kind="pharmacy", lat=60.0, lon=1.0)["status"], "empty")
        self.assertIn("one of: pharmacy, cafe", self.ex("nearby_places", kind="zoo", place="X")["notes"][0])

    def test_wikipedia_checks_the_match(self):
        self.assertEqual(self.ex("wikipedia", query="ocelot")["status"], "ok")

    def test_files_and_clock(self):
        self.assertEqual(self.ex("read_file", path="a.py")["status"], "ok")
        self.assertEqual(self.ex("read_file", path="nope.py")["status"], "error")
        self.assertEqual(self.ex("datetime", tz="Asia/Dhaka")["status"], "ok")
        self.assertEqual(self.ex("datetime", tz="Mars/Base")["status"], "error")
        d = self.ex("datetime", tz="UTC+6", date="2999-01-01")
        self.assertGreater(d["data"]["days"], 0)

    def test_a_crashing_tool_is_contained(self):
        self.c.openmeteo.forecast = lambda *a: 1 / 0
        r = self.ex("weather", place="Dhaka")
        self.assertEqual(r["status"], "error")
        self.assertIn("ZeroDivisionError", r["notes"][0])

    def test_round_runs_independent_calls_in_parallel_and_times_out(self):
        gate = threading.Barrier(2, timeout=3)
        self.c.websearch.research = lambda base, q, **k: (gate.wait(), {"results": [{"title": q, "url": "https://e.org/" + q, "domain": "e.org", "snippet": q}], "read": 0})[1]
        t0 = time.time()
        res = list(tb.run_round([{"name": "web_search", "arguments": {"query": "a b", "read": 0}}, {"name": "web_search", "arguments": {"query": "c d", "read": 0}}], self.c, self.run))
        self.assertEqual(sorted(r["status"] for r in res), ["ok", "ok"])             # both had to be in flight at once to pass the barrier
        self.assertLess(time.time() - t0, 2.5)
        self.c.websearch.research = lambda *a, **k: time.sleep(2) or {"results": []}
        res = list(tb.run_round([{"name": "web_search", "arguments": {"query": "slow one"}}], self.c, self.run, timeout=0.3))
        self.assertIn("timed out", res[0]["notes"][0])


class GroundingTests(unittest.TestCase):
    def test_only_unsupported_figures_are_flagged(self):
        ev = "The price is 1,234.5 dollars. Population 9876. Today is 15 November 2026."
        self.assertEqual(tb.ungrounded("It costs 1234.5 dollars, about 9876 people, in 2026.", ev), [])
        self.assertEqual(tb.ungrounded("It costs 1234.5 and 4567 and 3.14 (3 items, 12 of them).", ev), ["4567", "3.14"])
        self.assertEqual(tb.ungrounded("rounded: 1234.50", ev), [])


class OrchestrateTests(unittest.TestCase):
    def types(self, evs):
        return [e["type"] for e in evs]

    def text(self, evs):
        return "".join(e["text"] for e in evs if e["type"] == "token")

    def test_one_tool_then_answer(self):
        llm, seen = script(call("calculator", expression="12*12"), "It is 144.")
        evs = go(llm, make_ctx(), sel_of("calculator"))
        t = self.types(evs)
        self.assertEqual((t[:3], set(t[3:-1]), t[-1]), (["plan", "tool_start", "tool_result"], {"token"}, "done"))
        self.assertEqual(self.text(evs), "It is 144.")
        self.assertEqual(evs[-1]["tools"], {"calls": 1, "ok": 1, "failed": 0})
        self.assertIn("calculator", seen[0][0][0])                                   # catalog is in the system prompt
        self.assertTrue(seen[0][1] and not seen[1][1])                               # low temperature only while choosing tools
        self.assertIn("TOOL RESULTS", seen[1][0][-1])
        self.assertIn("= 144", seen[1][0][-1])

    def test_answer_without_tools_streams_and_skips_grounding(self):
        llm, _ = script("Just text, 42424.")
        evs = go(llm, make_ctx(), sel_of("calculator"))
        self.assertEqual((self.text(evs), evs[-1]["tools"]["calls"]), ("Just text, 42424.", 0))
        self.assertNotIn("grounding", self.types(evs))

    def test_multi_tool_workflow_dependent_calls(self):
        llm, seen = script(call("geocode", query="Dhaka") + "\n" + call("wikipedia", query="Dhaka"),
                           call("weather", lat=1.5, lon=2.5, days=3), "Dhaka is at 1.5, 2.5; it is 31 °C and sunny.")
        c = make_ctx()
        evs = go(llm, c, sel_of("geocode", "weather", "wikipedia"))
        res = [e for e in evs if e["type"] == "tool_result"]
        self.assertEqual([(r["name"], r["status"]) for r in res].count(("weather", "ok")), 1)
        self.assertEqual({r["name"] for r in res}, {"geocode", "wikipedia", "weather"})
        self.assertEqual(c.calls["fc"], [(1.5, 2.5, "metric", 3)])                   # round-two call used round-one output
        self.assertIn("lat 1.50000", seen[1][0][-1])
        self.assertEqual(seen[2][0][-2], call("weather", lat=1.5, lon=2.5, days=3))  # the call is replayed in normalised form
        self.assertNotIn("grounding", self.types(evs))                               # 1.5, 2.5, 31 all come from results

    def test_ids_pair_start_and_result_and_sources_are_cumulative(self):
        llm, _ = script(call("web_search", query="cats food") + call("web_search", query="dogs food"), "Done [1] [3].")
        evs = go(llm, make_ctx(), sel_of("web_search"))
        starts = {e["id"]: e for e in evs if e["type"] == "tool_start"}
        for r in [e for e in evs if e["type"] == "tool_result"]:
            self.assertEqual(starts[r["id"]]["name"], r["name"])
        last = [e for e in evs if e["type"] == "tool_result"][-1]
        self.assertEqual(len(last["sources"]), 4)

    def test_bad_arguments_are_fed_back_and_repaired(self):
        llm, seen = script(call("calculator"), call("calculator", expression="3*3"), "Nine.")
        evs = go(llm, make_ctx(), sel_of("calculator"))
        res = [e for e in evs if e["type"] == "tool_result"]
        self.assertEqual([r["status"] for r in res], ["invalid", "ok"])
        self.assertIn("missing required argument 'expression'", seen[1][0][-1])
        self.assertEqual(self.text(evs), "Nine.")

    def test_failed_search_falls_back_to_wikipedia(self):
        llm, seen = script(call("web_search", query="boom ocelot"), "Ocelots are cats.")
        evs = go(llm, make_ctx(), sel_of("web_search", "wikipedia"))
        res = [e for e in evs if e["type"] == "tool_result"]
        self.assertEqual([(r["name"], r["status"], r["via"]) for r in res], [("web_search", "error", "model"), ("wikipedia", "ok", "fallback")])
        self.assertIn("automatic fallback", seen[1][0][-1])

    def test_step_limit_forces_an_answer(self):
        llm, seen = script(*[call("calculator", expression=f"{i}+1") for i in range(1, 3)], "Final.")
        evs = go(llm, make_ctx(), sel_of("calculator"), steps=2)
        self.assertEqual(self.text(evs), "Final.")
        self.assertIn("Do not call any more tools", seen[-1][0][-1])
        self.assertEqual(sum(1 for e in evs if e["type"] == "tool_result"), 2)

    def test_a_model_that_ignores_the_limit_is_shown_as_text_not_run(self):
        llm, _ = script(call("calculator", expression="1+1"), call("calculator", expression="2+2"))
        evs = go(llm, make_ctx(), sel_of("calculator"), steps=1)
        self.assertEqual(sum(1 for e in evs if e["type"] == "tool_result"), 1)
        self.assertEqual(evs[-1]["type"], "done")

    def test_call_budget(self):
        llm, _ = script(call("calculator", expression="1+1") + call("calculator", expression="2+2") + call("calculator", expression="3+3"), "ok")
        evs = go(llm, make_ctx(), sel_of("calculator"), max_calls=2)
        self.assertEqual(sum(1 for e in evs if e["type"] == "tool_result"), 2)

    def test_seeds_run_before_the_model_speaks(self):
        llm, seen = script("It is 8.")
        evs = go(llm, make_ctx(), sel_of("calculator", seeds=[{"name": "calculator", "arguments": {"expression": "2*4"}}]))
        self.assertEqual(self.types(evs)[:4], ["plan", "tool_start", "tool_result", "token"])
        self.assertEqual(evs[1]["via"], "intent")
        self.assertIn("= 8", seen[0][0][-1])
        self.assertFalse(seen[0][1] is None)
        self.assertFalse(seen[0][1])                                                 # results are already in: normal temperature

    def test_malformed_call_gets_one_clear_retry(self):
        llm, seen = script(tb.TAG_OPEN + "{oops", call("calculator", expression="1+1"), "Two.")
        evs = go(llm, make_ctx(), sel_of("calculator"))
        self.assertIn("could not be parsed", seen[1][0][-1])
        self.assertEqual(self.text(evs), "Two.")

    def test_repeated_failures_end_in_an_honest_answer(self):
        llm, seen = script(call("web_search", query="boom a"), call("web_search", query="boom b"), "I could not look that up.")
        evs = go(llm, make_ctx(wiki=False), sel_of("web_search"))
        self.assertTrue(any("keep failing" in m for m in seen[-1][0]))
        self.assertEqual(evs[-1]["tools"]["failed"], 2)

    def test_invented_figures_are_flagged(self):
        llm, _ = script(call("calculator", expression="6*7"), "It is 42, which is 98765 in some units.")
        evs = go(llm, make_ctx(), sel_of("calculator"))
        g = [e for e in evs if e["type"] == "grounding"]
        self.assertEqual(g[0]["unsupported"], ["98765"])
        evs = go(script(call("calculator", expression="6*7"), "It is 98765.")[0], make_ctx(), sel_of("calculator"), verify=False)
        self.assertNotIn("grounding", self.types(evs))

    def test_narration_before_a_call_is_separated_from_the_answer(self):
        llm, _ = script("Let me check. " + call("calculator", expression="1+1"), "Two.")
        self.assertEqual(self.text(go(llm, make_ctx(), sel_of("calculator"))), "Let me check. \n\nTwo.")

    def test_closing_the_stream_closes_the_model_turn(self):
        closed = []

        def llm(msgs, low_temp=False):
            try:
                for i in range(100):
                    yield ("tok", "word ")
            finally:
                closed.append(True)
        g = tb.orchestrate([{"role": "user", "content": "q"}], "q", sel_of("calculator"), make_ctx(), llm)
        next(g)
        next(g)
        g.close()
        self.assertEqual(closed, [True])

    def test_model_failure_is_reported(self):
        def llm(msgs, low_temp=False):
            raise RuntimeError("model unloaded")
            yield
        evs = go(llm, make_ctx(), sel_of("calculator"))
        self.assertEqual(evs[-1], {"type": "error", "message": "model unloaded"})


if __name__ == "__main__":
    unittest.main()
