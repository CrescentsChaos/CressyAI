#!/usr/bin/env python3
"""Local GGUF chat server.   python server.py --model path/to/model.gguf   (or start without --model and pick models in the app)"""
import argparse, base64, codecs, gc, hashlib, importlib.util, json, math, os, platform, re, shutil, signal, subprocess, sys, tempfile, threading, time, uuid, webbrowser
from io import BytesIO
from collections import deque
from pathlib import Path

import uvicorn
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse, Response, StreamingResponse
from pypdf import PdfReader
from pypdf.errors import PdfReadError
from starlette.concurrency import run_in_threadpool
from llama_cpp import Llama

import embed as embedmod
import agent as agentmod
import agenttools
import fstools
import github
import hostinfo
import memory as memstore
import modelmgr
import rag
import websearch
import wikipedia
import inaturalist
import animal
import geo
import openmeteo
import openstreetmap
import toolbox

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
ap.add_argument("--chat-model", default=None, help="model .gguf for the Chat slot, which answers every message that is not about code (default: none, pick one in the app)")
ap.add_argument("--chat-n-ctx", type=int, default=8192, help="context window of the Chat model")
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
ap.add_argument("--no-github", action="store_true", help="turn off the GitHub lookup (it is also off with --no-web)")
ap.add_argument("--github-token", default=os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN") or "",
                help="optional GitHub token: 5,000 requests per hour instead of 60, and private repos it can see (prefer the GITHUB_TOKEN variable; command lines are visible to other programs)")
ap.add_argument("--no-inat", action="store_true", help="turn off the iNaturalist nature lookup (it is also off with --no-web)")
ap.add_argument("--inat-token", default=os.environ.get("INAT_TOKEN", ""), help="iNaturalist API token (JWT) for photo identification; or set INAT_TOKEN")
ap.add_argument("--inat-locale", default="en", help="language for iNaturalist common names, e.g. en, bn, es")
ap.add_argument("--no-geo", action="store_true", help="turn off Places & weather (Open-Meteo and OpenStreetMap; also off with --no-web)")
ap.add_argument("--no-weather", action="store_true", help="turn off only the Open-Meteo weather, air quality and climate lookups")
ap.add_argument("--no-osm", action="store_true", help="turn off only the OpenStreetMap place search and nearby search")
ap.add_argument("--osm-contact", default=os.environ.get("OSM_CONTACT", ""),
                help="your e-mail or a web address, sent in the User-Agent of OpenStreetMap requests as their usage policy asks (or set OSM_CONTACT)")
ap.add_argument("--nominatim-url", default=None, help="use your own Nominatim server instead of nominatim.openstreetmap.org")
ap.add_argument("--overpass-url", default=None, help="use another Overpass API endpoint instead of overpass-api.de")
ap.add_argument("--no-wiki", action="store_true", help="turn off the Wikipedia lookup (it is also off with --no-web)")
ap.add_argument("--no-tools", action="store_true", help="turn off tool orchestration (the model choosing and chaining tools such as web, weather, places, documents)")
ap.add_argument("--tool-steps", type=int, default=4, help="most rounds of tool calls the model may make for one answer (default 4)")
ap.add_argument("--no-agent-exec", action="store_true", help="the model may not write files or run commands through tools (filesystem.write/edit, terminal.run); reading and searching stay on")
ap.add_argument("--agent-auto-approve", action="store_true", help="DANGEROUS: do not ask before the model writes a file or runs a command (default: every one needs your click)")
ap.add_argument("--web-allow-private", action="store_true", help="let the page reader open links on your own network (off by default)")
ap.add_argument("--no-autoswitch", action="store_true",
                help="never pick the Coder or Reasoner slot automatically based on what you ask")
ap.add_argument("--autoswitch", action="store_true",
                help="opt in to picking the slot (Coder or Reasoner) automatically from what you ask; model files in the slots are never changed")

ap.add_argument("--bench-dir", default=None,
                help="folder with extra benchmark datasets (.jsonl); default ./benchmarks next to server.py")

ap.add_argument("--kb-dir", default=None,
                help="where the knowledge base (uploaded .txt / .md / .pdf documents) is stored (default: ./knowledge next to server.py)")

ap.add_argument("--kb-max-mb", type=int, default=30,
                help="largest single file the knowledge base accepts, in MB (default 30)")

ap.add_argument("--no-kb", action="store_true",
                help="turn off the knowledge base")
ap.add_argument("--embed-model", default=None,
                help="small embedding .gguf for semantic memory search (default: auto-find nomic-embed / bge / e5 / MiniLM in the models folder)")
ap.add_argument("--no-embed", action="store_true",
                help="turn off semantic memory search (memory still matches on keywords)")
ap.add_argument("--embed-idle", type=int, default=300,
                help="seconds of inactivity before the embedding model is freed from RAM (0 = keep it loaded)")
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
    "chat": base_cfg(str(Path(args.chat_model).expanduser().resolve()) if args.chat_model else "", args.chat_n_ctx),
    "reasoner": base_cfg(R_MODEL, args.reasoner_n_ctx),
    "vision": base_cfg(VL_MODEL, args.vision_n_ctx),
}
CFG["vision"]["chat_format"] = ""
LOADED_CFG = {}           # the settings each slot was actually loaded with (to show "reload to apply")


def model_cfg_key(path):
    return os.path.normcase(os.path.abspath(path))


MODEL_CFG = {
    (slot, model_cfg_key(c["path"])): dict(c)
    for slot, c in CFG.items() if c["path"]
}


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
cllm = None               # the chat model: answers everything that is not about code (never loaded together with the coder or reasoner)
ACTIVE_TEXT = {"slot": "coder"}   # the answering slot that was loaded last; restored after a vision / image / reasoning swap
LOAD_S = {"coder": 0.0, "chat": 0.0, "reasoner": 0.0}
META = {}                 # static facts about the coder, kept so /api/info works while it is unloaded
STATE = {"model": "none"} # which model is in memory right now: coder | chat | reasoner | vision | none


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


def unload_chat():
    global cllm
    m, cllm = cllm, None
    if m is not None:
        free_llama(m)
    if STATE["model"] == "chat":
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
    unload_chat()
    t0 = time.time()
    llm = Llama(**llama_kw("coder"))
    ACTIVE_TEXT["slot"] = "coder"
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
    unload_chat()
    t0 = time.time()
    rllm = Llama(**llama_kw("reasoner"))
    LOAD_S["reasoner"] = time.time() - t0
    LOADED_CFG["reasoner"] = dict(CFG["reasoner"])
    STATE["model"] = "reasoner"


def load_chat():
    global cllm
    if cllm is not None:
        return
    if not CFG["chat"]["path"]:
        raise RuntimeError("No chat model selected. Open the Model tab and pick one for the Chat slot.")
    unload_coder()           # only one model lives in memory at a time
    unload_reasoner()
    t0 = time.time()
    cllm = Llama(**llama_kw("chat"))
    ACTIVE_TEXT["slot"] = "chat"
    LOAD_S["chat"] = time.time() - t0
    LOADED_CFG["chat"] = dict(CFG["chat"])
    STATE["model"] = "chat"


SLOT_LOAD = {"coder": load_coder, "chat": load_chat, "reasoner": load_reasoner}
SLOT_UNLOAD = {"coder": unload_coder, "chat": unload_chat, "reasoner": unload_reasoner}


def restore_text():
    """After a vision / image / reasoning swap: bring back the answering model that was loaded before. With auto-pick on nothing
    is restored, so the Coder is never loaded for work that is not about code; the next message loads what it needs. Call with SWAP held."""
    if ROUTE["enabled"]:
        return
    slot = ACTIVE_TEXT["slot"]
    if CFG[slot]["path"]:
        SLOT_LOAD[slot]()


def noncode_model():
    """The model for work that is not about code (chat, helper calls, picture prompts): the Chat slot, else the Coder when auto-pick
    is off or it is already in memory. Never loads the Coder for it. Call with SWAP held."""
    if CFG["chat"]["path"]:
        load_chat()
        return cllm
    if cllm is not None:
        return cllm
    if llm is not None:
        return llm
    if not ROUTE["enabled"]:
        load_coder()
        return llm
    raise RuntimeError("This is not a code question, so the Coder stays unloaded, and the Chat slot is empty. "
                       "Pick a model in the Chat slot (Model tab), or turn Auto-pick slot off.")


def slot_loaded(slot):
    return {"coder": llm is not None, "chat": cllm is not None, "reasoner": rllm is not None, "vision": STATE["model"] == "vision",
            "image": STATE["model"] == "image"}[slot]


def slot_model(b):
    """The model object that answers this request. With auto-pick on the browser sends slot: 'coder' (code questions; loaded on demand),
    'chat' (everything else) or 'loaded' (whatever answering model is already in memory). Helper calls carry no slot: with auto-pick
    off the Coder answers as before, with it on they count as chat work. Call with SWAP held."""
    slot = str(b.get("slot") or "") if isinstance(b, dict) else ""
    if not slot:
        slot = "chat" if ROUTE["enabled"] else "coder"
    if slot == "coder":
        load_coder()        # no-op normally; reloads the coder if a vision run was interrupted
        return llm
    if slot == "reasoner" and CFG["reasoner"]["path"]:
        load_reasoner()
        return rllm
    if slot == "loaded":
        if cllm is not None:
            return cllm
        if llm is not None:
            return llm
    return noncode_model()


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


def dir_sig(p):
    """A cheap fingerprint of one folder listing (names, sizes, mtimes). The UI polls it to refresh the file list by itself."""
    h = hashlib.sha1()
    try:
        with os.scandir(p) as it:
            entries = sorted(it, key=lambda e: e.name)
        for c in entries:
            if c.name in SKIP:
                continue
            try:
                st = c.stat()
                h.update(f"{c.name}|{int(c.is_dir())}|{st.st_size}|{st.st_mtime_ns}\n".encode("utf-8", "replace"))
            except OSError:
                pass
    except OSError:
        return ""
    return h.hexdigest()[:16]


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


@app.get("/nature")
def nature():
    return FileResponse(HERE / "nature.html")


@app.get("/js/{name}")
def js_file(name: str):
    # front-end scripts live in ./js/ (loaded in order by index.html); only plain *.js names are served
    if not re.fullmatch(r"[\w.-]+\.js", name) or ".." in name:
        raise HTTPException(404)
    p = HERE / "js" / name
    if not p.is_file():
        raise HTTPException(404)
    return FileResponse(p, media_type="text/javascript")


