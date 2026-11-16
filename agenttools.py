"""Building blocks for CressyAI's agent mode. Standard library only, so it can be tested without a model.

  parse_actions / parse_edits / apply_edits   what the model asked for, and how SEARCH/REPLACE edits are applied
  Sandbox                                      confined file access, command running, snapshots and rollback
  classify_command                             block / ask / ok for a shell command
  syntax_check                                 instant "does this file even parse" check after every write
  diagnose / context_snippets                  turn raw error output into a short, repair-friendly diagnosis
  detect_tests / parse_test_result             find the project's test command(s) and read their results
  compact                                      keep a long agent conversation inside the context window
"""
import ast, difflib, hashlib, json, os, queue, re, shlex, shutil, signal, subprocess, sys, threading, time
import codecs
import tempfile
from html.parser import HTMLParser
from pathlib import Path

SKIP_DIRS = {".git", "node_modules", "__pycache__", ".venv", "venv", ".idea", ".vscode", ".pytest_cache", ".mypy_cache",
             ".ruff_cache", ".tox", "dist", "build", "target", ".next", ".gradle", ".cache", "site-packages"}
BIN_EXT = {".png", ".jpg", ".jpeg", ".gif", ".ico", ".pdf", ".zip", ".gz", ".gguf", ".pyc", ".exe", ".dll", ".so", ".woff",
           ".woff2", ".mp3", ".mp4", ".bin", ".onnx", ".pt", ".safetensors", ".sqlite", ".db", ".webp", ".tar", ".7z"}
NOISE_NAMES = {".coverage", ".DS_Store", "Thumbs.db"}


class SandboxError(Exception):
    pass


# ------------------------------------------------------------------ small text helpers
_ANSI = re.compile(r"\x1b\[[0-9;?]*[ -/]*[@-~]|\x1b\][^\x07]*\x07")


def clean_output(s):
    """Drop colour codes and keep only the last \\r-overwritten segment of each line (progress bars)."""
    s = _ANSI.sub("", str(s)).replace("\r\n", "\n")
    return re.sub(r"[^\n]*\r(?!\n)", "", s)


def head_tail(text, head=1500, tail=2500):
    text = str(text)
    if len(text) <= head + tail + 40:
        return text
    return text[:head] + f"\n... [{len(text) - head - tail} characters omitted] ...\n" + text[-tail:]


def numbered(text, start=1):
    return "\n".join(f"{i:>5}  {ln}" for i, ln in enumerate(str(text).split("\n"), start))


def first_line(text, n=140):
    for ln in str(text).splitlines():
        ln = ln.strip()
        if ln:
            return ln[:n]
    return ""


# ------------------------------------------------------------------ parsing what the model wrote
_OPEN = re.compile(r"^[ \t]{0,3}(`{3,})[ \t]*([^`\n]*?)[ \t]*$")
_CLOSE = re.compile(r"^[ \t]{0,3}(`{3,})[ \t]*$")
RUN_LANGS = {"bash", "sh", "shell", "zsh", "console", "terminal", "powershell", "ps1", "cmd", "bat", "batch"}
_FILE_KEYS = {"file", "write", "create", "new"}
_EDIT_KEYS = {"edit", "patch", "replace"}
_SIMPLE = {"test": "test", "tests": "test", "plan": "plan", "done": "done", "finish": "done", "ls": "ls", "grep": "grep", "read": "read"}


def clean_path(p):
    p = str(p or "").strip().strip("`'\"").replace("\\", "/")
    while p.startswith("./"):
        p = p[2:]
    return p.strip()


def classify_info(info):
    """The text after the opening fence -> (kind, path). kind is None for ordinary code blocks."""
    info = str(info or "").strip()
    low = info.lower()
    if low in RUN_LANGS:
        return "run", None
    m = re.match(r"^([\w+#.-]+)\s*:\s*(.+)$", info)
    if m:
        key, rest = m.group(1).lower(), clean_path(m.group(2))
        if key in _FILE_KEYS and rest:
            return "file", rest
        if key in _EDIT_KEYS and rest:
            return "edit", rest
        if key in ("read", "cat", "view") and rest:
            return "read", rest
        if key == "ls" and rest:
            return "ls", rest
        if key in ("test", "tests"):
            return "test", None
        if rest and " " not in rest and re.search(r"\.\w{1,8}$", rest):    # ```python:app.py  -> a file, like the chat UI does
            return "file", rest
    first = low.split()[0] if low else ""
    if first in _SIMPLE and len(low.split()) == 1:
        return _SIMPLE[first], None
    if first in ("ls", "read", "grep") and len(low.split()) == 2:
        return first, clean_path(low.split()[1])
    return None, None


def parse_actions(text):
    """Split a model reply into prose and action blocks.
    Returns {"prose": str, "actions": [{kind, path, body, closed, info}], "code_blocks": int, "cut": bool}.
    A block that never closed (the reply was cut off) has closed=False; file/edit blocks like that are never applied."""
    lines = str(text).replace("\r\n", "\n").split("\n")
    prose, actions, cur, code_blocks, raw = [], [], None, 0, []

    def finish(closed):
        nonlocal cur, code_blocks
        body = "\n".join(cur["body"])
        if cur["kind"]:
            actions.append({"kind": cur["kind"], "path": cur["path"], "body": body, "closed": closed, "info": cur["info"]})
        else:
            code_blocks += 1
            raw.append({"info": cur["info"], "body": body, "closed": closed, "at": len(prose)})
        cur = None

    for ln in lines:
        if cur is None:
            m = _OPEN.match(ln)
            if m:
                kind, path = classify_info(m.group(2))
                cur = {"fence": len(m.group(1)), "kind": kind, "path": path, "info": m.group(2), "body": []}
            else:
                prose.append(ln)
        else:
            m = _CLOSE.match(ln)
            if m and len(m.group(1)) >= cur["fence"]:
                finish(True)
            else:
                cur["body"].append(ln)
    cut = cur is not None
    if cut:
        finish(False)
    return {"prose": "\n".join(prose).strip(), "actions": actions, "code_blocks": code_blocks, "cut": cut, "raw_blocks": raw, "prose_lines": prose}


_LANG_EXT = {"python": ".py", "py": ".py", "html": ".html", "javascript": ".js", "js": ".js", "css": ".css", "c": ".c", "cpp": ".cpp",
             "c++": ".cpp", "go": ".go", "rust": ".rs", "ruby": ".rb", "php": ".php", "lua": ".lua", "java": ".java", "typescript": ".ts", "ts": ".ts"}
_LANG_DEFAULT = {".py": "main.py", ".html": "index.html", ".js": "script.js", ".css": "style.css", ".c": "main.c", ".cpp": "main.cpp", ".go": "main.go",
                 ".rs": "main.rs", ".rb": "main.rb", ".php": "index.php", ".lua": "main.lua", ".ts": "main.ts"}
_FIRST_LINE_NAME = re.compile(r"^\s*(?:#|//|--|<!--|/\*)\s*(?:file(?:name)?\s*:?\s*)?([\w./-]+\.[A-Za-z0-9]{1,5})\s*(?:-->|\*/)?\s*$", re.I)


