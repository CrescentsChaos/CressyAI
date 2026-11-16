"""Backend tests for memory.py v2 and embed.py. Run:  python tests/test_memory.py   (or pytest tests/)
No model and no network needed: a fake embedder stands in for the GGUF embedding model."""
import json, os, re, sys, tempfile, threading, time, math
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import memory as M
import embed as E

CLUSTERS = [
    {"database", "db", "postgres", "postgresql", "sql", "sqlite", "storage", "persist", "store", "stored"},
    {"test", "tests", "pytest", "unittest", "qa", "coverage"},
    {"deploy", "deployment", "deployments", "ship", "release", "host", "hosting"},
    {"short", "brief", "concise", "terse", "succinct"},
    {"login", "signin", "auth", "authentication", "password"},
]


class Fake:
    """Words in the same cluster point the same way, like a real embedding model would put synonyms close together."""
    def __init__(self, name="fake-embed-v1", on=True):
        self.name, self.on, self.why, self.docs, self.queries = name, on, "" if on else "model missing", 0, 0

    def available(self): return self.on
    def status(self): return {"on": self.on, "model": self.name, "loaded": self.on, "why": self.why}
    def reset(self): pass

    def _vec(self, text):
        v = [0.0] * (len(CLUSTERS) + 24)
        for w in re.findall(r"[a-z]+", text.lower()):
            hit = False
            for i, c in enumerate(CLUSTERS):
                if w in c:
                    v[i] += 1.0
                    hit = True
            if not hit:
                v[len(CLUSTERS) + hash_w(w) % 24] += 0.05
        return E.normalize(v)

    def embed_query(self, t): self.queries += 1; return self._vec(t)
    def embed_doc(self, t): self.docs += 1; return self._vec(t)


def hash_w(w):
    return sum(ord(c) * (i + 3) for i, c in enumerate(w))


def store(tmp=None, emb=None, project=None):
    tmp = tmp or tempfile.mkdtemp()
    proj = project or tmp
    return Store_(tmp, proj, emb), tmp


def Store_(tmp, proj, emb):
    return M.Store(Path(tmp) / "memory.json", proj, emb)


def texts(rows):
    return [r["text"] for r in rows]


# ---------------------------------------------------------------- semantic retrieval
def test_semantic_finds_what_keywords_miss():
    facts = ["We store all application data in PostgreSQL.", "Deployments go to a single Windows machine.",
             "The login page shows a vague error on bad passwords.", "Keep replies short."]
    lex, _ = store()
    sem, _ = store(emb=Fake())
    for s in (lex, sem):
        for f in facts:
            s.add_fact(f, "project", pin=False)
    q = "which database do we use?"
    assert not any("PostgreSQL" in t for t in texts(lex.recall(q)["matches"])), "keyword search should miss this"
    got = texts(sem.recall(q)["matches"])
    assert got and "PostgreSQL" in got[0], got
    assert all("Windows" not in t for t in got), got            # unrelated notes stay out
    assert sem.recall(q)["semantic"] is True and lex.recall(q)["semantic"] is False


def test_falls_back_to_keywords_when_the_model_is_unavailable():
    s, _ = store(emb=Fake(on=False))
    s.add_fact("This repo uses FastAPI and the tests live in tests/api.", "project", pin=False)
    assert any("FastAPI" in t for t in texts(s.recall("where are the fastapi tests")["matches"]))
    st = s.semantic_status()
    assert st["on"] is False and st["why"] == "model missing"


def test_vectors_persist_and_follow_the_model():
    tmp = tempfile.mkdtemp()
    e1 = Fake()
    a, _ = store(tmp, e1)
    a.add_fact("We store all application data in PostgreSQL.", "project", pin=False)
    a.add_fact("Deployments go to a single Windows machine.", "project", pin=False)
    a.warm()
    assert (Path(tmp) / "memory.vectors.json").exists()
    e2 = Fake()
    b, _ = store(tmp, e2)                                       # same model name: vectors are loaded, not recomputed
    assert b.semantic_status()["indexed"] == 2
    b.recall("database?")
    assert e2.docs == 0 and e2.queries == 1, (e2.docs, e2.queries)
    c, _ = store(tmp, Fake(name="another-model"))               # different model: old vectors are useless and dropped
    assert c.semantic_status()["indexed"] == 0


def test_warm_indexes_notes_written_before_the_model_existed():
    tmp = tempfile.mkdtemp()
    plain, _ = store(tmp)
    plain.add_fact("We store all application data in PostgreSQL.", "project", pin=False)
    s, _ = store(tmp, Fake())
    assert s.semantic_status()["indexed"] == 0
    assert s.warm() == 1 and s.semantic_status()["indexed"] == 1
    assert s.reindex()["indexed_now"] == 1


