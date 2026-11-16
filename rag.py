"""Local knowledge base for CressyAI (RAG).

Upload .txt / .md / .pdf files, split them into passages, rank passages for a question with BM25.
No embedding model and no network are involved, so retrieval never has to swap the chat model out of memory.

Storage: one JSON file per document in the knowledge folder (docs/<id>.json). Everything is plain text you can inspect.
"""
import hashlib, io, json, math, os, re, threading, time, unicodedata, uuid
from collections import Counter
from pathlib import Path

EXTS = {".txt": "txt", ".md": "md", ".markdown": "md", ".pdf": "pdf"}
MAX_DOCS = 300
MAX_CHARS = 10_000_000          # extracted text per document
MAX_PAGES = 3000
TARGET = 1000                   # preferred passage size (characters)
OVERLAP = 180                   # context carried from one passage into the next
UNIT_MAX = 600                  # paragraphs longer than this are split at sentence ends
ID_RE = re.compile(r"^[a-f0-9]{12}$")


class KBError(Exception):
    pass


try:
    import pypdf
except ImportError:             # PDFs then fail with a clear message; txt / md still work
    pypdf = None


def pdf_ready():
    return pypdf is not None


# ---------------------------------------------------------------- tokenizing
STOP = set("""a about above after again all also am an and any are as at be because been before being below between both but by
can could did do does doing down during each few for from further had has have having he her here hers him his how i if in into is it
its itself just me more most my no nor not now of off on once only or other our out over own same she should so some such than that the
their them then there these they this those through to too under until up very was we were what when where which while who whom why will
with would you your yours yourself please tell give show explain say said""".split())
_TOK = re.compile(r"(?:[^\W_]|[\u0300-\u036f\u0900-\u0dff\u200c\u200d])+")
_CJK = re.compile(r"[\u3040-\u30ff\u3400-\u9fff\uac00-\ud7af]+")


def _stem(w):
    if len(w) > 5 and w.endswith("ies"):
        return w[:-3] + "y"
    for suf in ("ing", "edly", "ed", "es", "ly", "s"):
        if w.endswith(suf) and len(w) - len(suf) >= 3:
            return w[:-len(suf)]
    return w


def tokens(text):
    """Lowercased, stop-word-free, lightly stemmed tokens. CJK runs become single characters plus pairs."""
    out = []
    for w in _TOK.findall(text.lower()):
        if _CJK.search(w):
            for run in re.split(r"([\u3040-\u30ff\u3400-\u9fff\uac00-\ud7af]+)", w):
                if not run:
                    continue
                if _CJK.fullmatch(run):
                    out.extend(run)
                    out.extend(run[i:i + 2] for i in range(len(run) - 1))
                elif len(run) > 1 and run not in STOP:
                    out.append(_stem(run))
            continue
        if w in STOP or (len(w) < 2 and not w.isdigit()):
            continue
        out.append(_stem(w))
    return out


# ---------------------------------------------------------------- text extraction
_CTRL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")


def clean(text, pdf=False):
    if pdf:
        text = unicodedata.normalize("NFKC", text)
        text = re.sub(r"(\w)-\n(\w)", r"\1\2", text)          # re-join words hyphenated at a line break
    text = text.replace("\r\n", "\n").replace("\r", "\n").replace("\u00a0", " ")
    text = _CTRL.sub("", text)
    text = re.sub(r"[ \t]+\n", "\n", text)
    return re.sub(r"\n{3,}", "\n\n", text).strip()


def decode(data):
    if data[:3] == b"\xef\xbb\xbf":
        return data[3:].decode("utf-8", errors="replace")
    if data[:2] in (b"\xff\xfe", b"\xfe\xff"):
        return data.decode("utf-16", errors="replace")
    try:
        return data.decode("utf-8")
    except UnicodeDecodeError:
        return data.decode("cp1252", errors="replace")


