#!/usr/bin/env python3
"""Local GGUF chat server.   python server.py --model path/to/model.gguf   (or start without --model and pick models in the app)"""
import argparse, codecs, gc, json, math, os, platform, re, shutil, signal, subprocess, threading, time, uuid, webbrowser
from pathlib import Path

import uvicorn
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from starlette.concurrency import run_in_threadpool
from llama_cpp import Llama

try:
    import psutil
    PROC = psutil.Process()
except ImportError:
    psutil = PROC = None

ap = argparse.ArgumentParser()
ap.add_argument("--model", default=None, help="path to the coder .gguf (optional: pick one in the app's Model tab)")
ap.add_argument("--models-dir", action="append", default=None,
                help="folder to scan for .gguf files (repeatable; default E:\\AI\\Models and the coder's folder)")
ap.add_argument("--no-autoload", action="store_true", help="start with no model in memory; load from the Model tab")
ap.add_argument("--reasoner", default=None, help="reasoning model .gguf (default: gemma-4-12b-it-Q4_K_M.gguf, then Qwen3-8B-Q6_K.gguf in the models folder)")
ap.add_argument("--reasoner-n-ctx", type=int, default=8192, help="context window while the reasoning model is loaded")
ap.add_argument("--no-reasoner", action="store_true", help="do not look for a reasoning model on start (you can still pick one in the app)")
ap.add_argument("--n-ctx", type=int, default=16384)
ap.add_argument("--n-gpu-layers", type=int, default=-1, help="-1 = offload all layers")
ap.add_argument("--threads", type=int, default=None)
ap.add_argument("--n-batch", type=int, default=None, help="prompt batch size (try 512 on fast CPUs)")
ap.add_argument("--flash-attn", action="store_true", help="enable flash attention (faster, less RAM on supported builds)")
ap.add_argument("--chat-format", default=None, help="override if the GGUF has no chat template")
ap.add_argument("--vision-model", default=None, help="Qwen2.5-VL .gguf (default: auto-find in E:\\AI\\Models or next to --model)")
ap.add_argument("--vision-mmproj", default=None, help="Qwen2.5-VL mmproj .gguf (default: auto-find next to the vision model)")
ap.add_argument("--vision-n-ctx", type=int, default=16384, help="context window used while the vision model is loaded")
ap.add_argument("--vision-max-tokens", type=int, default=4096, help="max tokens of extracted details per image")
ap.add_argument("--no-vision", action="store_true", help="disable image attachments")
ap.add_argument("--host", default="127.0.0.1")
ap.add_argument("--port", type=int, default=8000)
ap.add_argument("--root", default=".", help="folder the app may read (default: current folder)")
ap.add_argument("--open", action="store_true", help="open the browser on start")
ap.add_argument("--no-terminal", action="store_true", help="disable the Terminal tab / running commands")
ap.add_argument("--read-only", action="store_true", help="never write files into --root (downloads still work)")
ap.add_argument("--unsafe-remote-exec", action="store_true",
                help="allow terminal + file writes even when --host is not loopback (dangerous)")
args = ap.parse_args()

MODELS_DIR_DEFAULT = r"E:\AI\Models"
REASONER_PREFERRED = ["gemma-4-12b-it-Q4_K_M.gguf", "Qwen3-8B-Q6_K.gguf"]   # tried in this order


def model_dirs():
    ds = [Path(d).expanduser() for d in (args.models_dir or [MODELS_DIR_DEFAULT])]
    if args.model:
        ds.append(Path(args.model).expanduser().resolve().parent)
    seen, out = set(), []
    for d in ds:
        k = str(d).lower()
        if k not in seen:
            seen.add(k)
            out.append(d)
    return out


def scan_models():
    """Every .gguf in the model folders (plus any file named on the command line). The UI may only load files from this list."""
    out, seen = [], set()

    def add(p):
        try:
            rp = Path(p).expanduser().resolve()
            k = str(rp).lower()
            if k in seen or rp.suffix.lower() != ".gguf" or not rp.is_file():
                return
            seen.add(k)
            out.append({"name": rp.name, "path": str(rp), "size": rp.stat().st_size, "mmproj": "mmproj" in rp.name.lower()})
        except OSError:
            pass

    for d in model_dirs():
        try:
            for f in sorted(d.glob("*.gguf"), key=lambda f: f.name.lower()):
                add(f)
        except OSError:
            pass
    for p in (args.model, args.reasoner, args.vision_model):
        if p:
            add(p)
    return out


def find_reasoner():
    if args.no_reasoner:
        return "", "disabled with --no-reasoner"
    if args.reasoner:
        p = Path(args.reasoner).expanduser()
        return (str(p.resolve()), "") if p.is_file() else ("", f"reasoner not found: {p}")
    byname = {m["name"].lower(): m for m in scan_models() if not m["mmproj"]}
    for want in REASONER_PREFERRED:
        if want.lower() in byname:
            return byname[want.lower()]["path"], ""
    return "", ("no reasoning model found (looked for " + " or ".join(REASONER_PREFERRED) + " in "
                + ", ".join(str(d) for d in model_dirs()) + "). Pick one in the Model tab.")


VL_MODEL = VL_MMPROJ = ""
VL_OK, VL_WHY = False, ""


