"""GitHub lookup: link parsing, the lookup itself (GitHub API faked), the `github` tool, selection, and the API routes. No network.
Run: python -m unittest tests.test_github -v"""
import json
import sys
import tempfile
import types
import unittest
import urllib.request
from types import SimpleNamespace as NS

import github
import toolbox as tb
import websearch
from tests.test_toolbox import WebError, make_ctx

HDR = {"X-Cressy": "1"}


class ParseTests(unittest.TestCase):
    def test_links_and_names(self):
        p = github.parse_target
        self.assertEqual(p("https://github.com/psf/requests"), ("repo", "psf", "requests"))
        self.assertEqual(p("https://github.com/psf/requests.git"), ("repo", "psf", "requests"))
        self.assertEqual(p("psf/requests"), ("repo", "psf", "requests"))
        self.assertEqual(p("https://github.com/psf/requests/issues/12"), ("issue", "psf", "requests", 12))
        self.assertEqual(p("https://github.com/psf/requests/pull/7"), ("issue", "psf", "requests", 7))
        self.assertEqual(p("https://github.com/psf/requests/blob/main/README.md"), ("blob", "psf", "requests", ["main", "README.md"]))
        self.assertEqual(p("https://github.com/psf/requests/tree/main/src"), ("tree", "psf", "requests", ["main", "src"]))
        self.assertEqual(p("https://raw.githubusercontent.com/psf/requests/main/setup.py"), ("blob", "psf", "requests", ["main", "setup.py"]))
        self.assertEqual(p("https://github.com/psf/requests/releases"), ("release", "psf", "requests", None))
        self.assertEqual(p("https://github.com/psf/requests/releases/tag/v2.0"), ("release", "psf", "requests", "v2.0"))
        self.assertEqual(p("https://github.com/torvalds"), ("user", "torvalds"))

    def test_not_github(self):
        for t in ("https://example.com/a/b", "https://github.com/features", "https://github.com/settings/profile", "just words", ""):
            self.assertIsNone(github.parse_target(t), t)


class FakeApi:
    """Replaces github._get with canned answers keyed by path."""
    def __init__(self, routes):
        self.routes, self.calls = routes, []

    def __call__(self, path, params=None, raw=False, timeout=12, max_bytes=2_500_000):
        self.calls.append(path)
        v = self.routes.get(path)
        if v is None:
            raise github.NotFound("Not found on GitHub (private repositories need a token that can see them).")
        if isinstance(v, Exception):
            raise v
        return v


REPO = {"full_name": "acme/widget", "html_url": "https://github.com/acme/widget", "description": "A tiny widget library", "language": "Python",
        "stargazers_count": 1234, "forks_count": 56, "open_issues_count": 7, "license": {"spdx_id": "MIT"}, "default_branch": "main",
        "topics": ["widgets", "python"], "created_at": "2020-01-02T00:00:00Z", "pushed_at": "2026-11-01T00:00:00Z"}


class LookupTests(unittest.TestCase):
    def setUp(self):
        self._get, self.token = github._get, github.TOKEN
        github._CACHE.clear()

    def tearDown(self):
        github._get, github.TOKEN = self._get, self.token
        github._CACHE.clear()

    def test_repo_overview(self):
        github._get = FakeApi({"/repos/acme/widget": REPO, "/repos/acme/widget/readme": "# widget\nInstall with pip.",
                               "/repos/acme/widget/contents": [{"name": "src", "type": "dir"}, {"name": "setup.py", "type": "file"}],
                               "/repos/acme/widget/releases/latest": {"tag_name": "v1.2.3", "published_at": "2026-10-01T00:00:00Z"}})
        out = github.lookup(targets=["https://github.com/acme/widget"])
        r = out["results"][0]
        self.assertEqual(r["domain"], "github.com")
        for s in ("Stars: 1,234", "License: MIT", "Latest release: v1.2.3", "Top-level files: src/, setup.py", "Install with pip."):
            self.assertIn(s, r["text"])
        self.assertEqual(out["read"], 1)

    def test_one_bad_target_does_not_sink_the_rest(self):
        github._get = FakeApi({"/repos/acme/widget": REPO})
        out = github.lookup(targets=["acme/widget", "acme/missing"])
        self.assertTrue(out["results"][0].get("text"))
        self.assertIn("Not found", out["results"][1]["read_error"])

    def test_everything_failing_raises(self):
        github._get = FakeApi({})
        with self.assertRaises(websearch.WebError):
            github.lookup(targets=["acme/missing"])

    def test_bad_names_never_reach_the_api(self):
        api = github._get = FakeApi({})
        with self.assertRaises(websearch.WebError):
            github.lookup(targets=["https://github.com/../etc"])
        self.assertEqual(api.calls, [])

    def test_nothing_to_look_up(self):
        with self.assertRaises(websearch.WebError):
            github.lookup()

    def test_results_are_cached(self):
        api = github._get = FakeApi({"/repos/acme/widget": REPO})
        github.lookup(targets=["acme/widget"])
        n = len(api.calls)
        github.lookup(targets=["acme/widget"])
        self.assertEqual(len(api.calls), n)

    def test_redirects_to_other_hosts_are_refused(self):
        h = github._SameHost()
        req = urllib.request.Request("https://api.github.com/x", headers={"Authorization": "Bearer secret"})
        with self.assertRaises(websearch.WebError):
            h.redirect_request(req, None, 302, "Found", {}, "https://evil.example/steal")


