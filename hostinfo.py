"""What machine is CressyAI running on? OS, shell, CPU, RAM, GPU and the developer tools that exist.

Standard library only (psutil is used when it is installed). Everything is detected, never guessed, and every probe is wrapped:
a missing tool or a slow command can only make a field empty, never break the app.

    info = detect()                 # dict, cached for a minute (free RAM is always read fresh)
    prompt_block(info)              # a short text the coding model reads, with rules that fit THIS operating system
"""
import ctypes, os, platform, re, shutil, subprocess, sys, time

try:
    import psutil
except Exception:           # pragma: no cover - psutil is optional here
    psutil = None

_CACHE = {"t": 0.0, "info": None}
_TOOLS = ("git", "node", "npm", "python", "python3", "pip", "pip3", "docker", "cargo", "go", "java", "dotnet", "make", "gcc", "cmake")


def _run(cmd, timeout=4):
    try:
        kw = {"capture_output": True, "text": True, "timeout": timeout}
        if os.name == "nt":
            kw["creationflags"] = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        r = subprocess.run(cmd, **kw)
        return (r.stdout or "").strip() if r.returncode == 0 else ""
    except Exception:
        return ""


def _gb(n):
    return round(n / 1024 ** 3, 1) if n else 0.0


# ------------------------------------------------------------------ operating system
def os_info():
    system = platform.system() or "Unknown"
    name, ver = system, platform.release()
    if system == "Windows":
        build = 0
        try:
            build = sys.getwindowsversion().build
        except Exception:
            pass
        name = "Windows 11" if build >= 22000 else "Windows " + (platform.release() or "")
        ver = f"build {build}" if build else platform.version()
    elif system == "Darwin":
        mv = platform.mac_ver()[0]
        name, ver = "macOS", mv or platform.release()
    elif system == "Linux":
        pretty = ""
        try:
            with open("/etc/os-release", encoding="utf-8") as f:
                m = re.search(r'^PRETTY_NAME="?([^"\n]+)"?', f.read(), re.M)
                pretty = m.group(1) if m else ""
        except OSError:
            pass
        name, ver = pretty or "Linux", platform.release()
        if "microsoft" in platform.release().lower():
            name += " (WSL)"
    return {"system": system, "name": name, "version": ver, "machine": platform.machine(), "bits": 64 if sys.maxsize > 2 ** 32 else 32}


def shell_info():
    """The shell that agent commands really run in: the app starts them with shell=True."""
    if os.name == "nt":
        comspec = os.environ.get("COMSPEC", "cmd.exe")
        return {"name": os.path.basename(comspec).lower(), "kind": "cmd"}
    sh = os.environ.get("SHELL", "/bin/sh")
    return {"name": os.path.basename("/bin/sh"), "kind": "posix", "login": os.path.basename(sh)}   # shell=True always uses /bin/sh


# ------------------------------------------------------------------ hardware
def cpu_info():
    name = ""
    try:
        if platform.system() == "Windows":
            name = platform.processor()
            if not name:
                name = _run(["wmic", "cpu", "get", "name"]).replace("Name", "").strip()
        elif platform.system() == "Darwin":
            name = _run(["sysctl", "-n", "machdep.cpu.brand_string"])
        else:
            with open("/proc/cpuinfo", encoding="utf-8", errors="replace") as f:
                m = re.search(r"model name\s*:\s*(.+)", f.read())
                name = m.group(1).strip() if m else ""
    except Exception:
        pass
    logical = os.cpu_count() or 0
    physical = 0
    if psutil:
        try:
            physical = psutil.cpu_count(logical=False) or 0
        except Exception:
            pass
    return {"name": re.sub(r"\s+", " ", name).strip(), "logical": logical, "physical": physical}