def find_vision():
    if args.no_vision:
        return "", "", "disabled with --no-vision"
    model, proj = args.vision_model, args.vision_mmproj
    if not model:
        cands = []
        for d in model_dirs():
            try:
                cands += [f for f in d.glob("*.gguf") if "vl" in f.name.lower() and "mmproj" not in f.name.lower()]
            except OSError:
                pass
        cands.sort(key=lambda f: ("q4_k_m" not in f.name.lower(), f.name.lower()))
        if not cands:
            return "", "", "no Qwen2.5-VL .gguf found (looked in E:\\AI\\Models and next to --model; use --vision-model)"
        model = str(cands[0])
    if not Path(model).is_file():
        return "", "", f"vision model not found: {model}"
    if not proj:
        mm = sorted(Path(model).resolve().parent.glob("*mmproj*.gguf"), key=lambda f: ("f16" not in f.name.lower(), f.name.lower()))
        mm = [f for f in mm if f.name.lower().split("-mmproj")[0][:12] == Path(model).name.lower()[:12]] or mm
        if not mm:
            return "", "", "no mmproj .gguf next to the vision model (use --vision-mmproj)"
        proj = str(mm[0])
    if not Path(proj).is_file():
        return "", "", f"mmproj not found: {proj}"
    return model, proj, ""


VL_MODEL, VL_MMPROJ, VL_WHY = find_vision()
VL_OK = bool(VL_MODEL)

R_MODEL, R_WHY = find_reasoner()


def base_cfg(path, n_ctx):
    return dict(path=path, n_ctx=n_ctx, n_gpu_layers=args.n_gpu_layers, n_threads=args.threads or 0,
                n_batch=args.n_batch or 0, flash_attn=bool(args.flash_attn), chat_format=args.chat_format or "")


# settings per model slot; the Model tab edits these, and they are applied the next time that slot is loaded
CFG = {
    "coder": base_cfg(str(Path(args.model).expanduser().resolve()) if args.model else "", args.n_ctx),
    "reasoner": base_cfg(R_MODEL, args.reasoner_n_ctx),
    "vision": base_cfg(VL_MODEL, args.vision_n_ctx),
}
CFG["vision"]["chat_format"] = ""
LOADED_CFG = {}           # the settings each slot was actually loaded with (to show "reload to apply")


def llama_kw(slot):
    c = CFG[slot]
    kw = dict(model_path=c["path"], n_ctx=int(c["n_ctx"]), n_gpu_layers=int(c["n_gpu_layers"]), verbose=False)
    if c.get("n_threads"):
        kw["n_threads"] = int(c["n_threads"])
    if c.get("n_batch"):
        kw["n_batch"] = int(c["n_batch"])
    if c.get("flash_attn"):
        kw["flash_attn"] = True
    if c.get("chat_format"):
        kw["chat_format"] = c["chat_format"]
    return kw


LOCK = threading.Lock()   # llama.cpp contexts are not thread-safe; also held for a whole vision / reasoning swap
SWAP = threading.Lock()   # held only while a model is being created/freed (tokenizers must not touch it then)
llm = None                # the coder model
rllm = None               # the reasoning model (never loaded together with the coder)
LOAD_S = {"coder": 0.0, "reasoner": 0.0}
META = {}                 # static facts about the coder, kept so /api/info works while it is unloaded
STATE = {"model": "none"} # which model is in memory right now: coder | reasoner | vision | none


def model_call(name):
    try:
        return getattr(llm._model, name)()
    except Exception:
        return None


def free_llama(m=None, handler=None):
    """Really release a model (and its image projector) so RAM/VRAM go back to the system."""
    try:
        stack = getattr(handler, "_exit_stack", None)
        if stack is not None:
            stack.close()    # image projector first, it points into the model
    except Exception:
        pass
    try:
        if m is not None and hasattr(m, "close"):
            m.close()
    except Exception:
        pass
    gc.collect()


# NOTE: the load_/unload_ helpers do not take SWAP themselves; callers hold it.
def unload_coder():
    global llm
    m, llm = llm, None
    if m is not None:
        free_llama(m)
    if STATE["model"] == "coder":
        STATE["model"] = "none"


def unload_reasoner():
    global rllm
    m, rllm = rllm, None
    if m is not None:
        free_llama(m)
    if STATE["model"] == "reasoner":
        STATE["model"] = "none"


def load_coder():
    global llm
    if llm is not None:
        return
    if not CFG["coder"]["path"]:
        raise RuntimeError("No coder model selected. Open the Model tab and pick one.")
    unload_reasoner()        # only one model lives in memory at a time
    t0 = time.time()
    llm = Llama(**llama_kw("coder"))
    LOAD_S["coder"] = time.time() - t0
    LOADED_CFG["coder"] = dict(CFG["coder"])
    STATE["model"] = "coder"
    md = {k: (v if len(v) < 400 else v[:400] + "…") for k, v in llm.metadata.items()}
    META.clear()
    META.update(path=CFG["coder"]["path"], metadata=md, n_ctx=llm.n_ctx(), n_vocab=llm.n_vocab(), n_embd=model_call("n_embd"),
                n_params=model_call("n_params"), n_threads=llm.n_threads)


def load_reasoner():
    global rllm
    if rllm is not None:
        return
    if not CFG["reasoner"]["path"]:
        raise RuntimeError("No reasoning model selected. Open the Model tab and pick one.")
    unload_coder()           # the coder stays unloaded for as long as the reasoner is in memory
    t0 = time.time()
    rllm = Llama(**llama_kw("reasoner"))
    LOAD_S["reasoner"] = time.time() - t0
    LOADED_CFG["reasoner"] = dict(CFG["reasoner"])
    STATE["model"] = "reasoner"


SLOT_LOAD = {"coder": load_coder, "reasoner": load_reasoner}
SLOT_UNLOAD = {"coder": unload_coder, "reasoner": unload_reasoner}


def slot_loaded(slot):
    return {"coder": llm is not None, "reasoner": rllm is not None, "vision": STATE["model"] == "vision"}[slot]


def ntok(text):
    """Exact token count while the coder is loaded, a rough estimate while it is swapped out. Never blocks."""
    if SWAP.acquire(blocking=False):
        try:
            if llm is not None:
                return len(llm.tokenize(text.encode("utf-8"), add_bos=False))
        except Exception:
            pass
        finally:
            SWAP.release()
    return math.ceil(len(text) / 3.6)