def extract_pdf(data):
    """-> list of (page_number, text). Raises KBError for encrypted, broken or scanned files."""
    if pypdf is None:
        raise KBError("Reading PDFs needs the pypdf package:  pip install pypdf   (inside the app's .venv), then restart the server.")
    try:
        reader = pypdf.PdfReader(io.BytesIO(data))
        if reader.is_encrypted:
            try:
                if not reader.decrypt(""):
                    raise KBError("This PDF is password protected.")
            except KBError:
                raise
            except Exception:
                raise KBError("This PDF is encrypted and could not be opened.")
        n = len(reader.pages)
        if n > MAX_PAGES:
            raise KBError(f"This PDF has {n} pages (the limit is {MAX_PAGES}). Split it first.")
        pages = []
        for i, pg in enumerate(reader.pages, 1):
            try:
                t = pg.extract_text() or ""
            except Exception:
                t = ""
            pages.append((i, clean(t, pdf=True)))
    except KBError:
        raise
    except Exception as e:
        raise KBError(f"Could not read this PDF ({type(e).__name__}). It may be damaged.")
    chars = sum(len(t) for _, t in pages)
    if chars < 20 * max(1, len(pages)) and chars < 400:
        raise KBError("This PDF has no readable text; it looks like a scan. Run OCR on it first, then upload it again.")
    return pages


# ---------------------------------------------------------------- chunking
_SENT = re.compile(r"(?<=[.!?。！？])\s+")


def _pieces(text):
    """Split an over-long paragraph at sentence ends, then at spaces, into pieces of at most UNIT_MAX characters."""
    if len(text) <= UNIT_MAX:
        return [text]
    out, cur = [], ""
    for s in _SENT.split(text):
        while len(s) > UNIT_MAX:
            cut = s.rfind(" ", 0, UNIT_MAX)
            cut = cut if cut > UNIT_MAX // 2 else UNIT_MAX
            if cur:
                out.append(cur); cur = ""
            out.append(s[:cut].strip()); s = s[cut:].strip()
        if cur and len(cur) + len(s) + 1 > UNIT_MAX:
            out.append(cur); cur = s
        else:
            cur = (cur + " " + s).strip()
    if cur:
        out.append(cur)
    return [p for p in out if p]


def units_text(text, kind):
    """-> [(text, page, section)] for txt / md."""
    units, section = [], ""
    for para in re.split(r"\n\s*\n", text):
        para = para.strip("\n")
        if not para.strip():
            continue
        if kind == "md":
            h = re.match(r"^\s{0,3}(#{1,6})\s+(.+?)\s*#*\s*$", para.split("\n", 1)[0])
            if h:
                section = h.group(2).strip()[:120]
        for p in _pieces(para if kind == "md" else re.sub(r"[ \t]*\n[ \t]*", "\n", para)):
            units.append((p, 0, section))
    return units


def units_pdf(pages):
    units = []
    for no, text in pages:
        for para in re.split(r"\n\s*\n", text):
            para = re.sub(r"\s*\n\s*", " ", para).strip()
            for p in _pieces(para):
                if p:
                    units.append((p, no, ""))
    return units


def pack(units):
    """Greedy packing into ~TARGET-character passages; the tail of one passage opens the next so answers are not cut in half."""
    chunks, cur, fresh, ov = [], [], 0, 0

    def flush():
        body = [u for u in cur]
        text = "\n\n".join(u[0] for u in body)
        first = body[len(body) - fresh] if fresh else body[0]
        pages = [u[1] for u in body[len(body) - fresh:] if u[1]] if fresh else []
        chunks.append({"i": len(chunks), "t": text, "p": min(pages) if pages else 0, "pe": max(pages) if pages else 0, "s": first[2], "o": ov})

    n = 0
    for u in units:
        ulen = len(u[0]) + 2
        if cur and fresh and n + ulen > TARGET:
            flush()
            tail, t = [], 0
            for x in reversed(cur):
                if t + len(x[0]) + 2 > OVERLAP:
                    break
                tail.insert(0, x); t += len(x[0]) + 2
            cur, n, fresh = tail, t, 0
            ov = len("\n\n".join(x[0] for x in tail))   # characters at the start of the next passage that repeat this one's end
        cur.append(u); n += ulen; fresh += 1
    if cur and fresh:
        flush()
    return chunks


