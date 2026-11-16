"""Local text embeddings for CressyAI's semantic memory.

A small embedding GGUF (nomic-embed-text, bge-small, e5-small, all-MiniLM, ...) is loaded on the CPU the first
time a note has to be searched, and freed again after a few idle minutes. It never touches the GPU and it is not
one of the chat models, so the "one big model in memory at a time" rule of the app is unchanged.

If no embedding file is found, or llama-cpp-python cannot load it, nothing breaks: memory.py falls back to its
keyword matching (BM25 + trigrams) and the Memory tab says why semantic search is off.
"""
import base64
import math
import os
import re
import struct
import threading
import time
from pathlib import Path

# first match wins; the order is "good small models first"
PREFERRED = [r"nomic-embed-text", r"bge-small", r"bge-base", r"e5-small", r"e5-base", r"minilm", r"gte-small",
             r"gte-base", r"arctic-embed", r"mxbai-embed", r"embeddinggemma", r"qwen3-embedding", r"bge-m3"]
EMBED_RX = re.compile(r"embed|nomic|bge[-_]|e5[-_]|minilm|gte[-_]|mxbai", re.I)
NOT_EMBED_RX = re.compile(r"mmproj|rerank|vision|llava|whisper|clip|stable[-_ ]?diffusion|sdxl", re.I)
QUANT_RANK = {"F32": 0, "F16": 0, "BF16": 0, "Q8_0": 1, "Q6_K": 2, "Q5_K_M": 3, "Q5_K_S": 3, "Q4_K_M": 4}
MAX_TEXT = 2000          # characters sent to the model; notes are far shorter than this


def _quant(name):
    m = re.findall(r"(?i)(?<![A-Za-z0-9])(f32|f16|bf16|q\d(?:_[a-z0-9]+)*)(?![A-Za-z0-9])", name)
    return m[-1].upper() if m else ""


def find_model(dirs, explicit=None, disabled=False):
    """-> (path, why). path is '' when semantic search is off; why says what to do about it."""
    if disabled:
        return "", "turned off with --no-embed"
    if explicit:
        p = Path(explicit).expanduser()
        if not p.is_file():
            for d in dirs:
                if (Path(d) / explicit).is_file():
                    p = Path(d) / explicit
                    break
        if p.is_file():
            return str(p.resolve()), ""
        return "", f"--embed-model not found: {explicit}"
    found = []
    for d in dirs:
        try:
            for f in Path(d).glob("*.gguf"):
                if EMBED_RX.search(f.name) and not NOT_EMBED_RX.search(f.name):
                    found.append(f)
        except OSError:
            continue
    if not found:
        return "", "no embedding model found. Put a small one such as nomic-embed-text-v1.5.Q8_0.gguf in your models folder (or use --embed-model)"

    def rank(f):
        low = f.name.lower()
        pref = next((i for i, rx in enumerate(PREFERRED) if re.search(rx, low)), len(PREFERRED))
        try:
            size = f.stat().st_size
        except OSError:
            size = 0
        return (pref, QUANT_RANK.get(_quant(f.name), 5), size, low)
    found.sort(key=rank)
    return str(found[0].resolve()), ""


def prefixes(name):
    """(query_prefix, document_prefix). Several embedding families were trained with these and score worse without."""
    n = name.lower()
    if "nomic" in n:
        return "search_query: ", "search_document: "
    if re.search(r"e5[-_]", n):
        return "query: ", "passage: "
    if "bge-m3" in n:
        return "", ""
    if "bge" in n or "mxbai" in n or "arctic-embed" in n:
        return "Represent this sentence for searching relevant passages: ", ""
    if "qwen3" in n and "embedding" in n:
        return "Instruct: Given a note-taking query, retrieve the relevant note\nQuery: ", ""
    if "embeddinggemma" in n:
        return "task: search result | query: ", "title: none | text: "
    return "", ""


# ---- vectors: plain lists of floats, stored as half-precision base64 ----------------------------------------
def normalize(v):
    n = math.sqrt(sum(x * x for x in v))
    return [x / n for x in v] if n > 0 else list(v)


def dot(a, b):
    return sum(x * y for x, y in zip(a, b)) if a is not None and b is not None and len(a) == len(b) else 0.0


def pack(v):
    return base64.b64encode(struct.pack(f"<{len(v)}e", *v)).decode("ascii")


def unpack(s):
    raw = base64.b64decode(s)
    return list(struct.unpack(f"<{len(raw) // 2}e", raw))


def _as_vector(out):
    """llama-cpp-python returns one vector for a string, but one vector per token when the model has no pooling."""
    if not isinstance(out, (list, tuple)) or not out:
        return None
    if isinstance(out[0], (list, tuple)):
        rows = [r for r in out if isinstance(r, (list, tuple)) and r]
        if not rows:
            return None
        d = len(rows[0])
        out = [sum(r[i] for r in rows) / len(rows) for i in range(d)]
    v = [float(x) for x in out]
    if not all(math.isfinite(x) for x in v):
        return None
    return normalize(v)