if args.model and not args.no_autoload:
    with SWAP:
        load_coder()
app = FastAPI()


LOOPBACK = ("127.0.0.1", "localhost", "::1")
LOCAL = args.host in LOOPBACK


@app.middleware("http")
async def guard(req, call_next):
    """Block DNS-rebinding and cross-site requests when bound to loopback."""
    if LOCAL and req.url.hostname not in LOOPBACK:
        return JSONResponse({"detail": "Bad Host header"}, 403)
    if req.method not in ("GET", "HEAD", "OPTIONS"):
        origin = req.headers.get("origin")
        if origin:   # includes the literal "null" sent by sandboxed iframes
            from urllib.parse import urlparse
            oh = urlparse(origin).hostname
            if (LOCAL and oh not in LOOPBACK) or (not LOCAL and oh != req.url.hostname):
                return JSONResponse({"detail": "Cross-site request blocked"}, 403)
    return await call_next(req)


HERE = Path(__file__).parent
ROOT = Path(args.root).expanduser().resolve()
BIN = {".png", ".jpg", ".jpeg", ".gif", ".ico", ".pdf", ".zip", ".gz", ".gguf", ".pyc", ".exe", ".dll", ".so", ".woff", ".woff2", ".mp3", ".mp4"}
MAX_FILE = 200_000      # bytes read per file
FOLDER_FILE = 40_000    # bytes per file when attaching a whole folder
FOLDER_TOTAL = 300_000  # total bytes when attaching a whole folder
SKIP = {".git", "node_modules", "__pycache__", ".venv", "venv", ".idea", ".vscode"}


def safe(rel):
    p = (ROOT / rel).resolve()
    if p != ROOT and not p.is_relative_to(ROOT):
        raise HTTPException(403, "That path is outside the allowed folder")
    return p


def rel_of(p):
    return "" if p == ROOT else p.relative_to(ROOT).as_posix()


def load_text(p, limit):
    """Return a dict for a text file, or None if it is binary/unreadable."""
    try:
        with open(p, "rb") as f:
            data = f.read(limit + 1)
    except OSError:
        return None
    if b"\0" in data[:4096]:
        return None
    text = data[:limit].decode("utf-8", errors="replace")
    lossy = False   # True when the file is not valid UTF-8 (saving it back would corrupt it)
    try:
        data[:limit].decode("utf-8")
    except UnicodeDecodeError as e:
        lossy = not (len(data) > limit and e.start >= limit - 3)
    toks = ntok(text)
    return {"name": rel_of(p) or p.name, "text": text, "tokens": toks, "truncated": len(data) > limit, "lossy": lossy}


def fit(messages, max_new, model=None):
    """Drop the oldest turns so the prompt fits the context window."""
    model = model or llm
    budget = model.n_ctx() - (max_new or 512) - 64
    cost = lambda m: len(model.tokenize(str(m.get("content", "")).encode(), add_bos=False)) + 8
    msgs, trimmed = list(messages), 0
    total = sum(cost(m) for m in msgs)
    i = 1 if msgs and msgs[0]["role"] == "system" else 0
    while total > budget and len(msgs) - i > 1:
        total -= cost(msgs.pop(i)); trimmed += 1
    while len(msgs) - i > 1 and msgs[i]["role"] != "user":
        msgs.pop(i); trimmed += 1
    return msgs, trimmed


@app.get("/")
def index():
    return FileResponse(HERE / "index.html")


@app.get("/api/info")
def info():
    c = CFG["coder"]
    path = c["path"]
    meta = META if META.get("path") == path else {}
    try:
        size = os.path.getsize(path) if path else 0
    except OSError:
        size = 0
    return {
        "file": os.path.basename(path) if path else "", "file_size": size,
        "load_seconds": round(LOAD_S["coder"], 2), "n_ctx": meta.get("n_ctx") or c["n_ctx"], "n_vocab": meta.get("n_vocab"),
        "n_embd": meta.get("n_embd"), "n_params": meta.get("n_params"),
        "n_gpu_layers": c["n_gpu_layers"], "n_threads": meta.get("n_threads"), "metadata": meta.get("metadata", {}),
        "vision": os.path.basename(VL_MODEL) if VL_OK else None, "model_state": STATE["model"], "loaded": llm is not None,
    }


@app.get("/api/system")
def system():
    if not PROC:
        return {}
    return {"rss": PROC.memory_info().rss, "cpu": PROC.cpu_percent(interval=None),
            "ram_total": psutil.virtual_memory().total}


def sse(obj):
    return f"data: {json.dumps(obj)}\n\n"


@app.post("/api/chat")
async def chat(req: Request):
    b = await req.json()

    def gen():
        with LOCK:
            try:
                with SWAP:
                    load_coder()    # no-op normally; reloads the coder if a vision run was interrupted
                msgs, trimmed = fit(b["messages"], b.get("max_tokens"))
            except Exception as e:
                yield sse({"type": "error", "message": f"Could not load the coder model: {e}"})
                return
            start = time.perf_counter()
            first = None
            n = 0
            finish = None
            try:
                stream = llm.create_chat_completion(
                    messages=msgs, stream=True,
                    temperature=b.get("temperature", 0.7), top_p=b.get("top_p", 0.95),
                    top_k=b.get("top_k", 40), max_tokens=b.get("max_tokens") or None,
                    repeat_penalty=b.get("repeat_penalty", 1.1))
                for chunk in stream:
                    ch = chunk["choices"][0]
                    if ch.get("finish_reason"):
                        finish = ch["finish_reason"]
                    text = ch["delta"].get("content")
                    if text is not None:
                        first = first or time.perf_counter()
                        n += 1
                        yield sse({"type": "token", "text": text})
            except Exception as e:
                yield sse({"type": "error", "message": str(e)})
                return
            end = time.perf_counter()
            used = getattr(llm, "n_tokens", None)
            gen_s = (end - first) if first else 0
            yield sse({"type": "done", "trimmed": trimmed, "finish_reason": finish, "completion_tokens": n,
                       "prompt_tokens": (used - n) if used else None, "ctx_used": used,
                       "ttft": (first - start) if first else None, "gen_seconds": gen_s,
                       "tps": ((n - 1) / gen_s) if gen_s > 0 and n > 1 else None})

    return StreamingResponse(gen(), media_type="text/event-stream")


