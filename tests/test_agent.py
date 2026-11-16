"""Tests for agent mode (agenttools.py + agent.py). No model, no network: the model is a script.
Run: python -m unittest tests.test_agent   (or python tests/test_agent.py)"""
import os, shutil, sys, tempfile, threading, unittest
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import agent as A
import agenttools as T

F = "`" * 3


def _strict(*a, **kw):
    """These tests cover the full verify / rollback / retry machinery, which is the 'strict' mode (the default mode is 'fast')."""
    kw.setdefault("mode", "strict")
    return A.AgentRun(*a, **kw)


PY = f'"{sys.executable}"' if os.name == "nt" else sys.executable


def blk(info, body=""):
    return f"{F}{info}\n{body}\n{F}\n"


def make_project(files):
    d = tempfile.mkdtemp(prefix="cressy-agent-")
    for rel, text in files.items():
        p = os.path.join(d, rel)
        os.makedirs(os.path.dirname(p), exist_ok=True)
        with open(p, "w", encoding="utf-8", newline="") as f:
            f.write(text)
    return d


BUGGY = {
    "calc.py": "def add(a, b):\n    return a - b\n",
    "tests/__init__.py": "",
    "tests/test_calc.py": ("import unittest\nfrom calc import add\n\nclass T(unittest.TestCase):\n"
                           "    def test_add(self):\n        self.assertEqual(add(2, 3), 5)\n"
                           "    def test_zero(self):\n        self.assertEqual(add(0, 0), 0)\n"),
}
FIX = "<<<<<<< SEARCH\n    return a - b\n=======\n    return a + b\n>>>>>>> REPLACE"
WRONG = "<<<<<<< SEARCH\n    return a - b\n=======\n    return a * b\n>>>>>>> REPLACE"


class Script:
    """A fake model: answers with the given replies in order and remembers what it was shown."""
    def __init__(self, replies):
        self.replies, self.seen, self.i = list(replies), [], 0

    def __call__(self, messages, params):
        self.seen.append([dict(m) for m in messages])
        if self.i >= len(self.replies):
            raise AssertionError("the agent asked for more replies than the script has (%d)" % len(self.replies))
        text = self.replies[self.i]
        self.i += 1
        for k in range(0, len(text), 40):
            yield {"text": text[k:k + 40]}
        yield {"finish": "stop"}


class Approver:
    def __init__(self, answers):
        self.answers, self.asked = list(answers), []

    def wait_approval(self, aid, cancel):
        self.asked.append(aid)
        return self.answers.pop(0) if self.answers else False


class Base(unittest.TestCase):
    def setUp(self):
        self.dirs = []

    def tearDown(self):
        for d in self.dirs:
            shutil.rmtree(d, ignore_errors=True)

    def project(self, files=None):
        d = make_project(files if files is not None else BUGGY)
        self.dirs.append(d)
        return d

    def run_agent(self, root, replies, **kw):
        model = Script(replies)
        sb = T.Sandbox(root)
        kw.setdefault("max_steps", 12)
        run = _strict(kw.pop("goal", "fix add"), sb, model, **kw)
        evs = list(run.events())
        return run, evs, model

    def final(self, evs):
        done = [e for e in evs if e["type"] == "done"]
        self.assertEqual(len(done), 1, "exactly one done event; got " + str([e["type"] for e in evs][-6:]))
        return done[0]


class ParseTests(unittest.TestCase):
    def test_actions(self):
        r = T.parse_actions("Looking.\n" + blk("read:src/a.py", "10-20") + blk("edit:src/a.py", "<<<<<<< SEARCH\nx\n=======\ny\n>>>>>>> REPLACE")
                            + blk("bash", "pytest -q") + blk("python", "print(1)") + blk("done", "all good"))
        self.assertEqual([(a["kind"], a["path"]) for a in r["actions"]],
                         [("read", "src/a.py"), ("edit", "src/a.py"), ("run", None), ("done", None)])
        self.assertEqual(r["code_blocks"], 1)
        self.assertFalse(r["cut"])
        self.assertEqual(r["prose"], "Looking.")

    def test_lang_colon_path_is_a_file(self):
        self.assertEqual(T.classify_info("python:app.py"), ("file", "app.py"))
        self.assertEqual(T.classify_info("file: ./src/x.js"), ("file", "src/x.js"))

    def test_cut_off_block_is_not_closed(self):
        r = T.parse_actions("ok\n" + F + "file:a.py\nprint(1)\n")
        self.assertTrue(r["cut"])
        self.assertFalse(r["actions"][0]["closed"])

    def test_four_backtick_fence_keeps_inner_fences(self):
        r = T.parse_actions("````file:README.md\n# t\n" + F + "bash\nls\n" + F + "\n````\n")
        self.assertEqual(len(r["actions"]), 1)
        self.assertIn(F + "bash", r["actions"][0]["body"])
        self.assertTrue(r["actions"][0]["closed"])

    def test_stop_after(self):
        self.assertFalse(T.stop_after(T.parse_actions(blk("read:a.py"))["actions"]))
        self.assertTrue(T.stop_after(T.parse_actions(blk("bash", "ls"))["actions"]))
        self.assertFalse(T.stop_after(T.parse_actions(F + "bash\nls\n")["actions"]))

    def test_plan(self):
        p = T.parse_plan("- [ ] read\n- [x] write\n3. run tests")
        self.assertEqual([(x["text"], x["done"]) for x in p], [("read", False), ("write", True), ("run tests", False)])


