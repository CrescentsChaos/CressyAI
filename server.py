#!/usr/bin/env python3
"""Local GGUF chat server.   python server.py --model path/to/model.gguf   (or start without --model and pick models in the app)"""
import argparse, base64, codecs, gc, importlib.util, json, math, os, platform, re, shutil, signal, subprocess, sys, tempfile, threading, time, uuid, webbrowser
from io import BytesIO
from collections import deque
from pathlib import Path

import uvicorn
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from pypdf import PdfReader
from pypdf.errors import PdfReadError
from starlette.concurrency import run_in_threadpool
from llama_cpp import Llama

import websearch

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
ap.add_argument("--image-model", default=None,
                help="Stable Diffusion .gguf for /imagine and /imgedit (default: stable-diffusion-xl-base-1.0-Q5_K_M.gguf in the models folder)")
ap.add_argument("--image-vae", default=None, help="optional separate VAE (e.g. sdxl-vae-fp16-fix.safetensors) if images come out black")
ap.add_argument("--image-clip-l", default=None, help="CLIP-L text encoder file, only for UNet-only SDXL .gguf files (default: auto-find clip_l* next to the model)")
ap.add_argument("--image-clip-g", default=None, help="CLIP-G text encoder file, only for UNet-only SDXL .gguf files (default: auto-find clip_g*)")
ap.add_argument("--image-dir", default=None, help="where generated images are saved (default: ./generated next to server.py)")
ap.add_argument("--data-dir", default=None, help="where conversation history is stored, one JSON file per chat (default: ./data next to server.py)")
ap.add_argument("--image-threads", type=int, default=None, help="CPU threads for image generation (default: auto)")
ap.add_argument("--image-flash-attn", action="store_true", help="flash attention for the diffusion model (CUDA builds)")
ap.add_argument("--image-low-vram", action="store_true", help="keep the text encoder and VAE on the CPU (slower, needs less VRAM)")
ap.add_argument("--image-no-vae-tiling", action="store_true", help="decode the whole image at once (faster, needs more VRAM)")
ap.add_argument("--no-image", action="store_true", help="disable image generation and editing")
ap.add_argument("--searxng-url", default=os.environ.get("SEARXNG_URL", "http://127.0.0.1:8080"),
                help="SearXNG address used by the Web button (default http://127.0.0.1:8080, or the SEARXNG_URL variable)")
ap.add_argument("--no-web", action="store_true", help="turn off web search and the page reader")
ap.add_argument("--web-allow-private", action="store_true", help="let the page reader open links on your own network (off by default)")
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
IMAGE_PREFERRED = ["stable-diffusion-xl-base-1.0-Q5_K_M.gguf"]
# files that belong to stable-diffusion.cpp, not llama.cpp: they must never be offered as coder / reasoner models
IMG_NAME_RE = re.compile(r"stable[-_ ]?diffusion|sdxl|sd[-_]?xl|\bsd[-_]?3|\bsd[-_]?1|\bsd[-_]?2|flux", re.I)


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
            out.append({"name": rp.name, "path": str(rp), "size": rp.stat().st_size, "mmproj": "mmproj" in rp.name.lower(),
                        "image": bool(IMG_NAME_RE.search(rp.name))})
        except OSError:
            pass

    for d in model_dirs():
        try:
            for f in sorted(d.glob("*.gguf"), key=lambda f: f.name.lower()):
                add(f)
        except OSError:
            pass
    for p in (args.model, args.reasoner, args.vision_model, args.image_model):
        if p:
            add(p)
    return out


def find_reasoner():
    if args.no_reasoner:
        return "", "disabled with --no-reasoner"
    if args.reasoner:
        p = Path(args.reasoner).expanduser()
        return (str(p.resolve()), "") if p.is_file() else ("", f"reasoner not found: {p}")
    byname = {m["name"].lower(): m for m in scan_models() if not m["mmproj"] and not m["image"]}
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
                cands += [f for f in d.glob("*.gguf") if "vl" in f.name.lower() and "mmproj" not in f.name.lower() and not IMG_NAME_RE.search(f.name)]
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


def find_image():
    if args.no_image:
        return "", "disabled with --no-image"
    if args.image_model:
        p = Path(args.image_model).expanduser()
        return (str(p.resolve()), "") if p.is_file() else ("", f"image model not found: {p}")
    ms = [m for m in scan_models() if m["image"] and not m["mmproj"]]
    byname = {m["name"].lower(): m for m in ms}
    for want in IMAGE_PREFERRED:
        if want.lower() in byname:
            return byname[want.lower()]["path"], ""
    if ms:
        return ms[0]["path"], ""
    return "", ("no Stable Diffusion .gguf found (looked for " + " or ".join(IMAGE_PREFERRED) + " in "
                + ", ".join(str(d) for d in model_dirs()) + "; use --image-model)")