@app.get("/api/fs")
def fs(path: str = ""):
    p = safe(path)
    if not p.is_dir():
        raise HTTPException(400, "Not a folder")
    items = []
    for c in sorted(p.iterdir(), key=lambda c: (not c.is_dir(), c.name.lower())):
        if c.name in SKIP:
            continue
        try:
            items.append({"name": c.name, "dir": c.is_dir(), "size": c.stat().st_size})
        except OSError:
            pass
    return {"root": str(ROOT), "path": rel_of(p), "items": items[:500]}


@app.get("/api/file")
def read_file(path: str):
    p = safe(path)
    if not p.exists():
        raise HTTPException(404, "No such file")
    if not p.is_file():
        raise HTTPException(400, "Not a file")
    r = load_text(p, MAX_FILE)
    if r is None:
        raise HTTPException(415, "Binary or unreadable file")
    return r


@app.get("/api/folder")
def read_folder(path: str = ""):
    p = safe(path)
    if not p.is_dir():
        raise HTTPException(400, "Not a folder")
    files, total, skipped = [], 0, 0
    for dp, dns, fns in os.walk(p):
        dns[:] = sorted(d for d in dns if d not in SKIP)
        for fn in sorted(fns):
            if len(files) >= 40 or total >= FOLDER_TOTAL:
                skipped += 1
                continue
            r = load_text(Path(dp) / fn, FOLDER_FILE)
            if r is None:
                skipped += 1
                continue
            total += len(r["text"])
            files.append(r)
    return {"files": files, "skipped": skipped}


def walk_files():
    out = []
    for dp, dns, fns in os.walk(ROOT):
        dns[:] = sorted(d for d in dns if d not in SKIP)
        for fn in sorted(fns):
            if Path(fn).suffix.lower() not in BIN:
                out.append(Path(dp) / fn)
            if len(out) >= 4000:
                return out
    return out


@app.get("/api/files")
def files():
    return {"files": [rel_of(p) for p in walk_files()]}


@app.get("/api/search")
def search(q: str):
    q, hits = q.lower(), []
    for p in walk_files():
        try:
            if p.stat().st_size > 500_000:
                continue
            for n, line in enumerate(p.read_text("utf-8", errors="ignore").splitlines(), 1):
                if q in line.lower():
                    hits.append({"file": rel_of(p), "line": n, "text": line[:200]})
                    if len(hits) >= 100:
                        return {"hits": hits}
        except OSError:
            pass
    return {"hits": hits}


@app.get("/api/git")
def git(mode: str = "work"):
    args_ = {"work": ["diff"], "staged": ["diff", "--staged"], "last": ["show", "--stat", "--patch", "-1"]}.get(mode)
    if not args_ or not shutil.which("git"):
        raise HTTPException(400, "git is not installed" if args_ else "bad mode")
    r = subprocess.run(["git", "-C", str(ROOT), *args_], capture_output=True, text=True, timeout=15)
    if r.returncode:
        raise HTTPException(400, (r.stderr or "git failed").strip()[:200])
    if not r.stdout.strip():
        raise HTTPException(404, "No changes to show")
    text = r.stdout[:FOLDER_TOTAL]
    return {"name": f"git-{mode}.diff", "text": text, "truncated": len(r.stdout) > FOLDER_TOTAL,
            "tokens": ntok(text)}


# ---------------------------------------------------------------- vision: swap coder <-> Qwen2.5-VL

VISION_PROMPT = (
    "You are the eyes of a text-only coding assistant. Study the image and write down everything it needs to know.\n"
    "1. Say what kind of image it is (screenshot, error dialog, diagram, UI mockup, chart, photo, handwriting...).\n"
    "2. Transcribe ALL readable text exactly as written: code, commands, error messages, logs, labels, numbers. "
    "Keep line breaks and indentation, and put code or logs in fenced blocks.\n"
    "3. Describe the structure and the important visual details: layout, UI elements, shapes, colours, objects, "
    "arrows and relationships, chart axes and values.\n"
    "4. Mention anything cut off, blurry or unreadable instead of guessing it.\n"
    "Be factual and complete. Do not answer any question about the image; only report what is in it."
)
DATA_URL = re.compile(r"^data:image/(png|jpeg|jpg|gif|bmp|webp);base64,[A-Za-z0-9+/=\s]+$")


def load_vision():
    try:
        from llama_cpp.llama_chat_format import Qwen25VLChatHandler
    except ImportError:
        raise RuntimeError("this llama-cpp-python has no Qwen2.5-VL support (Qwen25VLChatHandler is missing). "
                           "Upgrade it:  pip install -U llama-cpp-python  (use the same CUDA/Metal build as before)")
    handler = Qwen25VLChatHandler(clip_model_path=VL_MMPROJ, verbose=False)
    try:
        kw = llama_kw("vision")
        kw["model_path"] = VL_MODEL
        m = Llama(chat_handler=handler, **kw)
    except Exception:
        free_llama(None, handler)
        raise
    return m, handler


