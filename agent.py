"""CressyAI agent mode: act, observe, verify, repair, retry, until the work is verified or the budget runs out.

Standard library only. The language model is injected as `model(messages, params)`, an iterator of {"text": str} chunks and
a final {"finish": str}; the server wires it to llama.cpp and the tests wire it to a script.

One run is a generator of JSON-able events (see AgentRun.events). The loop:
  1. every step the model answers with action blocks (read / grep / edit / file / bash / test / plan / done)
  2. the system executes them and sends back the real result, plus a diagnosis when something fails
  3. every written file is syntax-checked at once; commands and tests are read, not trusted
  4. the same failure three times in a row rolls the attempt back and starts a different one (limited number of attempts)
  5. `done` is only accepted after the system re-ran the checks itself and they passed
"""
import hashlib, os, re, threading, time, uuid
from pathlib import Path

import agenttools as T

T3 = "`" * 3

SYSTEM_PROMPT = "\n".join([
    "You are CressyAI's coding agent. You work alone inside the user's project folder and finish the task step by step. "
    "After every reply the system carries out your actions and shows you the REAL results. Never invent results, file contents or test output.",
    "",
    "Reply format: one or two short sentences (what you will do and why), then action blocks. A block is a fenced code block whose info string says what it is:",
    T3 + "ls            list the project (" + T3 + "ls:src lists one folder)",
    T3 + "read:PATH     show a file (optional body: a line range such as 40-90)",
    T3 + "grep          search the project (body: the pattern; optional second line: in: src)",
    T3 + "file:PATH     create a file or replace it completely (the whole file goes inside)",
    T3 + "edit:PATH     change a file with <<<<<<< SEARCH / ======= / >>>>>>> REPLACE sections; SEARCH is copied exactly from the file",
    T3 + "bash          run ONE shell command in the project folder",
    T3 + "test          run the project's tests (or put a test command inside)",
    T3 + "plan          a checklist: - [ ] step, - [x] finished step",
    T3 + "done          finish: say what you changed and how you verified it",
    "",
    "Rules:",
    "1. Look before you change: ls, read, grep. Do not edit a file you have not read.",
    "2. Make small, focused changes. Prefer edit blocks for existing files. If a file itself contains triple backticks, fence the block with four.",
    "3. At most one bash or test block per reply, and put it last. Then stop and wait for its result.",
    "4. After changing code, run the tests. If something fails, read the error, find the root cause, fix it, run again. If the same fix fails twice, change your approach instead of repeating it.",
    "5. For a bug fix or a new feature in a project that has tests, add or update a test for it.",
    "6. Do not say you are done without evidence. When you send a done block the system re-runs the checks itself and sends you back to work if they fail.",
    "7. No destructive commands, no network, no installs unless the task needs them. Some commands need the user's approval; blocked ones never run.",
    "8. New project, empty folder, or a task like 'create/make/build X': do NOT ls, read or grep first. Write every file straight away with " + T3 + "file:PATH blocks, several blocks in one reply "
    "(for example index.html, style.css and script.js together). Folders are created automatically. NEVER create files with bash (no echo, cat, touch, mkdir or redirects): only file blocks create files.",
    "9. A web page (html/css/js) cannot be run here. Once all its files are written and syntax-checked, finish with a " + T3 + "done block that lists the files and says to open index.html in a browser. Do not try to run it with commands.",
    "10. Errors are information. When a command or test fails, read the diagnosis, fix the cause, and run it again. 'command not found' or 'is not recognized' means you used a command from the wrong operating system: use the equivalent listed under THIS COMPUTER.",
    "11. Stay inside what this computer can do (see THIS COMPUTER): do not start work that needs more memory, a GPU or a tool that is not installed.",
    "12. If your first message contains a PLAN, follow it: it names the files and the order. Send a " + T3 + "plan block (- [x] finished, - [ ] next) when you finish a step. "
    "If the plan turns out wrong, say so in one sentence and send a corrected plan block.",
])

FAST_PROMPT = "\n".join([
    "You are CressyAI's coding agent. You work inside the user's project folder and do what the task asks, quickly and directly. "
    "The system carries out your actions and shows you the REAL results. Never invent results or file contents.",
    "",
    "Reply with one short sentence, then action blocks. A block is a fenced code block whose label says what it does:",
    T3 + "file:PATH     create a file or replace it completely (the whole file goes inside)",
    T3 + "edit:PATH     change part of a file: <<<<<<< SEARCH / ======= / >>>>>>> REPLACE (SEARCH is copied exactly from the file)",
    T3 + "read:PATH     show a file",
    T3 + "ls            list the project",
    T3 + "grep          search the project (body: the pattern)",
    T3 + "bash          run one shell command in the project folder",
    T3 + "done          finish: one or two sentences saying what you made or changed",
    "",
    "Rules:",
    "- A new or small task: write the finished files at once, all in your FIRST reply, one file block each. Do not explore, plan, ask questions or add features nobody asked for.",
    "- Changing existing code: read what you need, then use edit blocks.",
    "- Create files only with file blocks, never with shell commands (no echo, cat, touch, redirects).",
    "- You have NO internet and cannot browse. For research, planning or explanation tasks, write what you know into a file (for example NOTES.md) and send done. Never install packages unless the task asks for it.",
    "- Never send the same block twice: if an action already worked, move on to the next one.",
    "- Programs run with NO keyboard: never use input() or wait for typing. Read a file given as an argument (or stdin), or use built-in sample text, and print the result.",
    "- A script or program may be run once with a bash block to check it. A web page (html/css/js) cannot be run here: write the files and send done.",
    "- If something fails, read the error, fix the real cause, run again. Never repeat the same fix.",
    "- Send done as soon as the task works.",
    "",
    "Example of a complete reply for 'make a word counter':",
    "I'll write a small script that counts words, lines and characters.",
    T3 + "file:wordcount.py",
    "import sys",
    "text = open(sys.argv[1], encoding='utf-8').read() if len(sys.argv) > 1 else sys.stdin.read()",
    "print(len(text.split()), 'words,', len(text.splitlines()), 'lines,', len(text), 'characters')",
    T3,
    T3 + "done",
    "Created wordcount.py. Run: python wordcount.py file.txt",
    T3,
])

PLANNER_PROMPT = "\n".join([
    "You are the planner of a coding agent. You cannot read files or run anything, and you must not write code. "
    "Another model, the coder, carries out your plan with file, edit and command tools.",
    "Reply in exactly this shape, as plain text with no code blocks:",
    "Approach: 2-4 sentences on how to solve the task.",
    "Files: one line per file to create or change, as 'path - what it does'. For a new project list EVERY file. For an existing project name the files you expect to touch.",
    "Steps:",
    "- [ ] 3 to 8 short, ordered steps. The last step checks the result.",
    "Watch out: up to 3 short risks or mistakes to avoid.",
    "Be concrete and short. Do not repeat the task.",
])

CODE_EXT = {".py", ".js", ".mjs", ".cjs", ".ts", ".tsx", ".jsx", ".go", ".rs", ".java", ".kt", ".c", ".cc", ".cpp", ".h", ".hpp",
            ".cs", ".rb", ".php", ".sh", ".bat", ".ps1", ".html", ".css", ".vue", ".svelte", ".swift", ".lua", ".sql"}
RUNNABLE_EXT = {".py", ".go", ".rs", ".java", ".kt", ".c", ".cc", ".cpp", ".cs", ".rb", ".php", ".sh", ".bat", ".ps1", ".lua", ".mjs", ".cjs", ".js", ".ts"}
MAKE_FILE_CMD = re.compile(r"(^|[;&|]\s*)(touch|ni|new-item|set-content|out-file|add-content|tee)\b|\bcat\s*>|\bcat\s*<<|\becho\b[^\n]*>{1,2}\s*\S|\btype\s+nul\s*>|\bcopy\s+con\b", re.I)
READONLY_CMDS = {"ls", "dir", "cat", "type", "echo", "pwd", "cd", "find", "head", "tail", "wc", "grep", "rg", "git", "which", "where", "tree", "true"}