def salvage_blocks(parsed, taken=()):
    """Small local models often forget the `file:PATH` label and send a bare ```python block. Instead of bouncing the reply and wasting a
    step, work out a sensible file name (first-line comment, a name mentioned in the prose, or a default for the language) and treat the
    block as a file block. Returns a list of file actions (empty when nothing can be salvaged). Shell / data / unknown blocks are skipped."""
    prose_lines = parsed.get("prose_lines") or []
    mentioned = re.findall(r"`?([\w./-]+\.(?:py|html|js|css|c|cpp|go|rs|rb|php|lua|java|ts))`?\b", "\n".join(prose_lines))
    used, out = set(n.lower() for n in taken), []
    for blk in parsed.get("raw_blocks") or []:
        if not blk["closed"] or not blk["body"].strip():
            continue
        lang = (blk["info"] or "").strip().split()[0].lower() if (blk["info"] or "").strip() else ""
        ext = _LANG_EXT.get(lang)
        if not ext:
            continue
        first = blk["body"].lstrip("\n").split("\n", 1)[0]
        m = _FIRST_LINE_NAME.match(first)
        name = m.group(1) if m and m.group(1).lower().endswith(ext) else None
        if not name:
            name = next((n for n in mentioned if n.lower().endswith(ext) and n.lower() not in used), None)
        if not name and ext == ".java":
            c = re.search(r"public\s+(?:final\s+)?class\s+(\w+)", blk["body"])
            name = (c.group(1) if c else "Main") + ".java"
        name = name or _LANG_DEFAULT.get(ext)
        if not name or name.lower() in used:
            continue
        used.add(name.lower())
        out.append({"kind": "file", "path": clean_path(name), "body": blk["body"], "closed": True, "info": "salvaged"})
    return out


def stop_after(actions):
    """True once the reply contains a finished block after which the model must wait (a command, tests, or done)."""
    return any(a["closed"] and a["kind"] in ("run", "test", "done") for a in actions)


def cut_after_stop(text):
    """The reply up to and including the closing fence of its first finished command / test / done block."""
    pos, cur = 0, None
    for ln in text.split("\n"):
        end = pos + len(ln) + 1
        if cur is None:
            m = _OPEN.match(ln)
            if m:
                cur = {"fence": len(m.group(1)), "kind": classify_info(m.group(2))[0]}
        else:
            m = _CLOSE.match(ln)
            if m and len(m.group(1)) >= cur["fence"]:
                if cur["kind"] in ("run", "test", "done"):
                    return text[:min(end, len(text))]
                cur = None
        pos = end
    return text


def parse_edits(text):
    L = str(text).replace("\r\n", "\n").split("\n")
    edits, st, a, b, seen = [], 0, [], [], False
    for ln in L:
        if st == 0:
            if re.match(r"^<{5,9}\s*SEARCH\s*$", ln):
                st, a, b, seen = 1, [], [], True
        elif st == 1:
            if re.match(r"^={5,9}\s*$", ln):
                st = 2
            else:
                a.append(ln)
        elif re.match(r"^>{5,9}\s*REPLACE\s*$", ln):
            edits.append({"search": "\n".join(a), "replace": "\n".join(b)})
            st = 0
        else:
            b.append(ln)
    return {"edits": edits, "complete": st == 0, "seen": seen}


def _find_all(text, needle):
    r, i = [], -1
    while True:
        i = text.find(needle, i + 1)
        if i < 0:
            break
        r.append(i)
        if len(r) > 1:
            break
    return r


def closest_lines(text, search, min_ratio=0.55):
    """The stretch of `text` that looks most like `search` (ignoring indentation); used to repair a failed SEARCH."""
    tl = text.split("\n")
    sl = [x.strip() for x in search.split("\n")]
    while sl and not sl[0]:
        sl.pop(0)
    while sl and not sl[-1]:
        sl.pop()
    if not sl or not tl or len(tl) > 20000:
        return None
    k = len(sl)
    norm = [x.strip() for x in tl]
    target = "\n".join(sl)
    best = (0.0, 0)
    for i in range(0, max(1, len(tl) - k + 1)):
        cand = "\n".join(norm[i:i + k])
        sm = difflib.SequenceMatcher(None, cand, target, autojunk=False)
        if sm.real_quick_ratio() < best[0] or sm.quick_ratio() < max(best[0], min_ratio):
            continue
        r = sm.ratio()
        if r > best[0]:
            best = (r, i)
    if best[0] < min_ratio:
        return None
    i = best[1]
    return {"start": i + 1, "end": min(len(tl), i + k), "ratio": round(best[0], 2), "text": "\n".join(tl[i:i + k])}


def apply_edits(base, edits):
    """Apply SEARCH/REPLACE edits (same rules as the chat UI). Returns {text, applied, already, errors:[{n,msg,hint?}]}."""
    text, applied, already, errors = base, 0, 0, []
    for n, e in enumerate(edits, 1):
        s, r, tag = e["search"], e["replace"], f"Edit {n}"
        if not s.strip():
            errors.append({"n": n, "msg": f"{tag}: the SEARCH part is empty"})
            continue
        hits = _find_all(text, s)
        if len(hits) == 1:
            text = text[:hits[0]] + r + text[hits[0] + len(s):]
            applied += 1
            continue
        if len(hits) > 1:
            errors.append({"n": n, "msg": f"{tag}: the SEARCH text matches more than one place; add surrounding lines so it is unique"})
            continue
        tl = text.split("\n")
        sl = s.split("\n")
        for trial in (sl, _trim_blank(sl)):
            found = []
            for i in range(0, len(tl) - len(trial) + 1):
                if trial and all(tl[i + j].strip() == trial[j].strip() for j in range(len(trial))):
                    found.append(i)
                    if len(found) > 1:
                        break
            if len(found) == 1:
                tl[found[0]:found[0] + len(trial)] = ([] if r == "" else r.split("\n"))
                text = "\n".join(tl)
                applied += 1
                break
            if len(found) > 1:
                errors.append({"n": n, "msg": f"{tag}: the SEARCH text matches more than one place"})
                break
        else:
            if r.strip() and r in text:
                already += 1
                continue
            err = {"n": n, "msg": f"{tag}: could not find the SEARCH text starting with: {first_line(s, 70)!r}"}
            c = closest_lines(text, s)
            if c:
                err["hint"] = f"The closest text in the file is lines {c['start']}-{c['end']} (similarity {c['ratio']}):\n{c['text']}"
            errors.append(err)
            continue
    return {"text": text, "applied": applied, "already": already, "errors": errors}


def _trim_blank(lines):
    a, b = 0, len(lines)
    while a < b and not lines[a].strip():
        a += 1
    while b > a and not lines[b - 1].strip():
        b -= 1
    return lines[a:b]


def parse_plan(body):
    """A plan block -> [{text, done}]"""
    items = []
    for ln in str(body).split("\n"):
        m = re.match(r"^\s*(?:[-*+]|\d+[.)])\s+(?:\[([ xX])\]\s*)?(.+?)\s*$", ln)
        if m:
            items.append({"text": m.group(2).replace("**", "").strip()[:200], "done": (m.group(1) or " ").lower() == "x"})
    return items[:20]


def line_stats(a, b):
    """(added, removed) line counts between two texts."""
    A, B = (a or "").split("\n"), (b or "").split("\n")
    s = 0
    while s < len(A) and s < len(B) and A[s] == B[s]:
        s += 1
    ea, eb = len(A), len(B)
    while ea > s and eb > s and A[ea - 1] == B[eb - 1]:
        ea -= 1
        eb -= 1
    X, Y = A[s:ea], B[s:eb]
    if not X or not Y or len(X) * len(Y) > 4_000_000:
        return len(Y), len(X)
    add = rem = 0
    for tag, i1, i2, j1, j2 in difflib.SequenceMatcher(None, X, Y, autojunk=False).get_opcodes():
        if tag in ("replace", "delete"):
            rem += i2 - i1
        if tag in ("replace", "insert"):
            add += j2 - j1
    return add, rem


# ------------------------------------------------------------------ command guard
def _segments(cmd):
    return [s.strip() for s in re.split(r"\s*(?:;|&&|\|\||\||&|\n)\s*", cmd) if s.strip()]


def _tokens(seg):
    try:
        return shlex.split(seg, posix=(os.name != "nt"))
    except ValueError:
        return seg.split()