def vision_gen(images, hint, keep_unloaded=False):
    """Unload the coder -> load the VL model -> extract details -> unload it -> reload the coder.
    keep_unloaded: a reasoning step follows, so leave the coder out of memory (the reasoning step reloads it)."""
    n = len(images)
    err = None
    ok = False
    with LOCK:
        vl = handler = None
        t0 = time.perf_counter()
        try:
            yield sse({"type": "stage", "stage": "unload_coder", "label": "Unloading coder model…"})
            with SWAP:
                unload_coder()
                unload_reasoner()
            yield sse({"type": "stage", "stage": "load_vision", "label": "Loading vision model…"})
            with SWAP:
                vl, handler = load_vision()
            STATE["model"] = "vision"
            ask = VISION_PROMPT
            if hint:
                ask += ("\n\nThe user's message about this image (only use it to decide which details matter, "
                        "do not answer it): " + json.dumps(hint[:600]))
            for i, im in enumerate(images):
                yield sse({"type": "stage", "stage": "analyze",
                           "label": f"Reading image {i + 1} of {n}…" if n > 1 else "Reading the image…"})
                yield sse({"type": "image_start", "index": i})
                try:
                    vl.reset()
                except Exception:
                    pass
                stream = vl.create_chat_completion(
                    messages=[{"role": "user", "content": [
                        {"type": "image_url", "image_url": {"url": im["data"]}},
                        {"type": "text", "text": ask}]}],
                    stream=True, temperature=0.1, top_p=0.9, top_k=20, repeat_penalty=1.0,
                    max_tokens=args.vision_max_tokens)
                finish = None
                for chunk in stream:
                    ch = chunk["choices"][0]
                    if ch.get("finish_reason"):
                        finish = ch["finish_reason"]
                    text = ch["delta"].get("content")
                    if text:
                        yield sse({"type": "token", "index": i, "text": text})
                yield sse({"type": "image_done", "index": i, "finish_reason": finish})
            yield sse({"type": "stage", "stage": "unload_vision", "label": "Unloading vision model…"})
            with SWAP:
                free_llama(vl, handler)
            vl = handler = None
            STATE["model"] = "none"
            ok = True
            if not keep_unloaded:
                yield sse({"type": "stage", "stage": "load_coder", "label": "Loading coder model…"})
                with SWAP:
                    load_coder()
        except GeneratorExit:
            raise
        except Exception as e:
            err = str(e) or e.__class__.__name__
            yield sse({"type": "stage", "stage": "load_coder", "label": "Restoring coder model…"})
        finally:
            # every exit path (done, error, user pressed Stop) ends with the vision model gone and the coder back
            if vl is not None or handler is not None:
                with SWAP:
                    free_llama(vl, handler)
            if STATE["model"] == "vision":
                STATE["model"] = "none"
            if not (keep_unloaded and ok and err is None):
                try:
                    with SWAP:
                        load_coder()
                except Exception as e:
                    print("could not reload the coder model:", e)
    if err:
        yield sse({"type": "error", "message": err})
    else:
        yield sse({"type": "done", "seconds": round(time.perf_counter() - t0, 2)})


@app.post("/api/vision")
async def vision(req: Request):
    need_ui(req)
    if not VL_OK:
        raise HTTPException(503, f"Image analysis is unavailable: {VL_WHY}")
    b = await req.json()
    imgs = b.get("images")
    if not isinstance(imgs, list) or not 1 <= len(imgs) <= 6:
        raise HTTPException(400, "send 1 to 6 images")
    for im in imgs:
        d = im.get("data") if isinstance(im, dict) else None
        if not isinstance(d, str) or len(d) > 30_000_000 or not DATA_URL.match(d):
            raise HTTPException(400, "images must be base64 data URLs (png, jpeg, gif, bmp, webp)")
    return StreamingResponse(vision_gen(imgs, str(b.get("hint") or "").strip(), bool(b.get("keep_unloaded"))),
                             media_type="text/event-stream")


# ---------------------------------------------------------------- workspace: assets, writing files, terminal
ASSETS = (HERE / "assets").resolve()
CAN_EXEC = LOCAL or args.unsafe_remote_exec
CAN_RUN = CAN_EXEC and not args.no_terminal
CAN_WRITE = CAN_EXEC and not args.read_only
MAX_WRITE = 2_000_000   # bytes per file written
MAX_OUT = 200_000       # characters of command output streamed back
RUNS = {}               # run id -> Popen, so the UI can stop a command


@app.get("/assets/{name}")
def asset(name: str):
    p = (ASSETS / name).resolve()
    if not p.is_file() or not p.is_relative_to(ASSETS):
        raise HTTPException(404, "Not found")
    return FileResponse(p)


@app.get("/api/caps")
def caps():
    return {"terminal": CAN_RUN, "write": CAN_WRITE, "root": str(ROOT), "os": platform.system(),
            "vision": VL_OK, "vision_why": VL_WHY, "reasoner": bool(CFG["reasoner"]["path"]), "reasoner_why": R_WHY,
            "can_manage": CAN_EXEC,
            "shell": os.environ.get("COMSPEC", "cmd") if os.name == "nt" else os.environ.get("SHELL", "/bin/sh")}


def need_ui(req):
    """State-changing endpoints require a custom header, which forces a CORS preflight for other sites."""
    if req.headers.get("x-cressy") != "1":
        raise HTTPException(403, "Missing X-Cressy header")


# ---------------------------------------------------------------- models: manual load / unload / tweak, and the reasoning swap
BUSY_MSG = "A model is busy (generating, reading an image or swapping). Press Stop or wait, then try again."
CFG_FIELDS = ("n_ctx", "n_gpu_layers", "n_threads", "n_batch", "flash_attn", "chat_format")


def need_manage():
    if not CAN_EXEC:
        raise HTTPException(403, "Managing models from the UI is disabled on a non-loopback host (use --unsafe-remote-exec)")