def ram_info():
    """(total, available) in bytes; 0 when unknown."""
    try:
        if psutil:
            v = psutil.virtual_memory()
            return int(v.total), int(v.available)
        if platform.system() == "Linux":
            vals = {}
            with open("/proc/meminfo", encoding="utf-8") as f:
                for line in f:
                    k, _, rest = line.partition(":")
                    vals[k] = int(rest.split()[0]) * 1024
            return vals.get("MemTotal", 0), vals.get("MemAvailable", 0)
        if platform.system() == "Windows":
            class MS(ctypes.Structure):
                _fields_ = [("dwLength", ctypes.c_ulong), ("dwMemoryLoad", ctypes.c_ulong), ("ullTotalPhys", ctypes.c_ulonglong),
                            ("ullAvailPhys", ctypes.c_ulonglong), ("ullTotalPageFile", ctypes.c_ulonglong),
                            ("ullAvailPageFile", ctypes.c_ulonglong), ("ullTotalVirtual", ctypes.c_ulonglong),
                            ("ullAvailVirtual", ctypes.c_ulonglong), ("sullAvailExtendedVirtual", ctypes.c_ulonglong)]
            ms = MS()
            ms.dwLength = ctypes.sizeof(MS)
            ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(ms))
            return int(ms.ullTotalPhys), int(ms.ullAvailPhys)
        if platform.system() == "Darwin":
            total = int(_run(["sysctl", "-n", "hw.memsize"]) or 0)
            return total, 0
    except Exception:
        pass
    return 0, 0


def gpu_info():
    """List of {name, vram_gb, vendor}. NVIDIA through nvidia-smi, Apple Silicon by architecture, others by the OS's device list."""
    out = []
    smi = shutil.which("nvidia-smi")
    if smi:
        for line in _run([smi, "--query-gpu=name,memory.total,memory.free", "--format=csv,noheader,nounits"]).splitlines():
            parts = [p.strip() for p in line.split(",")]
            if len(parts) >= 2:
                try:
                    total = float(parts[1]) / 1024
                    free = float(parts[2]) / 1024 if len(parts) > 2 else 0
                except ValueError:
                    total = free = 0
                out.append({"name": parts[0], "vram_gb": round(total, 1), "free_gb": round(free, 1), "vendor": "nvidia"})
    if not out and platform.system() == "Darwin" and platform.machine() == "arm64":
        out.append({"name": "Apple Silicon GPU (Metal, shares system memory)", "vram_gb": 0, "free_gb": 0, "vendor": "apple"})
    if not out and platform.system() == "Windows":
        names = _run(["wmic", "path", "win32_VideoController", "get", "name"])
        for n in [x.strip() for x in names.splitlines()[1:] if x.strip()]:
            out.append({"name": n, "vram_gb": 0, "free_gb": 0, "vendor": "amd" if "amd" in n.lower() or "radeon" in n.lower() else "intel" if "intel" in n.lower() else "other"})
    if not out and platform.system() == "Linux" and shutil.which("lspci"):
        for line in _run(["lspci"]).splitlines():
            if re.search(r"VGA|3D controller|Display controller", line):
                out.append({"name": line.split(": ", 1)[-1].strip()[:80], "vram_gb": 0, "free_gb": 0, "vendor": "other"})
    return out[:3]


def tool_versions():
    found = {}
    for t in _TOOLS:
        p = shutil.which(t)
        if p:
            found[t] = p
    ver = {}
    if "node" in found:
        ver["node"] = _run([found["node"], "--version"], 3).lstrip("v")
    if "git" in found:
        ver["git"] = _run([found["git"], "--version"], 3).replace("git version", "").strip()
    return found, ver


# ------------------------------------------------------------------ everything together
def detect(force=False, extra=None):
    """The machine profile. OS, tools and GPU are cached for a minute; RAM is always fresh. `extra` (model settings etc.) is merged in."""
    now = time.time()
    if force or not _CACHE["info"] or now - _CACHE["t"] > 60:
        found, ver = tool_versions()
        py = "python" if os.name == "nt" or "python3" not in found else "python3"
        _CACHE["info"] = {"os": os_info(), "shell": shell_info(), "cpu": cpu_info(), "gpu": gpu_info(), "tools": sorted(found), "versions": ver,
                          "python": {"cmd": py if py in found else (os.path.basename(sys.executable) or "python"), "version": platform.python_version()},
                          "sep": os.sep}
        _CACHE["t"] = now
    info = dict(_CACHE["info"])
    total, avail = ram_info()
    info["ram"] = {"total_gb": _gb(total), "free_gb": _gb(avail)}
    info["extra"] = dict(extra or {})
    return info


def _windows_rules(info):
    py = info["python"]["cmd"]
    return [
        "Commands run in cmd.exe, NOT bash. Use dir, type FILE, findstr, where, copy, move, del, mkdir, cd /d. These do not exist here: ls, cat, grep, touch, rm, cp, mv, which, export, head, tail.",
        "Chain commands with & or &&. Set variables with `set NAME=value` on its own line. Quote paths that contain spaces. Paths may use \\ or /.",
        f"Run Python as `{py}` (not python3) and pip as `{py} -m pip`.",
    ]