# ---------------------------------------------------------------- the knowledge base
class KB:
    def __init__(self, folder, max_bytes=30 * 1024 * 1024):
        self.dir = Path(folder)
        self.docs_dir = self.dir / "docs"
        self.max_bytes = max_bytes
        self.lock = threading.RLock()
        self.docs = {}          # id -> dict (with chunks)
        self.toks = {}          # id -> [Counter, ...] per chunk (+ lengths)
        self.docs_dir.mkdir(parents=True, exist_ok=True)
        self.skipped = []
        for p in sorted(self.docs_dir.glob("*.json")):
            try:
                d = json.loads(p.read_text(encoding="utf-8"))
                if not ID_RE.match(d["id"]) or not isinstance(d["chunks"], list):
                    raise ValueError("bad document")
                self.docs[d["id"]] = d
                self._index(d)
            except Exception as e:
                self.skipped.append(f"{p.name}: {e}")

    # -- index
    def _index(self, d):
        name_t = tokens(Path(d["name"]).stem)
        rows = []
        for c in d["chunks"]:
            tk = tokens(c["t"]) + tokens(c.get("s", "")) + name_t
            rows.append((Counter(tk), len(tk)))
        self.toks[d["id"]] = rows

    def _save(self, d):
        p = self.docs_dir / (d["id"] + ".json")
        tmp = p.with_suffix(".tmp")
        tmp.write_text(json.dumps(d, ensure_ascii=False), encoding="utf-8")
        os.replace(tmp, p)

    @staticmethod
    def meta(d):
        return {k: d[k] for k in ("id", "name", "kind", "size", "added", "chars", "pages", "enabled")} | {"chunks": len(d["chunks"])}

    # -- public
    def status(self):
        with self.lock:
            return {"docs": [self.meta(d) for d in sorted(self.docs.values(), key=lambda x: x["added"], reverse=True)],
                    "chunks": sum(len(d["chunks"]) for d in self.docs.values()),
                    "chars": sum(d["chars"] for d in self.docs.values()),
                    "pdf": pdf_ready(), "max_mb": self.max_bytes // (1024 * 1024), "skipped": self.skipped,
                    "exts": sorted(EXTS)}

    def add(self, name, data):
        name = re.sub(r"[\x00-\x1f\\/]+", " ", os.path.basename(name.replace("\\", "/"))).strip()[:120] or "untitled.txt"
        kind = EXTS.get(Path(name).suffix.lower())
        if not kind:
            raise KBError("Only .txt, .md and .pdf files can be added.")
        if not data:
            raise KBError("That file is empty.")
        if len(data) > self.max_bytes:
            raise KBError(f"That file is larger than {self.max_bytes // (1024 * 1024)} MB.")
        digest = hashlib.sha256(data).hexdigest()
        with self.lock:
            for d in self.docs.values():
                if d.get("sha") == digest:
                    return {**self.meta(d), "duplicate": True}
            if len(self.docs) >= MAX_DOCS:
                raise KBError(f"The knowledge base is full ({MAX_DOCS} documents). Remove some first.")
        pages = 0
        if kind == "pdf":
            if not data[:1024].lstrip().startswith(b"%PDF"):
                raise KBError("This file is named .pdf but is not a PDF.")
            pg = extract_pdf(data)
            pages = len(pg)
            units, chars = units_pdf(pg), sum(len(t) for _, t in pg)
        else:
            if b"\0" in data[:4096] and data[:2] not in (b"\xff\xfe", b"\xfe\xff"):
                raise KBError("This looks like a binary file, not text.")
            text = clean(decode(data))
            if not text:
                raise KBError("That file has no text in it.")
            units, chars = units_text(text, kind), len(text)
        if chars > MAX_CHARS:
            raise KBError("That document has too much text (over 10 million characters). Split it up.")
        chunks = pack(units)
        if not chunks:
            raise KBError("No text could be extracted from that file.")
        d = {"id": uuid.uuid4().hex[:12], "name": name, "kind": kind, "size": len(data), "sha": digest, "added": int(time.time()),
             "chars": chars, "pages": pages, "enabled": True, "chunks": chunks}
        with self.lock:
            self._save(d)
            self.docs[d["id"]] = d
            self._index(d)
        return self.meta(d)

    def _get(self, id):
        d = self.docs.get(id) if isinstance(id, str) and ID_RE.match(id) else None
        if not d:
            raise KBError("That document is not in the knowledge base.")
        return d

    def set_enabled(self, id, on):
        with self.lock:
            d = self._get(id)
            d["enabled"] = bool(on)
            self._save(d)
            return self.meta(d)

    def remove(self, id):
        with self.lock:
            d = self._get(id)
            try:
                (self.docs_dir / (d["id"] + ".json")).unlink()
            except FileNotFoundError:
                pass
            del self.docs[id]
            self.toks.pop(id, None)

    def clear(self):
        with self.lock:
            for id in list(self.docs):
                self.remove(id)

    def query(self, q, k=5, min_ratio=0.2):
        """BM25 over every enabled passage. -> {hits: [{doc, doc_id, n, page, section, score, text}], searched, query_terms}"""
        qt = list(dict.fromkeys(tokens(q)))
        with self.lock:
            rows = [(d, c, *self.toks[d["id"]][j]) for d in self.docs.values() if d["enabled"] for j, c in enumerate(d["chunks"])]
        N = len(rows)
        if not qt or not N:
            return {"hits": [], "searched": N, "terms": qt}
        avg = sum(r[3] for r in rows) / N or 1.0
        df = {t: sum(1 for r in rows if t in r[2]) for t in qt}
        idf = {t: math.log(1 + (N - n + 0.5) / (n + 0.5)) for t, n in df.items() if n}
        k1, b = 1.4, 0.75
        scored = []
        for d, c, tf, ln in rows:
            s = 0.0
            for t, w in idf.items():
                f = tf.get(t)
                if f:
                    s += w * f * (k1 + 1) / (f + k1 * (1 - b + b * ln / avg))
            if s > 0:
                scored.append((s, d, c))
        scored.sort(key=lambda x: -x[0])
        if len(qt) > 1:    # reward passages that contain the question's words next to each other
            pairs = list(zip(qt, qt[1:]))
            rer = []
            for s, d, c in scored[:60]:
                seq = tokens(c["t"])
                have = {(a, b2) for a, b2 in zip(seq, seq[1:])}
                rer.append((s * (1 + 0.35 * sum(p in have for p in pairs) / len(pairs)), d, c))
            scored = sorted(rer, key=lambda x: -x[0]) + scored[60:]
        hits, kept = [], {}
        qset = set(qt)
        if scored:
            top = scored[0][0]
            for s, d, c in scored:
                if len(hits) >= max(1, min(12, int(k))) or s < top * min_ratio:
                    break
                i, cs = c["i"], d["chunks"]
                # a passage that only matches inside text it shares with a neighbour already chosen adds nothing
                if i - 1 in kept.get(d["id"], ()) and c.get("o") and not qset & set(tokens(c["t"][c["o"]:])):
                    continue
                if i + 1 in kept.get(d["id"], ()) and i + 1 < len(cs) and cs[i + 1].get("o") \
                        and not qset & set(tokens(c["t"][:-cs[i + 1]["o"]])):
                    continue
                kept.setdefault(d["id"], set()).add(i)
                hits.append({"doc": d["name"], "doc_id": d["id"], "n": c["i"] + 1, "page": c.get("p") or 0, "page_end": c.get("pe") or 0,
                             "section": c.get("s", ""), "score": round(s, 3), "text": c["t"]})
        return {"hits": hits, "searched": N, "terms": qt}
