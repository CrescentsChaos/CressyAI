"""Helper for test_server_agent.py (run in its own process: it stubs fastapi/llama_cpp so server.py can be imported anywhere).
Usage: python tests/server_agent_harness.py PROJECT_DIR   -> prints one JSON line describing what /api/agentrun did."""
import asyncio, json, os, runpy, sys, types

ROOT = sys.argv[1]
REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
F = "`" * 3


def mod(name, **kw):
    m = types.ModuleType(name)
    m.__dict__.update(kw)
    sys.modules[name] = m


class HTTPException(Exception):
    def __init__(self, status_code, detail=None):
        super().__init__(detail)
        self.status_code, self.detail = status_code, detail


class FastAPI:
    def __init__(self):
        self.routes = {}

    def _r(self, kind):
        return lambda path: (lambda fn: self.routes.setdefault((kind, path), fn) and fn)

    def get(self, p): return self._r("GET")(p)
    def post(self, p): return self._r("POST")(p)
    def put(self, p): return self._r("PUT")(p)
    def delete(self, p): return self._r("DELETE")(p)
    def middleware(self, t): return lambda f: f
    def on_event(self, t): return lambda f: f
    def mount(self, *a, **k): pass


class Resp:
    def __init__(self, content=None, *a, **k): self.content = content


async def run_in_threadpool(fn, *a, **k): return fn(*a, **k)


mod("fastapi", FastAPI=FastAPI, HTTPException=HTTPException, Request=object)
mod("fastapi.responses", FileResponse=Resp, JSONResponse=Resp, Response=Resp, StreamingResponse=Resp)
mod("uvicorn", run=lambda *a, **k: None)
mod("pypdf", PdfReader=object)
mod("pypdf.errors", PdfReadError=Exception)
mod("starlette")
mod("starlette.concurrency", run_in_threadpool=run_in_threadpool)

FIX = "<<<<<<< SEARCH\n    return a - b\n=======\n    return a + b\n>>>>>>> REPLACE"
REPLIES = [f"Fix it.\n{F}edit:calc.py\n{FIX}\n{F}\n{F}test\n{F}\n", f"{F}done\nfixed add\n{F}\n"]


class FakeLlama:
    calls = 0
    metadata = {}
    n_threads = 4

    class _m:
        def __getattr__(self, n): return lambda *a, **k: 0
    _model = _m()

    def __init__(self, **kw): pass
    def n_ctx(self): return 8192
    def n_vocab(self): return 100
    def tokenize(self, b, add_bos=False): return list(range(len(b) // 4 + 1))

    def create_chat_completion(self, messages, stream, **kw):
        assert messages[0]["role"] == "system" and kw["temperature"] <= 0.4, kw
        text = REPLIES[FakeLlama.calls]
        FakeLlama.calls += 1

        def it():
            for i in range(0, len(text), 25):
                yield {"choices": [{"delta": {"content": text[i:i + 25]}, "finish_reason": None}]}
            yield {"choices": [{"delta": {}, "finish_reason": "stop"}]}
        return it()


mod("llama_cpp", Llama=FakeLlama)
sys.argv = ["server.py", "--no-web", "--no-autoload", "--root", ROOT, "--data-dir", os.path.join(ROOT, ".data"), "--model", "/tmp/fake.gguf"]
sys.path.insert(0, REPO)
app = runpy.run_path(os.path.join(REPO, "server.py"), run_name="server_under_test")["app"]


class Req:
    headers = {"x-cressy": "1"}
    def __init__(self, body): self.body = body
    async def json(self): return self.body


async def main():
    out = {}
    start = app.routes[("POST", "/api/agentrun")]
    resp = await start(Req({"goal": "fix add", "max_steps": 6, "temperature": 1.5}))
    evs = [json.loads(x[6:]) for x in resp.content if x.startswith("data: ")]
    fin = [e for e in evs if e["type"] == "done"][0]
    out.update(types=[e["type"] for e in evs], status=fin["status"], verification=fin["verification"], files=[f["path"] for f in fin["files"]],
               fixed="a + b" in open(os.path.join(ROOT, "calc.py")).read(), caps=app.routes[("GET", "/api/caps")]()["agent"])
    out["revert"] = await app.routes[("POST", "/api/agentrun/revert")](Req({"id": fin["id"]}))
    out["reverted"] = "a - b" in open(os.path.join(ROOT, "calc.py")).read()
    out["detect"] = app.routes[("GET", "/api/agentrun/detect")](Req({}))["tests"]
    for name, body in (("empty", {"goal": ""}),):
        try:
            await start(Req(body))
        except HTTPException as e:
            out[name] = e.status_code
    try:
        await app.routes[("POST", "/api/agentrun/revert")](Req({"id": "nope"}))
    except HTTPException as e:
        out["revert_unknown"] = e.status_code
    print("RESULT " + json.dumps(out))

asyncio.run(main())