def slot_status(slot):
    c = CFG[slot]
    p = c["path"]
    try:
        size = os.path.getsize(p) if p else 0
    except OSError:
        size = 0
    loaded = slot_loaded(slot)
    out = {"slot": slot, "loaded": loaded, "path": p, "name": os.path.basename(p) if p else "", "size": size,
           "cfg": {k: c.get(k) for k in CFG_FIELDS},
           "dirty": bool(loaded and slot in LOADED_CFG and any(LOADED_CFG[slot].get(k) != c.get(k) for k in CFG_FIELDS))}
    if slot in LOAD_S:
        out["load_seconds"] = round(LOAD_S[slot], 2)
    if slot == "reasoner":
        out["why"] = R_WHY
        out["n_ctx_loaded"] = rllm.n_ctx() if rllm is not None else None
    if slot == "coder":
        out["n_ctx_loaded"] = llm.n_ctx() if llm is not None else None
    if slot == "vision":
        out["available"] = VL_OK
        out["why"] = VL_WHY
        out["mmproj"] = os.path.basename(VL_MMPROJ) if VL_MMPROJ else ""
    return out


def status():
    ms = scan_models()
    names = {m["name"].lower() for m in ms}
    return {"state": STATE["model"], "busy": LOCK.locked(),
            "slots": {k: slot_status(k) for k in ("coder", "reasoner", "vision")},
            "available": [m for m in ms if not m["mmproj"]],
            "preferred": [n for n in REASONER_PREFERRED if n.lower() in names],
            "wanted": REASONER_PREFERRED, "dirs": [str(d) for d in model_dirs()]}


@app.get("/api/models")
def models_state():
    return status()


def allowed_model(path):
    try:
        rp = Path(str(path)).expanduser().resolve()
    except (OSError, ValueError):
        raise HTTPException(400, "Bad model path")
    for m in scan_models():
        if not m["mmproj"] and Path(m["path"]) == rp:
            return str(rp)
    raise HTTPException(400, "That file is not in the models folder list")


def num(v, lo, hi, label):
    try:
        n = int(v)
    except (TypeError, ValueError):
        raise HTTPException(400, f"{label} must be a whole number")
    if not lo <= n <= hi:
        raise HTTPException(400, f"{label} must be between {lo} and {hi}")
    return n


def do_load(slot, path, force):
    if not LOCK.acquire(blocking=False):
        raise HTTPException(409, BUSY_MSG)
    try:
        if path:
            new = allowed_model(path)
            cur = CFG[slot]["path"]
            if slot_loaded(slot) and (not cur or Path(cur) != Path(new)):
                with SWAP:
                    SLOT_UNLOAD[slot]()
            CFG[slot] = {**CFG[slot], "path": new}
        elif not CFG[slot]["path"]:
            raise HTTPException(400, "Choose a model file first")
        if force and slot_loaded(slot):
            with SWAP:
                SLOT_UNLOAD[slot]()
        with SWAP:
            SLOT_LOAD[slot]()
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(500, f"Could not load the {slot} model: {e}")
    finally:
        LOCK.release()
    return status()


def do_unload(which):
    if not LOCK.acquire(blocking=False):
        raise HTTPException(409, BUSY_MSG)
    try:
        with SWAP:
            for slot in (("coder", "reasoner") if which == "all" else (which,)):
                SLOT_UNLOAD[slot]()
    finally:
        LOCK.release()
    return status()


def do_config(slot, new, reload):
    if not LOCK.acquire(blocking=False):
        raise HTTPException(409, BUSY_MSG)
    try:
        CFG[slot] = new
        if reload and slot in SLOT_LOAD and slot_loaded(slot):
            try:
                with SWAP:
                    SLOT_UNLOAD[slot]()
                    SLOT_LOAD[slot]()
            except Exception as e:
                raise HTTPException(500, f"Could not reload the {slot} model with those settings: {e}")
    finally:
        LOCK.release()
    return status()


async def model_body(req):
    need_ui(req)
    need_manage()
    b = await req.json()
    if not isinstance(b, dict):
        raise HTTPException(400, "bad request")
    return b


@app.post("/api/model/load")
async def model_load(req: Request):
    b = await model_body(req)
    slot = b.get("slot")
    if slot not in SLOT_LOAD:
        raise HTTPException(400, "slot must be coder or reasoner")
    return await run_in_threadpool(do_load, slot, b.get("path"), bool(b.get("force")))


@app.post("/api/model/unload")
async def model_unload(req: Request):
    b = await model_body(req)
    which = b.get("slot", "all")
    if which not in ("coder", "reasoner", "all"):
        raise HTTPException(400, "slot must be coder, reasoner or all")
    return await run_in_threadpool(do_unload, which)


@app.post("/api/model/config")
async def model_config(req: Request):
    b = await model_body(req)
    slot = b.get("slot")
    if slot not in CFG:
        raise HTTPException(400, "unknown slot")
    c = dict(CFG[slot])
    if "n_ctx" in b:
        c["n_ctx"] = num(b["n_ctx"], 512, 1_048_576, "Context size")
    if "n_gpu_layers" in b:
        c["n_gpu_layers"] = num(b["n_gpu_layers"], -1, 1000, "GPU layers")
    if "n_threads" in b:
        c["n_threads"] = num(b["n_threads"], 0, 512, "Threads")
    if "n_batch" in b:
        c["n_batch"] = num(b["n_batch"], 0, 16384, "Batch size")
    if "flash_attn" in b:
        c["flash_attn"] = bool(b["flash_attn"])
    if "chat_format" in b and slot != "vision":
        cf = str(b["chat_format"] or "").strip()
        if not re.fullmatch(r"[\w.-]{0,40}", cf):
            raise HTTPException(400, "Chat format looks wrong (letters, digits, - _ . only)")
        c["chat_format"] = cf
    return await run_in_threadpool(do_config, slot, c, bool(b.get("reload")))