IMG = {"path": "", "why": "", "proc": None, "cancel": False}
IMG["path"], IMG["why"] = find_image()
IMG_WORKER = Path(__file__).parent / "imagegen.py"
IMG_DEP = ("" if importlib.util.find_spec("stable_diffusion_cpp") and importlib.util.find_spec("PIL") else
           "stable-diffusion-cpp-python or Pillow is not installed. Run:  pip install -r requirements-image.txt")


def find_companions(model_path):
    """clip_l / clip_g / SDXL VAE files lying next to the model or in the models folders (used only for UNet-only checkpoints)."""
    found = {"clip_l": args.image_clip_l or "", "clip_g": args.image_clip_g or "", "auto_vae": ""}
    pats = {"clip_l": re.compile(r"(^|[^a-z])clip[-_ ]?l([^a-z]|$)", re.I), "clip_g": re.compile(r"(^|[^a-z])clip[-_ ]?g([^a-z]|$)", re.I),
            "auto_vae": re.compile(r"vae", re.I)}
    dirs = [Path(model_path).parent] + model_dirs()
    for d in dirs:
        try:
            files = sorted(f for f in d.iterdir() if f.suffix.lower() in (".safetensors", ".gguf", ".sft", ".pt") and f.is_file())
        except OSError:
            continue
        for key, rx in pats.items():
            if found[key]:
                continue
            cands = [f for f in files if rx.search(f.name) and not IMG_NAME_RE.search(f.name)]
            if key == "auto_vae":
                cands.sort(key=lambda f: ("xl" not in f.name.lower(), f.name.lower()))
            if cands:
                found[key] = str(cands[0])
    return found


def image_ready():
    """(ok, why-not). Checked on every call, because the Model tab can change the file and the folder can change too."""
    if args.no_image:
        return False, "disabled with --no-image"
    if not IMG["path"]:
        return False, IMG["why"] or "no Stable Diffusion model chosen"
    if not Path(IMG["path"]).is_file():
        return False, "image model file is gone: " + IMG["path"]
    if not IMG_WORKER.is_file():
        return False, "imagegen.py is missing next to server.py"
    if IMG_DEP:
        return False, IMG_DEP
    return True, ""


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
    return {"coder": llm is not None, "reasoner": rllm is not None, "vision": STATE["model"] == "vision",
            "image": STATE["model"] == "image"}[slot]


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
MAX_PDF_BYTES = 20 * 1024 * 1024
MAX_PDF_TEXT = 200_000
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


def load_pdf(data, name):
    """Extract selectable text from a PDF attachment, bounded by file and prompt size."""
    try:
        reader = PdfReader(BytesIO(data), strict=False)
        if reader.is_encrypted and not reader.decrypt(""):
            raise HTTPException(400, "This PDF is password-protected.")
        text = ""
        pages_read = 0
        truncated = False
        for page in reader.pages:
            pages_read += 1
            page_text = page.extract_text() or ""
            if not page_text:
                continue
            separator = "\n\n" if text else ""
            remaining = MAX_PDF_TEXT - len(text) - len(separator)
            if remaining <= 0:
                truncated = True
                break
            if len(page_text) > remaining:
                text += separator + page_text[:remaining]
                truncated = True
                break
            text += separator + page_text
        if not text.strip():
            raise HTTPException(422, "This PDF has no selectable text. Scanned PDFs need OCR, which is not supported yet.")
        return {"name": name, "text": text, "tokens": ntok(text), "truncated": truncated, "pages": pages_read}
    except HTTPException:
        raise
    except (PdfReadError, ValueError, KeyError) as e:
        raise HTTPException(400, f"Could not read PDF: {e}") from e


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
    if p.suffix.lower() == ".pdf":
        try:
            with p.open("rb") as f:
                data = f.read(MAX_PDF_BYTES + 1)
        except OSError as e:
            raise HTTPException(500, f"Could not read PDF: {e}") from e
        if len(data) > MAX_PDF_BYTES:
            raise HTTPException(413, "PDF is too large to attach (maximum 20 MB).")
        return load_pdf(data, rel_of(p) or p.name)
    r = load_text(p, MAX_FILE)
    if r is None:
        raise HTTPException(415, "Binary or unreadable file")
    return r