def gh_ctx(**over):
    seen = []

    def lookup(targets=None, query="", n=5, read=1, max_chars=6000):
        seen.append({"targets": targets, "query": query, "n": n, "read": read})
        if query and "nothing" in query:
            return {"results": [], "read": 0}
        if targets and "bad" in targets[0]:
            return {"results": [{"title": targets[0], "url": "https://github.com/" + targets[0], "snippet": "", "read_error": "Not found on GitHub."}], "read": 0}
        name = (targets or ["acme/found"])[0].split("github.com/")[-1]
        return {"results": [{"title": name, "url": "https://github.com/" + name, "domain": "github.com", "snippet": "A library (★1,234)", "text": "Repository: " + name + "\nStars: 1,234"}], "read": 1}
    c = make_ctx(gh=True, github=NS(lookup=lookup), **over)
    c.gh_seen = seen
    return c


class ToolTests(unittest.TestCase):
    def run_tool(self, ctx, **args):
        return tb.execute({"name": "github", "arguments": args}, ctx, tb.Run(ctx))

    def test_target_and_query(self):
        c = gh_ctx()
        r = self.run_tool(c, target="https://github.com/acme/widget")
        self.assertEqual(r["status"], "ok")
        self.assertIn("Stars: 1,234", r["text"])
        self.assertEqual(c.gh_seen[0]["targets"], ["https://github.com/acme/widget"])
        r = self.run_tool(gh_ctx(), query="pdf parsing")
        self.assertEqual(r["status"], "ok")

    def test_needs_target_or_query(self):
        r = self.run_tool(gh_ctx())
        self.assertEqual(r["status"], "error")

    def test_empty_and_failed(self):
        self.assertEqual(self.run_tool(gh_ctx(), query="nothing here")["status"], "empty")
        r = self.run_tool(gh_ctx(), target="acme/bad")
        self.assertEqual(r["status"], "error")
        self.assertIn("Not found", " ".join(r["notes"]))

    def test_off_means_unavailable(self):
        c = gh_ctx()
        c.gh = False
        r = self.run_tool(c, query="x")
        self.assertEqual(r["status"], "invalid")
        self.assertIn("GitHub", r["notes"][0])
        self.assertNotIn("github", tb.available(c))

    def test_sources_feed_citations(self):
        c = gh_ctx()
        run = tb.Run(c)
        r = tb.execute({"name": "github", "arguments": {"target": "acme/widget"}}, c, run)
        self.assertEqual(r["sources"], [1])
        self.assertEqual(run.sources[0]["domain"], "github.com")


class SelectTests(unittest.TestCase):
    def test_github_link_goes_to_the_api_not_the_page_reader(self):
        s = tb.select("What does https://github.com/acme/widget do?", gh_ctx())
        self.assertTrue(s["use"])
        self.assertEqual(s["tools"][0], "github")
        self.assertNotIn("read_url", [x["name"] for x in s["seeds"]])
        self.assertEqual(s["seeds"][0], {"name": "github", "arguments": {"target": "https://github.com/acme/widget"}})

    def test_other_links_still_use_read_url(self):
        s = tb.select("Summarize https://example.org/post", gh_ctx())
        self.assertEqual(s["seeds"][0]["name"], "read_url")

    def test_mixed_links(self):
        s = tb.select("Compare https://github.com/acme/widget with https://example.org/post", gh_ctx())
        names = [x["name"] for x in s["seeds"]]
        self.assertIn("github", names)
        self.assertIn("read_url", names)

    def test_browser_hint_seeds_a_search(self):
        s = tb.select("find a github repo for pdf parsing", gh_ctx(), {"gh": {"query": "pdf parsing"}, "q": "pdf parsing"})
        self.assertEqual(s["tools"][0], "github")
        self.assertIn({"name": "github", "arguments": {"query": "pdf parsing"}}, s["seeds"])

    def test_owner_repo_hint(self):
        s = tb.select("tell me about acme/widget on github", gh_ctx(), {"gh": {"targets": ["acme/widget"]}})
        self.assertEqual(s["seeds"][0], {"name": "github", "arguments": {"target": "acme/widget"}})

    def test_github_word_alone_offers_the_tool(self):
        self.assertIn("github", tb.select("is there a github action for caching?", gh_ctx())["tools"])

    def test_without_github_a_link_is_read_as_a_page(self):
        c = gh_ctx()
        c.gh = False
        s = tb.select("What does https://github.com/acme/widget do?", c)
        self.assertEqual(s["seeds"][0]["name"], "read_url")
        self.assertNotIn("github", s["tools"])

    def test_small_talk_is_untouched(self):
        self.assertFalse(tb.select("thanks!", gh_ctx())["use"])