REASON_SYS = (
    "You are the reasoning engine for a coding assistant. A different model will write the final answer after you, "
    "using your notes. Work the task through: state the goal in one line, list constraints and edge cases, compare the "
    "plausible approaches and choose one, check it for pitfalls, then outline the steps or the structure of the solution. "
    "Do NOT write the final answer, complete code or finished prose; short snippets are fine only where they pin down a decision. "
    "Be concrete and economical. Plain text, no greeting, no closing remarks."
)


def reason_gen(msgs, context, o):
    """Unload the coder -> load the reasoner -> stream its reasoning -> unload it -> reload the coder."""
    err = None
    t0 = time.perf_counter()
    n = 0
    finish = None
    first = None
    with LOCK:
        try:
            if llm is not None:
                yield sse({"type": "stage", "stage": "unload_coder", "label": "Unloading coder model…"})
            with SWAP:
                unload_coder()
            if rllm is None:
                yield sse({"type": "stage", "stage": "load_reasoner", "label": "Loading " + (os.path.basename(CFG["reasoner"]["path"]) or "reasoner") + "…"})
            with SWAP:
                load_reasoner()
            yield sse({"type": "stage", "stage": "reasoning", "label": "Reasoning…"})
            system = o["system"] or REASON_SYS
            if context:
                system += "\n\nStanding instructions the assistant works under (context only, do not follow them yourself):\n" + context[:3000]
            m, trimmed = fit([{"role": "system", "content": system}, *msgs], o["max_tokens"], rllm)
            if len(m) == 2:   # a single huge message is left: keep its tail (the question usually comes last)
                budget = (rllm.n_ctx() - o["max_tokens"] - 64) * 3
                body = str(m[1]["content"])
                if len(body) > budget:
                    m[1] = {**m[1], "content": "[…earlier part cut to fit the reasoning model's context…]\n" + body[-budget:]}
            def start(mm):
                return rllm.create_chat_completion(
                    messages=mm, stream=True, temperature=o["temperature"], top_p=o["top_p"], top_k=o["top_k"],
                    repeat_penalty=o["repeat_penalty"], max_tokens=o["max_tokens"], stop=["</think>"])
            try:
                stream = start(m)
            except Exception as e:
                # some chat templates (older Gemma ones) refuse a system role: fold it into the first user turn and retry
                if "system" in str(e).lower() and m[0]["role"] == "system" and len(m) > 1 and m[1]["role"] == "user":
                    m = [{"role": "user", "content": m[0]["content"] + "\n\n---\n\n" + str(m[1]["content"])}, *m[2:]]
                    stream = start(m)
                else:
                    raise
            for chunk in stream:
                ch = chunk["choices"][0]
                if ch.get("finish_reason"):
                    finish = ch["finish_reason"]
                text = ch["delta"].get("content")
                if text:
                    first = first or time.perf_counter()
                    n += 1
                    yield sse({"type": "token", "text": text})
            yield sse({"type": "stage", "stage": "unload_reasoner", "label": "Unloading reasoning model…"})
            with SWAP:
                unload_reasoner()
            if CFG["coder"]["path"]:
                yield sse({"type": "stage", "stage": "load_coder", "label": "Loading coder model…"})
                with SWAP:
                    load_coder()
        except GeneratorExit:
            raise
        except Exception as e:
            err = str(e) or e.__class__.__name__
            yield sse({"type": "stage", "stage": "load_coder", "label": "Restoring coder model…"})
        finally:
            # every exit path (done, error, user pressed Stop) ends with the reasoner gone and the coder back
            with SWAP:
                unload_reasoner()
            if CFG["coder"]["path"]:
                try:
                    with SWAP:
                        load_coder()
                except Exception as e:
                    print("could not reload the coder model:", e)
    if err:
        yield sse({"type": "error", "message": err})
    else:
        gen = (time.perf_counter() - first) if first else 0
        yield sse({"type": "done", "finish_reason": finish, "tokens": n, "seconds": round(time.perf_counter() - t0, 2),
                   "tps": ((n - 1) / gen) if gen > 0 and n > 1 else None})


@app.post("/api/reason")
async def reason(req: Request):
    need_ui(req)
    if not CFG["reasoner"]["path"]:
        raise HTTPException(503, "No reasoning model is selected: " + (R_WHY or "pick one in the Model tab"))
    b = await req.json()
    raw = b.get("messages")
    if not isinstance(raw, list) or not raw:
        raise HTTPException(400, "messages are required")
    msgs = [{"role": x["role"], "content": x["content"]} for x in raw[-24:]
            if isinstance(x, dict) and x.get("role") in ("user", "assistant") and isinstance(x.get("content"), str)]
    while msgs and msgs[0]["role"] != "user":
        msgs.pop(0)
    if not msgs:
        raise HTTPException(400, "no user message to reason about")

    def f(key, default, lo, hi, cast=float):
        try:
            return min(hi, max(lo, cast(b.get(key, default))))
        except (TypeError, ValueError):
            return default

    o = {"temperature": f("temperature", 0.6, 0, 2), "top_p": f("top_p", 0.95, 0, 1), "top_k": f("top_k", 20, 0, 1000, int),
         "repeat_penalty": f("repeat_penalty", 1.0, 1.0, 2.0), "max_tokens": f("max_tokens", 1500, 64, 8192, int),
         "system": str(b.get("system") or "").strip()[:4000]}
    return StreamingResponse(reason_gen(msgs, str(b.get("context") or "").strip(), o), media_type="text/event-stream")


