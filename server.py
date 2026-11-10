#!/usr/bin/env python3
"""Local GGUF chat server.   python server.py --model path/to/model.gguf"""
import argparse, json, os, shutil, subprocess, threading, time, webbrowser
from pathlib import Path

import uvicorn
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from llama_cpp import Llama

try:
    import psutil
    PROC = psutil.Process()
except ImportError:
    psutil = PROC = None

ap = argparse.ArgumentParser()
ap.add_argument("--model", required=True, help="path to .gguf file")
ap.add_argument("--n-ctx", type=int, default=4096)
ap.add_argument("--n-gpu-layers", type=int, default=0, help="-1 = offload all layers")
ap.add_argument("--threads", type=int, default=None)
ap.add_argument("--chat-format", default=None, help="override if the GGUF has no chat template")
ap.add_argument("--host", default="127.0.0.1")
ap.add_argument("--port", type=int, default=8000)
ap.add_argument("--root", default=".", help="folder the app may read (default: current folder)")
ap.add_argument("--open", action="store_true", help="open the browser on start")
args = ap.parse_args()

t0 = time.time()
llm = Llama(model_path=args.model, n_ctx=args.n_ctx, n_gpu_layers=args.n_gpu_layers,
            n_threads=args.threads, chat_format=args.chat_format, verbose=False)
LOAD_S = time.time() - t0
LOCK = threading.Lock()  # llama.cpp contexts are not thread-safe
app = FastAPI()


@app.middleware("http")
async def guard(req, call_next):
    """Block DNS-rebinding: when bound to loopback, only accept loopback Host headers."""
    if args.host in ("127.0.0.1", "localhost", "::1") and req.url.hostname not in ("127.0.0.1", "localhost", "::1"):
        return JSONResponse({"detail": "Bad Host header"}, 403)
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
    toks = len(llm.tokenize(text.encode("utf-8"), add_bos=False))
    return {"name": rel_of(p) or p.name, "text": text, "tokens": toks, "truncated": len(data) > limit}


def model_call(name):
    try:
        return getattr(llm._model, name)()
    except Exception:
        return None


def fit(messages, max_new):
    """Drop the oldest turns so the prompt fits the context window."""
    budget = llm.n_ctx() - (max_new or 512) - 64
    cost = lambda m: len(llm.tokenize(str(m.get("content", "")).encode(), add_bos=False)) + 8
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
    md = {k: (v if len(v) < 400 else v[:400] + "…") for k, v in llm.metadata.items()}
    return {
        "file": os.path.basename(args.model), "file_size": os.path.getsize(args.model),
        "load_seconds": round(LOAD_S, 2), "n_ctx": llm.n_ctx(), "n_vocab": llm.n_vocab(),
        "n_embd": model_call("n_embd"), "n_params": model_call("n_params"),
        "n_gpu_layers": args.n_gpu_layers, "n_threads": llm.n_threads, "metadata": md,
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
    msgs, trimmed = fit(b["messages"], b.get("max_tokens"))

    def gen():
        with LOCK:
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
            "tokens": len(llm.tokenize(text.encode("utf-8"), add_bos=False))}


@app.post("/api/tokens")
async def count_tokens(req: Request):
    b = await req.json()
    return {"tokens": len(llm.tokenize(b["text"].encode("utf-8"), add_bos=False))}


if __name__ == "__main__":
    print(f"Open http://{args.host}:{args.port}   (readable folder: {ROOT})")
    if args.open:
        threading.Timer(1.0, webbrowser.open, [f"http://{args.host}:{args.port}"]).start()
    uvicorn.run(app, host=args.host, port=args.port, log_level="warning")
