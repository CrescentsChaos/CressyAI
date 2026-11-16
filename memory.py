"""Long-term memory for CressyAI.

What lives here (one JSON file next to the chats, plus a vectors file next to it):
  * facts      notes about you (global) or about the project folder the server was started in (project)
  * tasks      unfinished work with a short progress note, always scoped to the project
  * sessions   one hand-off note per chat: what was worked on, where it stands, what comes next

Retrieval is hybrid. Keyword matching (BM25 over the same tokenizer as the knowledge base, blended with character
trigrams) always runs. When an embedding model is available (see embed.py) every note also gets a vector and a
message is matched on meaning too, so "which db do we use?" finds "We store data in PostgreSQL". Without an embedding
model everything still works, on keywords alone.

Notes are scoped: global notes reach every project, project notes only the folder they were written for. Open tasks
and sessions are always project-scoped, so a new chat in the same project can pick up unfinished work.
"""
import hashlib, json, math, os, re, statistics, threading, time, uuid
from collections import Counter
from pathlib import Path

import embed as emb_utils
from rag import tokens

ID_RE = re.compile(r"^[a-f0-9]{12}$")
CHAT_RE = re.compile(r"^[A-Za-z0-9_-]{1,64}$")
PIN_RE = re.compile(
    r"\b(my name|i am|i'm|i prefer|call me|always use|never use|we use|this (?:repo|project|app) uses|the stack is)\b",
    re.I)
SECRET_RE = re.compile(
    r"(?i)(?:\b(?:api[_-]?key|secret|passw(?:or)?d|passwd|token|bearer)\s*(?:is|=|:)\s*\S{6,})"
    r"|sk-[A-Za-z0-9]{16,}|AKIA[0-9A-Z]{16}|gh[pousr]_[A-Za-z0-9]{20,}|xox[baprs]-[A-Za-z0-9-]{10,}"
    r"|-----BEGIN [A-Z ]*PRIVATE KEY-----|eyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\.")
MAX_FACTS = 400
MAX_TASKS = 80
MAX_SESSIONS = 60           # per project
FACT_LEN = 500
TASK_LEN = 240
PROGRESS_LEN = 200
SUMMARY_LEN = 1800
HANDOFF_LEN = 500
STALE_DAYS = 21
RECENT_SESSION_DAYS = 45

# semantic matching knobs. Cosine scores are not comparable between embedding models, so a note counts as related
# when it clears DENSE_MIN *and* sits clearly above the typical score for unrelated notes (median + DENSE_MARGIN).
# Start with these and adjust with the environment variables if your embedding model is more or less generous.
DENSE_MIN = float(os.environ.get("CRESSY_DENSE_MIN", "0.42"))
DENSE_MARGIN = float(os.environ.get("CRESSY_DENSE_MARGIN", "0.07"))
DENSE_STRONG = float(os.environ.get("CRESSY_DENSE_STRONG", "0.62"))
DUP_COS = float(os.environ.get("CRESSY_DUP_COS", "0.93"))


class MemoryError(Exception):
    pass


def _now():
    return int(time.time() * 1000)


def _id():
    return uuid.uuid4().hex[:12]


def _h(text):
    return hashlib.sha1(str(text).encode("utf-8", "surrogatepass")).hexdigest()[:16]


def clean_text(text, limit):
    text = re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f]", "", str(text or ""))
    text = re.sub(r"\s+", " ", text).strip().strip("-*•").strip()
    return text[:limit].strip()


def clean_block(text, limit):
    """Like clean_text, but keeps line breaks (summaries are short bullet lists)."""
    text = re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f]", "", str(text or "")).replace("\r\n", "\n")
    text = re.sub(r"[ \t]+\n", "\n", text)
    text = re.sub(r"\n{3,}", "\n\n", text).strip()
    return text[:limit].strip()


def looks_secret(text):
    return bool(SECRET_RE.search(str(text or "")))


def _sig(text):
    tk = tokens(text)
    compact = "".join(tk)
    grams = Counter(compact[i:i + 3] for i in range(max(0, len(compact) - 2)))
    return tk, grams


def _cos(a, b):
    if not a or not b:
        return 0.0
    dot = sum(a[k] * b[k] for k in a if k in b)
    na = math.sqrt(sum(v * v for v in a.values()))
    nb = math.sqrt(sum(v * v for v in b.values()))
    return dot / (na * nb) if na and nb else 0.0


def _near(text, rows, thresh=0.72):
    """True when text repeats a stored note closely enough that saving both would just add noise."""
    a = set(tokens(text))
    if len(a) < 2:
        return any(clean_text(r.get("text", ""), 500).lower() == text.lower() for r in rows)
    for r in rows:
        b = set(tokens(r.get("text", "")))
        if not b:
            continue
        if a <= b or b <= a or len(a & b) / len(a | b) >= thresh:
            return True
    return False


def _ago(ms):
    return max(0, _now() - int(ms or 0))


def _public_fact(f):
    return {"id": f["id"], "text": f["text"], "scope": f.get("scope") or "global", "pin": bool(f.get("pin")),
            "source": f.get("source") or "user", "created": f.get("created") or 0, "project": f.get("project") or "",
            "hits": int(f.get("hits") or 0), "updated": f.get("updated") or f.get("created") or 0}