def test_semantic_duplicate_is_refused():
    s, _ = store(emb=Fake())
    s.add_fact("Our database is Postgres.", "project")
    try:
        s.add_fact("We persist data in PostgreSQL storage.", "project")
    except M.MemoryError as e:
        assert "similar" in str(e)
    else:
        raise AssertionError("a paraphrase of a saved note should be refused")
    s.add_fact("Tests run with pytest and coverage.", "project")    # a different topic is fine


# ---------------------------------------------------------------- project scoping
def test_projects_are_separate_and_can_be_adopted():
    tmp, other = tempfile.mkdtemp(), tempfile.mkdtemp()
    a, _ = store(tmp, project=other)
    a.add_fact("The other project is a Rust CLI called fern.", "project")
    a.add_task("Ship the fern release notes", "c1")
    a.add_fact("The user likes tabs over spaces.", "global")
    b, _ = store(tmp, project=tmp)
    snap = b.snapshot()
    assert texts(snap["facts"]) == ["The user likes tabs over spaces."] and snap["tasks"] == []
    plist = {p["name"]: p for p in b.projects()["projects"]}
    assert plist[Path(other).name]["facts"] == 1 and plist[Path(other).name]["tasks"] == 1
    assert plist[Path(tmp).name]["current"] is True
    moved = b.adopt(other)
    assert moved["moved"] == 2 and any("fern" in t for t in texts(moved["facts"])) and len(moved["tasks"]) == 1
    for bad in (tmp, "/no/such/project"):
        try:
            b.adopt(bad)
        except M.MemoryError:
            pass
        else:
            raise AssertionError("adopting yourself or an unknown project must fail")


def test_scope_can_be_changed_and_facts_are_project_checked():
    tmp, other = tempfile.mkdtemp(), tempfile.mkdtemp()
    a, _ = store(tmp, project=tmp)
    fid = a.add_fact("We use tabs here, never spaces.", "project")["fact"]["id"]
    assert a.set_fact(fid, scope="global")["facts"][0]["scope"] == "global"
    b, _ = store(tmp, project=other)
    assert texts(b.snapshot()["facts"]) == ["We use tabs here, never spaces."]
    a.set_fact(fid, scope="project")
    b, _ = store(tmp, project=other)
    assert b.snapshot()["facts"] == []
    try:
        b.set_fact(fid, pin=True)
    except M.MemoryError:
        pass
    else:
        raise AssertionError("another project's note must not be editable")


def test_profile_comes_from_the_project_folder():
    d = Path(tempfile.mkdtemp()) / "cressy"
    d.mkdir()
    (d / "requirements.txt").write_text("fastapi\nuvicorn\nllama-cpp-python\n")
    (d / "README.md").write_text("# CressyAI\n\nA private chat UI for a local GGUF model.\n")
    (d / "package.json").write_text(json.dumps({"dependencies": {"react": "^18"}, "devDependencies": {"typescript": "5"}}))
    p = M.detect_profile(d)
    assert p.startswith("cressy (") and "Python" in p and "FastAPI" in p and "TypeScript" in p and "React" in p, p
    assert "private chat UI" in p and str(d.parent) not in p          # no absolute path leaks into the prompt
    assert M.detect_profile(Path(tempfile.mkdtemp())).count("(") == 0


# ---------------------------------------------------------------- sessions and task continuity
def test_session_handoff_and_resume():
    s, _ = store(emb=Fake())
    s.upsert_session("c1", title="Fix the login error copy", handoff="Rewrote the error text; tests still missing.",
                     next=["Add a test for the empty password case"], turns=6, quality="llm")
    s.upsert_session("c2", title="Database migration", handoff="Chose PostgreSQL over SQLite.", quality="llm")
    r = s.recall("hello there", fresh=True, chat="c3")
    assert r["last_session"]["chat"] == "c2" or r["last_session"]["chat"] == "c1"
    assert r["last_session"] is not None
    assert s.recall("hello there", fresh=False, chat="c3")["last_session"] is None
    r = s.recall("why did we pick that database?", chat="c3", fresh=False)
    assert [x["chat"] for x in r["sessions"]][:1] == ["c2"], r["sessions"]
    r = s.recall("anything", fresh=True, chat="c2")             # the open chat is never offered back to itself
    assert r["last_session"]["chat"] == "c1"


def test_auto_notes_never_overwrite_model_written_ones_but_summary_updates():
    s, _ = store()
    s.upsert_session("c1", title="T", handoff="Model-written hand-off.", next=["do X for real"], quality="llm")
    out = s.upsert_session("c1", summary="Rolling summary v2", handoff="Quick fallback", turns=9, quality="auto")
    assert out["handoff"] == "Model-written hand-off." and out["next"] == ["do X for real"]
    assert out["summary"] == "Rolling summary v2" and out["turns"] == 9 and out["quality"] == "llm"
    out = s.absorb(handoff={"summary": "A fresh model hand-off.", "next": ["one", "two"]}, chat="c1", title="T")
    assert out["sessions"][0]["handoff"] == "A fresh model hand-off." and out["sessions"][0]["summary"] == "Rolling summary v2"