def is_code(path):
    return any(str(path).lower().endswith(e) for e in CODE_EXT)


def clean_reply(text):
    """A planner reply without the reasoning model's <think> section."""
    text = str(text or "")
    if "</think>" in text:
        text = text.split("</think>")[-1]
    text = re.sub(r"<think>.*\Z", "", text, flags=re.S)
    return text.strip()


def needs_run(changes):
    """Should a written change be run to count as checked? Static web pages cannot be run here; scripts and programs can."""
    paths = [c["path"].lower() for c in changes if not c.get("deleted")]
    if any(p.endswith((".html", ".htm")) for p in paths):
        return False
    return any(Path(p).suffix in RUNNABLE_EXT for p in paths)


def _plural(n, word):
    return f"{n} {word}{'' if n == 1 else 's'}"


class Hooks:
    """What the run needs from the outside world. Default: nothing may be approved."""
    def wait_approval(self, aid, cancel):
        return False


class AgentRun:
    def __init__(self, goal, sandbox, model, *, system="", max_steps=25, max_attempts=3, tests=None, auto_tests=True,
                 baseline=True, approval="ask", timeout=60, count=None, ctx_budget=9000, hooks=None, params=None,
                 stuck_limit=3, max_seconds=1800, planner=None, plan_mode="off", planner_label="", coder_label="", environment="", resume=None, mode="fast"):
        self.id = uuid.uuid4().hex[:10]
        self.fast = mode != "strict"             # fast: no planner, no baseline run, no rollbacks, no step / attempt nagging; strict: the full verify-and-retry machinery
        if self.fast:
            planner, plan_mode, baseline, max_attempts = None, "off", False, 1
        self.seen_sigs, self.skips = set(), 0            # fast mode: an action already done on an unchanged project is not done again
        self.ok_cmds, self.ok_reasons = set(), set()     # commands (exact) and kinds of command ("installs packages") the user already allowed in this run
        self.test_bounces = 0
        self.bounces = 0                         # fast mode: how often a done block was sent back (at most twice, then the run ends honestly)
        self.goal = goal.strip()
        self.sb, self.model, self.system_extra = sandbox, model, (system or "").strip()
        self.environment = (environment or "").strip()       # OS, shell, CPU, RAM, GPU and the rules that follow from them
        self.max_steps = max(1, int(max_steps))
        self.max_attempts = max(1, int(max_attempts))
        self.auto_tests, self.do_baseline = bool(auto_tests), bool(baseline)
        self.approval = approval if approval in ("ask", "deny", "allow") else "ask"
        self.timeout = max(5, int(timeout))
        self.count = count or (lambda t: len(t) // 3 + 1)
        self.ctx_budget = int(ctx_budget)
        self.hooks, self.params = hooks or Hooks(), dict(params or {})
        self.stuck_limit = max(2, int(stuck_limit))
        self.planner = planner or model                  # the model that plans: a reasoning model when the server has one, else the coder
        self.plan_mode = plan_mode if plan_mode in ("auto", "off") else "off"
        self.planner_label, self.coder_label = planner_label or "the coder", coder_label or "the coder"
        self.plan_text = ""
        self.resume = resume if isinstance(resume, dict) else None     # state of an interrupted run: continue from it instead of starting over
        self.written = set()                 # paths written or edited successfully in this run (drives the automatic plan ticking)
        self.max_seconds = max_seconds
        self.tests_override = tests          # None -> detect; list of command strings -> use these
        self.tests = []
        self.cancel = threading.Event()
        self.finished = False
        self.started = False
        self.created = time.monotonic()
        self.result = None
        self.baseline_failing, self.baseline_failed = None, 0
        # run state
        self.step, self.attempt = 0, 1
        self.plan = []
        self.broken = {}                     # path -> syntax error message (still unfixed)
        self.dirty = False                   # files changed since the last passing verification
        self.verified = False
        self.smoke_ok = False                # a real command succeeded after the last change
        self.last_verify = None
        self.baseline_info = None
        self.repeat = {}                     # failure signature -> count within this attempt
        self.attempt_notes = []
        self.attempt_files = set()
        self.edit_fail = {}
        self.idle = 0
        self.challenges = {"nosuite": 0, "preexisting": 0}
        self.last_hash, self.same_reply = "", 0
        self.giveup = None
        self.t0 = time.monotonic()
        self.n_actions = 0
        self.fix_goal = bool(re.search(r"\b(fix\w*|bug\w*|fail\w*|pass\w*|broken|error\w*|repair\w*|green|crash\w*|wrong)\b", self.goal, re.I))

    def stop(self):
        self.cancel.set()

    # ------------------------------------------------------------------ public: the event stream
    def events(self):
        self.started = True
        try:
            yield from self._main()
        except GeneratorExit:
            self.cancel.set()
            raise
        except Exception as e:                      # a bug here must never leave the UI hanging
            yield {"type": "error", "message": f"{e.__class__.__name__}: {e}"}
            yield self._final("error", f"The agent stopped because of an internal error: {e}")
        finally:
            self.cancel.set()                       # kills a command that may still be running
            self.finished = True

    # ------------------------------------------------------------------ main loop
    def _main(self):
        sb = self.sb
        self.tests = self._resolve_tests()
        yield {"type": "start", "id": self.id, "goal": self.goal, "root": str(sb.root), "max_steps": self.max_steps,
               "max_attempts": self.max_attempts, "tests": [t["cmd"] for t in self.tests], "can_run": sb.can_run,
               "can_write": sb.can_write, "approval": self.approval, "planner": self.planner_label if self.plan_mode == "auto" else "",
               "coder": self.coder_label, "resumed": bool(self.resume),
               "warn": "The context window is small; agent runs work much better with --n-ctx 8192 or more." if self.ctx_budget < 3500 else ""}
        if self.resume:
            rp = [{"text": str(p.get("text", ""))[:200], "done": bool(p.get("done"))} for p in (self.resume.get("plan") or []) if isinstance(p, dict) and p.get("text")]
            self.plan = rp[:20]
            self.plan_text = str(self.resume.get("plan_text") or "")
            if self.plan:
                yield {"type": "plan", "items": [dict(p) for p in self.plan]}
            yield {"type": "note", "text": "Continuing the previous run from where it stopped" + (f" ({sum(p['done'] for p in self.plan)} of {len(self.plan)} plan steps were already done)." if self.plan else ".")}
        else:
            if self.tests and self.auto_tests and self.do_baseline and sb.can_run:
                yield from self._baseline()
            if self.plan_mode == "auto":
                yield from self._plan_phase()
        messages = [{"role": "system", "content": (FAST_PROMPT if self.fast else SYSTEM_PROMPT) + (("\n\n" + self.environment) if self.environment else "") + (("\n\n" + self.system_extra) if self.system_extra else "")},
                    {"role": "user", "content": self._first_message()}]
        for step in range(1, self.max_steps + 1):
            if self.cancel.is_set():
                yield self._final("stopped", "Stopped by you.")
                return
            if time.monotonic() - self.t0 > self.max_seconds:
                yield self._final("budget", "Stopped: the time limit for one agent run was reached.")
                return
            self.step = step
            yield {"type": "step", "n": step, "max": self.max_steps, "attempt": self.attempt, "attempts": self.max_attempts}
            msgs, dropped = T.compact(messages, self.ctx_budget, self.count)
            if dropped:
                yield {"type": "note", "text": f"Context is full: {dropped // 2} older steps were summarised away."}
            text, finish = yield from self._generate(msgs)
            if self.cancel.is_set():
                yield self._final("stopped", "Stopped by you.")
                return
            parsed = T.parse_actions(text)
            messages.append({"role": "assistant", "content": text.strip() or "(no reply)"})
            yield {"type": "assistant", "n": step, "text": parsed["prose"][:3000]}
            h = hashlib.sha1(re.sub(r"\s+", " ", text).encode()).hexdigest()
            self.same_reply = self.same_reply + 1 if (h == self.last_hash and text.strip()) else 0
            self.last_hash = h
            sig = self._action_sig(parsed) if self.fast else None
            if sig and sig in self.seen_sigs:
                self.skips += 1
                label = next(((x["body"].strip().split("\n")[0] if x["kind"] in ("run", "test") else x["path"]) for x in parsed["actions"] if x["kind"] in ("run", "test", "file", "edit")), "")[:80]
                yield {"type": "note", "text": f"Skipped a repeat: {label}"}
                obs = [f"[skipped] You already did exactly this ({label}) and nothing in the project has changed since, so it would give the same result again. "
                       "Do NOT repeat it. Write the missing files with file blocks, fix the problem with an edit block, or send a done block."]
                outcome = None
                if self.skips >= 3:
                    self.giveup = f"Stopped: the model kept repeating the same action ({label}) without making progress. Your files are kept."
            else:
                self.skips = 0
                obs, outcome = yield from self._execute(step, parsed, finish)
                if sig:
                    self.seen_sigs.add(sig)
            if self.fast and self.same_reply >= 2 and not self.giveup:
                self.giveup = "Stopped: the model sent the same reply three times in a row. Your files are kept as they are."
            if self.same_reply:
                obs.append("You sent exactly the same reply as last time. Do something different: read the failing code again or try another fix.")
                if self.same_reply >= 2:
                    obs.append((yield from self._failure(step, {"signature": "loop", "headline": "the same reply repeated", "locations": [], "related": []})))
            if outcome == "finished":
                yield self.result
                return
            if self.giveup:
                yield self._final("failed", self.giveup)
                return
            messages.append({"role": "user", "content": "\n\n".join(x for x in obs if x) + "\n\n" + self._ledger()})
        yield self._final("budget", f"Stopped: used all {self.max_steps} steps without a verified result.")

    def _world(self):
        """A fingerprint of what the run has changed so far: the same action on the same fingerprint gives the same result."""
        try:
            return hashlib.sha1(repr([(c["path"], c["added"], c["removed"]) for c in self.sb.changes()]).encode()).hexdigest()[:12]
        except Exception:
            return ""

    def _action_sig(self, parsed):
        """Signature of a reply's actions (None when it holds nothing worth de-duplicating: only reads, plans or done)."""
        parts, eligible = [], False
        for x in parsed["actions"]:
            k = x["kind"]
            if k in ("plan", "done"):
                continue
            if k in ("run", "test", "file", "edit"):
                eligible = True
            body = x["body"] if k in ("file", "edit") else " ".join(x["body"].split())
            parts.append((k, x["path"] or "", hashlib.sha1(body.encode()).hexdigest()[:10]))
        return (self._world(), tuple(parts)) if (eligible and parts) else None

    def _generate(self, msgs):
        text, finish = "", None
        it = self.model(msgs, self.params)
        try:
            for ev in it:
                if self.cancel.is_set():
                    break
                if "text" in ev:
                    text += ev["text"]
                    yield {"type": "token", "text": ev["text"]}
                    if "`" in ev["text"] and T.stop_after(T.parse_actions(text)["actions"]):
                        finish = "stop-early"
                        text = T.cut_after_stop(text)
                        break
                if ev.get("finish"):
                    finish = ev["finish"]
        finally:
            close = getattr(it, "close", None)
            if close:
                close()
        return text, finish

    # ------------------------------------------------------------------ setup helpers
    def _resolve_tests(self):
        if not self.auto_tests:
            return []
        if self.tests_override:
            return [{"cmd": c, "name": T.first_line(c, 60), "source": "settings"} for c in self.tests_override if c.strip()]
        try:
            return T.detect_tests(self.sb.root)
        except Exception:
            return []

    def _first_message_fast(self):
        sb = self.sb
        tree = "\n".join(sb.tree(depth=2, limit=60))
        parts = [f"TASK: {self.goal}", "", f"Folder: {sb.root.name}/", ("Files:\n" + tree) if tree.strip() else "(the folder is empty)"]
        if self.tests:
            parts.append("The system runs these tests when you finish: " + "; ".join(t["cmd"] for t in self.tests))
        if not sb.can_run:
            parts.append("Running commands is disabled here: you can only read, search, edit and write files.")
        if self.resume:
            parts.append(self._resume_block())
        elif not tree.strip() or (re.search(r"\b(create|make|build|write|generate|scaffold|new)\b", self.goal, re.I) and not self.fix_goal):
            parts.append("Write all the files now, in this reply, with file blocks, then send done.")
        return "\n".join(parts)

    def _first_message(self):
        if self.fast:
            return self._first_message_fast()
        sb = self.sb
        tree = "\n".join(sb.tree(depth=2, limit=80))
        parts = [f"TASK: {self.goal}", "", f"PROJECT FOLDER: {sb.root.name}/ ({sb.root})", "Files (two levels deep):", tree or "(empty folder)", ""]
        if self.tests:
            parts.append("Test command(s) the system will use to verify your work:\n" + "\n".join("  " + t["cmd"] for t in self.tests))
        else:
            parts.append("No test command was found. The system checks syntax after every write. If you wrote a script or program, you may run it once with a bash block; "
                         "if you wrote a web page (html/css/js), do not run anything: just write the files and finish.")
        if self.baseline_info:
            parts.append(self.baseline_info)
        if not sb.can_run:
            parts.append("Running commands is disabled on this server: you can only read, search, edit and write files.")
        if self.plan_text:
            parts.append(f"PLAN (made by {self.planner_label}; follow it and tick steps off with a plan block as you finish them):\n{self.plan_text}\n")
        creating = bool(re.search(r"\b(create|make|build|write|generate|scaffold|start|new)\b", self.goal, re.I))
        if self.resume:
            parts.append(self._resume_block())
        elif not tree.strip():
            parts.append(f"The folder is empty, so there is nothing to look at. Your FIRST reply must write the files with " + T3 + "file:PATH blocks (all of them, one block each). Do not use ls, read or bash first. You have {self.max_steps} steps.")
        elif creating and not self.fix_goal:
            parts.append(f"This task creates new files. Write them straight away with " + T3 + f"file:PATH blocks; only read existing files that the new ones depend on. You have {self.max_steps} steps.")
        else:
            parts.append(f"You have {self.max_steps} steps. Start by looking at the relevant files.")
        return "\n".join(parts)

    def _resume_block(self):
        r = self.resume or {}
        out = ["RESUMING AN INTERRUPTED RUN. Do NOT start over and do NOT redo finished work: everything listed below is already on disk."]
        if r.get("status") or r.get("summary"):
            out.append(f"The previous run ended with status '{r.get('status') or 'interrupted'}'" + (f": {r['summary']}" if r.get("summary") else "."))
        if self.plan:
            out.append("Plan progress:\n" + "\n".join(f"- [{'x' if p['done'] else ' '}] {p['text']}" for p in self.plan))
            nxt = next((p["text"] for p in self.plan if not p["done"]), None)
            out.append(f"Next unfinished step: {nxt}" if nxt else "Every plan step is ticked; check the work once and finish with a done block.")
        if r.get("files"):
            out.append("Files the previous run already changed: " + ", ".join(str(f) for f in r["files"][:40]))
        if r.get("steps"):
            out.append("What the previous run did last:\n" + "\n".join("- " + str(s) for s in r["steps"][-12:]))
        out.append(f"Read a changed file again before you edit it, then carry on with the next unfinished step. Tick steps off with a plan block as you finish them. You have {self.max_steps} steps.")
        return "\n".join(out)

    def _baseline(self):
        """Run the tests once before touching anything, so old failures are not blamed on this task."""
        yield {"type": "verify", "phase": "start", "baseline": True, "commands": [t["cmd"] for t in self.tests]}
        res = yield from self._run_suites([t["cmd"] for t in self.tests], baseline=True)
        self.baseline_failing = set(res["failing"])
        self.baseline_failed = res["failed"]
        if res["ok"]:
            self.baseline_info = "Baseline: the tests pass right now."
        else:
            names = ", ".join(res["failing"][:8]) or "see output"
            self.baseline_info = (f"Baseline: the tests FAIL before any change ({res['summary']}). Failing: {names}. "
                                  "If the task is about these failures, fix them; otherwise do not break anything further.")
        yield {"type": "verify", "phase": "done", "baseline": True, "ok": res["ok"], "summary": res["summary"],
               "checks": res["checks"], "failing": res["failing"]}

    # ------------------------------------------------------------------ planning: a reasoning model (or the coder) thinks first
    def _plan_request(self, replan, why):
        sb = self.sb
        tree = "\n".join(sb.tree(depth=2, limit=80)) or "(empty folder)"
        parts = [f"TASK: {self.goal}", "", f"PROJECT FOLDER: {sb.root.name}/", "Files (two levels deep):", tree, ""]
        if self.tests:
            parts.append("Test command(s): " + "; ".join(t["cmd"] for t in self.tests))
        else:
            parts.append("No test command exists.")
        if self.baseline_info:
            parts.append(self.baseline_info)
        if replan:
            ch = sb.changes()
            parts += ["", "A previous attempt FAILED and was rolled back. " + (why or ""), "Propose a DIFFERENT approach. Earlier attempts:",
                      "- " + "\n- ".join(self.attempt_notes or ["(none recorded)"]), "Files changed so far: " + (", ".join(c["path"] for c in ch) or "none")]
        user = "\n".join(parts)
        if "qwen" in self.planner_label.lower():
            user += "\n/no_think"                        # Qwen3 would otherwise spend the whole budget thinking
        return [{"role": "system", "content": PLANNER_PROMPT}, {"role": "user", "content": user}]

    def _plan_phase(self, replan=False, why=""):
        """Ask the planner for approach / files / steps. Returns the plan text ('' when planning failed). Never stops the run."""
        yield {"type": "planning", "phase": "start", "by": self.planner_label, "replan": replan}
        params = dict(self.params)
        params["max_tokens"] = max(300, min(1400, int(params.get("max_tokens") or 1400)))
        params["temperature"] = min(0.4, float(params.get("temperature", 0.3)))
        text, err = "", ""
        try:
            for ev in self.planner(self._plan_request(replan, why), params):
                if self.cancel.is_set():
                    break
                if "text" in ev:
                    text += ev["text"]
        except Exception as e:
            err = f"{e.__class__.__name__}: {e}"
        text = clean_reply(text)[:3500]
        if not text or err:
            yield {"type": "planning", "phase": "done", "ok": False, "by": self.planner_label, "text": "", "replan": replan}
            yield {"type": "note", "text": f"Planning with {self.planner_label} failed ({err or 'empty reply'}); continuing without a plan."}
            return ""
        steps = text.split("Steps:", 1)[1] if "Steps:" in text else text
        steps = re.split(r"\n\s*(?:Watch out|Risks?)\s*:", steps, 1, flags=re.I)[0]
        items = T.parse_plan(steps)
        if items:
            self.plan = items
            yield {"type": "plan", "items": [dict(p) for p in self.plan]}
        self.plan_text = text
        yield {"type": "planning", "phase": "done", "ok": True, "by": self.planner_label, "text": text, "replan": replan}
        return text

    # ------------------------------------------------------------------ the ledger the model sees after every step
    def _ledger(self):
        if self.fast:
            return ("SYNTAX ERRORS still unfixed in: " + ", ".join(self.broken)) if self.broken else ""
        ch = self.sb.changes()
        lines = [f"[state] step {self.step}/{self.max_steps} · attempt {self.attempt}/{self.max_attempts}"]
        if self.plan:
            nxt = next((p["text"] for p in self.plan if not p["done"]), None)
            lines.append(f"plan: {sum(p['done'] for p in self.plan)}/{len(self.plan)} done" + (f"; next: {nxt}" if nxt else ""))
        lines.append("changed: " + (", ".join(f"{c['path']} (+{c['added']} -{c['removed']})" for c in ch)[:400] if ch else "nothing yet"))
        if self.broken:
            lines.append("SYNTAX ERRORS still unfixed in: " + ", ".join(self.broken))
        if self.last_verify:
            lines.append(f"last verification: {'PASSED' if self.last_verify['ok'] else 'FAILED'} ({self.last_verify['summary']})"
                         + ("; files changed since then" if self.dirty else ""))
        elif ch:
            lines.append("verification: not run yet")
        left = self.max_steps - self.step
        if left <= 3:
            lines.append(f"Only {left} step(s) left: finish or fix the last problem now.")
        return "\n".join(lines)

    # ------------------------------------------------------------------ executing one reply
    def _execute(self, step, parsed, finish):
        acts, obs = parsed["actions"], []
        if self.fast and parsed.get("raw_blocks") and not any(x["kind"] in ("file", "edit") for x in acts):
            sal = T.salvage_blocks(parsed)
            if sal:
                acts = sal + list(acts)
                obs.append("(Your code block had no label, so I saved it as " + ", ".join(s["path"] for s in sal) + ". Next time label blocks like " + T3 + "file:PATH.)")
        if finish == "length" or parsed["cut"]:
            obs.append("Your reply was cut off. A file or edit block that was not finished is NOT applied. Send smaller steps: edit blocks instead of whole files.")
        if not acts and self.fast:
            self.idle += 1
            prose = parsed["prose"].strip()
            if prose and (self.sb.changes() or len(prose) >= 160 or self.idle >= 2):        # a plain answer, or a summary after the work: that is the end
                done = yield from self._try_finish(step, prose)
                if done is True:
                    return obs, "finished"
                obs.append(done)
            elif prose:
                obs.append("Do the work now: write the files with " + T3 + "file:PATH blocks (the whole file inside), then send a " + T3 + "done block.")
            else:
                obs.append("Your reply was empty. Write the files with " + T3 + "file:PATH blocks, then send a " + T3 + "done block.")
                if self.idle >= 2:
                    self.giveup = "The model returned empty replies twice in a row."
            return obs, None
        if not acts:
            self.idle += 1
            if parsed["code_blocks"]:
                obs.append("Your code block had no action label, so NOTHING was written. Put the file name in the label, for example " + T3 + "file:index.html (then the whole file, then the closing fence). "
                           "Change existing files with " + T3 + "edit:PATH, run commands with " + T3 + "bash.")
            answer = not self.sb.changes() and self.n_actions > 0 and len(parsed["prose"]) >= 160     # a written answer after reading
            if parsed["prose"] and not self.sb.changes() and (answer or self.idle >= 2):
                done = yield from self._try_finish(step, parsed["prose"])
                if done is True:
                    return obs, "finished"
                obs.append(done)
            else:
                obs.append("No action block found. Take a step (ls, read, edit, bash, test) or finish with a " + T3 + "done block.")
            return obs, None
        self.idle = 0
        write_failed, ran, ignored = False, False, []
        for i, a in enumerate(acts):
            k = a["kind"]
            if ran:
                ignored.append(k)
                continue
            if k in ("run", "test", "done") and write_failed:
                obs.append(f"[skipped] {k}: an earlier file change in this reply failed, so there is nothing sensible to run yet. Fix it first.")
                ran = True
                continue
            self.n_actions += 1
            yield {"type": "action", "n": step, "i": i, "kind": k, "path": a["path"],
                   "cmd": a["body"].strip()[:300] if k in ("run", "test") else None}
            if k == "done":
                if any(x["kind"] in ("run", "test") for x in acts[:i]):
                    obs.append("[ignored] done: you ran a command in the same reply; wait for its result first.")
                    continue
                r = yield from self._try_finish(step, a["body"].strip() or parsed["prose"])
                if r is True:
                    return obs, "finished"
                obs.append(r)
                yield {"type": "result", "n": step, "i": i, "ok": False, "text": "not accepted yet"}
                ran = True
                continue
            handler = {"plan": self._a_plan, "ls": self._a_ls, "read": self._a_read, "grep": self._a_grep, "file": self._a_file,
                       "edit": self._a_edit, "run": self._a_run, "test": self._a_test}.get(k)
            if handler is None:
                continue
            ok, text, detail = yield from handler(step, i, a)
            yield {"type": "result", "n": step, "i": i, "ok": ok, "text": T.first_line(text, 160), "detail": detail}
            if ok and k in ("file", "edit") and a.get("path"):
                self.written.add(T.clean_path(a["path"]))
            if ok and k in ("file", "edit", "run", "test"):
                yield from self._auto_tick()
            obs.append(f"[{'ok' if ok else 'FAILED'}] {text}")
            if k in ("file", "edit") and not ok:
                write_failed = True
            if k in ("run", "test"):
                ran = True
        if ignored:
            obs.append(f"[ignored] {', '.join(ignored)}: only one command per reply, and anything after it depends on its result. Send them in the next reply.")
        return obs, None

    # ------------------------------------------------------------------ the actions
    def _a_plan(self, step, i, a):
        new = T.parse_plan(a["body"])
        if new:
            before = {re.sub(r"\W+", " ", p["text"]).strip().lower() for p in self.plan if p["done"]}
            for p in new:                    # a step that was ticked off stays ticked, even when the model forgets to repeat the [x]
                if re.sub(r"\W+", " ", p["text"]).strip().lower() in before:
                    p["done"] = True
            self.plan = new
        yield {"type": "plan", "items": [dict(p) for p in self.plan]}
        return True, f"Plan recorded: {sum(p['done'] for p in self.plan)}/{len(self.plan)} done.", None
        yield  # pragma: no cover  (makes this a generator)

    def _auto_tick(self):
        """Tick plan steps off by themselves: a step that names files is done once every one of them was written without a syntax error,
        and a test / verify step is done once the checks pass. The model's own plan blocks still win; this only fills in what it forgot."""
        if not self.plan:
            return
        ok_names = {os.path.basename(p).lower() for p in self.written if p not in self.broken}
        verified = bool(self.last_verify and self.last_verify.get("ok") and not self.dirty)
        changed = False
        for p in self.plan:
            if p["done"]:
                continue
            names = {os.path.basename(n.replace("\\", "/")).lower() for n in re.findall(r"[\w./\\-]+\.[A-Za-z][A-Za-z0-9]{1,5}\b", p["text"])}
            if names and names <= ok_names:
                p["done"], changed = True, True
            elif not names and verified and re.search(r"\b(test|tests|verify|verif\w+|check|run)\b", p["text"], re.I):
                p["done"], changed = True, True
        if changed:
            yield {"type": "plan", "items": [dict(p) for p in self.plan]}

    def _tick_all(self):
        if self.plan and not all(p["done"] for p in self.plan):
            for p in self.plan:
                p["done"] = True
            yield {"type": "plan", "items": [dict(p) for p in self.plan]}

    def _a_ls(self, step, i, a):
        path = a["path"] or T.first_line(a["body"]) or ""
        try:
            rows = self.sb.list_dir(T.clean_path(path))
        except T.SandboxError as e:
            return False, str(e), None
        return True, f"ls {path or '.'}:\n" + "\n".join(rows), None
        yield  # pragma: no cover

    def _a_read(self, step, i, a):
        path = a["path"] or T.clean_path(T.first_line(a["body"]))
        if not path:
            return False, "read needs a path, for example " + T3 + "read:src/app.py", None
        try:
            r = self.sb.read_text(path)
        except T.SandboxError as e:
            return False, str(e), None
        lines = r["text"].split("\n")
        m = re.search(r"(\d+)\s*[-:\u2013]\s*(\d+)", a["body"] if a["path"] else "") or re.search(r"lines?\s*(\d+)", a["body"] or "")
        lo, hi = 1, min(len(lines), 250)
        if m:
            lo = max(1, int(m.group(1)))
            hi = min(len(lines), int(m.group(2)) if m.lastindex and m.lastindex > 1 else lo + 120)
        if hi < lo:
            lo, hi = hi, lo
        chunk = "\n".join(lines[lo - 1:hi])
        if len(chunk) > 14000:
            chunk = chunk[:14000]
            hi = lo + chunk.count("\n")
        note = f" (showing {lo}-{hi}; use a line range to see the rest)" if hi < len(lines) or lo > 1 else ""
        return True, f"{path}: {len(lines)} lines{note}\n{T.numbered(chunk, lo)}" + ("\n[file was larger than the read limit; the end is not shown]" if r["truncated"] else ""), None
        yield  # pragma: no cover

    def _a_grep(self, step, i, a):
        body = [x for x in a["body"].split("\n") if x.strip()]
        if not body:
            return False, "grep needs a pattern inside the block", None
        where = ""
        for ln in body[1:]:
            m = re.match(r"^\s*in\s*:\s*(.+)$", ln, re.I)
            if m:
                where = T.clean_path(m.group(1))
        try:
            hits = self.sb.grep(body[0].strip(), where)
        except T.SandboxError as e:
            return False, str(e), None
        return True, f"grep {body[0].strip()!r}: " + (f"{len(hits)} match(es)\n" + "\n".join(hits) if hits else "no matches"), None
        yield  # pragma: no cover

    def _after_write(self, path, text):
        """Record a write; returns the syntax problem (or None)."""
        self.dirty, self.verified, self.smoke_ok = True, False, False
        self.attempt_files.add(path)
        bad = T.syntax_check(self.sb, path, text)
        if bad:
            self.broken[path] = bad
        else:
            self.broken.pop(path, None)
        return bad

    def _a_file(self, step, i, a):
        path = a["path"]
        if not path:
            return False, "file block needs a path: " + T3 + "file:src/app.py", None
        if not a["closed"]:
            return False, f"{path}: the block was cut off, so the file was NOT written. Write it in smaller pieces or use edit blocks.", None
        text = a["body"].rstrip() + "\n" if a["body"].strip() else ""
        try:
            old = self.sb.read_text(path)["text"] if self.sb.exists(path) else None
        except T.SandboxError:
            old = None
        try:
            res = self.sb.write(path, text)
        except T.SandboxError as e:
            return False, str(e), None
        yield {"type": "file_change", "path": res["path"], "before": res["before"], "after": res["after"], "created": res["created"], "crlf": False}
        bad = self._after_write(res["path"], text)
        yield {"type": "syntax", "path": res["path"], "ok": not bad, "message": bad or ""}
        n = len(text.splitlines())
        msg = f"{'Created' if res['created'] else 'Replaced'} {res['path']} ({_plural(n, 'line')})."
        if old is not None and len(old.splitlines()) > 30 and n < len(old.splitlines()) * 0.5:
            msg += f" WARNING: the file shrank from {len(old.splitlines())} to {n} lines. If you did not mean to remove content, restore it."
        if bad:
            msg += f"\nSYNTAX ERROR right after writing: {bad}\nFix this before doing anything else."
        return not bad, msg, None

    def _a_edit(self, step, i, a):
        path = a["path"]
        if not path:
            return False, "edit block needs a path: " + T3 + "edit:src/app.py", None
        if not a["closed"]:
            return False, f"{path}: the edit block was cut off, so NOTHING was applied. Send fewer or smaller edits.", None
        pe = T.parse_edits(a["body"])
        if not pe["seen"]:
            return False, f"{path}: no <<<<<<< SEARCH / ======= / >>>>>>> REPLACE sections found in the edit block.", None
        if not pe["complete"]:
            return False, f"{path}: the last SEARCH/REPLACE section is unfinished, so nothing was applied.", None
        try:
            base = self.sb.read_text(path)
        except T.SandboxError as e:
            return False, f"{e}. For a new file use a file block.", None
        if base["truncated"]:
            return False, f"{path} is too large to edit safely. Replace it with a file block or edit a smaller file.", None
        r = T.apply_edits(base["text"], pe["edits"])
        errs = [e["msg"] + ("\n" + e["hint"] if e.get("hint") else "") for e in r["errors"]]
        if not r["applied"]:
            if r["already"] and not errs:
                return True, f"{path}: these edits were already applied.", None
            self.edit_fail[path] = self.edit_fail.get(path, 0) + 1
            extra = (f"\nThis is failure #{self.edit_fail[path]} on {path}. Read the file again" +
                     (" or replace it completely with a file block." if self.edit_fail[path] >= 3 else " and copy the SEARCH text exactly."))
            return False, f"{path}: NOT changed.\n" + "\n".join(errs) + extra, None
        try:
            res = self.sb.write(path, r["text"])
        except T.SandboxError as e:
            return False, str(e), None
        self.edit_fail.pop(path, None)
        yield {"type": "file_change", "path": res["path"], "before": res["before"], "after": res["after"], "created": False, "crlf": False}
        bad = self._after_write(res["path"], r["text"])
        yield {"type": "syntax", "path": res["path"], "ok": not bad, "message": bad or ""}
        msg = f"Edited {res['path']}: applied {_plural(r['applied'], 'edit')}."
        if errs:
            msg += f"\n{_plural(len(errs), 'edit')} NOT applied (send only these again):\n" + "\n".join(errs)
        if bad:
            msg += f"\nSYNTAX ERROR right after the edit: {bad}\nFix this before doing anything else."
        return (not errs) and (not bad), msg, None

    def _guard(self, cmd):
        """-> None when the command may run, else the reason it did not."""
        level, why = T.classify_command(cmd)
        if level == "block":
            return f"Refused: this command {why}. It will never run; choose another way."
        if level == "ask":
            if self.approval == "allow":
                return None
            if self.approval == "deny":
                return f"Not run: this command {why}, and approvals are switched off for this run."
            if " ".join(str(cmd).split()) in self.ok_cmds or why in self.ok_reasons:       # already allowed once: never ask again for it
                return None
            return "ask:" + why
        return None

    def _approve(self, cmd, why):
        aid = uuid.uuid4().hex[:8]
        yield {"type": "approval", "id": aid, "cmd": cmd[:600], "reason": why}
        answer = self.hooks.wait_approval(aid, self.cancel)
        ok = bool(answer)
        if ok:
            self.ok_cmds.add(" ".join(str(cmd).split()))
            if answer == "always":
                self.ok_reasons.add(why)
        yield {"type": "approval_done", "id": aid, "ok": ok}
        return ok

    def _stream_cmd(self, cmd, track=True):
        """Run a command, forwarding live output; returns its result dict."""
        res = None
        for kind, val in self.sb.run_iter(cmd, self.timeout, self.cancel, track):
            if kind == "out":
                yield {"type": "cmd_out", "text": val}
            else:
                res = val
        if res and res.get("changed"):
            by = {c["path"]: c for c in self.sb.changes()}
            for rel in res["changed"]:
                ch = by.get(rel)
                if ch:
                    yield {"type": "file_change", "path": rel, "before": ch["before"], "after": ch["after"], "created": ch["created"],
                           "crlf": ch["crlf"], "external": True}
            self.dirty, self.verified, self.smoke_ok = True, False, False
        return res

    def _a_run(self, step, i, a):
        cmd = re.sub(r"^\s*(?:\$|PS [^>]*>|>)\s+", "", a["body"].strip(), flags=re.M).strip()
        if not cmd:
            return False, "bash block is empty", None
        if not self.sb.can_run:
            return False, "Running commands is disabled on this server. Use read, edit and file blocks only.", None
        if MAKE_FILE_CMD.search(cmd):
            return False, ("[not run] Do not create files with shell commands. Write each file with a " + T3 + "file:PATH block instead, for example " + T3 +
                           "file:index.html followed by the complete file and a closing fence. Send all the files you need in one reply."), None
        have = T.already_installed(self.sb, cmd)         # an install of something that is already here is neither asked about nor run
        if have:
            return True, have, None
        why = self._guard(cmd)
        if why and why.startswith("ask:"):
            ok = yield from self._approve(cmd, why[4:])
            if not ok:
                return False, f"The user did not approve this command ({why[4:]}). Do not retry it; find another way.", None
        elif why:
            return False, why, None
        res = yield from self._stream_cmd(cmd)
        return (yield from self._command_outcome(step, cmd, res))

    def _command_outcome(self, step, cmd, res):
        out = res["out"]
        failed = bool(res["code"]) or res["timeout"] or res["killed"] or bool(res.get("error")) or res["code"] is None
        tr = T.parse_test_result(out, res["code"])
        head = f"$ {cmd}\n"
        if res.get("error"):
            return False, head + "Could not run: " + res["error"], None
        if res["timeout"]:
            diag = {"signature": hashlib.sha1(("timeout" + cmd).encode()).hexdigest()[:10], "headline": "command timed out", "locations": [], "related": [], "failing": []}
            extra = yield from self._failure(step, diag)
            return False, head + f"TIMED OUT after {self.timeout} s (killed). Output so far:\n{T.head_tail(out, 500, 1200)}\n{extra}", None
        if res["killed"]:
            return False, head + "Stopped.", None
        changed = f"\n(The command changed these files: {', '.join(res['changed'])}.)" if res.get("changed") else ""
        if not failed:
            if cmd.split()[0].lower() not in READONLY_CMDS:
                self.smoke_ok = True
            tline = f"\nTest result: {T.summarize_test(tr)}" if tr["framework"] else ""
            return True, head + f"exit code 0.{tline}{changed}\n{T.head_tail(out, 700, 1800) if out.strip() else '(no output)'}", None
        diag = T.diagnose(out, self.sb)
        extra = yield from self._failure(step, diag, out)
        if tr["framework"]:
            head += f"Test result: {T.summarize_test(tr)}\n"
        yield {"type": "diagnosis", "kind": diag["kind"], "headline": diag["headline"], "locations": diag["locations"], "failing": diag["failing"]}
        return False, head + f"exit code {res['code']}.{changed}\n{self._diag_text(diag, out)}\n{extra}", None

    def _diag_text(self, diag, out):
        parts = []
        if diag["headline"]:
            parts.append("Diagnosis: " + diag["headline"] + ("  at " + ", ".join(f"{l['path']}:{l['line']}" for l in diag["locations"][:3]) if diag["locations"] else ""))
        if diag["failing"]:
            parts.append("Failing: " + ", ".join(diag["failing"][:8]))
        if diag.get("related"):
            parts.append("The code under test is probably in: " + ", ".join(diag["related"]))
        if diag["hint"]:
            parts.append(diag["hint"])
        snip = T.context_snippets(self.sb, diag["locations"])
        if snip:
            parts.append(snip)
        parts.append("Output:\n" + T.head_tail(out, 600, 2200))
        return "\n".join(parts)

    def _a_test(self, step, i, a):
        body = a["body"].strip()
        cmds = [body] if body else [t["cmd"] for t in self.tests]
        if not cmds:
            return False, "No test command was found for this project. Run your code with a bash block, or add a cressy-agent.json with a \"test\" command.", None
        if not self.sb.can_run:
            return False, "Running commands is disabled on this server, so tests cannot run.", None
        for c in cmds:
            why = self._guard(c)
            if why and why.startswith("ask:"):
                ok = yield from self._approve(c, why[4:])
                if not ok:
                    return False, f"The user did not approve running: {c}", None
            elif why:
                return False, why, None
        yield {"type": "verify", "phase": "start", "commands": cmds}
        res = yield from self._verify(step, cmds)
        yield {"type": "verify", "phase": "done", "ok": res["ok"], "summary": res["summary"], "checks": res["checks"], "failing": res["failing"]}
        return res["ok"], res["text"], None

    # ------------------------------------------------------------------ verification
    def _run_suites(self, cmds, baseline=False, step=0):
        """Run test commands; returns {ok, summary, failing, failed, checks, text, diag}."""
        checks, failing, failed, texts, ok, diag0 = [], [], 0, [], True, None
        for c in cmds:
            res = yield from self._stream_cmd(c, track=False)
            tr = T.parse_test_result(res["out"], res["code"])
            suite_ok = bool(tr["ok"]) and not res["timeout"] and not res.get("error")
            ok = ok and suite_ok
            summ = "timed out" if res["timeout"] else (res.get("error") or T.summarize_test(tr))
            checks.append({"name": T.suite_label(c), "cmd": T.first_line(c, 120), "ok": suite_ok, "detail": summ})
            failing += tr["failing"]
            failed += tr["failed"] + tr["errors"]
            if suite_ok:
                texts.append(f"$ {c}\nPASSED ({summ})")
            else:
                d = T.diagnose(res["out"], self.sb)
                diag0 = diag0 or d
                texts.append(f"$ {c}\nFAILED ({summ}, exit {res['code']})\n" + (self._diag_text(d, res["out"]) if not baseline else T.head_tail(res["out"], 300, 900)))
        summary = "; ".join((f"{x['name']}: " if len(checks) > 1 else "") + x["detail"] for x in checks) or "no checks"
        return {"ok": ok, "summary": summary, "failing": failing, "failed": failed, "checks": checks, "text": "\n\n".join(texts), "diag": diag0}

    def _verify(self, step, cmds, final=False):
        """Syntax of every changed file, then the test suites. Updates dirty / verified / last_verify."""
        checks, text = [], []
        changed = [c["path"] for c in self.sb.changes() if not c["deleted"] and Path(c["path"]).suffix.lower() not in T.BIN_EXT]
        for p in changed:
            try:
                bad = T.syntax_check(self.sb, p, self.sb.read_text(p)["text"], strict=True)
            except T.SandboxError:
                bad = None
            if bad:
                self.broken[p] = bad
            else:
                self.broken.pop(p, None)
        if changed:
            nb = [p for p in changed if p in self.broken]
            checks.append({"name": "syntax", "cmd": "", "ok": not nb,
                           "detail": f"{_plural(len(changed), 'file')} ok" if not nb else "errors in " + ", ".join(nb)})
            if nb:
                text.append("SYNTAX ERRORS:\n" + "\n".join(self.broken[p] for p in nb))
        suite = {"ok": True, "summary": "", "failing": [], "failed": 0, "checks": [], "text": "", "diag": None}
        if cmds and not self.broken:
            suite = yield from self._run_suites(cmds, step=step)
            checks += suite["checks"]
            if suite["text"]:
                text.append(suite["text"])
        elif cmds and self.broken:
            text.append("The tests were not run because some files have syntax errors.")
        ok = suite["ok"] and not self.broken
        pre_only = False
        base = self.baseline_failing
        if not suite["ok"] and base is not None and not self.broken and suite["failing"] and set(suite["failing"]) <= base \
                and suite["failed"] <= self.baseline_failed:
            pre_only = True
            text.append("(All remaining failures were already failing before this task started.)")
        summary = "; ".join(f"{c['name']}: {c['detail']}" for c in checks) or "nothing to check"
        self.last_verify = {"ok": ok, "summary": summary, "pre_only": pre_only, "failing": list(suite["failing"])}
        if ok:
            self.dirty, self.verified = False, True
            self.sb.new_attempt()
            self.attempt_files = set()
            self.repeat.clear()
        else:
            diag = suite["diag"] or {"signature": hashlib.sha1(summary.encode()).hexdigest()[:10], "headline": summary, "locations": [], "related": [], "failing": suite["failing"]}
            if self.broken and not suite["diag"]:
                first = next(iter(self.broken))
                diag = {"signature": "syntax:" + first, "headline": T.first_line(self.broken[first], 120), "locations": [], "related": [], "failing": []}
            if not (pre_only and final):            # at the final gate old failures are handled by the completion rules, not by retry logic
                text.append((yield from self._failure(step, diag)))
        return {"ok": ok, "pre_only": pre_only, "summary": summary, "failing": suite["failing"], "checks": checks,
                "text": "\n\n".join(t for t in text if t) if text else "All checks passed: " + summary}

    # ------------------------------------------------------------------ repeated failures: warn, roll back, retry, give up
    def _failure(self, step, diag, out=""):
        sig = diag.get("signature") or hashlib.sha1((out[-300:] or "x").encode()).hexdigest()[:10]
        n = self.repeat[sig] = self.repeat.get(sig, 0) + 1
        if n == 1:
            return ""
        if self.fast:
            if n < 4:
                return ("This is the SAME failure as before: your last change did not fix it. Do not repeat that fix. "
                        "Re-read the failing code, name the root cause in one sentence, then try something different.")
            self.giveup = f"Stopped: the same problem happened {n} times ({diag.get('headline') or 'the checks keep failing'}). Your files are kept so you can look at them."
            return ""
        if n < self.stuck_limit:
            return ("This is the SAME failure as before: your last change did not fix it. Do not repeat that fix. "
                    "Re-read the failing code, state the root cause in one sentence, then try something different.")
        tried = f"attempt {self.attempt}: edited {', '.join(sorted(self.attempt_files)) or 'nothing'}; kept failing with: {diag.get('headline') or 'the same error'}"
        self.attempt_notes.append(tried)
        if self.attempt >= self.max_attempts:
            self.giveup = (f"Gave up after {self.attempt} attempts. The last problem: {diag.get('headline') or 'the checks keep failing'}. "
                           "The changes from the last attempt are still in your project; use Revert to undo them.")
            return ""
        implicated = set(l["path"] for l in diag.get("locations", [])) | set(diag.get("related", []))
        only = implicated & set(self.sb.cp) or None
        files = self.sb.rollback_attempt(only)
        for f in files:
            self.broken.pop(f, None)
        yield {"type": "rollback", "attempt": self.attempt, "files": files, "reason": diag.get("headline") or "the same failure repeated"}
        self.attempt += 1
        self.repeat.clear()
        self.edit_fail.clear()
        self.attempt_files = set()
        self.dirty = bool(self.sb.changes())
        self.verified = False
        yield {"type": "retry", "attempt": self.attempt, "max": self.max_attempts}
        newplan = ""
        if self.plan_mode == "auto":
            newplan = yield from self._plan_phase(replan=True, why=f"It kept failing with: {diag.get('headline') or 'the same error'}.")
        return ((f"NEW PLAN from {self.planner_label}:\n{newplan}\n\n" if newplan else "") + f"ROLLED BACK {', '.join(files) or 'nothing (no file had changed)'} because the same failure happened {n} times "
                f"({diag.get('headline') or 'unknown'}). Starting attempt {self.attempt} of {self.max_attempts}.\n"
                "What was tried and failed:\n- " + "\n- ".join(self.attempt_notes) + "\n"
                "Take a genuinely different approach: read the relevant code again and look for a different root cause.")

    # ------------------------------------------------------------------ finishing
    def _try_finish(self, step, summary):
        """Called for a done block. Returns True when the run is finished (final event already queued in self.result),
        else the text that goes back to the model. It is a generator: it yields events while it verifies."""
        if self.fast:
            return (yield from self._finish_fast(step, summary))
        sb = self.sb
        changes = sb.changes()
        if self.broken:
            return "Cannot finish yet: these files still have syntax errors:\n" + "\n".join(self.broken.values())
        caveats = []
        if changes and (self.dirty or not self.verified):
            cmds = [t["cmd"] for t in self.tests] if (self.tests and self.auto_tests and sb.can_run) else []
            yield {"type": "verify", "phase": "start", "final": True, "commands": cmds}
            res = yield from self._verify(step, cmds, final=True)
            yield {"type": "verify", "phase": "done", "final": True, "ok": res["ok"], "summary": res["summary"], "checks": res["checks"], "failing": res["failing"]}
            if not res["ok"] and (not res["pre_only"] or self.fix_goal):
                return "Cannot finish yet: my own verification FAILED.\n" + res["text"] + "\nFix the problem, then send done again."
        lv = self.last_verify
        if changes and lv and not lv["ok"] and lv.get("pre_only") and not self.fix_goal:
            if self.challenges["preexisting"] < 1:
                self.challenges["preexisting"] += 1
                return ("The checks still fail, but only on tests that were already failing before you started: " + ", ".join(lv["failing"][:8]) +
                        ".\nIf the task was to fix them, keep working. If they are unrelated to your task, send done again and say so.")
            caveats.append("Tests that were already failing before the task still fail: " + ", ".join(lv["failing"][:6]))
            self.dirty, self.verified = False, True
        if changes and not self.tests and any(is_code(c["path"]) for c in changes) and not self.smoke_ok:
            if not needs_run(changes):
                if any(c["path"].lower().endswith((".html", ".htm")) for c in changes):
                    caveats.append("Web page files cannot be run here, so they were only syntax-checked. Open the .html file in a browser to try it.")
            elif self.challenges["nosuite"] < 1 and sb.can_run:
                self.challenges["nosuite"] += 1
                return ("This project has no tests and nothing you ran has shown the change works. Run the code with a bash block (or add a test and run it), "
                        "then send done. If it truly cannot be run here, send done again and say what is unverified.")
            else:
                caveats.append("No tests exist and the change was not run, so it is only syntax-checked.")
        kind = ("tests" if (self.tests and lv and (lv["ok"] or lv.get("pre_only"))) else "run" if (self.smoke_ok and changes)
                else "syntax" if changes else "none")
        yield from self._tick_all()                 # the run finished and passed its checks: the whole plan is done
        self._final("success", summary or "Done.", caveats=caveats, verify_kind=kind)
        return True

    def _finish_fast(self, step, summary):
        """Fast mode: one cheap check (syntax, plus the project's own tests when it has some), at most two bounces back to the model, then the run ends."""
        sb = self.sb
        changes = sb.changes()
        caveats_t = None
        if changes and (self.dirty or not self.verified):
            code_changed = any(is_code(c["path"]) for c in changes if not c["deleted"])       # a notes file or a readme never needs the test suite
            cmds = [t["cmd"] for t in self.tests] if (self.tests and self.auto_tests and sb.can_run and code_changed) else []
            yield {"type": "verify", "phase": "start", "final": True, "commands": cmds}
            res = yield from self._verify(step, cmds, final=True)
            yield {"type": "verify", "phase": "done", "final": True, "ok": res["ok"], "summary": res["summary"], "checks": res["checks"], "failing": res["failing"]}
            if not res["ok"] and not res["pre_only"]:
                if self.broken:                                   # a file that does not even parse: back to the model, twice at most
                    self.bounces += 1
                    if self.bounces <= 2:
                        return "Cannot finish yet: the check failed.\n" + res["text"] + "\nFix it, then send done again."
                    self._final("failed", "The files were written, but some still do not parse: " + res["summary"] + ". Your files are kept.")
                    return True
                self.test_bounces += 1                            # failing tests: one more try (two for a bug-fix task), then report it honestly
                if self.test_bounces <= (2 if self.fix_goal else 1):
                    return "Cannot finish yet: the project's tests fail.\n" + res["text"] + "\nFix the problem, then send done again."
                caveats_t = "The project's tests still fail (" + res["summary"] + "); some of that may have been failing before this task."
        lv = self.last_verify
        kind = ("tests" if (self.tests and lv and (lv["ok"] or lv.get("pre_only"))) else "run" if (self.smoke_ok and changes) else "syntax" if changes else "none")
        caveats = [caveats_t] if caveats_t else []
        if changes and any(c["path"].lower().endswith((".html", ".htm")) for c in changes):
            caveats.append("Open the .html file in a browser to try it.")
        yield from self._tick_all()
        self._final("success", summary or "Done.", caveats=caveats, verify_kind=kind)
        return True

    def _final(self, status, summary, caveats=None, verify_kind=None):
        ch = self.sb.changes()
        files = [{"path": c["path"], "created": c["created"], "deleted": c["deleted"], "added": c["added"], "removed": c["removed"]} for c in ch]
        lv = self.last_verify
        verification = {"ran": bool(lv), "ok": (lv["ok"] if lv else None), "kind": verify_kind or ("tests" if (lv and self.tests) else "syntax" if lv else "none"),
                        "summary": lv["summary"] if lv else "", "caveats": list(caveats or [])}
        if status == "success" and not ch:
            verification = {"ran": False, "ok": None, "kind": "none", "summary": "nothing was changed", "caveats": []}
        md = self._markdown(status, summary, files, verification)
        self.result = {"type": "done", "id": self.id, "status": status, "summary": summary, "files": files, "verification": verification,
                       "steps": self.step, "attempts": self.attempt, "seconds": round(time.monotonic() - self.t0, 1),
                       "can_revert": bool(files), "markdown": md}
        return self.result

    def _markdown(self, status, summary, files, ver):
        head = {"success": "**Done.**", "failed": "**Could not finish.**", "stopped": "**Stopped.**", "budget": "**Out of steps.**", "error": "**Error.**"}[status]
        lines = [f"{head} {summary}".strip(), ""]
        if files:
            lines.append(f"Changed files (in `{self.sb.root}`):")
            lines += [f"- `{f['path']}` ({'new' if f['created'] else 'deleted' if f['deleted'] else 'edited'}, +{f['added']} −{f['removed']})" for f in files]
            lines.append("")
        if status == "success":
            if ver["kind"] == "tests":
                lines.append(f"Verified by the project's tests: {ver['summary']}.")
            elif ver["kind"] == "run":
                lines.append("Checked by running the code (no test suite found).")
            elif ver["kind"] == "syntax":
                lines.append("Only syntax-checked (no tests found).")
            for c in ver["caveats"]:
                lines.append(f"Note: {c}")
        elif ver.get("summary"):
            lines.append(f"Last check: {ver['summary']}.")
        return "\n".join(lines).strip()


# ---------------------------------------------------------------------- one run at a time, with approvals / cancel / revert
class Busy(Exception):
    pass


class AgentManager:
    def __init__(self):
        self._lock = threading.Lock()
        self.run = None
        self.last = None
        self._approvals = {}

    def begin(self, run):
        with self._lock:
            r = self.run
            if r is not None and not r.finished and (r.started or time.monotonic() - r.created < 30):
                raise Busy("An agent run is already in progress. Stop it first.")
            self.run = run
            self.last = None
        run.hooks = self
        return run

    # the run calls this (in its own thread) and blocks until the user answers
    def wait_approval(self, aid, cancel, timeout=900):
        ev = threading.Event()
        self._approvals[aid] = {"ev": ev, "ok": False}
        t0 = time.monotonic()
        try:
            while not ev.wait(0.3):
                if cancel.is_set() or time.monotonic() - t0 > timeout:
                    return False
            return self._approvals[aid]["ok"]
        finally:
            self._approvals.pop(aid, None)

    def approve(self, aid, ok, always=False):
        a = self._approvals.get(aid)
        if not a:
            return False
        a["ok"] = ("always" if always else True) if ok else False
        a["ev"].set()
        return True

    def cancel(self, rid=None):
        r = self.run
        if r is None or r.finished or (rid and r.id != rid):
            return False
        r.stop()
        return True

    def stream(self, run):
        """Wrap run.events() so the manager remembers the finished run (for Revert) and always frees the slot."""
        try:
            for ev in run.events():
                yield ev
                if ev.get("type") == "done":
                    self.last = run
        finally:
            run.cancel.set()
            if self.last is not run and run.result:
                self.last = run

    def revert(self, rid):
        r = self.last
        if r is None or r.id != rid:
            raise KeyError("That run can no longer be reverted (only the latest finished run can).")
        if self.run is not None and not self.run.finished:
            raise Busy("Wait for the running agent to finish first.")
        files = r.sb.revert_all()
        return files