def _public_task(t):
    status = t.get("status") or "open"
    return {"id": t["id"], "text": t["text"], "status": status, "source": t.get("source") or "user",
            "chat": t.get("chat") or "", "created": t.get("created") or 0, "updated": t.get("updated") or 0,
            "project": t.get("project") or "", "progress": t.get("progress") or "",
            "stale": status == "open" and _ago(t.get("updated") or t.get("created")) > STALE_DAYS * 86400000}


def _public_session(s):
    return {"chat": s["chat"], "title": s.get("title") or "", "summary": s.get("summary") or "",
            "handoff": s.get("handoff") or "", "next": list(s.get("next") or []), "updated": s.get("updated") or 0,
            "created": s.get("created") or 0, "turns": int(s.get("turns") or 0), "quality": s.get("quality") or "auto",
            "project": s.get("project") or ""}


def _session_text(s):
    return clean_text(f"{s.get('title', '')}. {s.get('handoff', '')} {' '.join(s.get('next') or [])} "
                      f"{(s.get('summary') or '')[:600]}", 1000)


def _task_text(t):
    return t["text"] + ((" " + t["progress"]) if t.get("progress") else "")


# ---------------------------------------------------------------- project profile (no model needed)
_PY_STACK = [("fastapi", "FastAPI"), ("django", "Django"), ("flask", "Flask"), ("torch", "PyTorch"), ("numpy", "NumPy"),
             ("pandas", "pandas"), ("llama-cpp-python", "llama.cpp"), ("sqlalchemy", "SQLAlchemy"),
             ("streamlit", "Streamlit"), ("pytest", "pytest")]
_JS_STACK = [("react", "React"), ("vue", "Vue"), ("svelte", "Svelte"), ("next", "Next.js"), ("nuxt", "Nuxt"),
             ("express", "Express"), ("vite", "Vite"), ("tailwindcss", "Tailwind"), ("electron", "Electron")]


def detect_profile(root):
    """A one-line description of the project folder from its marker files. Reads a few small files, runs nothing."""
    root = Path(root)

    def read(name, limit=60000):
        try:
            return (root / name).read_text(encoding="utf-8", errors="ignore")[:limit]
        except OSError:
            return ""

    def has(*names):
        return any((root / n).exists() for n in names)

    langs, stack = [], []
    py = (read("requirements.txt") + "\n" + read("pyproject.toml") + "\n" + read("Pipfile")).lower()
    try:
        top = [p.name.lower() for p in root.iterdir()][:400]
    except OSError:
        top = []
    if has("requirements.txt", "pyproject.toml", "setup.py", "Pipfile") or any(n.endswith(".py") for n in top):
        langs.append("Python")
        stack += [label for key, label in _PY_STACK if key in py]
    pkg = read("package.json")
    if pkg:
        deps = ""
        try:
            j = json.loads(pkg)
            deps = " ".join(list((j.get("dependencies") or {}).keys()) + list((j.get("devDependencies") or {}).keys())).lower()
        except ValueError:
            pass
        langs.append("TypeScript" if (has("tsconfig.json") or "typescript" in deps) else "JavaScript")
        stack += [label for key, label in _JS_STACK if re.search(r"(^|\s)" + re.escape(key) + r"(\s|$)", deps)]
    for marker, lang in (("Cargo.toml", "Rust"), ("go.mod", "Go"), ("composer.json", "PHP"), ("Gemfile", "Ruby"),
                         ("pubspec.yaml", "Dart"), ("CMakeLists.txt", "C/C++")):
        if has(marker):
            langs.append(lang)
    if has("pom.xml", "build.gradle", "build.gradle.kts"):
        langs.append("Java/Kotlin")
    if any(n.endswith((".csproj", ".sln")) for n in top):
        langs.append(".NET")
    if has("Dockerfile", "docker-compose.yml"):
        stack.append("Docker")
    about = ""
    for line in read("README.md", 4000).splitlines():
        s = re.sub(r"[#*`>_\[\]!]|<[^>]+>", "", line).strip()
        if len(s) > 8 and not s.lower().startswith(("http", "![")):
            about = s[:120]
            break
    stack_all = (langs + stack)[:7]
    name = root.name or str(root)
    text = name + (" (" + ", ".join(stack_all) + ")" if stack_all else "")
    if about:
        text += ": " + about
    return text[:300]