def test_deleting_a_chat_forgets_its_session_and_unlinks_tasks():
    s, _ = store()
    s.upsert_session("c1", handoff="Something about the project.", quality="llm")
    s.add_task("Write the migration guide", "c1")
    s.forget_chat("c1")
    snap = s.snapshot()
    assert snap["sessions"] == [] and snap["tasks"][0]["chat"] == ""


def test_task_progress_done_by_id_and_stale_flag():
    s, tmp = store()
    t1 = s.add_task("Finish the login error copy", "c1")["task"]["id"]
    t2 = s.add_task("Wire memory retrieval into chat", "c1")["task"]["id"]
    out = s.absorb(progress=[{"id": t1, "note": "Copy written, tests missing"}], done=[t2], chat="c2")
    by = {t["id"]: t for t in out["tasks"]}
    assert by[t1]["progress"] == "Copy written, tests missing" and by[t2]["status"] == "done" and out["closed"] == 1
    r = s.recall("continue the login work")
    assert r["tasks"][0]["id"] == t1 and "tests missing" in r["tasks"][0]["progress"]
    assert t2 not in [t["id"] for t in r["tasks"]]
    s.data["tasks"][0]["updated"] -= 40 * 86400000
    assert s.snapshot()["tasks"][0]["stale"] is True
    # a fragment closes a task only when it names exactly one
    s.add_task("Write docs for the login flow", "c1")
    assert s.absorb(done=["login"])["closed"] == 0
    assert s.absorb(done=["docs for the login"])["closed"] == 1


def test_a_finished_task_can_come_back():
    s, _ = store()
    s.add_task("Fix the flaky upload test", "c1")
    s.complete_matching("flaky upload")
    assert s.absorb(tasks=["Fix the flaky upload test"])["added_tasks"] == 1


# ---------------------------------------------------------------- learning from chats
def test_secrets_are_never_saved_from_chat_but_you_can_save_what_you_type():
    s, _ = store()
    bad = ["The API key is sk-abcdefghijklmnopqrstu123.", "password: hunter2hunter2 for the admin panel",
           "Deploy token = ghp_abcdefghijklmnopqrstuvwxyz0123456789"]
    out = s.absorb(facts=bad, tasks=["Rotate the secret: abcdef123456 soon please"])
    assert out["added_facts"] == 0 and out["added_tasks"] == 0 and out["skipped"] == 4
    s.add_fact("The staging token is rotated every Monday morning.", "project")   # talking about tokens is fine
    assert out["facts"] == []


def test_replace_only_touches_notes_written_by_the_chat():
    s, _ = store()
    chat_id = s.absorb(facts=["The project stores data in SQLite on disk."])["facts"][0]["id"]
    out = s.absorb(facts=[{"text": "The project now stores data in PostgreSQL.", "replaces": chat_id}])
    assert out["replaced"] == 1 and texts(out["facts"]) == ["The project now stores data in PostgreSQL."]
    mine = s.add_fact("I deploy everything to one Windows box.", "project")["fact"]["id"]
    out = s.absorb(facts=[{"text": "Deployments now go to a Linux server.", "replaces": mine}])
    assert out["replaced"] == 0 and "I deploy everything to one Windows box." in texts(out["facts"])


def test_eviction_drops_unused_unpinned_notes_first():
    old = M.MAX_FACTS
    M.MAX_FACTS = 4
    try:
        s, _ = store()
        keep = s.add_fact("My name is Rowan and I write the server.", "global")["fact"]["id"]   # pinned by the name pattern
        s.add_fact("Fact number two about the build system.", "project", pin=False)
        s.add_fact("Fact number three about the test layout.", "project", pin=False)
        s.add_fact("Fact number four about the deploy script.", "project", pin=False)
        s.recall("build system")                                  # note two is used, so it should outlive the others
        s.add_fact("Fact number five about the logging setup.", "project", pin=False)
        left = texts(s.snapshot()["facts"])
        assert len(left) == 4 and "My name is Rowan and I write the server." in left
        assert "Fact number two about the build system." in left, left
        for f in s.data["facts"]:
            f["pin"] = True
        try:
            s.add_fact("Fact number six about something else entirely.", "project", pin=False)
        except M.MemoryError as e:
            assert "pinned" in str(e)
        else:
            raise AssertionError("a memory full of pinned notes must refuse more")
    finally:
        M.MAX_FACTS = old