class Embedder:
    """Lazy, CPU-only embedding model with an idle timeout. Thread-safe."""

    def __init__(self, path="", why="", idle=300, threads=None, loader=None):
        self.path = path or ""
        self.why = why if not self.path else ""
        self.name = Path(self.path).name if self.path else ""
        self.idle = idle
        self.threads = threads
        self.q_prefix, self.d_prefix = prefixes(self.name)
        self._loader = loader          # tests pass a fake: loader(path, threads) -> object with .embed(text)
        self._llm = None
        self._lock = threading.RLock()
        self._last = 0.0
        self._fails = 0
        self._watching = False

    # -- state --------------------------------------------------------------------------------------------
    def available(self):
        return bool(self.path) and not self.why

    def loaded(self):
        return self._llm is not None

    def status(self):
        return {"on": self.available(), "model": self.name, "loaded": self.loaded(), "why": self.why}

    def reset(self):
        """Forget a past failure so the next call tries to load the model again."""
        with self._lock:
            self.why = "" if self.path else self.why
            self._fails = 0

    # -- loading ------------------------------------------------------------------------------------------
    def _load(self):
        if self._llm is not None:
            return self._llm
        threads = self.threads or max(1, min(4, (os.cpu_count() or 4) // 2))
        try:
            if self._loader:
                self._llm = self._loader(self.path, threads)
            else:
                from llama_cpp import Llama
                self._llm = Llama(model_path=self.path, embedding=True, n_ctx=512, n_batch=512, n_gpu_layers=0,
                                  n_threads=threads, verbose=False)
        except Exception as e:     # missing llama-cpp-python, not an embedding model, out of RAM ...
            self._llm = None
            self.why = f"could not load {self.name}: {str(e).splitlines()[0][:160] if str(e) else type(e).__name__}"
            return None
        self._start_watch()
        return self._llm

    def _start_watch(self):
        if self._watching or self.idle <= 0:
            return
        self._watching = True

        def run():
            while True:
                time.sleep(max(1.0, min(30.0, self.idle / 2)))
                with self._lock:
                    if self._llm is None:
                        self._watching = False
                        return
                    if time.time() - self._last > self.idle:
                        self.unload()
                        self._watching = False
                        return
        threading.Thread(target=run, daemon=True, name="embed-idle").start()

    def unload(self):
        with self._lock:
            llm, self._llm = self._llm, None
        if llm is not None:
            close = getattr(llm, "close", None)
            if callable(close):
                try:
                    close()
                except Exception:
                    pass

    # -- embedding ----------------------------------------------------------------------------------------
    def _embed(self, text):
        text = str(text or "").strip()[:MAX_TEXT]
        if not text or not self.available():
            return None
        with self._lock:
            llm = self._load()
            if llm is None:
                return None
            try:
                v = _as_vector(llm.embed(text))
            except Exception:
                self._fails += 1
                if self._fails >= 3:
                    self.why = f"{self.name} keeps failing to embed text"
                return None
            self._last = time.time()
            if v is None:
                self._fails += 1
                return None
            self._fails = 0
            return v

    def embed_query(self, text):
        return self._embed(self.q_prefix + str(text or ""))

    def embed_doc(self, text):
        return self._embed(self.d_prefix + str(text or ""))


if __name__ == "__main__":
    import tempfile
    d = Path(tempfile.mkdtemp())
    for n in ["Qwen2.5-Coder-7B-Instruct-Q4_K_M.gguf", "mmproj-f16.gguf", "bge-small-en-v1.5-Q4_K_M.gguf",
              "nomic-embed-text-v1.5.Q8_0.gguf", "nomic-embed-text-v1.5.Q4_K_M.gguf", "bge-reranker-v2-m3-Q8_0.gguf"]:
        (d / n).write_bytes(b"x" * 10)
    path, why = find_model([d])
    assert Path(path).name == "nomic-embed-text-v1.5.Q8_0.gguf", (path, why)
    assert find_model([d], disabled=True)[0] == ""
    assert Path(find_model([d], "bge-small-en-v1.5-Q4_K_M.gguf")[0]).name == "bge-small-en-v1.5-Q4_K_M.gguf"
    assert find_model([d], "nope.gguf")[0] == "" and "not found" in find_model([d], "nope.gguf")[1]
    assert find_model([tempfile.mkdtemp()])[0] == ""
    assert prefixes("nomic-embed-text-v1.5.Q8_0.gguf")[0].startswith("search_query")
    assert prefixes("multilingual-e5-small-q8_0.gguf") == ("query: ", "passage: ")
    assert prefixes("all-MiniLM-L6-v2.gguf") == ("", "")
    v = normalize([3.0, 4.0])
    assert abs(dot(v, v) - 1) < 1e-9
    back = unpack(pack(v))
    assert all(abs(a - b) < 1e-3 for a, b in zip(v, back))
    assert _as_vector([[1.0, 0.0], [3.0, 0.0]]) == [1.0, 0.0] and _as_vector([]) is None and _as_vector([float("nan")]) is None

    class Fake:
        def __init__(self): self.calls = []
        def embed(self, text): self.calls.append(text); return [1.0, 2.0, 2.0]
    fake = Fake()
    e = Embedder(path, idle=0, loader=lambda p, t: fake)
    q = e.embed_query("hello")
    assert fake.calls[0] == "search_query: hello" and abs(dot(q, q) - 1) < 1e-9
    e.embed_doc("a note")
    assert fake.calls[1] == "search_document: a note"
    e.unload()
    assert not e.loaded()

    def boom(p, t): raise RuntimeError("not an embedding model")
    bad = Embedder(path, loader=boom)
    assert bad.embed_query("x") is None and not bad.available() and "could not load" in bad.why
    assert not Embedder("", "nothing here").available()
    print("embed checks passed")