@app.post("/api/pdf")
async def read_uploaded_pdf(req: Request):
    need_ui(req)
    if req.headers.get("content-type", "").split(";", 1)[0].strip().lower() != "application/pdf":
        raise HTTPException(415, "Upload a PDF file.")
    data = bytearray()
    async for chunk in req.stream():
        if len(data) + len(chunk) > MAX_PDF_BYTES:
            raise HTTPException(413, "PDF is too large to attach (maximum 20 MB).")
        data.extend(chunk)
    if not data:
        raise HTTPException(400, "The PDF is empty.")
    return await run_in_threadpool(load_pdf, bytes(data), "document.pdf")


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


def walk_files(include_pdf=False):
    out = []
    for dp, dns, fns in os.walk(ROOT):
        dns[:] = sorted(d for d in dns if d not in SKIP)
        for fn in sorted(fns):
            suffix = Path(fn).suffix.lower()
            if suffix not in BIN or (include_pdf and suffix == ".pdf"):
                out.append(Path(dp) / fn)
            if len(out) >= 4000:
                return out
    return out


@app.get("/api/files")
def files():
    return {"files": [rel_of(p) for p in walk_files(include_pdf=True)]}


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


# ---------------------------------------------------------------- images: Stable Diffusion in a separate worker process
GEN_DIR = (Path(args.image_dir).expanduser() if args.image_dir else (HERE / "generated")).resolve()
MARK = "@@CRESSY@@ "
PROG_RE = re.compile(r"(\d+)\s*/\s*(\d+)\s*-\s*[\d.]+\s*(?:s/it|it/s)")   # sd.cpp's own progress line, used as a fallback
GEN_NAME = re.compile(r"^[A-Za-z0-9_.-]{1,120}\.png$")
SAMPLERS = ("euler_a", "euler", "dpm++2m", "dpm++2s_a", "heun", "dpm2", "lcm")
SCHEDULERS = ("default", "karras", "discrete", "exponential")
IMG_PROMPT_SYS = (
    "You write prompts for Stable Diffusion XL. Turn the user's request into ONE prompt: comma-separated phrases covering "
    "the subject, setting, style or medium, lighting, composition and quality (for example 'highly detailed, sharp focus'). "
    "Keep it under 60 words and keep every detail the user asked for; never swap in a different subject. "
    "If a current prompt and a requested change are given, output the full updated prompt: apply the change and keep "
    "everything else. If the request says what to leave out (no text, no blur...), add a second line that starts with "
    "NEGATIVE: and lists those things. Output only the prompt, with no explanation and no quotation marks."
)


def enhance_prompt(job):
    """Let the coder (already in memory) turn a casual request into a good SDXL prompt. Raises if it cannot."""
    if job["mode"] == "edit" and job.get("base_prompt"):
        user = f"Current prompt:\n{job['base_prompt']}\n\nRequested change:\n{job['prompt']}"
    elif job["mode"] == "edit":
        user = ("The user is editing an existing picture and described the change below. Write the prompt for the picture "
                f"as it should look after the change.\n\nRequested change:\n{job['prompt']}")
    else:
        user = f"Request:\n{job['prompt']}"
    r = llm.create_chat_completion(messages=[{"role": "system", "content": IMG_PROMPT_SYS}, {"role": "user", "content": user}],
                                   temperature=0.6, top_p=0.9, max_tokens=220, repeat_penalty=1.05)
    text = r["choices"][0]["message"].get("content") or ""
    text = re.sub(r"<think>[\s\S]*?</think>", "", text)
    text = re.sub(r"<think>[\s\S]*$", "", text).strip().strip("`\"' \n")
    parts = re.split(r"\n\s*NEGATIVE\s*:\s*", text, maxsplit=1, flags=re.I)
    pos = re.sub(r"^(?:positive\s+)?prompt\s*:\s*", "", " ".join(parts[0].split()), flags=re.I).strip("\"' ")
    neg = " ".join(parts[1].split()).strip("\"' ") if len(parts) > 1 else ""
    if not 3 <= len(pos) <= 1200:
        raise ValueError("the coder returned an empty or runaway prompt")
    return pos, neg[:400]


def log_hints(tail):
    """The few log lines worth showing: real errors first, never the Python traceback noise that follows a failed load."""
    noise = re.compile(r'^\s*(File "|\^+|Traceback|callback\(|self\.|return |raise |OSError|The above exception)')
    lines = [l for l in tail if not noise.match(l)]
    key = re.compile(r"error|fail|unknown|not found|missing|cannot|can't|unsupported|invalid|no such|out of memory|version", re.I)
    hits = [l for l in lines if key.search(l)]
    return (hits[:6] or lines[-6:]) or list(tail)[-3:]