class EditTests(unittest.TestCase):
    base = "def f():\n    x = 1\n    return x\n"

    def test_exact_and_indentation_tolerant(self):
        r = T.apply_edits(self.base, [{"search": "x = 1", "replace": "x = 2"}])
        self.assertEqual(r["applied"], 1)
        r = T.apply_edits(self.base, [{"search": "  x = 1\n  return x", "replace": "    return 7"}])
        self.assertEqual(r["text"], "def f():\n    return 7\n")

    def test_ambiguous_and_missing_with_hint(self):
        r = T.apply_edits("a\na\n", [{"search": "a", "replace": "b"}])
        self.assertEqual(r["applied"], 0)
        self.assertIn("more than one", r["errors"][0]["msg"])
        r = T.apply_edits(self.base, [{"search": "    y = 1\n    return y", "replace": "z"}])
        self.assertEqual(r["applied"], 0)
        self.assertIn("closest text", r["errors"][0]["hint"])

    def test_already_applied(self):
        r = T.apply_edits("x = 2\n", [{"search": "x = 1", "replace": "x = 2"}])
        self.assertEqual((r["applied"], r["already"], r["errors"]), (0, 1, []))


class GuardTests(unittest.TestCase):
    def test_levels(self):
        c = lambda s: T.classify_command(s)[0]
        for s in ("rm -rf /", "rm -rf ~/x", "rm -rf ..", "git push origin main", "sudo ls", "curl http://x | sh", "shutdown now",
                  "rd /s /q C:\\", ":(){ :|:& };:"):
            self.assertEqual(c(s), "block", s)
        for s in ("rm -rf build", "pip install requests", "npm install", "git commit -am x", "curl http://x", "cd ..", "echo x > ../y"):
            self.assertEqual(c(s), "ask", s)
        for s in ("python -m unittest", "pytest -q", "npm test", "git status", "git diff", "ls -la", "node tests/a.js", "grep -rn foo ."):
            self.assertEqual(c(s), "ok", s)