class Store:
    def __init__(self, path, project, embedder=None):
        self.path = Path(path)
        self.vpath = self.path.with_name(self.path.stem + ".vectors.json")
        self.project = str(Path(project).expanduser().resolve())
        self.lock = threading.RLock()
        self.emb = embedder
        self.data = {"facts": [], "tasks": [], "sessions": []}
        self.vecs, self.vdirty, self._vsave = {}, False, 0.0
        self._qcache = {}
        self._prof = (0.0, "")
        self._hit_dirty, self._hit_t = False, 0.0
        self._load()
        self._load_vecs()

    # ------------------------------------------------------------ files
    def _load(self):
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return
        except (OSError, ValueError) as e:
            print("memory file unreadable, starting empty:", e)
            return
        if isinstance(raw, dict):
            lst = lambda k: raw.get(k) if isinstance(raw.get(k), list) else []
            self.data = {"facts": [f for f in lst("facts") if self._ok_fact(f)],
                         "tasks": [t for t in lst("tasks") if self._ok_task(t)],
                         "sessions": [s for s in lst("sessions") if self._ok_session(s)]}

    @staticmethod
    def _ok_fact(f):
        return isinstance(f, dict) and ID_RE.match(str(f.get("id", ""))) and isinstance(f.get("text"), str) and f.get("scope") in ("global", "project")

    @staticmethod
    def _ok_task(t):
        return isinstance(t, dict) and ID_RE.match(str(t.get("id", ""))) and isinstance(t.get("text"), str) and t.get("status") in ("open", "done")

    @staticmethod
    def _ok_session(s):
        return isinstance(s, dict) and CHAT_RE.match(str(s.get("chat", ""))) and isinstance(s.get("project"), str)

    def _write_json(self, path, obj, **kw):
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_name("." + path.name + "." + uuid.uuid4().hex[:8] + ".tmp")
        tmp.write_text(json.dumps(obj, ensure_ascii=False, **kw), encoding="utf-8")
        os.replace(tmp, path)

    def _save(self):
        self._write_json(self.path, {"version": 2, **self.data}, indent=1)
        self._hit_dirty, self._hit_t = False, time.time()
        self._flush_vecs()

    # ------------------------------------------------------------ vectors
    def _sem(self):
        return self.emb is not None and self.emb.available()

    def _load_vecs(self):
        if self.emb is None or not getattr(self.emb, "name", ""):
            return
        try:
            raw = json.loads(self.vpath.read_text(encoding="utf-8"))
            if raw.get("model") == self.emb.name and isinstance(raw.get("v"), dict):
                self.vecs = {k: emb_utils.unpack(v) for k, v in raw["v"].items() if isinstance(v, str)}
        except (OSError, ValueError, TypeError):
            self.vecs = {}

    def _live_texts(self):
        d = self.data
        return [f["text"] for f in d["facts"]] + [_task_text(t) for t in d["tasks"]] + [_session_text(s) for s in d["sessions"]]

    def _flush_vecs(self, force=False):
        if not self.vdirty or self.emb is None or not getattr(self.emb, "name", ""):
            return
        if not force and time.time() - self._vsave < 8:
            return
        live = {_h(t) for t in self._live_texts()}
        self.vecs = {k: v for k, v in self.vecs.items() if k in live}
        try:
            self._write_json(self.vpath, {"model": self.emb.name, "v": {k: emb_utils.pack(v) for k, v in self.vecs.items()}})
            self.vdirty, self._vsave = False, time.time()
        except OSError as e:
            print("could not save memory vectors:", e)

    def _vec(self, text, budget=None):
        key = _h(text)
        with self.lock:
            v = self.vecs.get(key)
        if v is not None:
            return v
        if not self._sem():
            return None
        if budget is not None:
            if budget[0] <= 0:
                return None
            budget[0] -= 1
        v = self.emb.embed_doc(text)
        if v is not None:
            with self.lock:
                self.vecs[key] = v
                self.vdirty = True
        return v

    def _qvec(self, query):
        q = (query or "").strip()[:600]
        if q in self._qcache:
            return self._qcache[q]
        v = self.emb.embed_query(q) if self._sem() else None
        if v is not None:
            if len(self._qcache) >= 32:
                self._qcache.pop(next(iter(self._qcache)))
            self._qcache[q] = v
        return v

    def warm(self):
        """Embed every note that has no vector yet (runs in a background thread at start-up). Returns how many."""
        if not self._sem():
            return 0
        with self.lock:
            todo = [t for t in dict.fromkeys(self._live_texts()) if _h(t) not in self.vecs]
        n = 0
        for t in todo:
            v = self.emb.embed_doc(t)
            if v is None:
                break
            with self.lock:
                self.vecs[_h(t)] = v
                self.vdirty = True
            n += 1
        with self.lock:
            self._flush_vecs(force=True)
        return n

    def reindex(self):
        """Throw the vectors away and rebuild them (after swapping the embedding model, or if search seems off)."""
        with self.lock:
            self.vecs, self.vdirty, self._qcache = {}, True, {}
        if self.emb is not None and hasattr(self.emb, "reset"):
            self.emb.reset()
        n = self.warm()
        return {**self.snapshot(), "indexed_now": n}

    def semantic_status(self):
        with self.lock:
            rows = [f["text"] for f in self._visible_facts()] + [_task_text(t) for t in self._visible_tasks()] \
                   + [_session_text(s) for s in self._visible_sessions()]
            indexed = sum(1 for t in rows if _h(t) in self.vecs)
        st = self.emb.status() if self.emb is not None and hasattr(self.emb, "status") else {}
        on = self._sem()
        why = "" if on else (getattr(self.emb, "why", "") if self.emb is not None else "") or "no embedding model"
        return {"on": on, "model": getattr(self.emb, "name", "") if self.emb is not None else "",
                "loaded": bool(st.get("loaded")), "why": why, "indexed": indexed, "total": len(rows)}

    # ------------------------------------------------------------ visibility
    def _visible_facts(self):
        return [f for f in self.data["facts"] if f["scope"] == "global" or f.get("project") == self.project]

    def _visible_tasks(self):
        return [t for t in self.data["tasks"] if t.get("project") in ("", self.project)]

    def _visible_sessions(self):
        return [s for s in self.data["sessions"] if s.get("project") == self.project]

    def _fact(self, id):
        if not isinstance(id, str) or not ID_RE.match(id):
            raise MemoryError("Unknown memory.")
        for f in self._visible_facts():
            if f["id"] == id:
                return f
        raise MemoryError("That note is not in this project.")

    def _task(self, id):
        if not isinstance(id, str) or not ID_RE.match(id):
            raise MemoryError("Unknown task.")
        for t in self._visible_tasks():
            if t["id"] == id:
                return t
        raise MemoryError("That task is not in this project.")

    def project_name(self):
        return Path(self.project).name or self.project

    def profile_text(self):
        if time.time() - self._prof[0] > 120:
            try:
                self._prof = (time.time(), detect_profile(self.project))
            except Exception:
                self._prof = (time.time(), self.project_name())
        return self._prof[1]

    def snapshot(self):
        with self.lock:
            facts = sorted(self._visible_facts(), key=lambda f: f.get("created") or 0, reverse=True)
            tasks = sorted(self._visible_tasks(), key=lambda t: (t.get("status") != "open", -(t.get("updated") or 0)))
            sessions = sorted(self._visible_sessions(), key=lambda s: -(s.get("updated") or 0))
            snap = {"project": self.project, "project_name": self.project_name(), "profile": self.profile_text(),
                    "facts": [_public_fact(f) for f in facts], "tasks": [_public_task(t) for t in tasks[:40]],
                    "sessions": [_public_session(s) for s in sessions[:8]]}
        snap["semantic"] = self.semantic_status()
        return snap

    # ------------------------------------------------------------ duplicates
    def _dup(self, text, rows, text_of=lambda r: r.get("text", "")):
        if _near(text, rows):
            return True
        if self._sem() and rows:
            v = self.emb.embed_doc(text)
            if v is not None:
                with self.lock:
                    self.vecs.setdefault(_h(text), v)
                    self.vdirty = True
                budget = [60]
                for r in rows:
                    rv = self._vec(text_of(r), budget)
                    if rv is not None and emb_utils.dot(v, rv) >= DUP_COS:
                        return True
        return False

    # ------------------------------------------------------------ facts
    def _evict_fact(self):
        pool = [f for f in self.data["facts"] if not f.get("pin")]
        if not pool:
            raise MemoryError("Memory is full of pinned notes. Unpin or delete some first.")
        victim = min(pool, key=lambda f: (f.get("source") == "user", int(f.get("hits") or 0), f.get("used") or f.get("created") or 0))
        self.data["facts"] = [f for f in self.data["facts"] if f is not victim]

    def _add_fact(self, text, scope, pin, source):
        text = clean_text(text, FACT_LEN)
        if len(text) < 8:
            raise MemoryError("Write a fuller fact (at least a short sentence).")
        if source == "chat" and looks_secret(text):
            raise MemoryError("That looks like a secret, so it was not saved.")
        scope = "project" if scope == "project" else "global"
        if self._dup(text, self._visible_facts()):
            raise MemoryError("A very similar note is already saved.")
        if len(self.data["facts"]) >= MAX_FACTS:
            self._evict_fact()
        if pin is None:
            pin = bool(PIN_RE.search(text))
        now = _now()
        fact = {"id": _id(), "text": text, "scope": scope, "pin": bool(pin), "source": "chat" if source == "chat" else "user",
                "project": self.project if scope == "project" else "", "created": now, "updated": now, "hits": 0}
        self.data["facts"].append(fact)
        return fact

    def add_fact(self, text, scope="global", pin=None, source="user"):
        with self.lock:
            fact = self._add_fact(text, scope, pin, source)
            self._save()
            return {**self.snapshot(), "fact": _public_fact(fact)}

    def set_fact(self, id, pin=None, text=None, scope=None):
        with self.lock:
            f = self._fact(id)
            if text is not None:
                text = clean_text(text, FACT_LEN)
                if len(text) < 8:
                    raise MemoryError("Write a fuller fact (at least a short sentence).")
                f["text"] = text
                f["updated"] = _now()
            if pin is not None:
                f["pin"] = bool(pin)
            if scope in ("global", "project"):
                f["scope"] = scope
                f["project"] = self.project if scope == "project" else ""
            self._save()
            return self.snapshot()

    def delete_fact(self, id):
        with self.lock:
            f = self._fact(id)
            self.data["facts"] = [x for x in self.data["facts"] if x["id"] != f["id"]]
            self._save()
            return self.snapshot()

    def _try_replace(self, rid, text):
        """A note extracted from a chat may correct an earlier chat-written note. Notes you wrote yourself are never overwritten."""
        if not isinstance(rid, str) or not ID_RE.match(rid):
            return False
        text = clean_text(text, FACT_LEN)
        for f in self._visible_facts():
            if f["id"] == rid and f.get("source") == "chat" and len(text) >= 8 and not looks_secret(text):
                f["text"], f["updated"] = text, _now()
                return True
        return False

    # ------------------------------------------------------------ tasks
    def _add_task(self, text, chat, source):
        text = clean_text(text, TASK_LEN)
        if len(text) < 8:
            raise MemoryError("Describe the task in a few more words.")
        if source == "chat" and looks_secret(text):
            raise MemoryError("That looks like a secret, so it was not saved.")
        mine = [t for t in self._visible_tasks()]
        open_ = [t for t in mine if t["status"] == "open"]
        if self._dup(text, open_, _task_text):
            raise MemoryError("That task is already on the list.")
        if len(open_) >= 24:
            raise MemoryError("Too many open tasks. Mark some done first.")
        if len(self.data["tasks"]) >= MAX_TASKS:
            done = [t for t in self.data["tasks"] if t["status"] == "done"]
            if done:
                self.data["tasks"] = [t for t in self.data["tasks"] if t is not done[0]]
            else:
                raise MemoryError("The task list is full.")
        chat = str(chat or "")[:64]
        now = _now()
        task = {"id": _id(), "text": text, "status": "open", "source": "chat" if source == "chat" else "user",
                "chat": chat, "chats": [chat] if chat else [], "project": self.project, "created": now, "updated": now}
        self.data["tasks"].append(task)
        return task

    def add_task(self, text, chat="", source="user"):
        with self.lock:
            task = self._add_task(text, chat, source)
            self._save()
            return {**self.snapshot(), "task": _public_task(task)}

    def _touch_task(self, t, chat):
        t["updated"] = _now()
        chat = str(chat or "")[:64]
        if chat:
            chats = list(t.get("chats") or [])
            if chat not in chats:
                chats.append(chat)
            t["chats"] = chats[-6:]
            t["chat"] = t.get("chat") or chat

    def set_task(self, id, status=None, text=None, progress=None, chat=""):
        status = status or None
        if status not in (None, "open", "done"):
            raise MemoryError("A task is either open or done.")
        with self.lock:
            t = self._task(id)
            if text is not None:
                text = clean_text(text, TASK_LEN)
                if len(text) < 8:
                    raise MemoryError("Describe the task in a few more words.")
                t["text"] = text
            if progress is not None:
                t["progress"] = clean_text(progress, PROGRESS_LEN)
            if status:
                t["status"] = status
            self._touch_task(t, chat)
            self._save()
            return self.snapshot()

    def delete_task(self, id):
        with self.lock:
            t = self._task(id)
            self.data["tasks"] = [x for x in self.data["tasks"] if x["id"] != t["id"]]
            self._save()
            return self.snapshot()

    def clear_done(self):
        with self.lock:
            self.data["tasks"] = [t for t in self.data["tasks"] if not (t["status"] == "done" and t.get("project") in ("", self.project))]
            self._save()
            return self.snapshot()

    def complete_matching(self, fragment):
        frag = clean_text(fragment, 200).lower()
        if len(frag) < 2:
            raise MemoryError("Say which task to finish.")
        with self.lock:
            n = 0
            for t in self._visible_tasks():
                if t["status"] == "open" and frag in t["text"].lower():
                    t["status"] = "done"
                    t["updated"] = _now()
                    n += 1
            if n:
                self._save()
            return {**self.snapshot(), "updated": n}

    def _close(self, ref, chat):
        """Finish a task named by id, or by a fragment that matches exactly one open task."""
        ref = str(ref or "").strip()
        open_ = [t for t in self._visible_tasks() if t["status"] == "open"]
        hit = next((t for t in open_ if t["id"] == ref), None)
        if hit is None and len(ref) >= 4:
            m = [t for t in open_ if ref.lower() in t["text"].lower()]
            hit = m[0] if len(m) == 1 else None
        if hit is None:
            return 0
        hit["status"] = "done"
        self._touch_task(hit, chat)
        return 1

    # ------------------------------------------------------------ sessions (cross-chat hand-off)
    def upsert_session(self, chat, title="", summary="", handoff="", next=None, turns=0, quality="auto"):
        """One note per chat. 'llm' notes (written from a model's reading of the chat) are never overwritten by 'auto' ones."""
        chat = str(chat or "")
        if not CHAT_RE.match(chat):
            raise MemoryError("Bad chat id.")
        quality = "llm" if quality == "llm" else "auto"
        with self.lock:
            s = next_((x for x in self.data["sessions"] if x["chat"] == chat and x.get("project") == self.project), None)
            now = _now()
            if s is None:
                s = {"chat": chat, "project": self.project, "title": "", "summary": "", "handoff": "", "next": [],
                     "created": now, "updated": now, "turns": 0, "quality": "auto"}
                self.data["sessions"].append(s)
            if title:
                s["title"] = clean_text(title, 120)
            if summary:
                s["summary"] = clean_block(summary, SUMMARY_LEN)       # the rolling summary: newer is always better
            if handoff and (quality == "llm" or s.get("quality") != "llm"):
                s["handoff"] = clean_text(handoff, HANDOFF_LEN)
                s["quality"] = quality
                if next is not None:
                    s["next"] = [clean_text(x, 160) for x in (next or [])[:4] if clean_text(x, 160)]
            for field in ("summary", "handoff"):
                if looks_secret(s.get(field, "")):
                    s[field] = ""
            s["turns"] = max(int(turns or 0), int(s.get("turns") or 0))
            s["updated"] = now
            mine = sorted(self._visible_sessions(), key=lambda x: -(x.get("updated") or 0))
            for old in mine[MAX_SESSIONS:]:
                self.data["sessions"] = [x for x in self.data["sessions"] if x is not old]
            self._save()
            return _public_session(s)

    def delete_session(self, chat):
        with self.lock:
            before = len(self.data["sessions"])
            self.data["sessions"] = [s for s in self.data["sessions"] if not (s["chat"] == chat and s.get("project") == self.project)]
            if len(self.data["sessions"]) != before:
                self._save()
            return self.snapshot()

    def forget_chat(self, chat):
        """A chat was deleted: drop its hand-off note in every project, and unlink it from tasks."""
        with self.lock:
            before = len(self.data["sessions"])
            self.data["sessions"] = [s for s in self.data["sessions"] if s["chat"] != chat]
            changed = len(self.data["sessions"]) != before
            for t in self.data["tasks"]:
                if chat in (t.get("chats") or []):
                    t["chats"] = [c for c in t["chats"] if c != chat]
                    changed = True
                if t.get("chat") == chat:
                    t["chat"] = ""
                    changed = True
            if changed:
                self._save()

    # ------------------------------------------------------------ learning from a chat
    def absorb(self, facts=None, tasks=None, chat="", done=None, progress=None, handoff=None, title=""):
        """Save what was extracted from a chat. Duplicates, secrets and empty lines are skipped, never fatal."""
        with self.lock:
            out = {"added_facts": 0, "added_tasks": 0, "replaced": 0, "closed": 0, "skipped": 0}
            for item in (facts or [])[:4]:
                item = {"text": item} if isinstance(item, str) else item
                if not isinstance(item, dict):
                    continue
                try:
                    if item.get("replaces") and self._try_replace(item.get("replaces"), item.get("text")):
                        out["replaced"] += 1
                        continue
                    self._add_fact(item.get("text"), "global" if item.get("scope") == "global" else "project", None, "chat")
                    out["added_facts"] += 1
                except MemoryError:
                    out["skipped"] += 1
            for item in (tasks or [])[:4]:
                text = item.get("text") if isinstance(item, dict) else item
                try:
                    self._add_task(text, chat, "chat")
                    out["added_tasks"] += 1
                except MemoryError:
                    out["skipped"] += 1
            for ref in (done or [])[:6]:
                out["closed"] += self._close(ref, chat)
            for p in (progress or [])[:4]:
                if not isinstance(p, dict):
                    continue
                note = clean_text(p.get("note"), PROGRESS_LEN)
                try:
                    t = self._task(p.get("id"))
                except MemoryError:
                    continue
                if t["status"] == "open" and note and not looks_secret(note):
                    t["progress"] = note
                    self._touch_task(t, chat)
            changed = any(out[k] for k in ("added_facts", "added_tasks", "replaced", "closed")) or bool(progress)
            if changed:
                self._save()
            if isinstance(handoff, dict) and CHAT_RE.match(str(chat or "")):
                summary = clean_text(handoff.get("summary"), HANDOFF_LEN)
                if summary and not looks_secret(summary):
                    self.upsert_session(chat, title=title, handoff=summary, next=handoff.get("next") or [], quality="llm")
            return {**self.snapshot(), **out}

    # ------------------------------------------------------------ retrieval
    def _touch(self, rows):
        now = _now()
        for r in rows:
            r["hits"], r["used"] = int(r.get("hits") or 0) + 1, now
        if rows:
            self._hit_dirty = True
            if time.time() - self._hit_t > 30:
                self._save()

    def recall(self, query, k=6, chat="", fresh=False):
        """What to put in front of the model for this message.
        pinned: always; matches: facts ranked for the message; tasks: open tasks of this project, best match first;
        sessions: earlier chats that match; last_session: the most recent other chat (only when fresh=True, i.e. the
        first message of a new chat)."""
        k = max(1, min(8, int(k)))
        query = str(query or "")
        with self.lock:
            facts = list(self._visible_facts())
            tasks = [t for t in self._visible_tasks() if t["status"] == "open"]
            sessions = [s for s in self._visible_sessions() if s["chat"] != chat]
        pinned = [f for f in facts if f.get("pin")]
        matches = self._rank(query, [f for f in facts if not f.get("pin")], lambda r: r["text"], k)
        ranked_tasks = self._rank(query, tasks, _task_text, 6, keep_rest=True)
        last = None
        if fresh and sessions:
            recent = max(sessions, key=lambda s: s.get("updated") or 0)
            if _ago(recent.get("updated")) <= RECENT_SESSION_DAYS * 86400000:
                last = recent
        hits = self._rank(query, [s for s in sessions if s is not last], _session_text, 2) if query.strip() else []
        with self.lock:
            self._touch(matches)
        return {"profile": self.profile_text(), "pinned": [_public_fact(f) for f in pinned[:12]],
                "matches": [_public_fact(f) for f in matches], "tasks": [_public_task(t) for t in ranked_tasks],
                "sessions": [_public_session(s) for s in hits], "last_session": _public_session(last) if last else None,
                "project": self.project, "project_name": self.project_name(), "semantic": self._sem()}

    def search(self, query, k=5):
        """Facts and tasks that match a phrase, for /forget and the search box. Pinned notes are ranked like the rest."""
        k = max(1, min(10, int(k)))
        if not str(query or "").strip():
            return {"facts": [], "tasks": []}
        with self.lock:
            facts, tasks = list(self._visible_facts()), list(self._visible_tasks())
        return {"facts": [_public_fact(f) for f in self._rank(query, facts, lambda r: r["text"], k)],
                "tasks": [_public_task(t) for t in self._rank(query, tasks, _task_text, k)]}

    def _rank(self, query, rows, text_of, k, keep_rest=False, min_rel=0.38):
        if not rows:
            return []
        q_toks, q_grams = _sig(query)
        qt = list(dict.fromkeys(q_toks))
        qset = set(qt)
        texts = [text_of(r) for r in rows]
        N = len(rows)
        counters, lens, grams = [], [], []
        for t in texts:
            tk, g = _sig(t)
            counters.append(Counter(tk))
            lens.append(len(tk) or 1)
            grams.append(g)
        bm, top = [0.0] * N, 1.0
        if qt:
            avg = sum(lens) / N or 1.0
            df = {t: sum(1 for c in counters if t in c) for t in qt}
            idf = {t: math.log(1 + (N - n + 0.5) / (n + 0.5)) for t, n in df.items() if n}
            k1, b = 1.4, 0.75
            for i, (c, ln) in enumerate(zip(counters, lens)):
                s = 0.0
                for t, w in idf.items():
                    f = c.get(t)
                    if f:
                        s += w * f * (k1 + 1) / (f + k1 * (1 - b + b * ln / avg))
                bm[i] = s
            top = max(bm) or 1.0
        cos = [None] * N
        qv = self._qvec(query) if (self._sem() and query.strip()) else None
        thr = DENSE_MIN
        if qv is not None:
            budget = [24]       # at most a few notes are embedded on the spot; warm() indexes the rest in the background
            for i, t in enumerate(texts):
                v = self._vec(t, budget)
                if v is not None:
                    cos[i] = emb_utils.dot(qv, v)
            seen = [c for c in cos if c is not None]
            if len(seen) >= 5:
                thr = max(DENSE_MIN, statistics.median(seen) + DENSE_MARGIN)
        scored = []
        for i, row in enumerate(rows):
            tri = _cos(q_grams, grams[i])
            overlap = any(t in counters[i] for t in qset)
            lex = 0.72 * (bm[i] / top) + 0.28 * tri
            c = cos[i]
            if c is not None:
                dn = min(1.0, (c - thr) / max(0.05, 0.9 - thr)) if c >= thr else 0.0
                if c >= DENSE_STRONG:
                    dn = max(dn, 0.5)
                s = 0.6 * dn + 0.4 * lex if (dn > 0 or overlap or tri >= 0.45) else 0.0
            else:
                s = lex if (overlap or tri >= 0.45) else 0.0
            scored.append((s, row.get("updated") or row.get("created") or 0, row))
        scored.sort(key=lambda x: (-x[0], -x[1]))
        keep = [x for x in scored if x[0] > 0.12]
        if keep:
            keep = [x for x in keep if x[0] >= keep[0][0] * min_rel]
        out = [x[2] for x in keep[:k]]
        if keep_rest and len(out) < k:
            have = {id(r) for r in out}
            for row in sorted(rows, key=lambda r: r.get("updated") or 0, reverse=True):
                if id(row) not in have:
                    out.append(row)
                    if len(out) >= k:
                        break
        return out

    # ------------------------------------------------------------ projects, export, import
    def projects(self):
        """Every project that has notes, tasks or sessions. A folder that was renamed or moved shows up as missing."""
        with self.lock:
            groups = {self.project: {"facts": 0, "tasks": 0, "sessions": 0}}
            for f in self.data["facts"]:
                if f["scope"] == "project":
                    groups.setdefault(f.get("project") or "", {"facts": 0, "tasks": 0, "sessions": 0})["facts"] += 1
            for t in self.data["tasks"]:
                if t.get("project") and t["status"] == "open":
                    groups.setdefault(t["project"], {"facts": 0, "tasks": 0, "sessions": 0})["tasks"] += 1
            for s in self.data["sessions"]:
                groups.setdefault(s.get("project") or "", {"facts": 0, "tasks": 0, "sessions": 0})["sessions"] += 1
        out = [{"path": p, "name": Path(p).name or p, **g, "current": p == self.project, "exists": os.path.isdir(p)}
               for p, g in groups.items() if p]
        out.sort(key=lambda x: (not x["current"], not x["exists"], x["name"].lower()))
        return {"projects": out}

    def adopt(self, from_path):
        """Move another project's notes, tasks and sessions here (the folder was renamed or moved)."""
        src = str(from_path or "")
        with self.lock:
            known = {p["path"] for p in self.projects()["projects"]}
            if src == self.project or src not in known:
                raise MemoryError("Pick a different project from the list.")
            moved = 0
            mine = self._visible_facts()
            for f in list(self.data["facts"]):
                if f["scope"] == "project" and f.get("project") == src:
                    if _near(f["text"], mine):
                        self.data["facts"] = [x for x in self.data["facts"] if x is not f]
                    else:
                        f["project"] = self.project
                    moved += 1
            for t in self.data["tasks"]:
                if t.get("project") == src:
                    t["project"] = self.project
                    moved += 1
            have = {s["chat"] for s in self._visible_sessions()}
            for s in list(self.data["sessions"]):
                if s.get("project") == src:
                    if s["chat"] in have:
                        self.data["sessions"] = [x for x in self.data["sessions"] if x is not s]
                    else:
                        s["project"] = self.project
                    moved += 1
            self._save()
            return {**self.snapshot(), "moved": moved}

    def export(self, include_global=True):
        """A portable copy of this project's memory. Ids and folder paths are left out on purpose."""
        with self.lock:
            facts = [{"text": f["text"], "scope": f["scope"], "pin": bool(f.get("pin"))} for f in self._visible_facts()
                     if include_global or f["scope"] == "project"]
            tasks = [{"text": t["text"], "status": t["status"], "progress": t.get("progress") or ""} for t in self._visible_tasks()]
            sessions = [{"title": s.get("title") or "", "summary": s.get("summary") or "", "handoff": s.get("handoff") or "",
                         "next": list(s.get("next") or []), "updated": s.get("updated") or 0, "turns": s.get("turns") or 0}
                        for s in self._visible_sessions()]
        return {"app": "cressyai-memory", "version": 2, "exported": _now(), "project": self.project_name(),
                "facts": facts, "tasks": tasks, "sessions": sessions}

    def import_data(self, data):
        if not isinstance(data, dict) or data.get("app") != "cressyai-memory":
            raise MemoryError("That file is not a CressyAI memory export.")
        out = {"added_facts": 0, "added_tasks": 0, "added_sessions": 0, "skipped": 0}
        with self.lock:
            for f in (data.get("facts") if isinstance(data.get("facts"), list) else [])[:MAX_FACTS]:
                if not isinstance(f, dict):
                    continue
                try:
                    self._add_fact(f.get("text"), "project" if f.get("scope") == "project" else "global", bool(f.get("pin")), "user")
                    out["added_facts"] += 1
                except MemoryError:
                    out["skipped"] += 1
            for t in (data.get("tasks") if isinstance(data.get("tasks"), list) else [])[:MAX_TASKS]:
                if not isinstance(t, dict):
                    continue
                try:
                    task = self._add_task(t.get("text"), "", "user")
                    if t.get("status") == "done":
                        task["status"] = "done"
                    task["progress"] = clean_text(t.get("progress"), PROGRESS_LEN)
                    out["added_tasks"] += 1
                except MemoryError:
                    out["skipped"] += 1
            for s in (data.get("sessions") if isinstance(data.get("sessions"), list) else [])[:MAX_SESSIONS]:
                if not isinstance(s, dict) or not (s.get("handoff") or s.get("summary")):
                    continue
                self.upsert_session("imp" + _id(), title=s.get("title") or "", summary=s.get("summary") or "",
                                    handoff=s.get("handoff") or "", next=s.get("next") or [], turns=s.get("turns") or 0, quality="llm")
                out["added_sessions"] += 1
            self._save()
            return {**self.snapshot(), **out}