def image_fail(code, tail):
    low = " ".join(tail).lower()
    msg = f"The image engine stopped unexpectedly (exit code {code})."
    if any(k in low for k in ("out of memory", "failed to allocate", "bad_alloc", "cuda error", "vk::")):
        msg += " It looks like it ran out of memory: pick a smaller size in Settings → Images, or start the server with --image-low-vram."
    if tail:
        msg += " Last output: " + " | ".join(log_hints(tail)[:3])
    return msg


def image_gen(job, tmp):
    """Optionally let the coder write the prompt -> unload every LLM -> run imagegen.py -> stream its progress -> reload the coder."""
    err, cancelled, ok = None, False, False
    state = {"done": False, "err": None, "prog": False, "seed": None}
    tail = deque(maxlen=120)
    t0 = time.perf_counter()

    def handle(ln):
        ln = ln.strip()
        if not ln:
            return None
        if ln.startswith(MARK):
            try:
                ev = json.loads(ln[len(MARK):])
            except ValueError:
                return None
            t = ev.get("type")
            if t == "image":
                ev["name"] = str(ev.pop("file"))
                ev["url"] = "/generated/" + ev["name"]
            elif t == "source":
                ev["url"] = "/generated/" + str(ev["name"])
            elif t == "progress":
                state["prog"] = True
            elif t == "done":
                state["done"], state["seed"] = True, ev.get("seed")
                return None
            elif t == "error":
                state["err"] = str(ev.get("message") or "the image engine failed")
                return None
            return ev
        m = PROG_RE.search(ln)
        if m and not state["prog"]:
            return {"type": "progress", "step": int(m.group(1)), "steps": int(m.group(2)), "approx": True}
        tail.append(ln[:300])
        return None

    with LOCK:
        IMG["cancel"] = False
        try:
            if job["enhance"] and CFG["coder"]["path"]:
                yield sse({"type": "stage", "stage": "prompt", "label": "Writing the prompt…"})
                try:
                    with SWAP:
                        load_coder()
                    pos, neg = enhance_prompt(job)
                    job["prompt"] = pos
                    if neg:   # merge, without repeating terms the user's own negative prompt already has
                        seen, merged = set(), []
                        for term in (job["negative"] + ", " + neg).split(","):
                            term = term.strip()
                            if term and term.lower() not in seen:
                                seen.add(term.lower())
                                merged.append(term)
                        job["negative"] = ", ".join(merged)
                except Exception as e:
                    yield sse({"type": "note", "message": "Prompt helper skipped, using your words as they are: "
                               + (str(e) or e.__class__.__name__)[:160]})
            yield sse({"type": "prompt", "prompt": job["prompt"], "negative": job["negative"]})
            if IMG["cancel"]:
                cancelled = True
            else:
                yield sse({"type": "stage", "stage": "unload_coder", "label": "Unloading coder model…"})
                with SWAP:
                    unload_coder()
                    unload_reasoner()
                STATE["model"] = "image"
                job.update(find_companions(IMG["path"]))
                
                job.update(model=IMG["path"], vae=args.image_vae or "", out_dir=str(GEN_DIR), threads=args.image_threads or 0,
                           flash_attn=bool(args.image_flash_attn), low_vram=bool(args.image_low_vram),
                           vae_tiling=not args.image_no_vae_tiling)
                print("IMAGE COMPONENT CHECK:", flush=True)
                for key in ("model", "clip_l", "clip_g", "vae"):
                    path = job.get(key, "")
                    print(
                        f"{key}: {path!r} | exists: {Path(path).is_file() if path else False}",
                        flush=True
                    )
                jobfile = Path(tmp) / "job.json"
                jobfile.write_text(json.dumps(job), "utf-8")
                kw = dict(stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, bufsize=0, cwd=str(HERE),
                          env={**os.environ, "PYTHONUNBUFFERED": "1", "PYTHONIOENCODING": "utf-8"})
                if os.name == "nt":
                    kw["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP | getattr(subprocess, "CREATE_NO_WINDOW", 0)
                else:
                    kw["start_new_session"] = True
                yield sse({"type": "stage", "stage": "load_image", "label": "Starting the image engine…"})
                proc = subprocess.Popen([sys.executable, str(IMG_WORKER), str(jobfile)], **kw)
                IMG["proc"] = proc
                dec = codecs.getincrementaldecoder("utf-8")(errors="replace")
                buf = ""
                while True:
                    chunk = proc.stdout.read(4096)   # raw pipe: returns whatever is available
                    if not chunk:
                        break
                    buf += dec.decode(chunk)
                    parts = re.split(r"[\r\n]+", buf)
                    buf = parts.pop()
                    for ln in parts:
                        ev = handle(ln)
                        if ev:
                            yield sse(ev)
                ev = handle(buf)
                if ev:
                    yield sse(ev)
                code = proc.wait()
                if IMG["cancel"]:
                    cancelled = True
                elif state["err"]:
                    err = state["err"]
                    if tail:   # the engine's own log lines usually say why (missing VAE / text encoder, out of memory...)
                        err += "  Engine log: " + " | ".join(log_hints(tail))
                elif not state["done"]:
                    err = image_fail(code, tail)
                ok = state["done"] and not err and not cancelled
                if ok and CFG["coder"]["path"]:
                    yield sse({"type": "stage", "stage": "load_coder", "label": "Loading coder model…"})
                    with SWAP:
                        load_coder()
        except GeneratorExit:
            raise
        except Exception as e:
            err = str(e) or e.__class__.__name__
        finally:
            p_, IMG["proc"] = IMG["proc"], None
            if p_ is not None:
                if p_.poll() is None:
                    kill_tree(p_)
                try:
                    p_.wait(timeout=10)
                    p_.stdout.close()
                except Exception:
                    pass
            shutil.rmtree(tmp, ignore_errors=True)
            if STATE["model"] == "image":
                STATE["model"] = "none"
            if CFG["coder"]["path"]:
                try:
                    with SWAP:
                        load_coder()
                except Exception as e:
                    print("could not reload the coder model:", e)
    if cancelled:
        yield sse({"type": "cancelled"})
    elif err:
        yield sse({"type": "error", "message": err})
    else:
        yield sse({"type": "done", "seconds": round(time.perf_counter() - t0, 2), "seed": state["seed"]})


def resolve_source(b, tmp):
    src = b.get("source")
    if not isinstance(src, dict):
        raise HTTPException(400, "Editing needs a source image")
    if isinstance(src.get("name"), str):
        if not GEN_NAME.match(src["name"]):
            raise HTTPException(400, "Bad image name")
        p = (GEN_DIR / src["name"]).resolve()
        if not p.is_file() or not p.is_relative_to(GEN_DIR):
            raise HTTPException(404, "That image is no longer on disk")
        return str(p)
    d = src.get("data")
    if not isinstance(d, str) or len(d) > 30_000_000 or not DATA_URL.match(d):
        raise HTTPException(400, "the source must be a base64 data URL (png, jpeg, gif, bmp, webp)")
    p = Path(tmp) / "source.img"
    p.write_bytes(base64.b64decode(re.sub(r"\s+", "", d.split(",", 1)[1])))
    return str(p)


@app.post("/api/image")
async def image_endpoint(req: Request):
    need_ui(req)
    ok, why = image_ready()
    if not ok:
        raise HTTPException(503, "Image generation is unavailable: " + why)
    b = await req.json()
    if not isinstance(b, dict):
        raise HTTPException(400, "bad request")

    def f(key, default, lo, hi, cast=float):
        try:
            return min(hi, max(lo, cast(b.get(key, default))))
        except (TypeError, ValueError):
            return default

    mode = "edit" if b.get("mode") == "edit" else "gen"
    prompt = str(b.get("prompt") or "").strip()[:2000]
    if not prompt:
        raise HTTPException(400, "Describe the image first")
    job = dict(mode=mode, prompt=prompt, negative=str(b.get("negative") or "").strip()[:1000],
               base_prompt=str(b.get("base_prompt") or "").strip()[:2000],
               width=f("width", 1024, 256, 2048, int), height=f("height", 1024, 256, 2048, int),
               steps=f("steps", 25, 1, 80, int), cfg=f("cfg", 7.0, 0.0, 30.0), strength=f("strength", 0.6, 0.05, 1.0),
               count=f("count", 1, 1, 4, int), seed=f("seed", -1, -1, 2 ** 31 - 9, int),
               sampler=b.get("sampler") if b.get("sampler") in SAMPLERS else "euler_a",
               scheduler=b.get("scheduler") if b.get("scheduler") in SCHEDULERS else "default",
               enhance=bool(b.get("enhance")))
    tmp = tempfile.mkdtemp(prefix="cressy-img-")
    try:
        if mode == "edit":
            job["source"] = resolve_source(b, tmp)
    except Exception:
        shutil.rmtree(tmp, ignore_errors=True)
        raise
    return StreamingResponse(image_gen(job, tmp), media_type="text/event-stream")


@app.post("/api/image/cancel")
async def image_cancel(req: Request):
    need_ui(req)
    IMG["cancel"] = True
    p = IMG["proc"]
    if p is not None and p.poll() is None:
        kill_tree(p)
        return {"ok": True}
    return {"ok": False}


@app.get("/generated/{name}")
def generated(name: str):
    p = (GEN_DIR / name).resolve()
    if not GEN_NAME.match(name) or not p.is_file() or not p.is_relative_to(GEN_DIR):
        raise HTTPException(404, "Not found")
    return FileResponse(p, headers={"Cache-Control": "public, max-age=31536000, immutable"})


@app.post("/api/image/save")
async def image_save(req: Request):
    need_ui(req)
    if not CAN_WRITE:
        raise HTTPException(403, "Writing files is disabled (--read-only, or a non-loopback host)")
    b = await req.json()
    name = str(b.get("name") or "") if isinstance(b, dict) else ""
    src = (GEN_DIR / name).resolve()
    if not GEN_NAME.match(name) or not src.is_file() or not src.is_relative_to(GEN_DIR):
        raise HTTPException(404, "That image is no longer on disk")
    folder = safe("generated-images")
    folder.mkdir(exist_ok=True)
    dst, n = folder / name, 1
    while dst.exists():
        dst = folder / f"{src.stem}-{n}{src.suffix}"
        n += 1
    shutil.copyfile(src, dst)
    return {"path": rel_of(dst)}


@app.post("/api/image/model")
async def image_model(req: Request):
    b = await model_body(req)
    try:
        rp = Path(str(b.get("path") or "")).expanduser().resolve()
    except (OSError, ValueError):
        raise HTTPException(400, "Bad model path")
    for m in scan_models():
        if m["image"] and not m["mmproj"] and Path(m["path"]) == rp:
            IMG["path"], IMG["why"] = str(rp), ""
            return status()
    raise HTTPException(400, "That file is not a Stable Diffusion model in the models folder list")


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
            "image": image_ready()[0], "image_why": image_ready()[1],
            "image_model": os.path.basename(IMG["path"]) if IMG["path"] else "",
            "can_manage": CAN_EXEC,
            "can_manage": CAN_EXEC, "web": WEB_ON, "web_url": SEARX if WEB_ON else "",
            "shell": os.environ.get("COMSPEC", "cmd") if os.name == "nt" else os.environ.get("SHELL", "/bin/sh")}