_DANGER_TARGET = re.compile(r"^(?:/|~|\$home|%userprofile%|[a-z]:[\\/]?$|\*|\.|\.\.|\./?\*?|\.\./?.*|/.*|~/.*|\$home/.*|[a-z]:[\\/].*|\\)$", re.I)

_BLOCK = [(re.compile(p, re.I), why) for p, why in [
    (r"\bmkfs(\.\w+)?\b", "formats a disk"),
    (r"\bdd\b[^\n]*\bof=/dev/", "writes straight to a disk device"),
    (r"\bdiskpart\b", "partitions disks"),
    (r"\bformat\s+[a-z]:", "formats a drive"),
    (r"\b(shutdown|reboot|poweroff)\b", "shuts the computer down"),
    (r"\binit\s+[06]\b", "shuts the computer down"),
    (r":\(\)\s*\{[^}]*\};\s*:", "is a fork bomb"),
    (r"\b(sudo|doas|runas)\b", "asks for administrator rights"),
    (r"\bgit\s+push\b", "publishes commits to a remote"),
    (r"\b(curl|wget|iwr|invoke-webrequest)\b[^|;\n]*\|\s*(sudo\s+)?(ba|z|da)?sh\b", "pipes a download into a shell"),
    (r"\|\s*(iex|invoke-expression)\b", "runs downloaded code"),
    (r"\bchmod\s+(-\w+\s+)*[0-7]*777\s+/", "opens permissions on system folders"),
    (r">\s*/(etc|dev|boot|sys|proc|usr|bin|sbin|lib)\b", "writes into a system folder"),
]]
_ASK = [(re.compile(p, re.I), why) for p, why in [
    (r"\b(pip3?|pipx|poetry|uv|conda|mamba)\s+(install|uninstall|add|remove)\b", "installs or removes packages"),
    (r"\bpython3?(\.exe)?\s+-m\s+pip\s+(install|uninstall)\b", "installs or removes packages"),
    (r"\b(npm|pnpm|yarn|bun)\s+(install|i|add|remove|uninstall|ci|update|upgrade|link)\b", "installs or removes packages"),
    (r"\b(apt|apt-get|yum|dnf|brew|choco|winget|scoop|snap|pacman)\s+(install|remove|uninstall|upgrade)\b", "installs software"),
    (r"\b(cargo|go|gem|composer|dotnet)\s+(install|add|get)\b", "installs packages"),
    (r"\bgit\s+(commit|add|reset|checkout|switch|clean|rebase|merge|stash|restore|rm|mv|tag|pull|fetch|clone|cherry-pick|revert|branch\s+-d)\b", "changes git state"),
    (r"\b(curl|wget|iwr|invoke-webrequest|ssh|scp|sftp|ftp|nc|ncat|telnet)\b", "uses the network"),
    (r"\b(chmod|chown|chgrp|icacls|takeown)\b", "changes permissions"),
    (r"\b(docker|podman|kubectl|helm|terraform)\b", "controls containers or infrastructure"),
    (r"\b(kill|killall|pkill|taskkill|stop-process)\b", "stops processes"),
    (r"\b(reg\s+(add|delete)|netsh|schtasks|crontab)\b", "changes system settings"),
    (r">>?\s*(~|/|[a-z]:[\\/]|\.\./)", "writes outside the project folder"),
    (r"\bcd\s+(\.\.|/|~|[a-z]:)", "leaves the project folder"),
]]


_PIP_INSTALL = re.compile(r'^\s*(?P<pre>(?:(?:"[^"]+"|\S+)\s+(?:-3\s+)?-m\s+)?pip3?(?:\.exe)?)\s+install\s+(?P<rest>[^;&|<>]*?)\s*$', re.I)
_NPM_INSTALL = re.compile(r"^\s*(?:npm|pnpm|yarn|bun)(?:\.cmd)?\s+(?:install|i|add)\s+(?P<rest>[^;&|<>]*?)\s*$", re.I)
_NO_SKIP_FLAGS = {"-u", "--upgrade", "-r", "--requirement", "-e", "--editable", "--force-reinstall", "-t", "--target", "-i", "--index-url", "--user",
                  "-g", "--global", "--no-deps", "-c", "--constraint", "--pre"}


def already_installed(sb, cmd, timeout=25):
    """For `pip install X` / `npm install X`: when every named package is already installed here, a message saying so (the install is then
    neither asked about nor run); otherwise None. Anything fancier (upgrades, requirements files, paths, urls, global installs) is left alone."""
    cmd = str(cmd or "").strip()
    m = _PIP_INSTALL.match(cmd)
    if m:
        try:
            toks = shlex.split(m.group("rest"), posix=(os.name != "nt"))
        except ValueError:
            return None
        if not toks or any(t.lower() in _NO_SKIP_FLAGS or t.startswith("-") for t in toks):
            return None
        found = []
        for tok in toks:
            tok = tok.strip("'\"")
            if re.search(r"[/\\:@]|^\.", tok) or tok.endswith((".whl", ".zip", ".gz")):
                return None
            name, spec = re.match(r"^([A-Za-z0-9][A-Za-z0-9._-]*)(?:\[[^\]]*\])?(.*)$", tok).groups() if re.match(r"^[A-Za-z0-9]", tok) else (None, "")
            if not name:
                return None
            try:
                r = subprocess.run(f'{m.group("pre")} show {name}', shell=True, cwd=str(sb.root), capture_output=True, text=True, timeout=timeout,
                                   stdin=subprocess.DEVNULL)
            except (OSError, subprocess.TimeoutExpired):
                return None
            ver = re.search(r"^Version:\s*(\S+)", r.stdout or "", re.M)
            if r.returncode != 0 or not ver:
                return None
            spec = spec.strip()
            if spec and not (spec.startswith("==") and spec[2:].strip() == ver.group(1)):
                return None
            found.append(f"{name} {ver.group(1)}")
        return (f"[not run] Already installed here: {', '.join(found)}. Do NOT install it again; import it and use it. "
                "If the import still fails, the problem is somewhere else (a typo in the module name, or the wrong Python).")
    m = _NPM_INSTALL.match(cmd)
    if m:
        toks = m.group("rest").split()
        if not toks or any(t.startswith("-") and t.lower() in _NO_SKIP_FLAGS for t in toks):
            return None
        found = []
        for tok in toks:
            if tok.startswith("-"):
                continue
            name = re.sub(r"(?<=.)@[^/@]*$", "", tok)           # drop a trailing @version, keep a leading @scope
            if re.search(r"[:\\]|^\.", name):
                return None
            pj = Path(sb.root) / "node_modules" / name / "package.json"
            if not pj.is_file():
                return None
            try:
                found.append(f"{name} {json.loads(pj.read_text(encoding='utf-8')).get('version', '')}".strip())
            except (OSError, ValueError):
                return None
        if found:
            return f"[not run] Already installed in this project: {', '.join(found)}. Do NOT install it again; require/import it and use it."
    return None


def classify_command(cmd):
    """-> ("block" | "ask" | "ok", reason). block never runs; ask needs the user's approval; ok runs."""
    cmd = str(cmd or "")
    flat = " ".join(cmd.split())
    for rx, why in _BLOCK:
        if rx.search(flat):
            return "block", why
    asked = None
    for seg in _segments(cmd):
        tok = _tokens(seg)
        if not tok:
            continue
        name = os.path.basename(tok[0].replace("\\", "/")).lower()
        flags = [t for t in tok[1:] if t.startswith("-") or (os.name == "nt" or name in ("rd", "rmdir", "del", "erase")) and re.match(r"^/[a-z]$", t, re.I)]
        targets = [t for t in tok[1:] if t not in flags and not t.startswith("-")]
        if name == "rm":
            rec = any(re.match(r"^-[a-z]*r[a-z]*$", f, re.I) for f in flags if not f.startswith("--")) or "--recursive" in flags
            if rec and any(_DANGER_TARGET.match(t.strip("'\"")) for t in targets):
                return "block", "deletes the project folder, a parent folder or something outside the project"
            asked = asked or "deletes files"
        elif name in ("rd", "rmdir", "del", "erase", "remove-item", "ri", "unlink", "shred"):
            rec = any(f.lower() in ("/s", "-recurse", "-r") for f in flags) or "-recurse" in [t.lower() for t in tok]
            if rec and any(_DANGER_TARGET.match(t.strip("'\"")) for t in targets):
                return "block", "deletes the project folder, a parent folder or something outside the project"
            asked = asked or "deletes files"
    for rx, why in _ASK:
        if rx.search(flat):
            return "ask", why
    if asked:
        return "ask", asked
    return "ok", ""