def next_(it, default):
    """next(it, default) -- upsert_session has a parameter called next that shadows the builtin."""
    for x in it:
        return x
    return default


if __name__ == "__main__":
    import tempfile
    root, other = tempfile.mkdtemp(), tempfile.mkdtemp()
    store = Store(Path(root) / "memory.json", root)
    store.add_fact("The user prefers short answers with a table when comparing options.", "global")
    store.add_fact("This repo uses FastAPI and the tests live in tests/api.", "project")
    store.add_fact("My name is Rowan and I write the server in Python.", "global")
    Store(Path(root) / "memory.json", other).add_fact("The other project is a Rust CLI called fern.", "project")
    store = Store(Path(root) / "memory.json", root)
    hit = store.recall("where do the fastapi tests live?")
    texts = [f["text"] for f in hit["pinned"] + hit["matches"]]
    assert any("FastAPI" in t for t in texts), texts
    assert all("fern" not in t for t in texts), texts
    assert any(f["pin"] for f in hit["pinned"]), hit
    store.add_task("Finish the login error copy")
    store.add_task("Wire memory retrieval into the chat request")
    assert any("login" in t["text"] for t in store.recall("hello")["tasks"])
    dup = store.absorb(["This repo uses FastAPI and the tests live in tests/api."], ["Finish the login error copy"], "c1")
    assert dup["added_facts"] == 0 and dup["added_tasks"] == 0
    fresh = store.absorb(["The deploy target is a single Windows machine on the LAN."], ["Add a resume banner for open tasks"], "c1")
    assert fresh["added_facts"] == 1 and fresh["added_tasks"] == 1, fresh
    assert store.complete_matching("login error")["updated"] == 1
    assert all("login" not in t["text"] for t in store.recall("continue")["tasks"])
    print("memory checks passed (the full suite is tests/test_memory.py)")