def need_ui(req):
    """State-changing endpoints require a custom header, which forces a CORS preflight for other sites."""
    if req.headers.get("x-cressy") != "1":
        raise HTTPException(403, "Missing X-Cressy header")


# ---------------------------------------------------------------- chats: persistent conversation history
DATA_DIR = Path(args.data_dir).expanduser().resolve() if args.data_dir else HERE / "data"
CHAT_DIR = DATA_DIR / "chats"
CHAT_ID = re.compile(r"^[A-Za-z0-9_-]{1,64}$")
MAX_CHAT_BYTES = 40_000_000
CHAT_LOCK = threading.Lock()


def chat_path(cid):
    if not isinstance(cid, str) or not CHAT_ID.fullmatch(cid):
        raise HTTPException(400, "Bad chat id")
    return CHAT_DIR / (cid + ".json")


def clean_chat(cid, b):
    """Check the shape of a chat from the browser; the file name is the id."""
    if not isinstance(b, dict) or not isinstance(b.get("msgs"), list):
        raise HTTPException(400, "A chat needs a msgs list")
    if len(b["msgs"]) > 5000:
        raise HTTPException(400, "Too many messages in one chat")
    msgs = [m for m in b["msgs"] if isinstance(m, dict) and m.get("role") in ("user", "assistant") and isinstance(m.get("content"), str)]
    return {"id": cid, "title": str(b.get("title") or "New chat")[:200], "pin": bool(b.get("pin")), "msgs": msgs,
            "updated": int(time.time() * 1000)}