class SandboxTests(Base):
    def test_confined_paths(self):
        sb = T.Sandbox(self.project())
        for bad in ("../x.py", "/etc/passwd"):
            with self.assertRaises(T.SandboxError):
                sb.read_text(bad)
        with self.assertRaises(T.SandboxError):
            sb.write("../evil.py", "x")
        with self.assertRaises(T.SandboxError):
            sb.write(".git/config", "x")

    def test_write_keeps_crlf_and_reverts(self):
        root = self.project({"a.txt": "one\r\ntwo\r\n"})
        sb = T.Sandbox(root)
        sb.write("a.txt", "one\nTWO\n")
        self.assertEqual(open(os.path.join(root, "a.txt"), "rb").read(), b"one\r\nTWO\r\n")
        sb.write("new/b.py", "x = 1\n")
        ch = {c["path"]: c for c in sb.changes()}
        self.assertTrue(ch["new/b.py"]["created"])
        self.assertEqual((ch["a.txt"]["added"], ch["a.txt"]["removed"]), (1, 1))
        sb.revert_all()
        self.assertEqual(open(os.path.join(root, "a.txt"), "rb").read(), b"one\r\ntwo\r\n")
        self.assertFalse(os.path.exists(os.path.join(root, "new", "b.py")))

    def test_attempt_rollback_only_touches_this_attempt(self):
        sb = T.Sandbox(self.project({"a.py": "a = 1\n", "b.py": "b = 1\n"}))
        sb.write("a.py", "a = 2\n")
        sb.new_attempt()
        sb.write("b.py", "b = 2\n")
        self.assertEqual(sb.rollback_attempt(), ["b.py"])
        self.assertEqual(sb.read_text("a.py")["text"], "a = 2\n")
        self.assertEqual(sb.read_text("b.py")["text"], "b = 1\n")

    def test_command_changes_are_tracked_and_cancel_works(self):
        root = self.project({"a.txt": "hello\n"})
        sb = T.Sandbox(root)
        res = sb.run(f"{PY} -c \"open('a.txt','w').write('changed\\n'); open('n.txt','w').write('new')\"")
        self.assertEqual(res["code"], 0)
        self.assertEqual(sorted(res["changed"]), ["a.txt", "n.txt"])
        sb.revert_all()
        self.assertEqual(sb.read_text("a.txt")["text"], "hello\n")
        self.assertFalse(os.path.exists(os.path.join(root, "n.txt")))
        ev = threading.Event()
        threading.Timer(0.6, ev.set).start()
        res = sb.run(f"{PY} -c \"import time; time.sleep(30)\"", timeout=30, cancel=ev)
        self.assertTrue(res["killed"])
        res = sb.run(f"{PY} -c \"import time; time.sleep(30)\"", timeout=1)
        self.assertTrue(res["timeout"])

    def test_grep_and_tree_skip_junk(self):
        root = self.project({"src/a.py": "def hello(): pass\n", "node_modules/x/i.js": "hello\n"})
        sb = T.Sandbox(root)
        self.assertEqual(sb.grep("hello"), ["src/a.py:1: def hello(): pass"])
        self.assertNotIn("node_modules/", "\n".join(sb.tree()))