def _posix_rules(info, mac):
    py = info["python"]["cmd"]
    rules = [
        "Commands run in /bin/sh (POSIX), not PowerShell or cmd. Use ls, cat, grep, find, cp, mv, mkdir -p. Chain with && . Do not use bash-only syntax such as [[ ]] or arrays.",
        f"Run Python as `{py}` and pip as `{py} -m pip`.",
    ]
    if mac:
        rules.append("macOS uses BSD tools: `sed -i ''` needs an empty suffix argument, `date` and `grep -P` differ from GNU.")
    return rules


def prompt_block(info, compact=False):
    """A short, factual description of this computer plus the rules the model must follow here. Written for a small model: few words."""
    o, c, r = info["os"], info["cpu"], info["ram"]
    win, mac = o["system"] == "Windows", o["system"] == "Darwin"
    cpu = (c["name"] + " · " if c["name"] else "") + (f"{c['physical']} cores / {c['logical']} threads" if c["physical"] else f"{c['logical']} threads")
    ram = f"{r['total_gb']} GB RAM" + (f" ({r['free_gb']} GB free now)" if r["free_gb"] else "")
    gpus = info["gpu"]
    gpu = "; ".join(g["name"] + (f" {g['vram_gb']} GB VRAM" if g["vram_gb"] else "") for g in gpus) if gpus else "no dedicated GPU found"
    tools = ", ".join(t + (" " + info["versions"][t] if info["versions"].get(t) else "") for t in info["tools"] if t not in ("pip3", "python3") or not win) or "none detected"
    lines = [
        "THIS COMPUTER (detected, not guessed):",
        f"OS: {o['name']} {o['version']} ({o['machine']}) · shell for commands: {info['shell']['name']} · Python {info['python']['version']}",
        f"Hardware: {cpu} · {ram} · GPU: {gpu}",
        f"Tools on PATH: {tools}",
    ]
    ex = info.get("extra") or {}
    if ex.get("model"):
        lines.append(f"Local model: {ex['model']} · context {ex.get('n_ctx', '?')} tokens · GPU layers {ex.get('n_gpu_layers', 0)}")
    lines.append("Rules for this computer:")
    rules = _windows_rules(info) if win else _posix_rules(info, mac)
    missing = [t for t in ("node", "npm", "git", "docker") if t not in info["tools"]]
    if missing:
        rules.append("Not installed here, so do not call them: " + ", ".join(missing) + ". If the task needs one, say so in the done block instead of trying.")
    if r["total_gb"] and r["total_gb"] <= 8:
        rules.append(f"Only {r['total_gb']} GB RAM: choose light solutions, avoid heavy installs (torch, tensorflow, big datasets) unless the task requires them, keep test data small.")
    if not gpus or all(g["vendor"] not in ("nvidia", "apple") for g in gpus):
        rules.append("No CUDA or Metal GPU: write CPU code (device='cpu'); do not assume torch.cuda or mps exists. Check availability in code before using a GPU.")
    elif any(g["vendor"] == "nvidia" for g in gpus):
        rules.append("An NVIDIA GPU exists, but check torch.cuda.is_available() in code before using it, and keep VRAM use inside the card's size.")
    elif any(g["vendor"] == "apple" for g in gpus):
        rules.append("Apple Silicon: if a GPU is useful, use device 'mps' only after checking torch.backends.mps.is_available().")
    if not compact:
        lines += ["- " + x for x in rules]
    else:
        lines += ["- " + x for x in rules[:2]]
    return "\n".join(lines)


def one_line(info):
    """For chat answers about code: just enough for the answer to fit this computer."""
    o, c, r = info["os"], info["cpu"], info["ram"]
    gpus = info["gpu"]
    gpu = gpus[0]["name"] if gpus else "no GPU"
    shell = "Windows cmd" if o["system"] == "Windows" else "a POSIX shell"
    return (f"The user's computer: {o['name']}, {c['logical']} CPU threads, {r['total_gb']} GB RAM, {gpu}. Commands you suggest run in {shell}; "
            f"Python is `{info['python']['cmd']}`. Give commands that work on this OS.")