def write_chat(chat):
    data = json.dumps(chat, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    if len(data) > MAX_CHAT_BYTES:
        raise HTTPException(413, "This chat is too large to store")
    p = chat_path(chat["id"])
    tmp = p.with_name("." + p.name + "." + uuid.uuid4().hex[:8] + ".tmp")
    with CHAT_LOCK:
        try:
            CHAT_DIR.mkdir(parents=True, exist_ok=True)
            tmp.write_bytes(data)
            os.replace(tmp, p)        # a crash mid-write can never leave a half-written chat behind
        except OSError as e:
            raise HTTPException(500, f"Could not save the chat to {CHAT_DIR}: {e}")
        finally:
            try:
                tmp.unlink()
            except OSError:
                pass


def read_chats():
    out = []
    try:
        files = list(CHAT_DIR.glob("*.json"))
    except OSError:
        return out
    for p in files:
        if not CHAT_ID.fullmatch(p.stem):
            continue
        try:
            c = json.loads(p.read_text("utf-8"))
        except (OSError, ValueError):
            print("skipping unreadable chat file:", p.name)
            continue
        if isinstance(c, dict) and isinstance(c.get("msgs"), list):
            c["id"] = p.stem
            out.append(c)
    out.sort(key=lambda c: c.get("updated") or 0, reverse=True)
    return out


def chat_guard(req):
    need_ui(req)    # a custom header also on reads: other websites cannot fetch your history
    try:
        if int(req.headers.get("content-length") or 0) > MAX_CHAT_BYTES * 2:
            raise HTTPException(413, "Request too large")
    except ValueError:
        pass


@app.get("/api/chats")
async def chats_list(req: Request):
    chat_guard(req)
    return {"chats": await run_in_threadpool(read_chats), "dir": str(CHAT_DIR)}


@app.post("/api/chats/import")
async def chats_import(req: Request):
    """Add chats that are not stored yet (moving old browser-only chats here, restoring a backup)."""
    chat_guard(req)
    b = await req.json()
    items = b.get("chats") if isinstance(b, dict) else None
    if not isinstance(items, list) or len(items) > 2000:
        raise HTTPException(400, "chats must be a list")

    def work():
        n = 0
        for c in items:
            cid = c.get("id") if isinstance(c, dict) else None
            if not isinstance(cid, str) or not CHAT_ID.fullmatch(cid) or chat_path(cid).exists():
                continue
            try:
                chat = clean_chat(cid, c)
            except HTTPException:
                continue
            if isinstance(c.get("updated"), (int, float)):
                chat["updated"] = int(c["updated"])
            write_chat(chat)
            n += 1
        return n
    return {"imported": await run_in_threadpool(work)}


@app.put("/api/chats/{cid}")
async def chat_save(cid: str, req: Request):
    chat_guard(req)
    chat_path(cid)
    chat = clean_chat(cid, await req.json())
    await run_in_threadpool(write_chat, chat)
    return {"ok": True, "updated": chat["updated"]}


@app.delete("/api/chats/{cid}")
async def chat_delete(cid: str, req: Request):
    chat_guard(req)
    p = chat_path(cid)

    def work():
        with CHAT_LOCK:
            try:
                p.unlink()
            except FileNotFoundError:
                pass
            except OSError as e:
                raise HTTPException(500, f"Could not delete the chat: {e}")
    await run_in_threadpool(work)
    return {"ok": True}


# ---------------------------------------------------------------- web: SearXNG search + page reader
WEB_ON = not args.no_web
SEARX = websearch.norm_base(args.searxng_url)
WEB_SLOTS = threading.BoundedSemaphore(4)     # at most a few web jobs at once
WEB_PING = {"t": 0.0, "ok": False, "why": ""}


def web_guard(req):
    need_ui(req)
    if not WEB_ON:
        raise HTTPException(503, "Web search is turned off (--no-web).")


def web_int(v, default, lo, hi):
    try:
        return max(lo, min(hi, int(v)))
    except (TypeError, ValueError):
        return default


@app.get("/api/web/status")
def web_status(force: int = 0):
    """Is SearXNG reachable and answering JSON? Cached for a few seconds so opening Settings stays cheap."""
    if not WEB_ON:
        return {"enabled": False, "ok": False, "why": "Web search is turned off (--no-web).", "url": ""}
    if force or time.time() - WEB_PING["t"] > 15:
        ok, why = websearch.searx_ping(SEARX)
        WEB_PING.update(t=time.time(), ok=ok, why=why)
    return {"enabled": True, "ok": WEB_PING["ok"], "why": WEB_PING["why"], "url": SEARX}


@app.post("/api/web/search")
async def web_search(req: Request):
    """Body: {query, n, read, time_range, categories}. Returns numbered results; the top `read` pages come with their text."""
    web_guard(req)
    b = await req.json()
    q = str(b.get("query", "")).strip()[:300]
    if not q:
        raise HTTPException(400, "Nothing to search for.")
    kw = dict(n=web_int(b.get("n"), 6, 1, 10), read=web_int(b.get("read"), 2, 0, 5), time_range=str(b.get("time_range") or ""),
              categories=str(b.get("categories") or "general"), allow_private=args.web_allow_private)
    if not WEB_SLOTS.acquire(timeout=20):
        raise HTTPException(429, "Too many web requests at once. Try again in a moment.")
    t0 = time.perf_counter()
    try:
        out = await run_in_threadpool(lambda: websearch.research(SEARX, q, **kw))
    except websearch.WebError as e:
        WEB_PING.update(t=time.time(), ok=False, why=str(e))
        raise HTTPException(502, str(e))
    finally:
        WEB_SLOTS.release()
    WEB_PING.update(t=time.time(), ok=True, why="")
    out["seconds"] = round(time.perf_counter() - t0, 2)
    return out


@app.post("/api/web/read")
async def web_read(req: Request):
    """Body: {urls: [...]}. Opens links the user pasted and returns their text."""
    web_guard(req)
    b = await req.json()
    urls = [u.strip() for u in (b.get("urls") or []) if isinstance(u, str) and u.strip()][:4]
    if not urls:
        raise HTTPException(400, "No links to read.")
    if not WEB_SLOTS.acquire(timeout=20):
        raise HTTPException(429, "Too many web requests at once. Try again in a moment.")
    try:
        pages = await run_in_threadpool(lambda: websearch.read_pages(urls, allow_private=args.web_allow_private))
    finally:
        WEB_SLOTS.release()
    return {"pages": pages}


# ---------------------------------------------------------------- models: manual load / unload / tweak, and the reasoning swap
BUSY_MSG = "A model is busy (generating, reading an image or swapping). Press Stop or wait, then try again."
CFG_FIELDS = ("n_ctx", "n_gpu_layers", "n_threads", "n_batch", "flash_attn", "chat_format")


def need_manage():
    if not CAN_EXEC:
        raise HTTPException(403, "Managing models from the UI is disabled on a non-loopback host (use --unsafe-remote-exec)")


def slot_status(slot):
    if slot == "image":
        ok, why = image_ready()
        p = IMG["path"]
        try:
            size = os.path.getsize(p) if p else 0
        except OSError:
            size = 0
        return {"slot": "image", "loaded": STATE["model"] == "image", "path": p, "name": os.path.basename(p) if p else "",
                "size": size, "cfg": {}, "dirty": False, "available": ok, "why": why,
                "vae": os.path.basename(args.image_vae) if args.image_vae else ""}
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
            "slots": {k: slot_status(k) for k in ("coder", "reasoner", "vision", "image")},
            "available": [m for m in ms if not m["mmproj"] and not m["image"]],
            "image_models": [m for m in ms if m["image"] and not m["mmproj"]],
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
        if not m["mmproj"] and not m["image"] and Path(m["path"]) == rp:
            return str(rp)
    raise HTTPException(400, "That file is not in the models folder list (or it is an image model)")


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
    print(f"Chats: saved in {CHAT_DIR}  ({len(read_chats())} stored)")
    print("Vision: " + (f"{os.path.basename(VL_MODEL)} + {os.path.basename(VL_MMPROJ)} (loaded only while an image is read; the coder is unloaded meanwhile)"
                         if VL_OK else f"off ({VL_WHY})"))
    ok_, why_ = image_ready()
    print("Images: " + (f"{os.path.basename(IMG['path'])} (loaded only while an image is made; the coder is unloaded meanwhile)"
                        if ok_ else f"off ({why_})"))
    print(f"Terminal: {'on' if CAN_RUN else 'off'}   Write files: {'on' if CAN_WRITE else 'off'}"
          + ("   !! non-loopback host with --unsafe-remote-exec: anyone who can reach this port can run commands !!"
             if (not LOCAL and args.unsafe_remote_exec) else ""))
    print(f"Terminal: {'on' if CAN_RUN else 'off'}   Write files: {'on' if CAN_WRITE else 'off'}"
          + ("   !! non-loopback host with --unsafe-remote-exec: anyone who can reach this port can run commands !!"
             if (not LOCAL and args.unsafe_remote_exec) else ""))
    print(f"Web search: {'on, SearXNG at ' + SEARX if WEB_ON else 'off (--no-web)'}"
          + ("   (pages on your own network can be read)" if WEB_ON and args.web_allow_private else ""))
    if args.open:
        threading.Timer(1.0, webbrowser.open, [f"http://{args.host}:{args.port}"]).start()
    uvicorn.run(app, host=args.host, port=args.port, log_level="warning")