def setUpModule():
    global CLIENT, server
    m = types.ModuleType("llama_cpp")
    m.Llama = object
    sys.modules.setdefault("llama_cpp", m)
    d = tempfile.mkdtemp()
    sys.argv = ["server.py", "--no-vision", "--no-image", "--no-reasoner", "--no-kb", "--data-dir", d, "--models-dir", d, "--root", d, "--osm-contact", "tests@example.org"]
    import server as server_mod
    server = server_mod
    from fastapi.testclient import TestClient
    CLIENT = TestClient(server.app, base_url="http://127.0.0.1")


class RouteTests(unittest.TestCase):
    def setUp(self):
        self._lookup, self._ping, self._tok, self._on = server.github.lookup, server.github.ping, server.github.TOKEN, server.GH_ON
        server.GH_PING.update(t=0.0)

    def tearDown(self):
        server.github.lookup, server.github.ping, server.github.TOKEN, server.GH_ON = self._lookup, self._ping, self._tok, self._on
        server.GH_PING.update(t=0.0)

    def test_post_needs_the_custom_header(self):
        self.assertEqual(CLIENT.post("/api/github/lookup", json={"query": "x"}).status_code, 403)

    def test_nothing_to_look_up(self):
        self.assertEqual(CLIENT.post("/api/github/lookup", json={}, headers=HDR).status_code, 400)

    def test_lookup(self):
        got = {}

        def fake(targets=None, query="", n=5, read=1, max_chars=6000):
            got.update(targets=targets, query=query, n=n, read=read)
            return {"results": [{"title": "acme/widget", "url": "https://github.com/acme/widget", "text": "t"}], "read": 1, "answer": ""}
        server.github.lookup = fake
        r = CLIENT.post("/api/github/lookup", json={"targets": ["acme/widget", 5, " "], "n": 99, "read": 9}, headers=HDR)
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual(r.json()["results"][0]["title"], "acme/widget")
        self.assertIn("seconds", r.json())
        self.assertEqual(got["targets"], ["acme/widget"])
        self.assertEqual((got["n"], got["read"]), (10, 3))                # clamped

    def test_lookup_errors_become_502(self):
        def boom(**kw):
            raise server.websearch.WebError("GitHub rate limit reached.")
        server.github.lookup = boom
        r = CLIENT.post("/api/github/lookup", json={"query": "x"}, headers=HDR)
        self.assertEqual(r.status_code, 502)
        self.assertIn("rate limit", r.json()["detail"])

    def test_turned_off(self):
        server.GH_ON = False
        self.assertEqual(CLIENT.post("/api/github/lookup", json={"query": "x"}, headers=HDR).status_code, 503)
        s = CLIENT.get("/api/github/status").json()
        self.assertFalse(s["enabled"])
        by = {x["name"]: x for x in CLIENT.get("/api/tools").json()["tools"]}
        self.assertFalse(by["github"]["available"])

    def test_status_never_returns_the_token(self):
        server.github.ping = lambda timeout=8: (True, "")
        server.github.TOKEN = "ghp_supersecret"
        r = CLIENT.get("/api/github/status?force=1")
        self.assertTrue(r.json()["ok"] and r.json()["token"] is True)
        self.assertNotIn("ghp_supersecret", r.text)

    def test_tool_selection_uses_the_hint(self):
        s = CLIENT.post("/api/tools/select", json={"text": "tell me about acme/widget on github", "hints": {"gh": {"targets": ["acme/widget", 3], "query": ""}}}, headers=HDR).json()
        self.assertTrue(s["use"])
        self.assertEqual(s["seeds"][0], {"name": "github", "arguments": {"target": "acme/widget"}})
        by = {x["name"]: x for x in CLIENT.get("/api/tools").json()["tools"]}
        self.assertTrue(by["github"]["available"])


if __name__ == "__main__":
    unittest.main()