class DiagnoseTests(Base):
    def test_syntax_checks(self):
        sb = T.Sandbox(self.project())
        self.assertIn("line 1", T.syntax_check(sb, "a.py", "def f(:\n  pass\n"))
        self.assertIsNone(T.syntax_check(sb, "a.py", "def f():\n    pass\n"))
        self.assertIn("invalid JSON", T.syntax_check(sb, "a.json", '{"a":1,}'))

    def test_unittest_failure_points_at_code_under_test(self):
        root = self.project()
        sb = T.Sandbox(root)
        det = T.detect_tests(root)
        self.assertEqual(det[0]["name"], "unittest")
        res = sb.run(det[0]["cmd"])
        tr = T.parse_test_result(res["out"], res["code"])
        self.assertEqual((tr["framework"], tr["ran"], tr["passed"], tr["failed"], tr["ok"]), ("unittest", 2, 1, 1, False))
        d = T.diagnose(res["out"], sb)
        self.assertEqual(d["locations"][0]["path"], "tests/test_calc.py")
        self.assertEqual(d["related"], ["calc.py"])
        self.assertTrue(d["signature"])
        self.assertIn(">", T.context_snippets(sb, d["locations"]))

    def test_signature_ignores_numbers(self):
        a = T.diagnose("Traceback (most recent call last):\n  File \"x.py\", line 3, in f\nValueError: bad value 3\n")
        b = T.diagnose("Traceback (most recent call last):\n  File \"x.py\", line 9, in f\nValueError: bad value 77\n")
        self.assertEqual(a["signature"], b["signature"])

    def test_other_frameworks(self):
        r = T.parse_test_result("=== 2 failed, 3 passed in 0.1s ===\nFAILED tests/t.py::test_x - assert 1 == 2\n", 1)
        self.assertEqual((r["framework"], r["passed"], r["failed"], r["failing"]), ("pytest", 3, 2, ["tests/t.py::test_x"]))
        r = T.parse_test_result("Tests:       1 failed, 4 passed, 5 total\n", 1)
        self.assertEqual((r["framework"], r["passed"], r["failed"]), ("jest", 4, 1))
        r = T.parse_test_result("# tests 3\n# pass 3\n# fail 0\n", 0)
        self.assertTrue(r["ok"])
        r = T.parse_test_result("Ran 0 tests in 0.000s\n\nOK\n", 0)
        self.assertFalse(r["ok"])

    def test_detect_config_override(self):
        root = self.project({"cressy-agent.json": '{"test": ["make check", "make lint"]}'})
        self.assertEqual([t["cmd"] for t in T.detect_tests(root)], ["make check", "make lint"])

    def test_compact_keeps_task_and_alternation(self):
        msgs = [{"role": "system", "content": "s"}, {"role": "user", "content": "task"}]
        for i in range(10):
            msgs += [{"role": "assistant", "content": f"{F}file:a.py\n" + "x\n" * 200 + F}, {"role": "user", "content": "obs " * 400}]
        out, dropped = T.compact(msgs, 900, lambda t: len(t) // 4)
        self.assertEqual(out[1]["content"], "task")
        self.assertEqual(out[-1], msgs[-1] if len(out[-1]["content"]) <= len(msgs[-1]["content"]) else out[-1])
        self.assertGreater(dropped, 0)
        roles = [m["role"] for m in out]
        self.assertTrue(all(roles[i] != roles[i + 1] for i in range(len(roles) - 1)), roles)


class LoopTests(Base):
    def test_happy_path_fixes_and_verifies(self):
        root = self.project()
        run, evs, model = self.run_agent(root, [
            "Let me look.\n" + blk("read:calc.py"),
            "Subtraction instead of addition.\n" + blk("edit:calc.py", FIX) + blk("test"),
            "Fixed.\n" + blk("done", "Changed add to return a + b; tests pass."),
        ])
        fin = self.final(evs)
        self.assertEqual(fin["status"], "success")
        self.assertEqual(fin["verification"]["kind"], "tests")
        self.assertEqual([f["path"] for f in fin["files"]], ["calc.py"])
        self.assertIn("return a + b", open(os.path.join(root, "calc.py")).read())
        types = [e["type"] for e in evs]
        self.assertEqual(types[0], "start")
        self.assertIn("file_change", types)
        self.assertIn("verify", types)
        base = [e for e in evs if e["type"] == "verify" and e.get("baseline") and e["phase"] == "done"][0]
        self.assertFalse(base["ok"])        # the tests failed before the agent touched anything
        self.assertIn("FAIL before any change", model.seen[0][1]["content"])
        self.assertIn("calc.py (+1 -1)", model.seen[2][-1]["content"])

    def test_wrong_fix_is_detected_warned_and_repaired(self):
        root = self.project()
        run, evs, model = self.run_agent(root, [
            blk("edit:calc.py", WRONG) + blk("test"),
            blk("edit:calc.py", "<<<<<<< SEARCH\n    return a * b\n=======\n    return a * b + 0\n>>>>>>> REPLACE") + blk("test"),
            blk("edit:calc.py", "<<<<<<< SEARCH\n    return a * b + 0\n=======\n    return a + b\n>>>>>>> REPLACE") + blk("test"),
            blk("done", "fixed"),
        ])
        self.assertEqual(self.final(evs)["status"], "success")
        second = model.seen[2][-1]["content"]
        self.assertIn("SAME failure", second)           # warned on the repeat
        self.assertIn("Diagnosis", model.seen[1][-1]["content"])

    def test_stuck_rolls_back_and_retries_then_succeeds(self):
        root = self.project()
        ed = lambda old, new: blk("edit:calc.py", f"<<<<<<< SEARCH\n    return {old}\n=======\n    return {new}\n>>>>>>> REPLACE") + blk("test")
        run, evs, model = self.run_agent(root, [
            ed("a - b", "a * b"),                  # fails: 6 != 5
            ed("a * b", "a * b + 0"),              # same failure again -> warning
            ed("a * b + 0", "a * b + 0 * a"),      # third time -> rollback to the original
            ed("a - b", "a + b"),                  # new attempt works on the restored file
            blk("done", "ok"),
        ], max_steps=10)
        fin = self.final(evs)
        self.assertEqual(fin["status"], "success")
        self.assertEqual(fin["attempts"], 2)
        rb = [e for e in evs if e["type"] == "rollback"]
        self.assertEqual([e["files"] for e in rb], [["calc.py"]])
        self.assertIn("SAME failure", model.seen[2][-1]["content"])
        self.assertIn("ROLLED BACK", model.seen[3][-1]["content"])
        self.assertIn("What was tried and failed", model.seen[3][-1]["content"])

    def test_gives_up_after_all_attempts_and_can_revert(self):
        root = self.project()
        ed = lambda old, new: blk("edit:calc.py", f"<<<<<<< SEARCH\n    return {old}\n=======\n    return {new}\n>>>>>>> REPLACE") + blk("test")
        chain = ["a * b", "a * b + 0", "a * b + 0 * a"]
        replies = [ed("a - b", chain[0]), ed(chain[0], chain[1]), ed(chain[1], chain[2]),     # attempt 1 -> rolled back
                   ed("a - b", chain[0]), ed(chain[0], chain[1]), ed(chain[1], chain[2])]     # attempt 2 -> give up
        run, evs, model = self.run_agent(root, replies, max_attempts=2, max_steps=10)
        fin = self.final(evs)
        self.assertEqual(fin["status"], "failed")
        self.assertTrue(fin["can_revert"])
        self.assertIn("Gave up", fin["summary"])
        self.assertEqual(len([e for e in evs if e["type"] == "rollback"]), 1)
        run.sb.revert_all()
        self.assertIn("return a - b", open(os.path.join(root, "calc.py")).read())

    def test_syntax_error_is_caught_immediately_and_blocks_done(self):
        root = self.project()
        run, evs, model = self.run_agent(root, [
            blk("file:calc.py", "def add(a, b)\n    return a + b"),
            blk("done", "finished"),
            blk("file:calc.py", "def add(a, b):\n    return a + b"),
            blk("done", "finished"),
        ])
        fin = self.final(evs)
        self.assertEqual(fin["status"], "success")
        self.assertIn("SYNTAX ERROR", model.seen[1][-1]["content"])
        self.assertIn("syntax errors", model.seen[2][-1]["content"])
        self.assertTrue(any(e["type"] == "syntax" and not e["ok"] for e in evs))

    def test_done_without_running_tests_is_verified_by_the_system(self):
        root = self.project()
        run, evs, model = self.run_agent(root, [
            blk("edit:calc.py", WRONG),
            blk("done", "I fixed it, trust me."),
            blk("edit:calc.py", "<<<<<<< SEARCH\n    return a * b\n=======\n    return a + b\n>>>>>>> REPLACE"),
            blk("done", "now really fixed"),
        ])
        fin = self.final(evs)
        self.assertEqual(fin["status"], "success")
        self.assertIn("verification FAILED", model.seen[2][-1]["content"])
        self.assertEqual([e["ok"] for e in evs if e["type"] == "verify" and e.get("final") and e["phase"] == "done"], [False, True])

    def test_edit_mismatch_gets_a_hint(self):
        root = self.project()
        run, evs, model = self.run_agent(root, [
            blk("edit:calc.py", "<<<<<<< SEARCH\n    return a -b\n=======\n    return a + b\n>>>>>>> REPLACE"),
            blk("edit:calc.py", FIX) + blk("test"),
            blk("done", "ok"),
        ])
        self.assertIn("closest text", model.seen[1][-1]["content"])
        self.assertEqual(self.final(evs)["status"], "success")

    def test_blocked_and_approved_commands(self):
        root = self.project({"a.py": "x = 1\n"})
        ap = Approver([True, False])
        model = Script([blk("bash", "git push origin main"), blk("bash", "pip --version && pip install --help"),
                        blk("bash", "pip install nothing-real"), blk("done", "nothing to do")])
        run = _strict("t", T.Sandbox(root), model, hooks=ap, max_steps=8)
        evs = list(run.events())
        self.assertIn("never run", model.seen[1][-1]["content"])
        self.assertEqual(len(ap.asked), 2)
        self.assertIn("did not approve", model.seen[3][-1]["content"])
        self.assertEqual([e["ok"] for e in evs if e["type"] == "approval_done"], [True, False])
        self.assertEqual(self.final(evs)["status"], "success")

    def test_approval_policies(self):
        root = self.project({"a.py": "x = 1\n"})
        model = Script([blk("bash", "pip install x"), blk("done", "ok")])
        run = _strict("t", T.Sandbox(root), model, approval="deny", max_steps=4)
        list(run.events())
        self.assertIn("approvals are switched off", model.seen[1][-1]["content"])

    def test_no_tests_requires_a_real_run_then_accepts_with_caveat(self):
        root = self.project({"main.py": "print('hi')\n"})
        run, evs, model = self.run_agent(root, [
            blk("edit:main.py", "<<<<<<< SEARCH\nprint('hi')\n=======\nprint('hello')\n>>>>>>> REPLACE"),
            blk("done", "changed the greeting"),
            blk("done", "cannot run it here, unverified"),
        ])
        fin = self.final(evs)
        self.assertEqual(fin["status"], "success")
        self.assertIn("no tests", model.seen[2][-1]["content"].lower())
        self.assertEqual(fin["verification"]["kind"], "syntax")
        self.assertTrue(fin["verification"]["caveats"])

    def test_no_tests_with_a_successful_run_counts_as_checked(self):
        root = self.project({"main.py": "print('hi')\n"})
        run, evs, _ = self.run_agent(root, [
            blk("edit:main.py", "<<<<<<< SEARCH\nprint('hi')\n=======\nprint('hello')\n>>>>>>> REPLACE") + blk("bash", f"{PY} main.py"),
            blk("done", "ok"),
        ])
        fin = self.final(evs)
        self.assertEqual(fin["verification"]["kind"], "run")
        self.assertFalse(fin["verification"]["caveats"])

    def test_new_web_page_in_empty_folder_is_written_and_finished_without_commands(self):
        root = self.project({})
        run, evs, model = self.run_agent(root, [
            blk("file:index.html", "<!doctype html><html><body><div id='d'></div><script src='script.js'></script></body></html>")
            + blk("file:style.css", "body { margin: 0; }")
            + blk("file:script.js", "document.getElementById('d').textContent = '0';")
            + blk("done", "created index.html, style.css, script.js; open index.html in a browser"),
        ])
        fin = self.final(evs)
        self.assertEqual(fin["status"], "success")
        self.assertEqual(model.i, 1)                                   # one reply: no ls / bash detour
        for name in ("index.html", "style.css", "script.js"):
            self.assertTrue(os.path.exists(os.path.join(root, name)), name)
        self.assertIn("FIRST reply", model.seen[0][-1]["content"])
        self.assertTrue(any("browser" in c for c in fin["verification"]["caveats"]))

    def test_creating_files_with_shell_commands_is_refused_and_redirected(self):
        root = self.project({})
        run, evs, model = self.run_agent(root, [
            blk("bash", "touch index.html"),
            blk("file:index.html", "<html></html>") + blk("done", "created index.html"),
        ])
        self.assertFalse(os.path.exists(os.path.join(root, "x")))
        self.assertIn("file:PATH", model.seen[1][-1]["content"])
        self.assertTrue(os.path.exists(os.path.join(root, "index.html")))
        self.assertEqual(self.final(evs)["status"], "success")

    def test_planner_makes_a_plan_the_coder_then_follows(self):
        root = self.project({})
        plan = Script(["<think>hmm, a calculator</think>Approach: one page.\nFiles:\nindex.html - the page\nSteps:\n- [ ] write index.html\n- [ ] check it\nWatch out: none"])
        run, evs, model = self.run_agent(root, [blk("file:index.html", "<html></html>") + blk("done", "created index.html")],
                                         planner=plan, plan_mode="auto", planner_label="Qwen3-8B.gguf", coder_label="coder.gguf")
        self.assertEqual(self.final(evs)["status"], "success")
        self.assertEqual(plan.i, 1)
        kinds = [e["type"] for e in evs]
        self.assertLess(kinds.index("planning"), kinds.index("step"))
        start = next(e for e in evs if e["type"] == "start")
        self.assertEqual((start["planner"], start["coder"]), ("Qwen3-8B.gguf", "coder.gguf"))
        done = next(e for e in evs if e["type"] == "planning" and e["phase"] == "done")
        self.assertTrue(done["ok"])
        self.assertNotIn("think", done["text"])
        self.assertEqual([i["text"] for i in next(e for e in evs if e["type"] == "plan")["items"]], ["write index.html", "check it"])
        first = model.seen[0][-1]["content"]
        self.assertIn("PLAN (made by Qwen3-8B.gguf", first)
        self.assertIn("index.html - the page", first)
        self.assertIn("/no_think", plan.seen[0][-1]["content"])

    def test_a_failing_planner_does_not_stop_the_run(self):
        root = self.project({})

        def broken(messages, params):
            raise RuntimeError("model would not load")
            yield
        run, evs, model = self.run_agent(root, [blk("file:a.txt", "x") + blk("done", "ok")], planner=broken, plan_mode="auto")
        self.assertEqual(self.final(evs)["status"], "success")
        self.assertTrue(any(e["type"] == "note" and "continuing without a plan" in e["text"] for e in evs))
        self.assertNotIn("PLAN (made by", model.seen[0][-1]["content"])

    def test_planning_off_never_calls_the_planner(self):
        root = self.project({})
        plan = Script([])
        run, evs, model = self.run_agent(root, [blk("file:a.txt", "x") + blk("done", "ok")], planner=plan, plan_mode="off")
        self.assertEqual(plan.i, 0)
        self.assertFalse(any(e["type"] == "planning" for e in evs))

    def test_preexisting_failures_do_not_trap_an_unrelated_task(self):
        files = dict(BUGGY)
        files["notes.md"] = "# notes\n"
        root = self.project(files)
        run, evs, model = self.run_agent(root, [
            blk("file:notes.md", "# notes\nmore\n"),
            blk("done", "added a note"),
            blk("done", "the failing test is unrelated to the note"),
        ], goal="append a line to notes.md")
        fin = self.final(evs)
        self.assertEqual(fin["status"], "success")
        self.assertIn("already failing", model.seen[2][-1]["content"])
        self.assertTrue(any("already failing" in c for c in fin["verification"]["caveats"]))

    def test_read_only_question_finishes_without_verification(self):
        root = self.project()
        run, evs, _ = self.run_agent(root, [blk("read:calc.py"), "add subtracts, which is a bug. " * 8], goal="explain add")
        fin = self.final(evs)
        self.assertEqual((fin["status"], fin["files"]), ("success", []))

    def test_cut_off_file_block_is_never_written(self):
        root = self.project()
        run, evs, model = self.run_agent(root, [F + "file:calc.py\ndef add(a, b):\n    return a +", blk("done", "x")], max_steps=3)
        self.assertEqual(open(os.path.join(root, "calc.py")).read(), BUGGY["calc.py"])
        self.assertIn("NOT written", model.seen[1][-1]["content"])

    def test_cancel_stops_the_run(self):
        root = self.project()
        sb = T.Sandbox(root)
        run = _strict("t", sb, Script([blk("read:calc.py")] * 5), max_steps=5)
        out = []
        for ev in run.events():
            out.append(ev)
            if ev["type"] == "result":
                run.stop()
        self.assertEqual(out[-1]["status"], "stopped")

    def test_step_budget(self):
        root = self.project({"a.py": "x = 1\n"})
        run, evs, _ = self.run_agent(root, [blk("ls")] * 3, max_steps=3, tests=None)
        self.assertEqual(self.final(evs)["status"], "budget")

    def test_no_run_mode(self):
        root = self.project()
        model = Script([blk("bash", "ls"), blk("edit:calc.py", FIX), blk("done", "done without running")])
        run = _strict("t", T.Sandbox(root, can_run=False), model, max_steps=4)
        fin = self.final(list(run.events()))
        self.assertIn("disabled", model.seen[1][-1]["content"])
        self.assertEqual(fin["status"], "success")

    def test_stops_generating_after_a_closed_command_block(self):
        root = self.project({"a.py": "x = 1\n"})
        text = blk("bash", f"{PY} -c \"print(1)\"") + "SECRET-AFTER-COMMAND " * 20 + blk("done", "x")
        run, evs, model = self.run_agent(root, [text, blk("done", "ok")])
        assistant = [e for e in evs if e["type"] == "assistant"][0]
        self.assertNotIn("SECRET", assistant["text"])


class ManagerTests(Base):
    def test_one_run_at_a_time_and_revert(self):
        root = self.project()
        mgr = A.AgentManager()
        run = mgr.begin(_strict("t", T.Sandbox(root), Script([blk("edit:calc.py", FIX), blk("done", "x")]), max_steps=3, auto_tests=False))
        with self.assertRaises(A.Busy):
            mgr.begin(_strict("t", T.Sandbox(root), Script([]), max_steps=1))
        evs = list(mgr.stream(run))
        fin = self.final(evs)
        self.assertEqual(mgr.revert(fin["id"]), ["calc.py"])
        self.assertIn("a - b", open(os.path.join(root, "calc.py")).read())
        with self.assertRaises(KeyError):
            mgr.revert("nope")
        mgr.begin(_strict("t", T.Sandbox(root), Script([]), max_steps=1))     # slot is free again

    def test_approval_roundtrip(self):
        mgr = A.AgentManager()
        cancel, got = threading.Event(), []
        t = threading.Thread(target=lambda: got.append(mgr.wait_approval("abc", cancel)))
        t.start()
        for _ in range(50):
            if mgr.approve("abc", True):
                break
            threading.Event().wait(0.05)
        t.join(5)
        self.assertEqual(got, [True])
        t = threading.Thread(target=lambda: got.append(mgr.wait_approval("def", cancel)))
        t.start()
        threading.Event().wait(0.4)
        cancel.set()
        t.join(5)
        self.assertEqual(got, [True, False])


PLAN3 = "Approach: two files.\nFiles:\nindex.html - page\nstyle.css - looks\nSteps:\n- [ ] write index.html\n- [ ] write style.css\n- [ ] check it\nWatch out: none"


class TickAndResumeTests(Base):
    def plan_events(self, evs):
        return [e["items"] for e in evs if e["type"] == "plan"]

    def test_steps_are_ticked_off_by_themselves_when_their_files_are_written(self):
        root = self.project({})
        run, evs, _ = self.run_agent(root, [blk("file:index.html", "<html></html>"),
                                            blk("file:style.css", "body{margin:0}") + blk("done", "page and styles written")],
                                     planner=Script([PLAN3]), plan_mode="auto")
        self.assertEqual(self.final(evs)["status"], "success")
        plans = self.plan_events(evs)
        self.assertEqual([p["done"] for p in plans[0]], [False, False, False])           # the planner's plan
        self.assertEqual([p["done"] for p in plans[1]], [True, False, False])            # index.html written -> step 1 ticked, nothing else
        self.assertEqual([p["done"] for p in plans[2]], [True, True, False])             # style.css written -> step 2 ticked
        self.assertEqual([p["done"] for p in plans[-1]], [True, True, True])             # finished and verified -> everything ticked
        done_idx = [e["type"] for e in evs].index("done")
        self.assertEqual([e["type"] for e in evs][done_idx - 1], "plan")                  # the final tick arrives before the result

    def test_a_ticked_step_stays_ticked_when_the_model_resends_the_plan_without_the_x(self):
        root = self.project({})
        run, evs, _ = self.run_agent(root, [blk("file:index.html", "<html></html>"),
                                            blk("plan", "- [ ] write index.html\n- [ ] write style.css\n- [ ] check it") + blk("file:style.css", "a{}") + blk("done", "ok")],
                                     planner=Script([PLAN3]), plan_mode="auto")
        self.assertEqual(self.final(evs)["status"], "success")
        resent = [p for p in self.plan_events(evs) if [x["text"] for x in p] == ["write index.html", "write style.css", "check it"]]
        self.assertTrue(any(p[0]["done"] and not p[1]["done"] for p in resent), "index.html must stay ticked after the plan block")

    def test_a_syntax_error_does_not_tick_the_step(self):
        root = self.project({})
        run, evs, _ = self.run_agent(root, [blk("file:app.py", "def broken(:\n"), blk("file:app.py", "def ok():\n    return 1\n") + blk("done", "fixed")],
                                     planner=Script(["Steps:\n- [ ] write app.py"]), plan_mode="auto", max_steps=6)
        plans = self.plan_events(evs)
        self.assertFalse(plans[0][0]["done"])                                             # the planner's plan: nothing done
        self.assertEqual(len(plans), 2)                                                   # the broken write ticked nothing, so no plan update came after it
        self.assertTrue(plans[-1][0]["done"])                                             # the fixed write and the finish did

    def test_resume_continues_from_the_saved_state_without_replanning_or_a_baseline(self):
        root = self.project({"index.html": "<html></html>"})
        planner = Script([])                                 # would raise if the planner were asked again
        saved = {"status": "interrupted", "summary": "Interrupted (the page was reloaded).", "plan_text": "Steps: ...",
                 "plan": [{"text": "write index.html", "done": True}, {"text": "write style.css", "done": False}, {"text": "check it", "done": False}],
                 "files": ["index.html"], "steps": ["step 1: Write index.html [Write index.html]"]}
        run, evs, model = self.run_agent(root, [blk("file:style.css", "body{margin:0}") + blk("done", "styles written")],
                                         planner=planner, plan_mode="auto", resume=saved, goal="build a page")
        self.assertEqual(self.final(evs)["status"], "success")
        self.assertEqual(planner.i, 0)
        start = next(e for e in evs if e["type"] == "start")
        self.assertTrue(start["resumed"])
        self.assertFalse([e for e in evs if e["type"] == "verify" and e.get("baseline")])
        self.assertFalse([e for e in evs if e["type"] == "planning"])
        plans = self.plan_events(evs)
        self.assertEqual([p["done"] for p in plans[0]], [True, False, False])            # the old tick is restored before anything runs
        self.assertEqual([p["done"] for p in plans[-1]], [True, True, True])
        first = model.seen[0][-1]["content"]
        self.assertIn("RESUMING AN INTERRUPTED RUN", first)
        self.assertIn("- [x] write index.html", first)
        self.assertIn("Next unfinished step: write style.css", first)
        self.assertIn("Files the previous run already changed: index.html", first)
        self.assertIn("step 1: Write index.html", first)

    def test_a_normal_run_is_not_marked_resumed(self):
        root = self.project()
        run, evs, _ = self.run_agent(root, ["Look.\n" + blk("read:calc.py"), blk("edit:calc.py", FIX) + blk("test"), blk("done", "fixed")])
        self.assertFalse(next(e for e in evs if e["type"] == "start")["resumed"])


if __name__ == "__main__":
    unittest.main()