# ------------------------------------------------------------------ sandbox
def kill_tree(proc):
    try:
        if os.name == "nt":
            subprocess.run(["taskkill", "/F", "/T", "/PID", str(proc.pid)], capture_output=True)
        else:
            os.killpg(proc.pid, signal.SIGKILL)
    except Exception:
        try:
            proc.kill()
        except Exception:
            pass


class Sandbox:
    """Everything the agent may touch lives under one folder. It keeps the original of every file it changes,
    so a failed attempt can be rolled back and the whole run can be undone."""

    def __init__(self, root, can_write=True, can_run=True, max_write=2_000_000, max_out=200_000):
        self.root = Path(root).expanduser().resolve()
        self.can_write, self.can_run, self.max_write, self.max_out = can_write, can_run, max_write, max_out
        self.orig = {}      # rel path -> bytes or None (did not exist): state before the agent first touched it
        self.cp = {}        # same, but since the start of the current attempt
        self.lock = threading.Lock()

    # -- paths
    def rel(self, p):
        p = Path(p)
        return "" if p == self.root else p.relative_to(self.root).as_posix()

    def resolve(self, rel):
        rel = str(rel or "").strip().replace("\\", "/")
        if rel in ("", "."):
            return self.root
        p = (self.root / rel).resolve()
        if p != self.root and not p.is_relative_to(self.root):
            raise SandboxError(f"{rel}: that path is outside the project folder")
        return p

    def to_rel(self, raw):
        """A path seen in an error message -> project-relative path, or None when it is not a project file."""
        raw = str(raw or "").strip().strip("'\"")
        if not raw or raw.startswith("<"):
            return None
        try:
            p = Path(raw)
            if not p.is_absolute():
                p = self.root / raw
            p = p.resolve()
            if p.is_file() and p.is_relative_to(self.root) and not (set(p.relative_to(self.root).parts) & SKIP_DIRS):
                return self.rel(p)
        except (OSError, ValueError):
            pass
        return None

    # -- reading
    def read_text(self, rel, limit=200_000):
        p = self.resolve(rel)
        if p.is_dir():
            raise SandboxError(f"{rel} is a folder, not a file")
        if not p.exists():
            raise SandboxError(f"{rel}: no such file")
        if p.suffix.lower() in BIN_EXT:
            raise SandboxError(f"{rel}: binary file")
        try:
            data = p.read_bytes()
        except OSError as e:
            raise SandboxError(f"{rel}: {e.strerror or e}")
        if b"\0" in data[:4096]:
            raise SandboxError(f"{rel}: binary file")
        truncated = len(data) > limit
        try:
            text = data[:limit].decode("utf-8") if not truncated else data[:limit].decode("utf-8", errors="ignore")
        except UnicodeDecodeError:
            raise SandboxError(f"{rel}: not valid UTF-8")
        crlf = "\r\n" in text
        return {"text": text.replace("\r\n", "\n"), "crlf": crlf, "truncated": truncated}

    def exists(self, rel):
        try:
            return self.resolve(rel).is_file()
        except SandboxError:
            return False

    def list_dir(self, rel="", limit=150):
        p = self.resolve(rel)
        if not p.is_dir():
            raise SandboxError(f"{rel or '.'} is not a folder")
        rows = []
        for c in sorted(p.iterdir(), key=lambda x: (not x.is_dir(), x.name.lower())):
            if c.name in SKIP_DIRS:
                continue
            rows.append(c.name + "/" if c.is_dir() else f"{c.name}  ({_size(c)})")
            if len(rows) >= limit:
                rows.append("... (more omitted)")
                break
        return rows

    def tree(self, depth=2, limit=90):
        lines = []

        def walk(d, level):
            try:
                kids = sorted(d.iterdir(), key=lambda x: (not x.is_dir(), x.name.lower()))
            except OSError:
                return
            for c in kids:
                if len(lines) >= limit:
                    return
                if c.name in SKIP_DIRS or c.name in NOISE_NAMES:
                    continue
                lines.append("  " * level + c.name + ("/" if c.is_dir() else ""))
                if c.is_dir() and level + 1 < depth:
                    walk(c, level + 1)

        walk(self.root, 0)
        if len(lines) >= limit:
            lines.append("... (more files not shown; use ls or grep)")
        return lines

    def iter_text_files(self, base=None, max_files=6000, max_size=1_000_000):
        base = base or self.root
        n = 0
        for dp, dn, fn in os.walk(base):
            dn[:] = [d for d in dn if d not in SKIP_DIRS]
            for f in fn:
                if f in NOISE_NAMES or Path(f).suffix.lower() in BIN_EXT:
                    continue
                p = Path(dp) / f
                try:
                    if p.stat().st_size > max_size:
                        continue
                except OSError:
                    continue
                n += 1
                if n > max_files:
                    return
                yield p

    def grep(self, pattern, rel="", limit=40):
        base = self.resolve(rel)
        pat = str(pattern).strip()
        if not pat:
            raise SandboxError("grep needs a pattern")
        flags = 0 if re.search(r"[A-Z]", pat) else re.I
        try:
            rx = re.compile(pat, flags)
        except re.error:
            rx = re.compile(re.escape(pat), flags)
        hits, files = [], ([base] if base.is_file() else self.iter_text_files(base))
        for p in files:
            try:
                data = p.read_bytes()
            except OSError:
                continue
            if b"\0" in data[:4096]:
                continue
            for i, ln in enumerate(data.decode("utf-8", errors="replace").splitlines(), 1):
                if rx.search(ln):
                    hits.append(f"{self.rel(p)}:{i}: {ln.strip()[:200]}")
                    if len(hits) >= limit:
                        return hits + ["... (more matches omitted; narrow the pattern or the folder)"]
        return hits

    # -- writing and undo
    def touch(self, rel, before):
        with self.lock:
            self.orig.setdefault(rel, before)
            self.cp.setdefault(rel, before)

    def write(self, rel, text):
        if not self.can_write:
            raise SandboxError("Writing files is disabled on this server")
        p = self.resolve(rel)
        if p == self.root or p.is_dir():
            raise SandboxError(f"{rel} is a folder, not a file")
        if ".git" in p.relative_to(self.root).parts:
            raise SandboxError("Writing inside .git is not allowed")
        data = text.encode("utf-8")
        if len(data) > self.max_write:
            raise SandboxError(f"{rel}: file is too large to write")
        before = None
        if p.exists():
            before = p.read_bytes()
            if b"\0" in before[:4096]:
                raise SandboxError(f"{rel}: refusing to overwrite a binary file")
            if b"\r\n" in before and b"\r\n" not in data:
                data = data.replace(b"\n", b"\r\n")
        self.touch(self.rel(p), before)
        try:
            p.parent.mkdir(parents=True, exist_ok=True)
            tmp = p.with_name(p.name + ".cressy-tmp")
            tmp.write_bytes(data)
            os.replace(tmp, p)
        except OSError as e:
            raise SandboxError(f"{rel}: could not write ({e.strerror or e})")
        if p.suffix == ".py":       # a same-second, same-size edit would otherwise run the old bytecode
            for pyc in (p.parent / "__pycache__").glob(p.stem + ".*.pyc"):
                try:
                    pyc.unlink()
                except OSError:
                    pass
        return {"path": self.rel(p), "created": before is None, "bytes": len(data),
                "before": None if before is None else before.decode("utf-8", errors="replace").replace("\r\n", "\n"),
                "after": text}

    def _restore(self, rel, data):
        p = self.root / rel
        if data is None:
            try:
                p.unlink()
            except FileNotFoundError:
                pass
            except OSError:
                return False
        else:
            try:
                p.parent.mkdir(parents=True, exist_ok=True)
                tmp = p.with_name(p.name + ".cressy-tmp")
                tmp.write_bytes(data)
                os.replace(tmp, p)
            except OSError:
                return False
        return True

    def new_attempt(self):
        with self.lock:
            self.cp = {}

    def rollback_attempt(self, only=None):
        """Undo this attempt's changes (only the given files when they were touched this attempt, else all of them)."""
        with self.lock:
            pick = [r for r in self.cp if (not only) or r in only]
            if only and not pick:
                pick = list(self.cp)
            done = []
            for r in pick:
                if self._restore(r, self.cp[r]):
                    done.append(r)
                    del self.cp[r]
            return sorted(done)

    def revert_all(self):
        with self.lock:
            done = [r for r, data in list(self.orig.items()) if self._restore(r, data)]
            for r in done:
                self.orig.pop(r, None)
            self.cp = {}
            return sorted(done)

    def current_bytes(self, rel):
        try:
            return (self.root / rel).read_bytes()
        except OSError:
            return None

    def changes(self):
        """[{path, created, deleted, added, removed, before, after}] for every file that now differs from its original."""
        out = []
        for rel, before in sorted(self.orig.items()):
            now = self.current_bytes(rel)
            if now == before:
                continue
            b = None if before is None else before.decode("utf-8", errors="replace").replace("\r\n", "\n")
            a = None if now is None else now.decode("utf-8", errors="replace").replace("\r\n", "\n")
            add, rem = line_stats(b or "", a or "")
            out.append({"path": rel, "created": before is None, "deleted": now is None, "added": add, "removed": rem,
                        "before": b, "after": a, "crlf": bool(before and b"\r\n" in before)})
        return out

    # -- running commands
    def scan(self, with_bytes):
        res, total = {}, 0
        for p in self.iter_text_files():
            try:
                st = p.stat()
            except OSError:
                continue
            data = None
            if with_bytes and st.st_size <= 200_000 and total < 25_000_000:
                try:
                    data = p.read_bytes()
                    total += len(data)
                except OSError:
                    data = None
            res[self.rel(p)] = (st.st_mtime_ns, st.st_size, data)
        return res

    def run_iter(self, cmd, timeout=60, cancel=None, track=True):
        """Run a shell command in the project folder. Yields ("out", text) while it runs, then ("done", result).
        With track=False (test runs) files the command touches are not recorded as the agent's changes."""
        t0 = time.monotonic()
        if not self.can_run:
            yield ("done", {"code": None, "out": "", "timeout": False, "killed": False, "truncated": False, "seconds": 0,
                            "error": "Running commands is disabled on this server (--no-terminal)", "changed": []})
            return
        before = self.scan(True) if track else {}
        env = {**os.environ, "PYTHONUNBUFFERED": "1", "NO_COLOR": "1", "TERM": "dumb", "GIT_PAGER": "cat", "PAGER": "cat", "CI": "1",
               "PYTHONDONTWRITEBYTECODE": "1"}
        kw = dict(stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, cwd=str(self.root), env=env,
                  bufsize=0, shell=True)
        if os.name == "nt":
            kw["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP
        else:
            kw["start_new_session"] = True
        try:
            proc = subprocess.Popen(cmd, **kw)
        except Exception as e:
            yield ("done", {"code": None, "out": "", "timeout": False, "killed": False, "truncated": False, "seconds": 0,
                            "error": str(e), "changed": []})
            return
        q = queue.Queue()

        def pump():
            try:
                while True:
                    chunk = proc.stdout.read(4096)
                    if not chunk:
                        break
                    q.put(chunk)
            except Exception:
                pass
            finally:
                q.put(None)

        threading.Thread(target=pump, daemon=True).start()
        dec = codecs.getincrementaldecoder("utf-8")(errors="replace")
        parts, sent, flags, kill_at = [], 0, {"timeout": False, "killed": False, "truncated": False}, None
        try:
            while True:
                if kill_at is None:
                    if cancel is not None and cancel.is_set():
                        flags["killed"] = True
                    elif time.monotonic() - t0 > timeout:
                        flags["timeout"] = True
                    if flags["killed"] or flags["timeout"]:
                        kill_tree(proc)
                        kill_at = time.monotonic()
                elif time.monotonic() - kill_at > 3:
                    break
                try:
                    chunk = q.get(timeout=0.15)
                except queue.Empty:
                    continue
                if chunk is None:
                    break
                text = dec.decode(chunk)
                if sent + len(text) > self.max_out:
                    text, flags["truncated"] = text[:max(0, self.max_out - sent)], True
                if text:
                    sent += len(text)
                    parts.append(text)
                    yield ("out", text)
                if flags["truncated"] and kill_at is None:
                    kill_tree(proc)
                    kill_at = time.monotonic()
        finally:
            if proc.poll() is None:
                kill_tree(proc)
            try:
                code = proc.wait(timeout=5)
            except Exception:
                code = None
        after = self.scan(False) if track else {}
        changed = []
        for rel in sorted(set(before) | set(after)):
            b, a = before.get(rel), after.get(rel)
            if (b and a and b[:2] == a[:2]) or (b is None and a is None):
                continue
            if b is not None and b[2] is None and a is not None:
                continue            # too big to keep a copy of: cannot be undone, so it is not tracked
            self.touch(rel, b[2] if b else None)
            changed.append(rel)
        yield ("done", {"code": code, "out": clean_output("".join(parts)), "timeout": flags["timeout"], "killed": flags["killed"],
                        "truncated": flags["truncated"], "seconds": round(time.monotonic() - t0, 2), "error": None,
                        "changed": changed})

    def run(self, cmd, timeout=60, cancel=None, track=True):
        res = None
        for kind, val in self.run_iter(cmd, timeout, cancel, track):
            if kind == "done":
                res = val
        return res


def _size(p):
    try:
        n = p.stat().st_size
    except OSError:
        return "?"
    return f"{n} B" if n < 1024 else f"{n / 1024:.1f} KB" if n < 1048576 else f"{n / 1048576:.1f} MB"


# ------------------------------------------------------------------ syntax checks (instant error detection)
def _which(name):
    return shutil.which(name)


class _PageScan(HTMLParser):
    """Collects what a web page loads: scripts (src or inline text) and stylesheets."""
    def __init__(self):
        super().__init__(convert_charrefs=False)
        self.refs, self.scripts, self._open, self._buf = [], [], None, []

    def handle_starttag(self, tag, attrs):
        a = {k: (v or "") for k, v in attrs}
        if tag == "script":
            if a.get("src"):
                self.refs.append(("script", a["src"]))
            else:
                self._open, self._buf = a, []
        elif tag == "link" and a.get("href") and "stylesheet" in a.get("rel", "").lower():
            self.refs.append(("stylesheet", a["href"]))

    def handle_data(self, data):
        if self._open is not None:
            self._buf.append(data)

    def handle_endtag(self, tag):
        if tag == "script" and self._open is not None:
            self.scripts.append((self._open, "".join(self._buf)))
            self._open = None


_REMOTE = re.compile(r"^(?:[a-z][a-z0-9+.-]*:|//|#)", re.I)
_JS_TYPES = ("", "text/javascript", "application/javascript", "module")


def web_check(sb, rel, text, strict=False):
    """A web page cannot be run here, but most of what breaks one can be found without running it: JavaScript that does not parse
    (inline <script> blocks, checked with node when it is installed) and, when strict, local scripts / stylesheets the page loads
    that do not exist. Strict is used by the final check only, because a page is often written before the files it loads."""
    scan = _PageScan()
    try:
        scan.feed(text)
        scan.close()
    except Exception:
        return None
    problems = []
    node = _which("node")
    for n, (attrs, code) in enumerate((x for x in scan.scripts if x[0].get("type", "").strip().lower() in _JS_TYPES), 1):
        if not node or not code.strip():
            continue
        module = attrs.get("type", "").strip().lower() == "module"
        fd, tmp = tempfile.mkstemp(suffix=".mjs" if module else ".js")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                f.write(code)
            r = subprocess.run([node, "--check", tmp], capture_output=True, text=True, timeout=15)
            if r.returncode != 0:
                err = [x for x in (r.stderr or r.stdout).replace(tmp, f"{rel} <script #{n}>").strip().splitlines() if x.strip()][:5]
                problems.append(f"{rel}: JavaScript syntax error in inline <script> #{n}\n" + "\n".join(err))
        except (subprocess.TimeoutExpired, OSError):
            pass
        finally:
            try:
                os.unlink(tmp)
            except OSError:
                pass
    if strict:
        base = Path(rel).parent
        for kind, url in scan.refs:
            if _REMOTE.match(url.strip()):
                continue
            target = url.strip().split("#")[0].split("?")[0]
            if not target:
                continue
            path = Path(target.lstrip("/")) if target.startswith("/") else base / target
            try:
                if not sb.exists(str(path).replace("\\", "/")):
                    problems.append(f"{rel}: loads {kind} '{url}' but that file does not exist in the project")
            except (SandboxError, OSError):
                pass
    return "\n".join(problems) if problems else None


def syntax_check(sb, rel, text, strict=False):
    """Check that a freshly written file parses. Returns None when fine or unknown, else a short message with the line."""
    ext = Path(rel).suffix.lower()
    try:
        if ext == ".py":
            try:
                ast.parse(text, rel)
            except SyntaxError as e:
                ln = e.lineno or 0
                src = (e.text or "").rstrip("\n")
                if not src and ln:
                    lines = text.split("\n")
                    src = lines[ln - 1] if 0 < ln <= len(lines) else ""
                msg = f"{rel}: line {ln}: {e.__class__.__name__}: {e.msg}"
                if src:
                    msg += f"\n    {src.strip()[:160]}"
                return msg
            except (ValueError, RecursionError) as e:
                return f"{rel}: {e}"
        elif ext == ".json":
            try:
                json.loads(text)
            except ValueError as e:
                return f"{rel}: invalid JSON: {e}"
        elif ext == ".toml":
            try:
                import tomllib
                tomllib.loads(text)
            except ImportError:
                return None
            except ValueError as e:
                return f"{rel}: invalid TOML: {e}"
        elif ext in (".js", ".mjs", ".cjs") and _which("node"):
            r = subprocess.run(["node", "--check", str(sb.resolve(rel))], capture_output=True, text=True, timeout=15)
            if r.returncode != 0:
                err = (r.stderr or r.stdout).strip().splitlines()
                keep = [x for x in err if x.strip()][:6]
                return f"{rel}: JavaScript syntax error\n" + "\n".join(keep)
        elif ext in (".html", ".htm"):
            return web_check(sb, rel, text, strict)
        elif ext == ".sh" and _which("bash"):
            r = subprocess.run(["bash", "-n", str(sb.resolve(rel))], capture_output=True, text=True, timeout=15)
            if r.returncode != 0:
                return f"{rel}: shell syntax error: {first_line(r.stderr, 200)}"
    except (subprocess.TimeoutExpired, OSError, SandboxError):
        return None
    return None


# ------------------------------------------------------------------ error diagnosis
_PY_FRAME = re.compile(r'^\s*File "([^"]+)", line (\d+)(?:, in (.+))?\s*$', re.M)
_PY_EXC = re.compile(r"^((?:[A-Za-z_][\w.]*\.)?(?:[A-Z]\w*(?:Error|Exception|Exit|Interrupt|Warning)|AssertionError)):?[ \t]*(.*)$", re.M)
_NODE_FRAME = re.compile(r"\bat (?:.*? \()?((?:[A-Za-z]:)?[^\s():]+?\.[cm]?[jt]sx?):(\d+):(\d+)\)?")
_NODE_HEAD = re.compile(r"^((?:[A-Za-z]:)?[^\s:]+\.[cm]?[jt]sx?):(\d+)\s*$", re.M)
_NODE_ERR = re.compile(r"^((?:Uncaught )?(?:Syntax|Reference|Type|Range|Eval|URI|Assertion)?Error[\w\[\] ]*): (.*)$", re.M)
_COMPILER = re.compile(r"^((?:[A-Za-z]:)?[^\s:()]+\.\w{1,5})[:(](\d+)(?:[:,](\d+))?\)?:? *(?:fatal )?(?:error|Error)\b[^:]*:? *(.*)$", re.M)
_RUST = re.compile(r"^error(?:\[E\d+\])?: (.*)\n\s+--> ([^\s:]+):(\d+):(\d+)", re.M)
_MISSING_CMD = re.compile(r"(command not found|is not recognized as an internal or external command|The term '[^']+' is not recognized|No such file or directory: '[^']+'|: not found$)", re.M)


def _norm_headline(s):
    s = re.sub(r"0x[0-9a-fA-F]+", "#", s.lower())
    s = re.sub(r"\d+", "#", s)
    return re.sub(r"\s+", " ", s).strip()[:160]


def py_related(sb, rel, limit=3):
    """Project modules imported by a Python file (for a failing test: where the code under test probably lives)."""
    try:
        tree = ast.parse(sb.read_text(rel)["text"])
    except (SandboxError, SyntaxError, ValueError):
        return []
    mods = []
    for n in ast.walk(tree):
        if isinstance(n, ast.ImportFrom) and n.module and n.level == 0:
            mods.append(n.module)
        elif isinstance(n, ast.Import):
            mods += [a.name for a in n.names]
    out = []
    for m in mods:
        base = "/".join(m.split("."))
        for cand in (base + ".py", base + "/__init__.py"):
            if cand != rel and cand not in out and sb.exists(cand):
                out.append(cand)
                break
    return out[:limit]


def diagnose(out, sb=None):
    """Raw failure output -> {kind, headline, locations:[{path,line}], failing:[names], hint, signature}."""
    out = clean_output(out or "")
    d = {"kind": "unknown", "headline": "", "locations": [], "failing": [], "hint": "", "signature": "", "related": []}
    locs = []

    def add(path, line):
        rel = sb.to_rel(path) if sb else path
        if rel and not any(l["path"] == rel and l["line"] == line for l in locs):
            locs.append({"path": rel, "line": int(line)})

    tb = out.rfind("Traceback (most recent call last):")
    seg = out[tb:] if tb >= 0 else out
    frames = _PY_FRAME.findall(seg)
    excs = list(_PY_EXC.finditer(seg))
    rust = _RUST.search(out)
    if frames and excs:
        d["kind"] = "python"
        e = excs[-1]
        d["headline"] = (e.group(1) + (": " + e.group(2) if e.group(2) else "")).strip()
        for path, line, _fn in reversed(frames):        # innermost project frame first
            add(path, line)
        name = e.group(1).split(".")[-1]
        if name == "ModuleNotFoundError" or name == "ImportError":
            d["hint"] = ("An import failed. Check the module name and whether it is a project file that is imported from the wrong place "
                         "before assuming a package must be installed (installs need the user's approval).")
        elif name in ("SyntaxError", "IndentationError", "TabError"):
            d["hint"] = "Fix the syntax at the line shown; re-read that region of the file first."
        elif name == "EOFError":
            d["hint"] = ("The program called input() / read from the keyboard, but commands here run with no keyboard attached. "
                         "Do NOT use input(). Take the text from a file path in sys.argv (and fall back to sys.stdin or a built-in sample), then run it as: python script.py sample.txt")
        elif name == "AssertionError":
            d["hint"] = "An assertion failed: compare the expected and actual values, then decide whether the code or the test is wrong."
    elif rust:
        d["kind"], d["headline"] = "rust", "error: " + rust.group(1)
        add(rust.group(2), rust.group(3))
    elif _NODE_ERR.search(out) and (_NODE_FRAME.search(out) or _NODE_HEAD.search(out)):
        d["kind"] = "node"
        m = _NODE_ERR.search(out)
        d["headline"] = (m.group(1) + ": " + m.group(2)).strip()
        h = _NODE_HEAD.search(out)
        if h:
            add(h.group(1), h.group(2))
        for path, line, _c in _NODE_FRAME.findall(out):
            add(path, line)
    elif _COMPILER.search(out):
        d["kind"] = "compiler"
        ms = list(_COMPILER.finditer(out))
        d["headline"] = f"{ms[0].group(1)}: {ms[0].group(4)}".strip()[:200]
        for m in ms[:4]:
            add(m.group(1), m.group(2))
    elif _MISSING_CMD.search(out):
        d["kind"] = "missing-command"
        d["headline"] = first_line(_MISSING_CMD.search(out).group(0), 160) or "command not found"
        d["hint"] = "That program or file is not available here. Check the name and path, or use a different tool; do not guess."
    else:
        ln = [x for x in out.splitlines() if re.search(r"\b(error|failed|exception|fatal|cannot|can't|unable)\b", x, re.I)]
        if ln:
            d["kind"], d["headline"] = "generic", ln[-1].strip()[:200]
    d["locations"] = locs[:4]
    if sb and d["kind"] == "python" and locs and re.search(r"(^|/)(test_[^/]*|[^/]*_test|tests?)(\.py|/)", locs[0]["path"]):
        d["related"] = py_related(sb, locs[0]["path"])
    names = parse_test_result(out, None)["failing"]
    d["failing"] = names
    if names and d["kind"] == "unknown":
        d["kind"] = "tests"
    basis = d["kind"] + "|" + _norm_headline(d["headline"]) + "|" + ",".join(sorted(names))[:300] + "|" + (locs[0]["path"] if locs else "")
    d["signature"] = hashlib.sha1(basis.encode()).hexdigest()[:10] if (d["headline"] or names) else ""
    return d


def context_snippets(sb, locations, radius=5, max_files=3):
    """The code around each failure location, with line numbers, so the model does not need a separate read."""
    blocks, seen = [], set()
    for loc in locations:
        if len(blocks) >= max_files:
            break
        key = (loc["path"], loc["line"] // 8)
        if key in seen:
            continue
        seen.add(key)
        try:
            text = sb.read_text(loc["path"])["text"].split("\n")
        except SandboxError:
            continue
        a, b = max(0, loc["line"] - 1 - radius), min(len(text), loc["line"] + radius)
        rows = [f"{i + 1:>5}{'>' if i + 1 == loc['line'] else ' '} {text[i]}" for i in range(a, b)]
        blocks.append(f"{loc['path']} around line {loc['line']}:\n" + "\n".join(rows))
    return "\n\n".join(blocks)


# ------------------------------------------------------------------ tests: find them, read their results
def project_python(root):
    root = Path(root)
    for cand in (root / ".venv" / "Scripts" / "python.exe", root / ".venv" / "bin" / "python",
                 root / "venv" / "Scripts" / "python.exe", root / "venv" / "bin" / "python"):
        if cand.is_file():
            return str(cand)
    return sys.executable


def _q(path):
    return f'"{path}"' if os.name == "nt" else shlex.quote(path)


def _has_module(py, mod):
    try:
        return subprocess.run([py, "-c", f"import {mod}"], capture_output=True, timeout=25).returncode == 0
    except (OSError, subprocess.TimeoutExpired):
        return False


def _find_files(root, pred, depth=3, cap=60):
    out = []
    root = Path(root)
    base = len(root.parts)
    for dp, dn, fn in os.walk(root):
        dn[:] = [d for d in dn if d not in SKIP_DIRS and len(Path(dp, d).parts) - base <= depth]
        for f in fn:
            if pred(f):
                out.append(Path(dp, f))
                if len(out) >= cap:
                    return out
    return out


def read_project_config(root):
    """cressy-agent.json (or .cressy-agent.json): {"test": "cmd" | ["cmd", ...]} overrides auto-detection."""
    for name in ("cressy-agent.json", ".cressy-agent.json"):
        p = Path(root) / name
        if p.is_file():
            try:
                cfg = json.loads(p.read_text("utf-8"))
                return cfg if isinstance(cfg, dict) else {}
            except (OSError, ValueError):
                return {}
    return {}


def detect_tests(root):
    """[{cmd, name, source}] best guess first. Empty when the project has no recognisable tests."""
    root = Path(root)
    cfg = read_project_config(root).get("test")
    if cfg:
        cmds = [cfg] if isinstance(cfg, str) else [c for c in cfg if isinstance(c, str)]
        return [{"cmd": c, "name": first_line(c, 60), "source": "cressy-agent.json"} for c in cmds if c.strip()][:6]
    found = []
    py = project_python(root)
    pyfiles = _find_files(root, lambda f: (f.startswith("test_") or f.endswith("_test.py")) and f.endswith(".py"))
    if pyfiles:
        pyproject = (root / "pyproject.toml").read_text("utf-8", errors="ignore") if (root / "pyproject.toml").is_file() else ""
        has_pytest_cfg = any((root / n).is_file() for n in ("pytest.ini", "conftest.py", "tox.ini", "tests/conftest.py")) or "[tool.pytest" in pyproject
        if _has_module(py, "pytest") and has_pytest_cfg:
            found.append({"cmd": f"{_q(py)} -m pytest -q --tb=short", "name": "pytest", "source": "auto"})
        else:
            tdir = root / "tests"
            if tdir.is_dir() and any(f.parent == tdir for f in pyfiles):
                top = "-s tests -t ." if (tdir / "__init__.py").is_file() else "-s tests"
            else:
                top = "-s ."
            pat = "" if any(f.name.startswith("test") for f in pyfiles) else ' -p "*_test.py"'
            found.append({"cmd": f"{_q(py)} -m unittest discover {top}{pat}", "name": "unittest", "source": "auto"})
    pkg = root / "package.json"
    if pkg.is_file():
        try:
            script = (json.loads(pkg.read_text("utf-8")).get("scripts") or {}).get("test", "")
        except (OSError, ValueError, AttributeError):
            script = ""
        if script and "no test specified" not in script:
            found.append({"cmd": "npm test --silent", "name": "npm test", "source": "auto"})
    elif _which("node"):
        jstests = [f for f in _find_files(root, lambda f: re.search(r"(^test[_.-]|[_.-]test\.|\.test\.|\.spec\.)", f) and f.endswith((".js", ".mjs", ".cjs")), cap=12)]
        for f in jstests[:5]:
            found.append({"cmd": "node " + _q(f.relative_to(root).as_posix()), "name": "node " + f.name, "source": "auto"})
    if (root / "go.mod").is_file():
        found.append({"cmd": "go test ./...", "name": "go test", "source": "auto"})
    if (root / "Cargo.toml").is_file():
        found.append({"cmd": "cargo test --quiet", "name": "cargo test", "source": "auto"})
    mk = root / "Makefile"
    if mk.is_file() and re.search(r"^test\s*:", mk.read_text("utf-8", errors="ignore"), re.M):
        found.append({"cmd": "make test", "name": "make test", "source": "auto"})
    if not found and (any(root.glob("*.csproj")) or any(root.glob("*.sln"))):
        found.append({"cmd": "dotnet test --nologo", "name": "dotnet test", "source": "auto"})
    return found[:6]


def suite_label(cmd):
    """A short name for a test command: 'unittest', 'pytest', 'npm test', 'node tests/x.js', ..."""
    c = " ".join(str(cmd).split())
    for key, label in (("pytest", "pytest"), ("unittest", "unittest"), ("npm test", "npm test"), ("go test", "go test"),
                       ("cargo test", "cargo test"), ("make test", "make test"), ("dotnet test", "dotnet test")):
        if key in c:
            return label
    m = re.search(r"\bnode\s+(\S+)", c)
    if m:
        return "node " + m.group(1).strip("\"'")
    return " ".join(c.split()[:2])[:40]


def parse_test_result(out, code):
    """Read a test runner's output. -> {framework, ran, passed, failed, errors, ok, failing:[names], note}"""
    out = clean_output(out or "")
    r = {"framework": None, "ran": None, "passed": None, "failed": 0, "errors": 0, "ok": None, "failing": [], "note": ""}
    fails = []
    m = re.search(r"^Ran (\d+) tests? in [\d.]+s", out, re.M)
    if m:
        r["framework"], r["ran"] = "unittest", int(m.group(1))
        f = re.search(r"^FAILED \((.*?)\)", out, re.M)
        if f:
            r["failed"] = int((re.search(r"failures=(\d+)", f.group(1)) or [0, 0])[1])
            r["errors"] = int((re.search(r"errors=(\d+)", f.group(1)) or [0, 0])[1])
        r["passed"] = max(0, r["ran"] - r["failed"] - r["errors"])
        r["ok"] = bool(re.search(r"^OK\b", out, re.M)) and not f
        fails += [f"{a.group(2)} ({a.group(3)})" for a in re.finditer(r"^(FAIL|ERROR): (\S+) \(([^)]+)\)", out, re.M)]
        if r["ran"] == 0:
            r["ok"], r["note"] = False, "no tests were found"
    elif re.search(r"\b\d+ (?:passed|failed|error|errors|skipped|deselected)\b.*\bin [\d.]+s", out) or "no tests ran" in out:
        r["framework"] = "pytest"
        line = [x for x in out.splitlines() if re.search(r"\b\d+ (?:passed|failed|errors?)\b", x)] or [out]
        nums = {k: int(v) for v, k in re.findall(r"(\d+) (passed|failed|errors?|skipped)", line[-1])}
        r["passed"], r["failed"] = nums.get("passed", 0), nums.get("failed", 0)
        r["errors"] = nums.get("error", 0) + nums.get("errors", 0)
        r["ran"] = r["passed"] + r["failed"] + r["errors"] + nums.get("skipped", 0)
        r["ok"] = not r["failed"] and not r["errors"] and bool(r["passed"] or nums.get("skipped"))
        if "no tests ran" in out:
            r["ok"], r["note"] = False, "no tests were collected"
        fails += [a.group(2) for a in re.finditer(r"^(FAILED|ERROR) (\S+)", out, re.M)]
    elif re.search(r"^Tests:\s+.*\btotal\b", out, re.M):
        r["framework"] = "jest"
        line = re.search(r"^Tests:\s+(.*)$", out, re.M).group(1)
        nums = {k: int(v) for v, k in re.findall(r"(\d+) (passed|failed|skipped|total)", line)}
        r["passed"], r["failed"], r["ran"] = nums.get("passed", 0), nums.get("failed", 0), nums.get("total")
        r["ok"] = not r["failed"]
        fails += [a.group(1).strip() for a in re.finditer(r"^\s*●\s+(.+?)\s*$", out, re.M) if "›" in a.group(1)]
    elif re.search(r"^# (?:tests|pass|fail) \d+", out, re.M):
        r["framework"] = "node:test"
        g = lambda k: int((re.search(rf"^# {k} (\d+)", out, re.M) or [0, 0])[1])
        r["ran"], r["passed"], r["failed"] = g("tests"), g("pass"), g("fail")
        r["ok"] = not r["failed"]
        fails += [a.group(1).strip() for a in re.finditer(r"^\s*not ok \d+ - (.+)$", out, re.M)]
    elif re.search(r"^test result: (ok|FAILED)\.", out, re.M):
        r["framework"] = "cargo"
        tot = [(int(a), int(b)) for a, b in re.findall(r"(\d+) passed; (\d+) failed", out)]
        r["passed"], r["failed"] = sum(a for a, _ in tot), sum(b for _, b in tot)
        r["ran"] = r["passed"] + r["failed"]
        r["ok"] = not r["failed"]
        fails += [a.group(1) for a in re.finditer(r"^---- (\S+) stdout ----", out, re.M)]
    elif re.search(r"^(--- FAIL|FAIL\s|ok\s+\S+\s+[\d.]+s)", out, re.M):
        r["framework"] = "go"
        r["failed"] = len(re.findall(r"^--- FAIL", out, re.M))
        r["ok"] = not re.search(r"^(--- FAIL|FAIL\b)", out, re.M)
        fails += [a.group(1) for a in re.finditer(r"^--- FAIL: (\S+)", out, re.M)]
    seen = set()
    r["failing"] = [x for x in fails if not (x in seen or seen.add(x))][:12]
    if r["ok"] is None and code is not None:
        r["ok"] = code == 0
    if code is not None and code != 0:
        r["ok"] = False
    return r


def summarize_test(r):
    if r["framework"] is None:
        return "ok" if r["ok"] else "failed"
    bits = []
    if r["passed"] is not None:
        bits.append(f"{r['passed']} passed")
    if r["failed"]:
        bits.append(f"{r['failed']} failed")
    if r["errors"]:
        bits.append(f"{r['errors']} errors")
    return (", ".join(bits) or ("ok" if r["ok"] else "failed")) + (f" ({r['note']})" if r["note"] else "")


# ------------------------------------------------------------------ keeping the conversation inside the context window
def _stub_blocks(text):
    def sub(m):
        info, body = m.group(2).strip(), m.group(3)
        kind, path = classify_info(info)
        if kind in ("file", "edit"):
            return f"[{kind} {path}: {len(body.splitlines())} lines, already applied]"
        return m.group(0) if len(body) < 300 else f"```{info}\n{body[:200]}\n...\n```"
    return re.sub(r"(?ms)^(`{3,})([^\n`]*)\n(.*?)\n\1[ \t]*$", sub, text)


def compact(messages, budget, count):
    """Shrink older steps until the conversation fits `budget` tokens. messages[0] is the system prompt and messages[1] the task;
    both are always kept. Older replies lose their file contents; older observations are cut down; as a last resort the oldest
    steps are dropped in (reply, observation) pairs so roles keep alternating."""
    msgs = [dict(m) for m in messages]
    tot = lambda: sum(count(str(m["content"])) + 6 for m in msgs)
    if tot() <= budget:
        return msgs, 0
    keep_tail = 4
    for i in range(2, max(2, len(msgs) - keep_tail)):
        c = msgs[i]["content"]
        msgs[i]["content"] = _stub_blocks(c) if msgs[i]["role"] == "assistant" else head_tail(c, 500, 400)
        if msgs[i]["role"] == "assistant" and len(msgs[i]["content"]) > 700:
            msgs[i]["content"] = msgs[i]["content"][:700] + " ..."
    dropped = 0
    while tot() > budget and len(msgs) > 2 + keep_tail:
        del msgs[2:4]
        dropped += 2
    if tot() > budget and len(msgs) > 2:        # still too big: cut the biggest recent observation
        for i in range(len(msgs) - 1, 1, -1):
            if msgs[i]["role"] == "user":
                msgs[i]["content"] = head_tail(msgs[i]["content"], 1200, 1200)
                break
    return msgs, dropped