# ---------------------------------------------------------------- export / import / compatibility
def test_export_import_round_trip():
    a, _ = store()
    a.add_fact("We use tabs here, never spaces.", "project")
    a.add_fact("My name is Rowan and I like short answers.", "global")
    a.add_task("Add the resume banner", "c1")
    a.upsert_session("c1", title="Banner", handoff="Designed the banner.", next=["wire it up"], quality="llm")
    blob = json.dumps(a.export())
    assert "chat" not in blob and str(a.project) not in blob and '"id"' not in blob
    other, _ = store()
    out = other.import_data(json.loads(blob))
    assert (out["added_facts"], out["added_tasks"], out["added_sessions"]) == (2, 1, 1), out
    again = other.import_data(json.loads(blob))                  # importing twice adds no duplicates
    assert again["added_facts"] == 0 and again["added_tasks"] == 0
    for bad in ({}, {"app": "something-else"}, [], "x"):
        try:
            other.import_data(bad)
        except M.MemoryError:
            pass
        else:
            raise AssertionError("foreign files must be rejected")


def test_old_memory_files_still_load():
    tmp = tempfile.mkdtemp()
    old = {"facts": [{"id": "a" * 12, "text": "The user prefers short answers.", "scope": "global", "pin": True, "source": "user", "created": 1}],
           "tasks": [{"id": "b" * 12, "text": "Finish the login error copy", "status": "open", "source": "user", "chat": "", "project": "", "created": 1, "updated": 1}]}
    (Path(tmp) / "memory.json").write_text(json.dumps(old))
    s = M.Store(Path(tmp) / "memory.json", tmp)
    snap = s.snapshot()
    assert len(snap["facts"]) == 1 and len(snap["tasks"]) == 1 and snap["sessions"] == []
    assert s.set_task("b" * 12, "done")["tasks"][0]["status"] == "done"      # the old positional call still works
    (Path(tmp) / "memory.json").write_text("{not json")
    assert M.Store(Path(tmp) / "memory.json", tmp).snapshot()["facts"] == []   # a damaged file never crashes start-up


def test_search_for_forget():
    s, _ = store(emb=Fake())
    s.add_fact("We store all application data in PostgreSQL.", "project", pin=True)
    s.add_fact("Tests run with pytest.", "project")
    hit = s.search("database")
    assert texts(hit["facts"])[:1] == ["We store all application data in PostgreSQL."]
    assert s.search("   ") == {"facts": [], "tasks": []}


def test_recall_counts_use_and_survives_threads():
    s, _ = store()
    s.add_fact("Tests run with pytest and live in tests/api.", "project", pin=False)
    s.recall("where do the pytest tests live")
    assert s.snapshot()["facts"][0]["hits"] == 1
    errs = []

    def work(i):
        try:
            for j in range(15):
                try:
                    s.add_fact(f"Thread {i} note number {j} about unique topic {i}{j}x.", "project", pin=False)
                except M.MemoryError:
                    pass
                s.recall("topic")
        except Exception as e:
            errs.append(e)
    ts = [threading.Thread(target=work, args=(i,)) for i in range(4)]
    [t.start() for t in ts]
    [t.join() for t in ts]
    assert not errs, errs
    assert json.loads(s.path.read_text())["version"] == 2


def test_embedder_unload_after_idle_and_failure_handling():
    class FakeLlm:
        closed = False
        def embed(self, t): return [0.5, 0.5, 0.5, 0.5]
        def close(self): FakeLlm.closed = True
    e = E.Embedder("/m/nomic-embed-text-v1.5.Q8_0.gguf", idle=1, loader=lambda p, t: FakeLlm())
    assert e.embed_doc("hello") is not None and e.loaded()
    time.sleep(2.6)
    assert not e.loaded() and FakeLlm.closed                    # freed after the idle timeout
    assert e.embed_query("again") is not None and e.loaded()    # and it comes back on demand
    e.unload()

    class Bad:
        def embed(self, t): raise RuntimeError("boom")
    b = E.Embedder("/m/x-embed.gguf", idle=0, loader=lambda p, t: Bad())
    for _ in range(3):
        assert b.embed_doc("x") is None
    assert not b.available() and "keeps failing" in b.why
    b.reset()
    assert b.available()


if __name__ == "__main__":
    fns = [(n, f) for n, f in sorted(globals().items()) if n.startswith("test_") and callable(f)]
    bad = 0
    for n, f in fns:
        try:
            f()
            print("ok   ", n)
        except Exception as e:
            bad += 1
            import traceback
            print("FAIL ", n, "->", repr(e))
            traceback.print_exc()
    print(f"\n{len(fns) - bad}/{len(fns)} passed")
    sys.exit(1 if bad else 0)
