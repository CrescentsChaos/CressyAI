"""Tests for the default 'fast' agent mode: simple tasks finish in one step, no planner, no rollbacks, no step/attempt nagging."""
import os, sys, unittest
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import agent as A
import agenttools as T
from tests.test_agent import Base, Script, blk, F, PY

WC = "import sys\ntext = sys.stdin.read()\nprint(len(text.split()), 'words')\n"


class FastTests(Base):
    def go(self, root, replies, goal="create a word counter", **kw):
        kw.setdefault("mode", "fast")
        return self.run_agent(root, replies, goal=goal, **kw)

    def test_word_counter_finishes_in_one_step(self):
        root = self.project({})
        run, evs, model = self.go(root, ["I'll write it.\n" + blk("file:wordcount.py", WC) + blk("done", "Created wordcount.py")])
        d = self.final(evs)
        self.assertEqual(d["status"], "success")
        self.assertEqual(d["steps"], 1)
        self.assertEqual(model.i, 1)
        self.assertTrue(os.path.exists(os.path.join(root, "wordcount.py")))
        self.assertFalse([e for e in evs if e["type"] in ("planning", "rollback", "retry")])

    def test_the_model_is_not_told_about_steps_or_attempts(self):
        root = self.project({})
        run, evs, model = self.go(root, [blk("file:a.py", "print(1\n"), blk("file:a.py", "print(1)\n") + blk("done", "ok")])
        shown = "\n".join(m["content"] for m in model.seen[-1])
        self.assertNotIn("attempt", shown.lower())
        self.assertNotRegex(shown, r"step \d+/\d+")
        self.assertEqual(self.final(evs)["status"], "success")

    def test_unlabeled_code_block_is_saved_instead_of_bounced(self):
        root = self.project({})
        run, evs, model = self.go(root, ["Here is wordcount.py\n" + F + "python\n" + WC + F + "\n" + blk("done", "done")])
        self.assertEqual(self.final(evs)["status"], "success")
        self.assertTrue(os.path.exists(os.path.join(root, "wordcount.py")))
        self.assertEqual(model.i, 1)

    def test_unlabeled_block_default_names_and_first_line_comment(self):
        parsed = T.parse_actions(F + "html\n<h1>x</h1>\n" + F + "\n" + F + "python\n# tools/util.py\nx = 1\n" + F + "\n" + F + "bash\nls\n" + F + "\n")
        got = [(s["path"]) for s in T.salvage_blocks(parsed)]
        self.assertEqual(got, ["index.html", "tools/util.py"])

    def test_plain_answer_ends_the_run(self):
        root = self.project({})
        run, evs, model = self.go(root, ["A word counter splits text on whitespace and counts the pieces; that is all there is to it. " * 3], goal="how does a word counter work")
        d = self.final(evs)
        self.assertEqual(d["status"], "success")
        self.assertEqual(model.i, 1)

    def test_short_chatter_is_nudged_once_then_work_happens(self):
        root = self.project({})
        run, evs, model = self.go(root, ["Sure!", blk("file:wc.py", WC) + blk("done", "made wc.py")])
        self.assertEqual(self.final(evs)["status"], "success")
        self.assertTrue(os.path.exists(os.path.join(root, "wc.py")))

    def test_repeated_failure_never_rolls_back_files(self):
        root = self.project({})
        bad = blk("file:a.py", "raise SystemExit(3)\n") + blk("bash", f"{PY} a.py")
        run, evs, model = self.go(root, [bad] * 6, max_steps=10)
        d = self.final(evs)
        self.assertEqual(d["status"], "failed")
        self.assertFalse([e for e in evs if e["type"] == "rollback"])
        self.assertTrue(os.path.exists(os.path.join(root, "a.py")), "files are kept, not rolled back")
        self.assertLessEqual(model.i, 5)

    def test_same_reply_loop_ends_gracefully(self):
        root = self.project({})
        run, evs, model = self.go(root, [blk("ls")] * 8, max_steps=10)
        d = self.final(evs)
        self.assertEqual(d["status"], "failed")
        self.assertLessEqual(model.i, 4)

    def test_done_with_syntax_error_is_bounced_at_most_twice(self):
        root = self.project({})
        broken = blk("file:a.py", "print(\n") + blk("done", "x")
        run, evs, model = self.go(root, [broken] * 5, max_steps=10)
        d = self.final(evs)
        self.assertEqual(d["status"], "failed")
        self.assertLessEqual(model.i, 4)

    def test_no_baseline_run_and_no_challenges(self):
        root = self.project()          # has a failing test suite; the task is unrelated
        run, evs, model = self.go(root, [blk("file:notes.txt", "hi") + blk("done", "added notes")], goal="add a notes file")
        self.assertFalse([e for e in evs if e["type"] == "verify" and e.get("baseline")])
        self.assertEqual(self.final(evs)["steps"], 1)



class StdinTests(unittest.TestCase):
    def test_input_call_gets_a_clear_hint(self):
        out = 'Traceback (most recent call last):\n  File "wc.py", line 1, in <module>\n    t = input()\nEOFError: EOF when reading a line\n'
        d = T.diagnose(out)
        self.assertEqual(d["headline"], "EOFError: EOF when reading a line")
        self.assertIn("Do NOT use input()", d["hint"])