def write_text(rel, text):
    """Write a UTF-8 text file inside ROOT (atomic). Keeps CRLF line endings if the old file used them."""
    p = safe(rel)
    if p == ROOT or p.is_dir():
        raise HTTPException(400, "That path is a folder, not a file")
    if ".git" in p.relative_to(ROOT).parts:
        raise HTTPException(403, "Writing inside .git is not allowed")
    data = text.encode("utf-8")
    if len(data) > MAX_WRITE:
        raise HTTPException(413, "File is too large to write")
    existed = p.exists()
    try:
        if existed:
            old = p.read_bytes()
            if b"\0" in old[:4096]:
                raise HTTPException(415, "Refusing to overwrite a binary file")
            if b"\r\n" in old and b"\r\n" not in data:
                data = data.replace(b"\n", b"\r\n")
        p.parent.mkdir(parents=True, exist_ok=True)
        tmp = p.with_name(p.name + ".cressy-tmp")
        tmp.write_bytes(data)
        os.replace(tmp, p)
    except HTTPException:
        raise
    except OSError as e:
        raise HTTPException(400, f"Could not write: {e.strerror or e}")
    return {"path": rel_of(p), "bytes": len(data), "created": not existed}


@app.post("/api/write")
async def write_endpoint(req: Request):
    need_ui(req)
    if not CAN_WRITE:
        raise HTTPException(403, "Writing files is disabled (--read-only, or a non-loopback host)")
    b = await req.json()
    if not isinstance(b.get("path"), str) or not isinstance(b.get("text"), str) or not b["path"].strip():
        raise HTTPException(400, "path and text are required")
    return write_text(b["path"], b["text"])


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


def run_stream(cmd, cwd, timeout):
    """Run a shell command inside ROOT and yield server-sent events: start, out (many), done."""
    env = {**os.environ, "PYTHONUNBUFFERED": "1", "NO_COLOR": "1", "TERM": "dumb", "GIT_PAGER": "cat", "PAGER": "cat"}
    kw = dict(stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
              cwd=str(cwd), env=env, bufsize=0, shell=True)
    if os.name == "nt":
        kw["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP
    else:
        kw["start_new_session"] = True
    try:
        proc = subprocess.Popen(cmd, **kw)
    except Exception as e:
        yield sse({"type": "error", "message": str(e)})
        return
    rid = uuid.uuid4().hex[:8]
    RUNS[rid] = proc
    flags = {"timeout": False, "cap": False}

    def on_timeout():
        flags["timeout"] = True
        kill_tree(proc)

    timer = threading.Timer(timeout, on_timeout)
    timer.start()
    t0 = time.perf_counter()
    dec = codecs.getincrementaldecoder("utf-8")(errors="replace")
    sent = 0
    try:
        yield sse({"type": "start", "id": rid, "cwd": rel_of(cwd) or "."})
        while True:
            chunk = proc.stdout.read(4096)   # raw pipe: returns whatever is available, so output streams live
            if not chunk:
                break
            text = dec.decode(chunk)
            if sent + len(text) > MAX_OUT:
                text, flags["cap"] = text[:max(0, MAX_OUT - sent)], True
            if text:
                sent += len(text)
                yield sse({"type": "out", "text": text})
            if flags["cap"]:
                kill_tree(proc)
                break
        code = proc.wait()
        yield sse({"type": "done", "code": code, "timeout": flags["timeout"], "truncated": flags["cap"],
                   "killed": bool(getattr(proc, "cressy_killed", False)), "seconds": round(time.perf_counter() - t0, 2)})
    finally:
        timer.cancel()
        if proc.poll() is None:
            kill_tree(proc)
        RUNS.pop(rid, None)


@app.post("/api/run")
async def run_cmd(req: Request):
    need_ui(req)
    if not CAN_RUN:
        raise HTTPException(403, "The terminal is disabled (--no-terminal, or a non-loopback host)")
    b = await req.json()
    cmd = str(b.get("cmd") or "").strip()
    if not cmd or len(cmd) > 8000:
        raise HTTPException(400, "Empty or too long command")
    cwd = safe(str(b.get("cwd") or ""))
    if not cwd.is_dir():
        raise HTTPException(400, "Working folder does not exist")
    try:
        timeout = max(1, min(int(b.get("timeout") or 60), 300))
    except (TypeError, ValueError):
        timeout = 60
    return StreamingResponse(run_stream(cmd, cwd, timeout), media_type="text/event-stream")


@app.post("/api/kill")
async def kill_run(req: Request):
    need_ui(req)
    b = await req.json()
    proc = RUNS.get(b.get("id"))
    if proc:
        proc.cressy_killed = True
        kill_tree(proc)
    return {"ok": bool(proc)}


@app.post("/api/tokens")
async def count_tokens(req: Request):
    b = await req.json()
    return {"tokens": ntok(b["text"])}


if __name__ == "__main__":
    print(f"Open http://{args.host}:{args.port}   (project folder: {ROOT})")
    print("Coder: " + (os.path.basename(CFG["coder"]["path"]) + ("" if llm is not None else "  (not loaded yet)") if CFG["coder"]["path"] else "none selected (pick one in the Model tab)"))
    print("Reasoner: " + (os.path.basename(CFG["reasoner"]["path"]) + "  (loaded only while reasoning; the coder is unloaded meanwhile)" if CFG["reasoner"]["path"] else f"off ({R_WHY})"))
    print("Vision: " + (f"{os.path.basename(VL_MODEL)} + {os.path.basename(VL_MMPROJ)} (loaded only while an image is read; the coder is unloaded meanwhile)"
                         if VL_OK else f"off ({VL_WHY})"))
    print(f"Terminal: {'on' if CAN_RUN else 'off'}   Write files: {'on' if CAN_WRITE else 'off'}"
          + ("   !! non-loopback host with --unsafe-remote-exec: anyone who can reach this port can run commands !!"
             if (not LOCAL and args.unsafe_remote_exec) else ""))
    if args.open:
        threading.Timer(1.0, webbrowser.open, [f"http://{args.host}:{args.port}"]).start()
    uvicorn.run(app, host=args.host, port=args.port, log_level="warning")