@app.get("/api/info")
def info():
    slot, c = ("chat", CFG["chat"]) if cllm is not None else ("coder", CFG["coder"])    # the context meter follows whichever answering model is in memory
    path = c["path"]
    meta = META if META.get("path") == path else {}
    if slot == "chat":
        meta = dict(path=path, n_ctx=cllm.n_ctx(), n_vocab=cllm.n_vocab(), n_threads=cllm.n_threads,
                    metadata={k: (v if len(v) < 400 else v[:400] + "…") for k, v in cllm.metadata.items()})
    try:
        size = os.path.getsize(path) if path else 0
    except OSError:
        size = 0
    return {
        "file": os.path.basename(path) if path else "", "file_size": size,
        "load_seconds": round(LOAD_S[slot], 2), "n_ctx": meta.get("n_ctx") or c["n_ctx"], "n_vocab": meta.get("n_vocab"),
        "n_embd": meta.get("n_embd"), "n_params": meta.get("n_params"),
        "n_gpu_layers": c["n_gpu_layers"], "n_threads": meta.get("n_threads"), "metadata": meta.get("metadata", {}),
        "vision": os.path.basename(VL_MODEL) if VL_OK else None, "model_state": STATE["model"], "loaded": llm is not None or cllm is not None,
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
                    model = slot_model(b)
                msgs, trimmed = fit(b["messages"], b.get("max_tokens"), model)
            except Exception as e:
                yield sse({"type": "error", "message": f"Could not load the model: {e}"})
                return
            start = time.perf_counter()
            first = None
            n = 0
            finish = None
            try:
                stream = model.create_chat_completion(
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
            used = getattr(model, "n_tokens", None)
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
    return {"root": str(ROOT), "path": rel_of(p), "items": items[:500], "sig": dir_sig(p)}


@app.get("/api/fs/stamp")
def fs_stamp(path: str = ""):
    """Fingerprint of the folder the file list is showing; changes when files appear, vanish or are edited."""
    try:
        p = safe(path)
    except HTTPException:
        return {"root": str(ROOT), "sig": "", "missing": True}
    if not p.is_dir():
        return {"root": str(ROOT), "sig": "", "missing": True}
    return {"root": str(ROOT), "path": rel_of(p), "sig": dir_sig(p), "missing": False}


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


def walk_files():
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
ANIMAL_VISION_PROMPT = (
    "Examine the attached photo for animal species identification. Be conservative: never invent a species if the image "
    "does not show enough detail. Return these labeled lines:\n"
    "COMMON: the most likely common name, or unknown\n"
    "SCIENTIFIC: the most likely scientific name, or unknown\n"
    "ALTERNATIVES: up to three plausible alternatives, or none\n"
    "VISIBLE DETAILS: concise visual evidence supporting or limiting the identification."
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


def vision_gen(images, hint, keep_unloaded=False, identify_species=False):
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
                unload_chat()
                unload_reasoner()
            yield sse({"type": "stage", "stage": "load_vision", "label": "Loading vision model…"})
            with SWAP:
                vl, handler = load_vision()
            STATE["model"] = "vision"
            ask = ANIMAL_VISION_PROMPT if identify_species else VISION_PROMPT
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
                yield sse({"type": "stage", "stage": "load_coder", "label": "Loading the answering model…"})
                with SWAP:
                    restore_text()
        except GeneratorExit:
            raise
        except Exception as e:
            err = str(e) or e.__class__.__name__
            yield sse({"type": "stage", "stage": "load_coder", "label": "Restoring the answering model…"})
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
                        restore_text()
                except Exception as e:
                    print("could not reload the answering model:", e)
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
    return StreamingResponse(vision_gen(imgs, str(b.get("hint") or "").strip(), bool(b.get("keep_unloaded")), bool(b.get("identify_species"))),
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


def enhance_prompt(job, model):
    """Let the coder (already in memory) turn a casual request into a good SDXL prompt. Raises if it cannot."""
    if job["mode"] == "edit" and job.get("base_prompt"):
        user = f"Current prompt:\n{job['base_prompt']}\n\nRequested change:\n{job['prompt']}"
    elif job["mode"] == "edit":
        user = ("The user is editing an existing picture and described the change below. Write the prompt for the picture "
                f"as it should look after the change.\n\nRequested change:\n{job['prompt']}")
    else:
        user = f"Request:\n{job['prompt']}"
    r = model.create_chat_completion(messages=[{"role": "system", "content": IMG_PROMPT_SYS}, {"role": "user", "content": user}],
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
            if job["enhance"] and (CFG["chat"]["path"] or CFG["coder"]["path"]):
                yield sse({"type": "stage", "stage": "prompt", "label": "Writing the prompt…"})
                try:
                    with SWAP:
                        helper = noncode_model()
                    pos, neg = enhance_prompt(job, helper)
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
                    unload_chat()
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
                if ok:
                    yield sse({"type": "stage", "stage": "load_coder", "label": "Loading the answering model…"})
                    with SWAP:
                        restore_text()
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
            try:
                with SWAP:
                    restore_text()
            except Exception as e:
                print("could not reload the answering model:", e)
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
"agent": CAN_WRITE,
"kb": KB_ON,
"embed": EMB.available(),
"embed_model": EMB.name,
"embed_why": EMB.why,
"web": WEB_ON,
"inat": INAT_ON,
"geo": GEO_ON, "weather": WEATHER_ON, "osm": OSM_ON,
"tools": TOOLS_ON,
"web_url": SEARX if WEB_ON else "",            "shell": os.environ.get("COMSPEC", "cmd") if os.name == "nt" else os.environ.get("SHELL", "/bin/sh")}


def host_env():
    """The machine profile, plus how the loaded models are set up (context, GPU layers)."""
    c = CFG["coder"]
    return hostinfo.detect(extra={"model": os.path.basename(c["path"]) if c["path"] else "", "n_ctx": c["n_ctx"], "n_gpu_layers": c["n_gpu_layers"]})


@app.get("/api/env")
def api_env():
    info = host_env()
    return {**info, "prompt": hostinfo.prompt_block(info), "line": hostinfo.one_line(info)}


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
    out = {"id": cid, "title": str(b.get("title") or "New chat")[:200], "pin": bool(b.get("pin")), "msgs": msgs,
           "updated": int(time.time() * 1000)}
    summary = b.get("summary")
    if isinstance(summary, str) and summary.strip():
        out["summary"] = summary.strip()[:8000]
        try:
            out["summary_upto"] = max(0, min(len(msgs), int(b.get("summary_upto") or 0)))
        except (TypeError, ValueError):
            out["summary_upto"] = 0
        sig = b.get("summary_sig")
        if isinstance(sig, str) and len(sig) <= 64:
            out["summary_sig"] = sig       # lets the browser notice that an earlier message was edited after summarizing
    try:
        mined = int(b.get("mem_upto") or 0)
    except (TypeError, ValueError):
        mined = 0
    if mined > 0:
        out["mem_upto"] = min(len(msgs), mined)   # how many messages memory has already learned from
    return out


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


# ---------------------------------------------------------------- memory: facts, project notes, open tasks
_embed_path, _embed_why = embedmod.find_model(model_dirs(), args.embed_model, args.no_embed)
EMB = embedmod.Embedder(_embed_path, _embed_why, idle=args.embed_idle, threads=args.threads)
MEM = memstore.Store(DATA_DIR / "memory.json", ROOT, EMB)
if EMB.available():
    threading.Thread(target=MEM.warm, daemon=True, name="memory-warm").start()   # index old notes without delaying start-up


def mem_guard(req):
    need_ui(req)


def _mem_call(fn):
    try:
        return fn()
    except memstore.MemoryError as e:
        raise HTTPException(400, str(e))


@app.get("/api/memory")
async def memory_state(req: Request):
    mem_guard(req)
    return await run_in_threadpool(MEM.snapshot)


@app.post("/api/memory/fact")
async def memory_add_fact(req: Request):
    mem_guard(req)
    b = await req.json()
    scope = "project" if b.get("scope") == "project" else "global"
    pin = b.get("pin") if isinstance(b.get("pin"), bool) else None
    return await run_in_threadpool(lambda: _mem_call(lambda: MEM.add_fact(b.get("text"), scope, pin)))


@app.post("/api/memory/fact/update")
async def memory_update_fact(req: Request):
    mem_guard(req)
    b = await req.json()
    pin = b.get("pin") if isinstance(b.get("pin"), bool) else None
    text = b.get("text") if isinstance(b.get("text"), str) else None
    scope = b.get("scope") if b.get("scope") in ("global", "project") else None
    return await run_in_threadpool(lambda: _mem_call(lambda: MEM.set_fact(b.get("id"), pin, text, scope)))


@app.post("/api/memory/fact/delete")
async def memory_delete_fact(req: Request):
    mem_guard(req)
    b = await req.json()
    return await run_in_threadpool(lambda: _mem_call(lambda: MEM.delete_fact(b.get("id"))))


@app.post("/api/memory/task")
async def memory_add_task(req: Request):
    mem_guard(req)
    b = await req.json()
    return await run_in_threadpool(lambda: _mem_call(lambda: MEM.add_task(b.get("text"), str(b.get("chat") or "")[:64])))


@app.post("/api/memory/task/update")
async def memory_update_task(req: Request):
    mem_guard(req)
    b = await req.json()
    status = b.get("status") if isinstance(b.get("status"), str) else None
    text = b.get("text") if isinstance(b.get("text"), str) else None
    progress = b.get("progress") if isinstance(b.get("progress"), str) else None
    return await run_in_threadpool(lambda: _mem_call(lambda: MEM.set_task(b.get("id"), status, text, progress, str(b.get("chat") or "")[:64])))


@app.post("/api/memory/task/delete")
async def memory_delete_task(req: Request):
    mem_guard(req)
    b = await req.json()
    return await run_in_threadpool(lambda: _mem_call(lambda: MEM.delete_task(b.get("id"))))


@app.post("/api/memory/task/done")
async def memory_done_task(req: Request):
    mem_guard(req)
    b = await req.json()
    return await run_in_threadpool(lambda: _mem_call(lambda: MEM.complete_matching(b.get("text"))))


@app.post("/api/memory/task/clear")
async def memory_clear_tasks(req: Request):
    mem_guard(req)
    return await run_in_threadpool(MEM.clear_done)


@app.post("/api/memory/absorb")
async def memory_absorb(req: Request):
    mem_guard(req)
    b = await req.json()
    lst = lambda k: b.get(k) if isinstance(b.get(k), list) else []
    handoff = b.get("handoff") if isinstance(b.get("handoff"), dict) else None
    return await run_in_threadpool(lambda: MEM.absorb(lst("facts"), lst("tasks"), str(b.get("chat") or "")[:64], lst("done"),
                                                      lst("progress"), handoff, str(b.get("title") or "")[:120]))


@app.post("/api/memory/recall")
async def memory_recall(req: Request):
    """Body: {query, k}. Pinned notes, notes that match the message, and open tasks for this project."""
    mem_guard(req)
    b = await req.json()
    q = str(b.get("query") or "")[:4000]
    return await run_in_threadpool(lambda: MEM.recall(q, web_int(b.get("k"), 6, 1, 8), str(b.get("chat") or "")[:64], bool(b.get("fresh"))))


@app.post("/api/memory/search")
async def memory_search(req: Request):
    """Body: {query, k}. Facts and tasks that match a phrase (for /forget)."""
    mem_guard(req)
    b = await req.json()
    return await run_in_threadpool(lambda: MEM.search(str(b.get("query") or "")[:400], web_int(b.get("k"), 5, 1, 10)))


@app.post("/api/memory/session")
async def memory_session(req: Request):
    """Body: {chat, title, summary, handoff, next, turns, quality}. The hand-off note a later chat can pick up."""
    mem_guard(req)
    b = await req.json()
    nxt = [str(x) for x in b["next"][:4]] if isinstance(b.get("next"), list) else None
    turns = web_int(b.get("turns"), 0, 0, 100000)
    quality = "llm" if b.get("quality") == "llm" else "auto"
    await run_in_threadpool(lambda: _mem_call(lambda: MEM.upsert_session(
        b.get("chat"), str(b.get("title") or ""), str(b.get("summary") or ""), str(b.get("handoff") or ""), nxt, turns, quality)))
    return {"ok": True}


@app.post("/api/memory/session/delete")
async def memory_session_delete(req: Request):
    mem_guard(req)
    b = await req.json()
    chat = str(b.get("chat") or "")
    return await run_in_threadpool(lambda: MEM.delete_session(chat) if b.get("only_here") else (MEM.forget_chat(chat), MEM.snapshot())[1])


@app.get("/api/memory/projects")
async def memory_projects(req: Request):
    mem_guard(req)
    return await run_in_threadpool(MEM.projects)


@app.post("/api/memory/adopt")
async def memory_adopt(req: Request):
    """Body: {from}. Move another project's notes here (the folder was renamed or moved)."""
    mem_guard(req)
    b = await req.json()
    return await run_in_threadpool(lambda: _mem_call(lambda: MEM.adopt(str(b.get("from") or ""))))


@app.get("/api/memory/export")
async def memory_export(req: Request):
    mem_guard(req)
    return await run_in_threadpool(MEM.export)


@app.post("/api/memory/import")
async def memory_import(req: Request):
    mem_guard(req)
    b = await req.json()
    return await run_in_threadpool(lambda: _mem_call(lambda: MEM.import_data(b)))


@app.post("/api/memory/reindex")
async def memory_reindex(req: Request):
    """Rebuild the search vectors (after swapping the embedding model)."""
    mem_guard(req)
    return await run_in_threadpool(MEM.reindex)


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


# ---------------------------------------------------------------- Wikipedia (MediaWiki API): encyclopedic facts, no SearXNG needed
WIKI_ON = WEB_ON and not args.no_wiki
WIKI_PING = {"t": 0.0, "ok": False, "why": "", "lang": ""}


def wiki_guard(req):
    need_ui(req)
    if not WIKI_ON:
        raise HTTPException(503, "Wikipedia lookup is turned off (--no-wiki or --no-web).")


@app.get("/api/wiki/status")
def wiki_status(lang: str = "en", force: int = 0):
    """Can this machine reach Wikipedia? Cached for a few seconds, like the web status."""
    if not WIKI_ON:
        return {"enabled": False, "ok": False, "why": "Wikipedia lookup is turned off (--no-wiki or --no-web)."}
    lang = wikipedia.lang_ok(lang)
    if force or lang != WIKI_PING["lang"] or time.time() - WIKI_PING["t"] > 15:
        ok, why = wikipedia.ping(lang)
        WIKI_PING.update(t=time.time(), ok=ok, why=why, lang=lang)
    return {"enabled": True, "ok": WIKI_PING["ok"], "why": WIKI_PING["why"], "lang": lang}


@app.post("/api/wiki/search")
async def wiki_search(req: Request):
    """Body: {query, n, read, lang}. Returns the best matching articles; the top `read` come with their text."""
    wiki_guard(req)
    b = await req.json()
    q = str(b.get("query", "")).strip()[:300]
    if not q:
        raise HTTPException(400, "Nothing to look up.")
    kw = dict(n=web_int(b.get("n"), 3, 1, 6), read=web_int(b.get("read"), 1, 0, 3), lang=wikipedia.lang_ok(str(b.get("lang") or "en")))
    if not WEB_SLOTS.acquire(timeout=20):
        raise HTTPException(429, "Too many web requests at once. Try again in a moment.")
    t0 = time.perf_counter()
    try:
        out = await run_in_threadpool(lambda: wikipedia.lookup(q, **kw))
    except websearch.WebError as e:
        raise HTTPException(502, str(e))
    finally:
        WEB_SLOTS.release()
    out["seconds"] = round(time.perf_counter() - t0, 2)
    return out


# ---------------------------------------------------------------- GitHub REST API: repos, files, issues, releases, repo search
GH_ON = WEB_ON and not args.no_github
github.TOKEN = (args.github_token or "").strip()
GH_PING = {"t": 0.0, "ok": False, "why": ""}


def gh_guard(req):
    need_ui(req)
    if not GH_ON:
        raise HTTPException(503, "GitHub lookup is turned off (--no-github or --no-web).")


@app.get("/api/github/status")
def github_status(force: int = 0):
    """Reachability and rate limit. The token itself is never sent to the browser, only whether one is set."""
    if not GH_ON:
        return {"enabled": False, "ok": False, "why": "GitHub lookup is turned off (--no-github or --no-web).", "token": False}
    if force or time.time() - GH_PING["t"] > 15:
        ok, why = github.ping()
        GH_PING.update(t=time.time(), ok=ok, why=why)
    return {"enabled": True, "ok": GH_PING["ok"], "why": GH_PING["why"], "token": bool(github.TOKEN),
            "limit": github.RATE["limit"], "remaining": github.RATE["remaining"], "reset": github.RATE["reset"]}


@app.post("/api/github/lookup")
async def github_lookup(req: Request):
    """Body: {targets: [link or owner/repo, ...] and/or query, n, read}. Returns results shaped like web results."""
    gh_guard(req)
    b = await req.json()
    targets = [str(t).strip()[:300] for t in (b.get("targets") or []) if isinstance(t, str) and t.strip()][:3]
    q = str(b.get("query") or "").strip()[:300]
    if not targets and not q:
        raise HTTPException(400, "Nothing to look up on GitHub.")
    kw = dict(targets=targets, query=q, n=web_int(b.get("n"), 5, 1, 10), read=web_int(b.get("read"), 1, 0, 3))
    if not WEB_SLOTS.acquire(timeout=20):
        raise HTTPException(429, "Too many web requests at once. Try again in a moment.")
    t0 = time.perf_counter()
    try:
        out = await run_in_threadpool(lambda: github.lookup(**kw))
    except websearch.WebError as e:
        raise HTTPException(502, str(e))
    finally:
        WEB_SLOTS.release()
    out["seconds"] = round(time.perf_counter() - t0, 2)
    return out


# ---------------------------------------------------------------- iNaturalist (API v1): species, places, nearby wildlife, seasonality, photo ID
INAT_ON = WEB_ON and not args.no_inat
INAT_LOC = inaturalist.locale_ok(args.inat_locale)
INAT_PING = {"t": 0.0, "ok": False, "why": ""}


def inat_guard(req):
    need_ui(req)
    if not INAT_ON:
        raise HTTPException(503, "iNaturalist lookup is turned off (--no-inat or --no-web).")


def _f(v):
    try:
        return None if v in (None, "") else float(v)
    except (TypeError, ValueError):
        raise HTTPException(400, "Latitude, longitude and radius must be numbers.")


def _geo(b):
    lat, lng = _f(b.get("lat")), _f(b.get("lng"))
    if lat is not None and not -90 <= lat <= 90 or lng is not None and not -180 <= lng <= 180:
        raise HTTPException(400, "That location is outside the map.")
    pid = web_int(b.get("place_id"), 0, 0, 10**9) or None
    return dict(lat=lat, lng=lng, radius=_f(b.get("radius")) or 10, place_id=pid)


def _flt(b):
    ic = b.get("iconic") or []
    ic = [ic] if isinstance(ic, str) else ic
    out = {"iconic": [str(x) for x in ic][:6], "quality": str(b.get("quality") or ""), "locale": INAT_LOC}
    for k in ("days", "month", "taxon_id", "user"):
        if b.get(k) not in (None, ""):
            out[k] = b[k]
    for k in ("threatened", "introduced", "native", "endemic", "photos"):
        out[k] = bool(b.get(k))
    return out


async def inat_call(req, fn):
    """Shared plumbing: guard, parse JSON body, limit parallel jobs, map errors."""
    inat_guard(req)
    try:
        b = await req.json()
    except Exception:
        raise HTTPException(400, "Bad request body.")
    if not isinstance(b, dict):
        raise HTTPException(400, "Bad request body.")
    if not WEB_SLOTS.acquire(timeout=20):
        raise HTTPException(429, "Too many web requests at once. Try again in a moment.")
    t0 = time.perf_counter()
    try:
        out = await run_in_threadpool(lambda: fn(b))
    except websearch.WebError as e:
        raise HTTPException(502, str(e))
    except (ValueError, TypeError) as e:
        raise HTTPException(400, "Bad input: " + str(e)[:100])
    finally:
        WEB_SLOTS.release()
    out["seconds"] = round(time.perf_counter() - t0, 2)
    return out


@app.get("/api/inat/status")
def inat_status(force: int = 0):
    if not INAT_ON:
        return {"enabled": False, "ok": False, "why": "iNaturalist lookup is turned off (--no-inat or --no-web).", "identify": False}
    if force or time.time() - INAT_PING["t"] > 15:
        ok, why = inaturalist.ping()
        INAT_PING.update(t=time.time(), ok=ok, why=why)
    return {"enabled": True, "ok": INAT_PING["ok"], "why": INAT_PING["why"], "identify": bool(args.inat_token), "locale": INAT_LOC,
            "iconic": inaturalist.ICONIC}


@app.post("/api/inat/search")
async def inat_search(req: Request):
    """Body: {query, rank, limit}. Species / taxa by common or scientific name."""
    return await inat_call(req, lambda b: inaturalist.search(str(b.get("query", "")), str(b.get("rank") or ""), web_int(b.get("limit"), 8, 1, 20), INAT_LOC))


@app.post("/api/inat/taxon")
async def inat_taxon(req: Request):
    """Body: {id}. Summary, classification, children, status, photos, look-alikes."""
    return await inat_call(req, lambda b: inaturalist.taxon(web_int(b.get("id"), 0, 1, 10**9), INAT_LOC))


@app.post("/api/inat/places")
async def inat_places(req: Request):
    return await inat_call(req, lambda b: inaturalist.places(str(b.get("query", "")), web_int(b.get("limit"), 6, 1, 15)))


@app.post("/api/inat/nearby")
async def inat_nearby(req: Request):
    """Body: {lat, lng, radius | place_id, limit, iconic[], days, threatened, native, introduced, ...}. What lives here?"""
    return await inat_call(req, lambda b: inaturalist.nearby(limit=web_int(b.get("limit"), 20, 1, 50), **_geo(b), **_flt(b)))


@app.post("/api/inat/observations")
async def inat_observations(req: Request):
    return await inat_call(req, lambda b: inaturalist.observations(limit=web_int(b.get("limit"), 12, 1, 30), order=str(b.get("order") or ""), **_geo(b), **_flt(b)))


@app.post("/api/inat/observation")
async def inat_observation(req: Request):
    return await inat_call(req, lambda b: inaturalist.observation(web_int(b.get("id"), 0, 1, 10**10)))


@app.post("/api/inat/season")
async def inat_season(req: Request):
    return await inat_call(req, lambda b: inaturalist.season(web_int(b.get("taxon_id"), 0, 1, 10**9), **_geo(b)))


@app.post("/api/inat/observers")
async def inat_observers(req: Request):
    return await inat_call(req, lambda b: inaturalist.observers(limit=web_int(b.get("limit"), 8, 1, 20), **_geo(b), **_flt(b)))


@app.post("/api/inat/identify")
async def inat_identify(req: Request):
    """Body: {image: 'data:image/jpeg;base64,...', lat, lng}. Needs --inat-token."""
    return await inat_call(req, lambda b: inaturalist.identify(str(b.get("image") or ""), args.inat_token, _f(b.get("lat")), _f(b.get("lng")), INAT_LOC))


@app.post("/api/animal/profile")
async def animal_profile(req: Request):
    """Body: {id | query, lang, lat, lng, radius, place_id}. One merged iNaturalist + Wikipedia record."""
    return await inat_call(req, lambda b: animal.profile(web_int(b.get("id"), 0, 0, 10**9) or None, str(b.get("query") or ""), INAT_LOC,
                           wikipedia.lang_ok(str(b.get("lang") or "en")), WIKI_ON, **_geo(b)))


@app.post("/api/animal/identify")
async def animal_identify(req: Request):
    """Body: {image: data URL, vision: optional text from /api/vision, lat, lng}. Uses every available signal."""
    g = lambda b: _geo(b)
    return await inat_call(req, lambda b: animal.identify(str(b.get("image") or ""), args.inat_token, str(b.get("vision") or "")[:4000],
                           g(b)["lat"], g(b)["lng"], INAT_LOC, wikipedia.lang_ok(str(b.get("lang") or "en")), WIKI_ON))


# ---------------------------------------------------------------- Places & weather: Open-Meteo (weather) + OpenStreetMap (places)
WEATHER_ON = WEB_ON and not (args.no_geo or args.no_weather)
OSM_ON = WEB_ON and not (args.no_geo or args.no_osm)
GEO_ON = WEATHER_ON or OSM_ON
try:
    openstreetmap.configure(args.osm_contact, args.nominatim_url, args.overpass_url)
except ValueError as e:
    ap.error(str(e))
GEO_PING = {"t": 0.0, "weather": (False, ""), "osm": (False, "")}


def geo_guard(req, need=None):
    need_ui(req)
    if not GEO_ON or (need == "osm" and not OSM_ON) or (need == "weather" and not WEATHER_ON):
        raise HTTPException(503, "Places and weather are turned off (--no-geo, --no-weather, --no-osm or --no-web).")


def geo_coord(b, key, lo, hi):
    try:
        v = float(b.get(key))
    except (TypeError, ValueError):
        raise HTTPException(400, f"Missing or invalid {key}.")
    if not lo <= v <= hi:
        raise HTTPException(400, f"{key} is out of range.")
    return v


@app.get("/api/geo/status")
def geo_status(force: int = 0):
    """Reachability of Open-Meteo and OpenStreetMap, cached for a few seconds so opening Settings stays cheap."""
    if not GEO_ON:
        return {"enabled": False, "weather": {"enabled": False, "ok": False, "why": ""}, "osm": {"enabled": False, "ok": False, "why": ""},
                "why": "Places and weather are turned off (--no-geo or --no-web)."}
    if force or time.time() - GEO_PING["t"] > 15:
        GEO_PING.update(t=time.time(), weather=openmeteo.ping() if WEATHER_ON else (False, ""), osm=openstreetmap.ping() if OSM_ON else (False, ""))
    return {"enabled": True, "contact": bool(openstreetmap.CONTACT), "kinds": openstreetmap.kinds_public(),
            "weather": {"enabled": WEATHER_ON, "ok": GEO_PING["weather"][0], "why": GEO_PING["weather"][1]},
            "osm": {"enabled": OSM_ON, "ok": GEO_PING["osm"][0], "why": GEO_PING["osm"][1]}}


async def geo_run(fn):
    if not WEB_SLOTS.acquire(timeout=20):
        raise HTTPException(429, "Too many web requests at once. Try again in a moment.")
    t0 = time.perf_counter()
    try:
        out = await run_in_threadpool(fn)
    except geo.NotFound as e:
        raise HTTPException(404, str(e))
    except websearch.WebError as e:
        raise HTTPException(502, str(e))
    finally:
        WEB_SLOTS.release()
    if isinstance(out, dict):
        out["seconds"] = round(time.perf_counter() - t0, 2)
    return out


@app.post("/api/geo/search")
async def geo_search(req: Request):
    """Body: {query, n, lang}. Candidate places for a name or address (OpenStreetMap, then Open-Meteo's geocoder if that is down)."""
    geo_guard(req)
    b = await req.json()
    q = str(b.get("query", "")).strip()[:200]
    if not q:
        raise HTTPException(400, "Nothing to look up.")
    n, lang = web_int(b.get("n"), 5, 1, 8), str(b.get("lang") or "en")[:5]

    def work():
        if OSM_ON:
            try:
                res = openstreetmap.search(q, n, lang)
                if res:
                    return {"results": res, "source": "openstreetmap"}
            except websearch.WebError:
                if not WEATHER_ON:
                    raise
        if WEATHER_ON:
            return {"results": openmeteo.geocode(q, n, lang), "source": "open-meteo"}
        return {"results": [], "source": "openstreetmap"}
    return await geo_run(work)


@app.post("/api/geo/reverse")
async def geo_reverse(req: Request):
    """Body: {lat, lon}. The place at a coordinate (used by 'Use my location')."""
    geo_guard(req, "osm")
    b = await req.json()
    lat, lon = geo_coord(b, "lat", -90, 90), geo_coord(b, "lon", -180, 180)
    return await geo_run(lambda: {"place": openstreetmap.reverse(lat, lon, lang=str(b.get("lang") or "en")[:5])})


@app.post("/api/geo/context")
async def geo_context(req: Request):
    """Body: {message | cmd + args, default:{name,lat,lon}, units, days, radius, climate, lang}.
    Works out what is being asked, fetches it, and returns {used, kind, text (for the model), card (for the Places tab), notes, sources}."""
    geo_guard(req)
    b = await req.json()
    if not isinstance(b, dict):
        raise HTTPException(400, "Bad request.")
    for k in ("message", "args"):
        if len(str(b.get(k) or "")) > 4000:
            raise HTTPException(400, "That message is too long for a place or weather lookup.")
    d = b.get("default")
    if isinstance(d, dict):
        try:
            b["default"] = {"name": str(d.get("name") or "")[:120], "lat": float(d["lat"]), "lon": float(d["lon"])}
        except (KeyError, TypeError, ValueError):
            b["default"] = None
    else:
        b["default"] = None
    b["days"], b["radius"] = web_int(b.get("days"), 7, 1, 16), web_int(b.get("radius"), 1000, 200, 10000)

    def work():
        try:
            return geo.handle(b, osm_ok=OSM_ON, weather_ok=WEATHER_ON)
        except geo.NeedPlace as e:      # not an error: the page tells the user to name a place or save theirs
            return {"used": False, "need_place": True, "why": str(e)}
    return await geo_run(work)


@app.post("/api/geo/gpx")
async def geo_gpx(req: Request):
    """Body: {name, points:[{name, lat, lon, address}]}. Waypoints as a .gpx file for any map app."""
    geo_guard(req, "osm")
    b = await req.json()
    pts = [p for p in (b.get("points") or [])[:60] if isinstance(p, dict) and isinstance(p.get("lat"), (int, float)) and isinstance(p.get("lon"), (int, float))]
    if not pts:
        raise HTTPException(400, "No places to export.")
    return Response(openstreetmap.gpx(pts, str(b.get("name") or "CressyAI places")[:80]), media_type="application/gpx+xml",
                    headers={"Content-Disposition": 'attachment; filename="places.gpx"'})


# ---------------------------------------------------------------- knowledge base: upload .txt / .md / .pdf, retrieve passages (RAG)
KB_ON = not args.no_kb
KB_MAX = max(1, args.kb_max_mb) * 1024 * 1024
KB = None
if KB_ON:
    try:
        KB = rag.KB(Path(args.kb_dir).expanduser() if args.kb_dir else HERE / "knowledge", max_bytes=KB_MAX)
        print(f"Knowledge base: {len(KB.docs)} document(s) in {KB.dir}" + ("" if rag.pdf_ready() else "  (PDF reading needs: pip install pypdf)"))
        for w in KB.skipped:
            print("  skipped unreadable knowledge file:", w)
    except OSError as e:
        KB_ON = False
        print("Knowledge base turned off:", e)
KB_SLOTS = threading.BoundedSemaphore(3)     # parsing a big PDF is CPU work; keep a few at once at most


def kb_guard(req):
    need_ui(req)
    if not KB_ON:
        raise HTTPException(503, "The knowledge base is turned off (--no-kb).")


@app.get("/api/kb")
def kb_status():
    if not KB_ON:
        return {"enabled": False, "docs": [], "chunks": 0, "chars": 0, "pdf": False, "max_mb": 0}
    return {"enabled": True, **KB.status()}


@app.post("/api/kb/upload")
async def kb_upload(req: Request):
    """Raw file body; the file name travels percent-encoded in the X-Filename header. One file per request."""
    kb_guard(req)
    from urllib.parse import unquote
    name = unquote(req.headers.get("x-filename", "")).strip()
    if not name:
        raise HTTPException(400, "Missing file name.")
    try:
        if int(req.headers.get("content-length") or 0) > KB_MAX + 4096:
            raise HTTPException(413, f"That file is larger than {KB_MAX // (1024 * 1024)} MB.")
    except ValueError:
        pass
    data = await req.body()
    if not KB_SLOTS.acquire(timeout=30):
        raise HTTPException(429, "Busy indexing other files. Try again in a moment.")
    t0 = time.perf_counter()
    try:
        meta = await run_in_threadpool(lambda: KB.add(name, data))
    except rag.KBError as e:
        raise HTTPException(400, str(e))
    finally:
        KB_SLOTS.release()
    meta["seconds"] = round(time.perf_counter() - t0, 2)
    return meta


@app.post("/api/kb/doc")
async def kb_doc(req: Request):
    """Body: {id, enabled}. Switch a document in or out of the search without deleting it."""
    kb_guard(req)
    b = await req.json()
    try:
        return KB.set_enabled(b.get("id"), bool(b.get("enabled")))
    except rag.KBError as e:
        raise HTTPException(404, str(e))


@app.post("/api/kb/delete")
async def kb_delete(req: Request):
    kb_guard(req)
    b = await req.json()
    try:
        KB.remove(b.get("id"))
    except rag.KBError as e:
        raise HTTPException(404, str(e))
    return {"ok": True}


@app.post("/api/kb/clear")
async def kb_clear(req: Request):
    kb_guard(req)
    KB.clear()
    return {"ok": True}


@app.post("/api/kb/query")
async def kb_query(req: Request):
    """Body: {query, k}. Returns the best-matching passages from the enabled documents."""
    kb_guard(req)
    b = await req.json()
    q = str(b.get("query", "")).strip()[:500]
    if not q:
        raise HTTPException(400, "Nothing to look up.")
    t0 = time.perf_counter()
    out = await run_in_threadpool(lambda: KB.query(q, web_int(b.get("k"), 5, 1, 12)))
    out["seconds"] = round(time.perf_counter() - t0, 3)
    return out


# ---------------------------------------------------------------- tool orchestration: the model picks tools, chains them, and every result is checked (toolbox.py)
TOOLS_ON = not args.no_tools
TOOL_STEPS = max(1, min(6, args.tool_steps))


def read_project_file(rel, limit):
    """-> (text, error, truncated) for the file tool; never leaves --root."""
    try:
        p = safe(str(rel))
    except HTTPException:
        return "", "That path is outside the project folder.", False
    if not p.is_file():
        return "", "No such file in the project folder.", False
    d = load_text(p, max(500, min(int(limit), 12000)))
    return ("", "That file is binary or unreadable.", False) if d is None else (d["text"], None, d["truncated"])


FS = fstools.Workspace(ROOT, SKIP, BIN, MAX_WRITE)
AGENT_EXEC = not args.no_agent_exec


def run_capture(cmd, cwd, timeout):
    """Run a shell command for the model and return its output (same runner, limits and process cleanup as the Terminal tab)."""
    try:
        folder = safe(str(cwd or ""))
    except HTTPException:
        raise toolbox.ToolError("that folder is outside the project folder")
    if not folder.is_dir():
        raise toolbox.ToolError("that folder does not exist")
    out, end = [], {}
    for raw in run_stream(cmd, folder, max(1, min(int(timeout or 60), 300))):
        ev = json.loads(raw[6:])
        if ev.get("type") == "out":
            out.append(ev["text"])
        elif ev.get("type") == "done":
            end = ev
        elif ev.get("type") == "error":
            raise toolbox.ToolError(str(ev.get("message") or "the command could not start"))
    return {"code": end.get("code"), "out": "".join(out), "timeout": bool(end.get("timeout")), "truncated": bool(end.get("truncated")), "seconds": end.get("seconds", 0)}


# Approvals: a write, an edit or a command from the model waits here until the user clicks Approve or Deny on the tool card.
APPROVALS = {}              # id -> {id, owner, kind, title, detail, event, ok, why}
APPROVAL_LOCK = threading.Lock()


def make_confirm(owner):
    def confirm(kind, title, detail=""):
        if args.agent_auto_approve:
            return True, "auto-approved (--agent-auto-approve)"
        ap = {"id": uuid.uuid4().hex[:12], "owner": owner, "kind": kind, "title": str(title)[:400], "detail": str(detail)[:6000], "event": threading.Event(), "ok": False, "why": ""}
        with APPROVAL_LOCK:
            APPROVALS[ap["id"]] = ap
        try:
            if not ap["event"].wait(toolbox.APPROVAL_WAIT):
                return False, "no answer within %d seconds" % toolbox.APPROVAL_WAIT
            return ap["ok"], ap["why"]
        finally:
            with APPROVAL_LOCK:
                APPROVALS.pop(ap["id"], None)
    return confirm


def cancel_approvals(owner):
    """The answer ended (Stop, closed tab, error): anything still waiting is refused."""
    with APPROVAL_LOCK:
        mine = [a for a in APPROVALS.values() if a["owner"] == owner]
    for a in mine:
        a["ok"], a["why"] = False, "the answer was stopped"
        a["event"].set()


@app.get("/api/tools/pending")
def tools_pending(req: Request):
    need_ui(req)
    with APPROVAL_LOCK:
        items = list(APPROVALS.values())
    return {"pending": [{k: a[k] for k in ("id", "kind", "title", "detail")} for a in items if not a["event"].is_set()]}


@app.post("/api/tools/approve")
async def tools_approve(req: Request):
    """Body: {id, ok}. The user's answer to one waiting write, edit or command."""
    need_ui(req)
    b = await req.json()
    if not isinstance(b, dict):
        raise HTTPException(400, "Bad request.")
    with APPROVAL_LOCK:
        a = APPROVALS.get(str(b.get("id") or ""))
    if a is None or a["event"].is_set():
        raise HTTPException(404, "Nothing is waiting for that answer (it may have timed out).")
    a["ok"], a["why"] = bool(b.get("ok")), "" if b.get("ok") else "denied by the user"
    a["event"].set()
    return {"ok": True}


def tool_ctx(b=None, confirm=None):
    b = b or {}
    return toolbox.Ctx(web=WEB_ON, wiki=WIKI_ON, gh=GH_ON, inat=INAT_ON, weather=WEATHER_ON, osm=OSM_ON, kb=KB if KB_ON else None, mem=MEM, searx=SEARX,
                       allow_private=args.web_allow_private, inat_locale=INAT_LOC, units=openmeteo.units_ok(b.get("units")), lang=wikipedia.lang_ok(str(b.get("lang") or "en")),
                       websearch=websearch, wikipedia=wikipedia, github=github, inaturalist=inaturalist, animal=animal, geo=geo, openmeteo=openmeteo, openstreetmap=openstreetmap,
                       files=True, read_file=read_project_file, search_files=lambda q: search(q)["hits"], slots=WEB_SLOTS,
                       fs=FS, can_write=CAN_WRITE and AGENT_EXEC, can_run=CAN_RUN and AGENT_EXEC, run_cmd=run_capture, confirm=confirm)


def tool_hints(b):
    h = b.get("hints") if isinstance(b.get("hints"), dict) else {}
    urls = [u.strip()[:500] for u in (h.get("urls") or []) if isinstance(u, str) and re.match(r"https?://", u.strip())][:3]
    gh = h.get("gh") if isinstance(h.get("gh"), dict) else {}
    ghx = [t.strip()[:300] for t in (gh.get("targets") or []) if isinstance(t, str) and t.strip()][:3]
    return {"urls": urls, "web": bool(h.get("web")), "wiki": bool(h.get("wiki")), "kb": bool(h.get("kb")), "q": str(h.get("q") or "")[:200],
            "gh": {"targets": ghx, "query": str(gh.get("query") or "")[:300]} if (ghx or gh.get("query")) else None}


def tool_disabled(b):
    return tuple(str(x)[:40] for x in (b.get("disabled") or [])[:20] if isinstance(x, str))


def tool_select(b, ctx):
    sel = toolbox.select(str(b.get("text") or "")[:4000], ctx, tool_hints(b), tool_disabled(b))
    if b.get("force") and not sel["use"]:     # /tools: the user wants the model to have the whole toolbox for this message
        names = [n for n in toolbox.available(ctx) if n not in tool_disabled(b)]
        sel.update(use=bool(names), tools=names, why="all available tools (forced)")
    return sel


@app.get("/api/tools")
def tools_state():
    ctx = tool_ctx()
    return {"enabled": TOOLS_ON, "steps": TOOL_STEPS, "tools": toolbox.catalog(ctx)}


@app.post("/api/tools/select")
async def tools_select(req: Request):
    """Body: {text, hints:{urls, web, wiki, gh, kb, q}, disabled:[tool], force}. Which tools fit this message? No model is used."""
    need_ui(req)
    b = await req.json()
    if not TOOLS_ON or not isinstance(b, dict):
        return {"use": False, "tools": [], "seeds": [], "intents": [], "why": "Tool orchestration is turned off (--no-tools)."}
    return tool_select(b, tool_ctx(b))


@app.post("/api/agent")
async def agent(req: Request):
    """Body: {messages, text, hints, disabled, force, steps, verify, units, lang, + the usual sampling fields}. Streams events:
    plan, tool_start, tool_result, token, grounding, done (or error). The model runs only while no tool is running."""
    need_ui(req)
    if not TOOLS_ON:
        raise HTTPException(503, "Tool orchestration is turned off (--no-tools).")
    b = await req.json()
    msgs = b.get("messages") if isinstance(b, dict) else None
    if not isinstance(msgs, list) or not msgs or len(msgs) > 400 or not all(isinstance(m, dict) and m.get("role") in ("system", "user", "assistant") and isinstance(m.get("content"), str) for m in msgs):
        raise HTTPException(400, "Bad messages.")
    owner = uuid.uuid4().hex
    ctx = tool_ctx(b, make_confirm(owner))
    sel = tool_select(b, ctx)
    steps = web_int(b.get("steps"), TOOL_STEPS, 1, TOOL_STEPS)

    def llm_turn(ms, low_temp=False):
        with LOCK:
            try:
                with SWAP:
                    model = slot_model(b)
                ms, trimmed = fit(ms, b.get("max_tokens"), model)
            except Exception as e:
                raise RuntimeError(f"Could not load the model: {e}")
            t = b.get("temperature", 0.7)
            start, first, n, finish = time.perf_counter(), None, 0, None
            stream = model.create_chat_completion(messages=ms, stream=True, temperature=min(t, 0.3) if low_temp else t, top_p=b.get("top_p", 0.95), top_k=b.get("top_k", 40),
                                                max_tokens=b.get("max_tokens") or None, repeat_penalty=b.get("repeat_penalty", 1.1))
            for chunk in stream:
                ch = chunk["choices"][0]
                if ch.get("finish_reason"):
                    finish = ch["finish_reason"]
                text = ch["delta"].get("content")
                if text is not None:
                    first = first or time.perf_counter()
                    n += 1
                    yield ("tok", text)
            used, gen_s = getattr(model, "n_tokens", None), (time.perf_counter() - first) if first else 0
            yield ("end", {"trimmed": trimmed, "finish_reason": finish, "completion_tokens": n, "prompt_tokens": (used - n) if used else None, "ctx_used": used,
                           "ttft": (first - start) if first else None, "gen_seconds": gen_s, "tps": ((n - 1) / gen_s) if gen_s > 0 and n > 1 else None})

    def gen():
        try:
            for ev in toolbox.orchestrate(msgs, str(b.get("text") or ""), sel, ctx, llm_turn, steps=steps, max_calls=min(12, steps * 3), n_ctx=next((m.n_ctx() for m in (cllm, llm, rllm) if m is not None), 8192),
                                          verify=b.get("verify") is not False, disabled=tool_disabled(b)):
                yield sse(ev)
        except Exception as e:
            yield sse({"type": "error", "message": str(e)})
        finally:
            cancel_approvals(owner)

    return StreamingResponse(gen(), media_type="text/event-stream")


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
    if slot == "chat":
        out["n_ctx_loaded"] = cllm.n_ctx() if cllm is not None else None
    if slot == "vision":
        out["available"] = VL_OK
        out["why"] = VL_WHY
        out["mmproj"] = os.path.basename(VL_MMPROJ) if VL_MMPROJ else ""
    return out


def status():
    ms = scan_models()
    names = {m["name"].lower() for m in ms}
    avail = [m for m in ms if not m["mmproj"] and not m["image"]]
    return {"state": STATE["model"], "busy": LOCK.locked(),
            "slots": {k: slot_status(k) for k in ("coder", "chat", "reasoner", "vision", "image")},
"available": [
    modelmgr.annotate(m)
    for m in avail
    if not m.get("mmproj") and not m.get("image")
],
"families": modelmgr.group_models(avail),
"ram": {
    "free": ram_free(),
    "total": psutil.virtual_memory().total if psutil else 0
},
"routing": route_state(),
"quantize": qjob_public(),
"quant_targets": modelmgr.QUANTIZE_TARGETS,            "image_models": [m for m in ms if m["image"] and not m["mmproj"]],
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


def remember_model_cfg(slot):
    c = CFG[slot]
    if c["path"]:
        MODEL_CFG[(slot, model_cfg_key(c["path"]))] = dict(c)


def cfg_for_model(slot, path):
    saved = MODEL_CFG.get((slot, model_cfg_key(path)))
    if saved:
        return {**saved, "path": path}
    default_ctx = args.reasoner_n_ctx if slot == "reasoner" else args.chat_n_ctx if slot == "chat" else args.n_ctx
    return base_cfg(path, min(int(default_ctx), 8192))


def do_load(slot, path, force):
    if not LOCK.acquire(blocking=False):
        raise HTTPException(409, BUSY_MSG)
    previous = dict(CFG[slot])
    was_loaded = slot_loaded(slot)
    try:
        if path:
            new = allowed_model(path)
            changed = not previous["path"] or model_cfg_key(previous["path"]) != model_cfg_key(new)
            if changed:
                remember_model_cfg(slot)
                CFG[slot] = cfg_for_model(slot, new)
            if was_loaded and changed:
                with SWAP:
                    SLOT_UNLOAD[slot]()
        elif not CFG[slot]["path"]:
            raise HTTPException(400, "Choose a model file first")
        if force and slot_loaded(slot) and not (path and changed):
            with SWAP:
                SLOT_UNLOAD[slot]()
        with SWAP:
            SLOT_LOAD[slot]()
        remember_model_cfg(slot)
        if slot == "coder" and CFG["coder"]["path"]:
            PERSIST.setdefault("slots", {})["coder"] = CFG["coder"]["path"]    # the Coder is remembered for the next start too
            save_state()
        if slot == "chat" and CFG["chat"]["path"]:
            PERSIST.setdefault("slots", {})["chat"] = CFG["chat"]["path"]      # loaded by default on the next start
            if not (PERSIST.get("routes") or {}).get("explicit") and not args.no_autoswitch:
                ROUTE["enabled"] = True        # a Chat model makes slot picking the default, until the user chooses otherwise
            save_state()
    except HTTPException:
        raise
    except Exception as e:
        CFG[slot] = previous
        if was_loaded and not slot_loaded(slot):
            try:
                with SWAP:
                    SLOT_LOAD[slot]()
            except Exception as restore_error:
                raise HTTPException(500, f"Could not load the {slot} model: {e}. The previous model also failed to reload: {restore_error}") from e
        raise HTTPException(500, f"Could not load the {slot} model: {e}")
    finally:
        LOCK.release()
    return status()


def do_unload(which):
    if not LOCK.acquire(blocking=False):
        raise HTTPException(409, BUSY_MSG)
    try:
        with SWAP:
            for slot in (tuple(SLOT_UNLOAD) if which == "all" else (which,)):
                SLOT_UNLOAD[slot]()
    finally:
        LOCK.release()
    return status()


def do_config(slot, new, reload):
    if not LOCK.acquire(blocking=False):
        raise HTTPException(409, BUSY_MSG)
    previous = dict(CFG[slot])
    was_loaded = slot_loaded(slot)
    try:
        CFG[slot] = new
        if reload and slot in SLOT_LOAD and was_loaded:
            try:
                with SWAP:
                    SLOT_UNLOAD[slot]()
                    SLOT_LOAD[slot]()
            except Exception as e:
                CFG[slot] = previous
                if not slot_loaded(slot):
                    try:
                        with SWAP:
                            SLOT_LOAD[slot]()
                    except Exception as restore_error:
                        raise HTTPException(500, f"Could not reload the {slot} model with those settings: {e}. The previous settings also failed to reload: {restore_error}") from e
                raise HTTPException(500, f"Could not reload the {slot} model with those settings: {e}")
        remember_model_cfg(slot)
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
        raise HTTPException(400, "slot must be coder, chat or reasoner")
    return await run_in_threadpool(do_load, slot, b.get("path"), bool(b.get("force")))


@app.post("/api/model/unload")
async def model_unload(req: Request):
    b = await model_body(req)
    which = b.get("slot", "all")
    if which != "all" and which not in SLOT_UNLOAD:
        raise HTTPException(400, "slot must be coder, chat, reasoner or all")
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


# ---------------------------------------------------------------- persistent state (routes, benchmark history)
STATE_FILE = HERE / "cressy-state.json"
STATE_IO = threading.Lock()
BENCH_DIR = Path(args.bench_dir).expanduser() if args.bench_dir else HERE / "benchmarks"


def load_state():
    try:
        d = json.loads(STATE_FILE.read_text("utf-8"))
        return d if isinstance(d, dict) else {}
    except (OSError, ValueError):
        return {}


PERSIST = load_state()


def save_state():
    with STATE_IO:
        try:
            tmp = STATE_FILE.with_suffix(".tmp")
            tmp.write_text(json.dumps(PERSIST, indent=1), "utf-8")
            os.replace(tmp, STATE_FILE)
        except OSError:
            pass     # history is a convenience; never break a request over it


def ram_free():
    try:
        return psutil.virtual_memory().available if psutil else 0
    except Exception:
        return 0


# ---------------------------------------------------------------- the Chat model is remembered and loaded by default
_cp = (PERSIST.get("slots") or {}).get("chat") if isinstance(PERSIST.get("slots"), dict) else None
if not CFG["chat"]["path"] and _cp and Path(str(_cp)).is_file():
    CFG["chat"] = cfg_for_model("chat", str(_cp))
    remember_model_cfg("chat")


# ---------------------------------------------------------------- the Coder is remembered too, and found by name when nothing was chosen
_CODER_QUANT_ORDER = ("q5_k_m", "q6_k", "q4_k_m", "q8_0", "q5_k_s", "q4_k_s", "q4_0")


def find_coder():
    """A coder .gguf in the model folders, chosen by name (qwen2.5-coder, codestral, deepseek-coder ...). Prefers a mid-size quantization."""
    cands = [m for m in modelmgr.chat_models(scan_models()) if modelmgr.CODE_NAME_RX.search(m["name"])]
    if not cands:
        return ""

    def rank(m):
        n = m["name"].lower()
        q = next((i for i, k in enumerate(_CODER_QUANT_ORDER) if k in n), len(_CODER_QUANT_ORDER))
        return (q, -m["size"] if q == len(_CODER_QUANT_ORDER) else 0, n)
    return sorted(cands, key=rank)[0]["path"]


def restore_coder():
    """Coder slot at start: --model wins, then the file remembered from the last session (or the older routes.map.code entry),
    then a coder found by file name in the models folders. Returns a short note about where it came from ('' when --model was given)."""
    if CFG["coder"]["path"]:
        return ""
    slots = PERSIST.get("slots") if isinstance(PERSIST.get("slots"), dict) else {}
    legacy = (PERSIST.get("routes") or {}).get("map", {}).get("code") if isinstance(PERSIST.get("routes"), dict) else ""
    allowed = {str(Path(m["path"])).lower(): m["path"] for m in scan_models() if not m["mmproj"] and not m["image"]}
    for src, cand in (("remembered", slots.get("coder")), ("remembered", legacy)):
        try:
            hit = allowed.get(str(Path(str(cand)).expanduser().resolve()).lower()) if cand else None
        except (OSError, ValueError):
            hit = None
        if hit:
            CFG["coder"] = cfg_for_model("coder", hit)
            remember_model_cfg("coder")
            return src
    found = find_coder()
    if found:
        CFG["coder"] = cfg_for_model("coder", found)
        remember_model_cfg("coder")
        return "found by name"
    return ""


CODER_SOURCE = restore_coder()

# ---------------------------------------------------------------- auto-switch: pick the slot (Coder / Chat) from what the user is asking
_saved = PERSIST.get("routes") if isinstance(PERSIST.get("routes"), dict) else {}
ROUTE = {"enabled": bool(args.autoswitch or (_saved.get("enabled") if _saved.get("explicit") else bool(CFG["chat"]["path"]))) and not args.no_autoswitch,
         "map": {k: str((_saved.get("map") or {}).get(k) or "") for k in ("code", "chat")}, "last": None}


def autoload_default():
    """At start: the Chat model loads by default; the Coder only when there is no Chat model (or with auto-pick off and --model)."""
    if args.no_autoload:
        return
    try:
        with SWAP:
            if CFG["chat"]["path"]:
                load_chat()
            elif args.model:
                load_coder()
    except Exception as e:
        print("could not load the model at start:", e)


autoload_default()


def route_state():
    return {"enabled": ROUTE["enabled"], "map": dict(ROUTE["map"]), "last": ROUTE["last"]}


def do_route(text, prev, dry):
    """Classify the message and pick the SLOT that answers it. The Coder loads only for code questions. Everything else goes
    to the Chat slot (or to a model that is already in memory when the Chat slot is empty). It never changes which model
    file a slot holds (that would throw away the slot's context and tweak settings)."""
    cls = modelmgr.classify(text, prev)
    intent = cls["intent"]
    name = lambda p: os.path.basename(p) if p else ""
    cur = CFG["coder"]["path"]
    out = {"intent": intent, "why": cls["why"], "enabled": ROUTE["enabled"], "switched": False, "reason": False, "slot": "coder",
           "model": name(cur), "from": name(cur), "target": ""}
    if not ROUTE["enabled"] and not dry:
        return out
    keys = ("intent", "why", "model", "target", "switched", "slot")
    if intent != "code":
        if CFG["chat"]["path"]:
            out.update(slot="chat", model=name(CFG["chat"]["path"]), target="" if cllm is not None else name(CFG["chat"]["path"]))
        elif cllm is not None or llm is not None:
            out.update(slot="loaded", model=name(CFG["chat"]["path"] if cllm is not None else cur))   # already in memory: use it, never load the Coder for this
        else:
            out.update(slot="none", model="", note="Not a code question, so the Coder stays unloaded, and the Chat slot is empty. Pick a model for the Chat slot.")
        if not dry:
            ROUTE["last"] = {k: out[k] for k in keys}
        return out
    if not cur:
        if CFG["chat"]["path"] or cllm is not None or llm is not None:     # never fail a code question just because no coder is chosen
            out.update(slot="chat" if CFG["chat"]["path"] else "loaded", model=name(CFG["chat"]["path"]),
                       note="No Coder model is chosen, so the Chat model answers this code question. Pick a coder in the Model tab, "
                            "or put a file with 'coder' in its name into the models folder: it is then found automatically.")
        else:
            out["note"] = "No model is selected in the Coder slot."
        return out
    if llm is None:
        out["target"] = name(cur)          # the Coder slot is not in memory: it is the slot that would be loaded
    if dry or llm is not None:
        if not dry:
            ROUTE["last"] = {k: out[k] for k in keys}
        return out
    if not LOCK.acquire(blocking=False):
        out["note"] = "Models are busy, so it did not load the Coder."
        return out
    try:
        with SWAP:
            try:
                load_coder()
                out.update(switched=True, model=name(CFG["coder"]["path"]))
            except Exception as e:
                out["error"] = f"Could not load the Coder: {e}"
    finally:
        LOCK.release()
    ROUTE["last"] = {k: out[k] for k in keys}
    return out


@app.post("/api/route")
async def route(req: Request):
    need_ui(req)
    b = await req.json()
    text = str(b.get("text") or "")[:4000]
    dry = bool(b.get("dry")) or not CAN_EXEC
    return await run_in_threadpool(do_route, text, str(b.get("prev") or ""), dry)


@app.post("/api/route/config")
async def route_config(req: Request):
    b = await model_body(req)
    if "enabled" in b:
        ROUTE["enabled"] = bool(b["enabled"]) and not args.no_autoswitch
    ROUTE["map"] = {"code": "", "chat": ""}      # slots never change file automatically; kept only so older saved state still loads
    PERSIST["routes"] = {"enabled": ROUTE["enabled"], "map": ROUTE["map"], "explicit": True}
    save_state()
    return status()


# ---------------------------------------------------------------- benchmarks on the loaded model
BENCH_FIELDS = ("id", "ts", "slot", "model", "quant", "size", "n_ctx", "gpu_layers", "load_seconds", "speed", "suites", "overall", "seconds")


def bench_suites():
    custom, errs = modelmgr.load_custom(BENCH_DIR)
    pool = {**modelmgr.SUITES, **custom}
    return pool, [{"name": k, "title": v["title"], "n": len(v["items"]), "custom": bool(v.get("custom"))} for k, v in pool.items()], errs


@app.get("/api/bench/suites")
def bench_list():
    _, lst, errs = bench_suites()
    return {"suites": lst, "dir": str(BENCH_DIR), "errors": errs}


@app.get("/api/bench/history")
def bench_history():
    return {"runs": PERSIST.get("bench", []), "running": LOCK.locked()}


@app.post("/api/bench/delete")
async def bench_delete(req: Request):
    need_ui(req)
    b = await req.json()
    runs = PERSIST.get("bench", [])
    PERSIST["bench"] = [] if b.get("all") else [r for r in runs if r.get("id") != b.get("id")]
    save_state()
    return {"runs": PERSIST["bench"]}


def bench_speed(m):
    """Prompt-processing speed (tokens/s over a fresh ~512-token prompt) and generation speed (tokens/s over ~96 tokens)."""
    n_ctx = int(m.n_ctx())
    target = max(64, min(512, n_ctx // 2 - 64))
    filler = "The quick brown fox jumps over the lazy dog while rain falls on quiet streets. " * 120
    ids = m.tokenize((uuid.uuid4().hex + " " + filler).encode("utf-8"), add_bos=True)[:target]
    m.reset()
    t0 = time.perf_counter()
    for _ in m.create_completion(prompt=ids, max_tokens=1, temperature=0.0, stream=True):
        pass
    pp = max(1e-6, time.perf_counter() - t0)
    m.reset()
    t0 = time.perf_counter()
    first, n = None, 0
    for _ in m.create_completion(prompt="Write a long story about a lighthouse keeper.\n\n", max_tokens=96, temperature=0.8, top_k=40, stream=True):
        first = first or time.perf_counter()
        n += 1
    end = time.perf_counter()
    tg = (n - 1) / (end - first) if first and n > 1 and end > first else 0.0
    return {"pp_tokens": len(ids), "pp_tps": round(len(ids) / pp, 1), "tg_tps": round(tg, 1), "tg_tokens": n,
            "ttft": round((first - t0) if first else 0.0, 2), "rss": PROC.memory_info().rss if PROC else 0}


def bench_events(slot, suite_names, speed):
    if slot not in ("coder", "chat", "reasoner"):
        yield sse({"type": "error", "message": "Pick the coder, the chat model or the reasoner to benchmark."})
        return
    pool, _, _ = bench_suites()
    chosen = [s for s in suite_names if s in pool]
    if not chosen and not speed:
        yield sse({"type": "error", "message": "Tick at least one test."})
        return
    if not CFG[slot]["path"]:
        yield sse({"type": "error", "message": f"No {slot} model is chosen. Pick one in the list above and load it first."})
        return
    if not LOCK.acquire(blocking=False):
        yield sse({"type": "error", "message": BUSY_MSG})
        return
    try:
        if not slot_loaded(slot):
            yield sse({"type": "status", "message": f"Loading {os.path.basename(CFG[slot]['path'])}…"})
            with SWAP:
                SLOT_LOAD[slot]()
        m = {"coder": llm, "chat": cllm, "reasoner": rllm}[slot]
        name = os.path.basename(CFG[slot]["path"])
        try:
            size = os.path.getsize(CFG[slot]["path"])
        except OSError:
            size = 0
        total = sum(len(pool[s]["items"]) for s in chosen)
        rec = {"id": uuid.uuid4().hex[:8], "ts": int(time.time()), "slot": slot, "model": name, "quant": modelmgr.quant_of(name)[0],
               "size": size, "n_ctx": int(m.n_ctx()), "gpu_layers": CFG[slot]["n_gpu_layers"],
               "load_seconds": round(LOAD_S.get(slot, 0.0), 1), "speed": None, "suites": {}, "overall": None}
        yield sse({"type": "start", "total": total, "speed": bool(speed), "model": name, "quant": rec["quant"]})
        t_all = time.perf_counter()
        if speed:
            yield sse({"type": "status", "message": "Measuring speed…"})
            rec["speed"] = bench_speed(m)
            yield sse({"type": "speed", **rec["speed"]})
        thinking = slot == "reasoner"
        done = 0
        for sname in chosen:
            s = pool[sname]
            res = {"correct": 0, "total": 0, "cut": 0, "seconds": 0.0, "tokens": 0}
            t_s = time.perf_counter()
            for item in s["items"]:
                msgs = modelmgr.build_messages(s["kind"], item)
                for attempt in (0, 1):
                    r = m.create_chat_completion(messages=msgs, temperature=0.0, max_tokens=modelmgr.max_tokens_for(s["kind"], thinking))
                    ch = r["choices"][0]
                    out = (ch.get("message") or {}).get("content") or ""
                    if attempt == 0 and not thinking and "<think>" in out.lower() and "</think>" not in out.lower():
                        thinking = True      # a reasoning model answering in the coder slot: give it room to think
                        continue
                    break
                ok = modelmgr.score_item(s["kind"], item, out)
                res["total"] += 1
                res["correct"] += 1 if ok else 0
                res["cut"] += 1 if (not ok and ch.get("finish_reason") == "length") else 0
                res["tokens"] += int((r.get("usage") or {}).get("completion_tokens") or 0)
                done += 1
                yield sse({"type": "item", "suite": sname, "ok": ok, "done": done, "total": total})
            res["seconds"] = round(time.perf_counter() - t_s, 1)
            rec["suites"][sname] = res
            yield sse({"type": "suite", "suite": sname, "title": s["title"], **res})
        rec["overall"] = modelmgr.overall(rec["suites"])
        rec["seconds"] = round(time.perf_counter() - t_all, 1)
        runs = PERSIST.setdefault("bench", [])
        runs.insert(0, {k: rec.get(k) for k in BENCH_FIELDS})
        del runs[40:]
        save_state()
        yield sse({"type": "done", "record": rec})
    except Exception as e:
        yield sse({"type": "error", "message": f"Benchmark stopped: {e}"})
    finally:
        LOCK.release()


@app.post("/api/bench")
async def bench(req: Request):
    """Body: {slot: coder|chat|reasoner, suites: [...], speed: bool}. Streams progress; closing the connection stops it."""
    b = await model_body(req)
    names = [str(s) for s in (b.get("suites") or []) if isinstance(s, str)][:30]
    return StreamingResponse(bench_events(b.get("slot", "coder"), names, bool(b.get("speed", True))), media_type="text/event-stream")


# ---------------------------------------------------------------- make another quantization of a file you already have
QJOB = {"state": "idle", "src": "", "dst": "", "target": "", "error": "", "started": 0.0, "proc": None}


def qjob_public():
    return {k: QJOB[k] for k in ("state", "src", "dst", "target", "error", "started")}


def run_quantize(src, part, dst, target, requant):
    err = ""
    try:
        proc = subprocess.Popen([sys.executable, "-c", modelmgr.QUANTIZE_SCRIPT, src, str(part), target, "1" if requant else "0"],
                                stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True)
        QJOB["proc"] = proc
        _, tail = proc.communicate()
        if QJOB["state"] == "cancelled":
            err = "cancelled"
        elif proc.returncode != 0:
            err = (tail or "").strip().splitlines()[-1][:300] if (tail or "").strip() else f"the quantizer exited with code {proc.returncode}"
        else:
            os.replace(part, dst)
    except Exception as e:
        err = str(e)
    if err:
        try:
            Path(part).unlink(missing_ok=True)
        except OSError:
            pass
    QJOB.update(state="cancelled" if err == "cancelled" else "error" if err else "done", error="" if err == "cancelled" else err, proc=None)


@app.post("/api/model/quantize")
async def model_quantize(req: Request):
    """Body: {path, target, allow_requantize}. Runs llama.cpp's quantizer in its own process and writes a new .gguf next to the source."""
    b = await model_body(req)
    src = allowed_model(b.get("path"))
    ok, why, outname, requant = modelmgr.quantize_plan(Path(src).name, b.get("target", ""))
    if not ok:
        raise HTTPException(400, why)
    if requant and not b.get("allow_requantize"):
        raise HTTPException(400, "This file is already quantized, so a smaller one made from it loses extra quality. Tick the box to allow it, or start from an F16 / BF16 file.")
    if QJOB["state"] == "running":
        raise HTTPException(409, "A quantization is already running.")
    dst = Path(src).with_name(outname)
    if dst.exists():
        raise HTTPException(400, f"{outname} already exists.")
    try:
        free = shutil.disk_usage(dst.parent).free
    except OSError:
        free = 0
    if free and free < os.path.getsize(src):
        raise HTTPException(400, f"Not enough free disk space next to the source ({free // 2**30} GB free, about {os.path.getsize(src) // 2**30} GB may be needed).")
    target = str(b["target"]).upper()
    QJOB.update(state="running", src=Path(src).name, dst=outname, target=target, error="", started=time.time())
    threading.Thread(target=run_quantize, args=(src, str(dst) + ".part", str(dst), target, requant), daemon=True).start()
    return status()


@app.post("/api/model/quantize/cancel")
async def quantize_cancel(req: Request):
    await model_body(req)
    p = QJOB.get("proc")
    if QJOB["state"] == "running" and p is not None:
        QJOB["state"] = "cancelled"
        kill_tree(p)
    return status()


REASON_SYS = (
    "You are the reasoning engine for a coding assistant. A different model will write the final answer after you, "
    "using your notes. Work the task through: state the goal in one line, list constraints and edge cases, compare the "
    "plausible approaches and choose one, check it for pitfalls, then outline the steps or the structure of the solution. "
    "Do NOT write the final answer, complete code or finished prose; short snippets are fine only where they pin down a decision. "
    "Be concrete and economical. Plain text, no greeting, no closing remarks."
)


def reason_gen(msgs, context, o):
    """Unload the answering model -> load the reasoner -> stream its reasoning -> unload it -> bring the answering model back."""
    err = None
    t0 = time.perf_counter()
    n = 0
    finish = None
    first = None
    with LOCK:
        try:
            if llm is not None or cllm is not None:
                yield sse({"type": "stage", "stage": "unload_coder", "label": "Unloading the answering model…"})
            with SWAP:
                unload_coder()
                unload_chat()
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
            if not ROUTE["enabled"]:
                yield sse({"type": "stage", "stage": "load_coder", "label": "Loading the answering model…"})
                with SWAP:
                    restore_text()
        except GeneratorExit:
            raise
        except Exception as e:
            err = str(e) or e.__class__.__name__
            yield sse({"type": "stage", "stage": "load_coder", "label": "Restoring the answering model…"})
        finally:
            # every exit path (done, error, user pressed Stop) ends with the reasoner gone and the answering model back
            with SWAP:
                unload_reasoner()
            try:
                with SWAP:
                    restore_text()
            except Exception as e:
                print("could not reload the answering model:", e)
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


# ---------------------------------------------------------------- agent mode: autonomous multi-step coding with verification
AGENT = agentmod.AgentManager()


def agent_model(msgs, o):
    """The coder's reply as {"text": ...} chunks and a final {"finish": ...}. The model lock is held only while it generates,
    so commands and tests run between steps without blocking the rest of the app."""
    with LOCK:
        with SWAP:
            load_coder()
        msgs, _ = fit(msgs, o.get("max_tokens"))
        stream = llm.create_chat_completion(
            messages=msgs, stream=True, temperature=o["temperature"], top_p=o["top_p"], top_k=o["top_k"],
            max_tokens=o["max_tokens"], repeat_penalty=o["repeat_penalty"])
        finish = None
        try:
            for chunk in stream:
                ch = chunk["choices"][0]
                if ch.get("finish_reason"):
                    finish = ch["finish_reason"]
                text = ch["delta"].get("content")
                if text:
                    yield {"text": text}
            yield {"finish": finish}
        finally:
            close = getattr(stream, "close", None)
            if close:
                close()


def agent_planner(msgs, o):
    """The planner's reply as {"text": ...} chunks. The reasoning model plans when one is chosen in the Model tab, otherwise the coder does.
    Only one model lives in memory, so the coder is loaded again by the next agent_model call."""
    with LOCK:
        with SWAP:
            if CFG["reasoner"]["path"]:
                load_reasoner()
                m = rllm
            else:
                load_coder()
                m = llm
        mm, _ = fit(msgs, o.get("max_tokens"), m)

        def start(x):
            return m.create_chat_completion(messages=x, stream=True, temperature=o["temperature"], top_p=o["top_p"], top_k=o["top_k"],
                                            max_tokens=o["max_tokens"], repeat_penalty=o["repeat_penalty"])
        try:
            stream = start(mm)
        except Exception as e:      # some chat templates (older Gemma ones) refuse a system role: fold it into the user turn
            if "system" in str(e).lower() and mm[0]["role"] == "system" and len(mm) > 1 and mm[1]["role"] == "user":
                mm = [{"role": "user", "content": mm[0]["content"] + "\n\n---\n\n" + str(mm[1]["content"])}, *mm[2:]]
                stream = start(mm)
            else:
                raise
        try:
            for chunk in stream:
                text = chunk["choices"][0]["delta"].get("content")
                if text:
                    yield {"text": text}
        finally:
            close = getattr(stream, "close", None)
            if close:
                close()


def agent_num(b, key, default, lo, hi, cast=int):
    try:
        return min(hi, max(lo, cast(b.get(key, default))))
    except (TypeError, ValueError):
        return default


@app.post("/api/agentrun")
async def agent_start(req: Request):
    need_ui(req)
    if not CAN_WRITE:
        raise HTTPException(403, "Agent mode needs to write files, which is off (--read-only, or a non-loopback host)")
    b = await req.json()
    goal = str(b.get("goal") or "").strip()
    if not goal or len(goal) > 8000:
        raise HTTPException(400, "Describe the task (up to 8000 characters)")
    n_ctx = llm.n_ctx() if llm is not None else int(CFG["coder"]["n_ctx"])
    max_new = min(agent_num(b, "max_tokens", 2048, 256, 4096), max(512, n_ctx // 3))
    params = {"temperature": min(agent_num(b, "temperature", 0.3, 0, 2, float), 0.4),    # tool use needs a steady hand
              "top_p": agent_num(b, "top_p", 0.9, 0, 1, float), "top_k": agent_num(b, "top_k", 40, 0, 1000),
              "repeat_penalty": agent_num(b, "repeat_penalty", 1.05, 1.0, 2.0, float), "max_tokens": max_new}
    tests = [ln.strip() for ln in str(b.get("test_cmd") or "").splitlines() if ln.strip()][:6]
    if any(len(t) > 500 for t in tests):
        raise HTTPException(400, "A test command is too long")
    resume = None
    rs = b.get("resume")
    if isinstance(rs, dict):        # the saved state of an interrupted run: the new run carries on from it
        resume = {"status": str(rs.get("status") or "")[:20], "summary": str(rs.get("summary") or "")[:600],
                  "plan_text": str(rs.get("plan_text") or "")[:6000],
                  "plan": [{"text": str(p.get("text") or "")[:200], "done": bool(p.get("done"))} for p in (rs.get("plan") or [])[:20] if isinstance(p, dict)],
                  "files": [str(x)[:300] for x in (rs.get("files") or [])[:40]],
                  "steps": [str(x)[:300] for x in (rs.get("steps") or [])[:12]]}
    mode = "strict" if str(b.get("mode") or "fast") == "strict" else "fast"     # fast (default): no planner, baseline run, rollbacks or step / attempt nagging
    run = agentmod.AgentRun(
        goal, agenttools.Sandbox(ROOT, can_write=CAN_WRITE, can_run=CAN_RUN, max_write=MAX_WRITE, max_out=MAX_OUT), agent_model,
        system=str(b.get("system") or "")[:6000], max_steps=agent_num(b, "max_steps", 25 if mode == "strict" else 15, 3, 60),
        max_attempts=agent_num(b, "max_attempts", 3, 1, 5), tests=tests or None, auto_tests=bool(b.get("auto_tests", True)),
        baseline=bool(b.get("baseline", True)), approval=str(b.get("approval") or "ask"),
        timeout=agent_num(b, "timeout", 60, 5, 300), count=ntok, ctx_budget=max(1200, n_ctx - max_new - 300), params=params,
        planner=agent_planner, plan_mode="off" if str(b.get("planner") or "auto") == "off" else "auto",
        planner_label=(os.path.basename(CFG["reasoner"]["path"]) if CFG["reasoner"]["path"] else (os.path.basename(CFG["coder"]["path"]) or "the coder")),
        coder_label=os.path.basename(CFG["coder"]["path"]) or "the coder",
        environment=hostinfo.prompt_block(host_env()), resume=resume, mode=mode)
    try:
        AGENT.begin(run)
    except agentmod.Busy as e:
        raise HTTPException(409, str(e))

    def gen():
        for ev in AGENT.stream(run):
            yield sse(ev)

    return StreamingResponse(gen(), media_type="text/event-stream")


@app.post("/api/agentrun/cancel")
async def agent_cancel(req: Request):
    need_ui(req)
    b = await req.json()
    return {"ok": AGENT.cancel(b.get("id"))}


@app.post("/api/agentrun/approve")
async def agent_approve(req: Request):
    need_ui(req)
    b = await req.json()
    return {"ok": AGENT.approve(str(b.get("id") or ""), bool(b.get("ok")), bool(b.get("always")))}


@app.post("/api/agentrun/revert")
async def agent_revert(req: Request):
    need_ui(req)
    if not CAN_WRITE:
        raise HTTPException(403, "Writing files is disabled")
    b = await req.json()
    try:
        files = await run_in_threadpool(AGENT.revert, str(b.get("id") or ""))
    except KeyError as e:
        raise HTTPException(404, str(e.args[0]))
    except agentmod.Busy as e:
        raise HTTPException(409, str(e))
    return {"ok": True, "files": files}


@app.get("/api/agentrun/detect")
def agent_detect(req: Request):
    need_ui(req)
    return {"root": str(ROOT), "tests": [t["cmd"] for t in agenttools.detect_tests(ROOT)]}


# ---------------------------------------------------------------- open any folder at any time
ROOT_LOCK = threading.Lock()


@app.get("/api/browse")
def browse_folders(path: str = ""):
    """Sub-folders of any folder on this computer, for the Open folder picker."""
    raw = (path or "").strip().strip('"')
    try:
        p = (Path(raw).expanduser() if raw else ROOT).resolve()
    except (OSError, RuntimeError, ValueError):
        raise HTTPException(400, "That path cannot be read")
    if not p.is_dir():
        raise HTTPException(404, "Not a folder: " + str(p))
    dirs, err = [], ""
    try:
        with os.scandir(p) as it:
            for e in it:
                try:
                    if e.is_dir() and not e.name.startswith("."):
                        dirs.append(e.name)
                except OSError:
                    pass
    except OSError as e:
        err = "Cannot list this folder: " + (e.strerror or str(e))
    dirs.sort(key=str.lower)
    places = [{"name": "Project folder", "path": str(ROOT)}]
    home = Path.home()
    places.append({"name": "Home", "path": str(home)})
    for n in ("Desktop", "Documents", "Downloads", "Projects"):
        if (home / n).is_dir():
            places.append({"name": n, "path": str(home / n)})
    drives = [f"{chr(c)}:\\" for c in range(65, 91) if os.path.exists(f"{chr(c)}:\\")] if os.name == "nt" else []
    return {"path": str(p), "parent": str(p.parent) if p.parent != p else "", "dirs": dirs[:1000], "sep": os.sep,
            "places": places, "drives": drives, "error": err}


@app.post("/api/root")
async def open_folder(req: Request):
    """Make another folder the project folder (file list, terminal, tools, agent mode, project memory)."""
    global ROOT, FS
    need_ui(req)
    b = await req.json()
    raw = str(b.get("path") or "").strip().strip('"')
    if not raw:
        raise HTTPException(400, "Give a folder path")
    try:
        p = Path(raw).expanduser()
        p = (p if p.is_absolute() else ROOT / p).resolve()
    except (OSError, RuntimeError, ValueError):
        raise HTTPException(400, "That path cannot be read")
    if not p.is_dir():
        raise HTTPException(404, "That folder does not exist: " + str(p))
    r = AGENT.run
    if r is not None and not r.finished and (r.started or time.monotonic() - r.created < 30):
        raise HTTPException(409, "An agent run is in progress. Stop it before opening another folder.")
    with ROOT_LOCK:
        ROOT = p
        FS = fstools.Workspace(p, SKIP, BIN, MAX_WRITE)
        with MEM.lock:
            MEM.project = str(p)
            MEM._prof = (0.0, "")
            MEM._qcache.clear()
    return {"root": str(ROOT)}


if __name__ == "__main__":
    print(f"Open http://{args.host}:{args.port}   (project folder: {ROOT})")
    print("Coder: " + (os.path.basename(CFG["coder"]["path"]) + ("" if llm is not None else "  (not loaded yet)") if CFG["coder"]["path"] else "none selected (pick one in the Model tab)"))
    print("Chat: " + (os.path.basename(CFG["chat"]["path"]) + ("" if cllm is not None else "  (loads when a message is not about code)") if CFG["chat"]["path"] else "none selected (pick one in the Model tab; it answers everything that is not code)"))
    print("Reasoner: " + (os.path.basename(CFG["reasoner"]["path"]) + "  (loaded only while reasoning; the coder is unloaded meanwhile)" if CFG["reasoner"]["path"] else f"off ({R_WHY})"))
    print(f"Chats: saved in {CHAT_DIR}  ({len(read_chats())} stored)")
    print(f"Memory: {MEM.path}  ({len(MEM.data['facts'])} notes, {sum(1 for t in MEM.data['tasks'] if t.get('status')=='open')} open tasks, project {MEM.project_name()})")
    print("Semantic memory: " + (f"{EMB.name} (CPU, loaded when a note is searched, freed after {args.embed_idle}s idle)" if EMB.available()
                                 else f"off, memory matches on keywords ({EMB.why})"))
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
    print(f"Agent mode: {'on' if CAN_WRITE else 'off (needs file writing)'}" + ("" if CAN_RUN else "   (no commands or tests: --no-terminal)"))
    print(f"Wikipedia: {'on' if WIKI_ON else 'off (--no-wiki or --no-web)'}")
    print(f"GitHub: {('on, with a token' if github.TOKEN else 'on, no token (60 requests per hour; set GITHUB_TOKEN for more)') if GH_ON else 'off (--no-github or --no-web)'}")
    print(f"iNaturalist: {('on' + (', photo ID ready' if args.inat_token else ', photo ID off (no --inat-token)')) if INAT_ON else 'off (--no-inat or --no-web)'}")
    print(f"Weather (Open-Meteo): {'on' if WEATHER_ON else 'off (--no-geo, --no-weather or --no-web)'}   "
          f"Places (OpenStreetMap): {'on' if OSM_ON else 'off (--no-geo, --no-osm or --no-web)'}"
          + ("" if (not OSM_ON or openstreetmap.CONTACT) else "   (tip: --osm-contact you@example.com identifies your requests, as OpenStreetMap's usage policy asks)"))
    print(f"Web search: {'on, SearXNG at ' + SEARX if WEB_ON else 'off (--no-web)'}"
          + ("   (pages on your own network can be read)" if WEB_ON and args.web_allow_private else ""))
    if args.open:
        threading.Timer(1.0, webbrowser.open, [f"http://{args.host}:{args.port}"]).start()
    uvicorn.run(app, host=args.host, port=args.port, log_level="warning")