class ApprovalMemoryTests(Base):
    CMD = f"{PY} -c \"print(1)\" > /tmp/x_out.txt"      # writes outside the project: always asks

    def test_the_same_command_is_asked_only_once(self):
        from tests.test_agent import Approver
        root = self.project({})
        ap = Approver([True])
        model = Script([blk("bash", self.CMD), blk("bash", self.CMD), blk("done", "ok")])
        run = A.AgentRun("run it", T.Sandbox(root), model, hooks=ap, max_steps=6, mode="fast")
        list(run.events())
        self.assertEqual(len(ap.asked), 1)

    def test_allow_for_the_run_covers_the_whole_kind_of_command(self):
        from tests.test_agent import Approver
        root = self.project({})
        ap = Approver(["always"])
        a, b = f"{PY} -c \"print(1)\" > /tmp/x_a.txt", f"{PY} -c \"print(2)\" > /tmp/x_b.txt"
        run = A.AgentRun("run it", T.Sandbox(root), Script([blk("bash", a), blk("bash", b), blk("done", "ok")]), hooks=ap, max_steps=6, mode="fast")
        list(run.events())
        self.assertEqual(len(ap.asked), 1)

    def test_a_denied_command_never_runs_and_is_not_asked_again(self):
        from tests.test_agent import Approver
        root = self.project({})
        ap = Approver([False])
        run = A.AgentRun("run it", T.Sandbox(root), Script([blk("bash", self.CMD), blk("bash", self.CMD), blk("done", "ok")]), hooks=ap, max_steps=6, mode="fast")
        evs = list(run.events())
        self.assertEqual(len(ap.asked), 1)
        self.assertFalse([e for e in evs if e["type"] == "cmd_out"])
        self.assertFalse(os.path.exists("/tmp/x_out.txt") and os.path.getmtime("/tmp/x_out.txt") > __import__("time").time() - 5)


class RepeatTests(Base):
    def test_the_same_command_with_different_chatter_runs_once_then_stops_the_run(self):
        root = self.project({})
        cmd = f"{PY} -c \"print('hello')\""
        replies = [f"Attempt {i}, I apologize for the repetition.\n" + blk("bash", cmd) for i in range(10)]
        run, evs, model = self.run_agent(root, replies, goal="research local llms", mode="fast", max_steps=12)
        d = self.final(evs)
        self.assertEqual(d["status"], "failed")
        self.assertIn("repeating the same action", d["summary"])
        self.assertEqual(len([e for e in evs if e["type"] == "action" and e["kind"] == "run"]), 1, "the command itself ran only once")
        self.assertLessEqual(model.i, 5)

    def test_the_same_command_may_run_again_after_the_project_changed(self):
        root = self.project({})
        cmd = f"{PY} wc.py"
        replies = [blk("file:wc.py", "print(1)\n") + blk("bash", cmd), blk("file:wc.py", "print(2)\n") + blk("bash", cmd), blk("done", "ok")]
        run, evs, model = self.run_agent(root, replies, goal="make wc", mode="fast", max_steps=6)
        self.assertEqual(self.final(evs)["status"], "success")
        self.assertEqual(len([e for e in evs if e["type"] == "action" and e["kind"] == "run"]), 2)



class AlreadyInstalledTests(Base):
    def test_pip_install_of_an_installed_package_is_not_asked_or_run(self):
        from tests.test_agent import Approver
        root = self.project({})
        ap = Approver([True])
        cmd = f"{PY} -m pip install pip"
        run = A.AgentRun("use pip", T.Sandbox(root), Script([blk("bash", cmd), blk("done", "ok")]), hooks=ap, max_steps=4, mode="fast")
        evs = list(run.events())
        self.assertEqual(ap.asked, [])
        res = [e for e in evs if e["type"] == "result"]
        self.assertTrue(res and res[0]["ok"])
        self.assertIn("Already installed", res[0]["text"])

    def test_message_names_the_package_and_version(self):
        root = self.project({})
        msg = T.already_installed(T.Sandbox(root), f"{PY} -m pip install pip")
        self.assertTrue(msg and msg.startswith("[not run] Already installed here: pip "), msg)

    def test_missing_package_upgrades_and_requirement_files_are_left_alone(self):
        root = self.project({})
        sb = T.Sandbox(root)
        self.assertIsNone(T.already_installed(sb, f"{PY} -m pip install definitely-not-a-real-package-xyz"))
        self.assertIsNone(T.already_installed(sb, f"{PY} -m pip install --upgrade pip"))
        self.assertIsNone(T.already_installed(sb, f"{PY} -m pip install -r requirements.txt"))
        self.assertIsNone(T.already_installed(sb, f"{PY} -m pip install pip==0.0.1"))
        self.assertIsNone(T.already_installed(sb, "pip install pip && rm -rf x"))

    def test_npm_checks_node_modules(self):
        root = self.project({"node_modules/left-pad/package.json": '{"name":"left-pad","version":"1.3.0"}'})
        sb = T.Sandbox(root)
        self.assertIn("left-pad 1.3.0", T.already_installed(sb, "npm install left-pad"))
        self.assertIn("left-pad", T.already_installed(sb, "npm i left-pad@1.3.0"))
        self.assertIsNone(T.already_installed(sb, "npm install left-pad lodash"))
        self.assertIsNone(T.already_installed(sb, "npm install -g left-pad"))
        self.assertIsNone(T.already_installed(sb, "npm install"))


if __name__ == "__main__":
    unittest.main()
