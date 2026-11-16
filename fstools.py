"""Project file operations for CressyAI's model tools: read, write, edit, glob, grep.

Pure Python with no server globals, so it can be tested without a model or a web server. Everything is confined to one
root folder (the app's --root): paths are resolved first (so `..` and symlinks cannot escape), nothing inside .git is
ever written, binary files are never overwritten, and writes are atomic (temp file, then rename).

write() and edit() take dry_run=True to return what they *would* do (including a short diff) without touching the disk;
the tool layer uses that to show the user exactly what they are approving."""
import difflib
import os
import re
import time
from pathlib import Path

SKIP = {".git", "node_modules", "__pycache__", ".venv", "venv", ".idea", ".vscode"}
BIN = {".png", ".jpg", ".jpeg", ".gif", ".ico", ".pdf", ".zip", ".gz", ".gguf", ".pyc", ".exe", ".dll", ".so", ".woff", ".woff2", ".mp3", ".mp4"}
MAX_READ_BYTES = 2_000_000      # bigger files: use grep or read a slice
MAX_WRITE = 2_000_000
MAX_GREP_FILE = 500_000
MAX_GREP_TOTAL = 30_000_000     # bytes scanned by one grep
GREP_SECONDS = 8
MAX_LINE = 500                  # characters of one line shown by read
NUMBERED = re.compile(r"^\s*\d+ \| ?")


class FsError(Exception):
    """A problem whose message can be shown to the model and the user as it is."""


def glob_regex(pat):
    """Glob -> compiled regex on a posix-style relative path. ** crosses folders, * and ? do not, {a,b} and [abc] work.
    A pattern with no slash matches at any depth (so '*.py' finds every Python file)."""
    pat = (pat or "").replace("\\", "/").strip()
    while pat.startswith("./"):
        pat = pat[2:]
    anchored = pat.startswith("/")          # '/*.py' means the top folder only
    pat = pat.lstrip("/")
    if not pat:
        raise FsError("the pattern is empty")
    if ".." in pat.split("/"):
        raise FsError("'..' is not allowed in a pattern")
    if "/" not in pat and not anchored:
        pat = "**/" + pat
    i, n, out = 0, len(pat), []
    while i < n:
        c = pat[i]
        if c == "*":
            if pat[i:i + 2] == "**":
                j = i + 2
                if pat[j:j + 1] == "/":
                    out.append("(?:.*/)?")
                    i = j + 1
                    continue
                out.append(".*")
                i = j
                continue
            out.append("[^/]*")
        elif c == "?":
            out.append("[^/]")
        elif c == "[":
            j = pat.find("]", i + 2)
            if j < 0:
                out.append(re.escape(c))
            else:
                body = pat[i + 1:j]
                if body[:1] in ("!", "^"):
                    body = "^" + body[1:]
                out.append("[" + body.replace("\\", "\\\\") + "]")
                i = j
        elif c == "{":
            j = pat.find("}", i)
            if j < 0:
                out.append(re.escape(c))
            else:
                out.append("(?:" + "|".join(re.escape(a) for a in pat[i + 1:j].split(",")) + ")")
                i = j
        else:
            out.append(re.escape(c))
        i += 1
    try:
        return re.compile("".join(out) + r"\Z", re.I if os.name == "nt" else 0)
    except re.error as e:
        raise FsError(f"bad pattern: {e}")


def _diff(old, new, name, max_lines=40):
    d = [ln[:200] for ln in difflib.unified_diff(old.splitlines(), new.splitlines(), f"a/{name}", f"b/{name}", lineterm="", n=2)]
    if len(d) > max_lines:
        d = d[:max_lines] + [f"... {len(d) - max_lines} more diff lines"]
    return "\n".join(d)


def _count_lines(text):
    return text.count("\n") + (1 if text and not text.endswith("\n") else 0)


class Workspace:
    def __init__(self, root, skip=SKIP, binary=BIN, max_write=MAX_WRITE):
        self.root = Path(root).expanduser().resolve()
        self.skip, self.binary, self.max_write = set(skip), set(binary), max_write

    # ------------------------------------------------------------ paths
    def path(self, rel="", what="path"):
        rel = str(rel or "").strip()
        try:
            p = (self.root / rel).resolve()
        except (OSError, ValueError, RuntimeError):
            raise FsError(f"bad {what}")
        if p != self.root and not p.is_relative_to(self.root):
            raise FsError("that path is outside the project folder")
        return p

    def rel(self, p):
        return "" if p == self.root else p.relative_to(self.root).as_posix()

    def _inside(self, p):
        try:
            r = p.resolve()
        except (OSError, RuntimeError):
            return False
        return r == self.root or r.is_relative_to(self.root)

    def walk(self, base):
        for dp, dns, fns in os.walk(base):
            dns[:] = sorted(d for d in dns if d not in self.skip)
            for fn in sorted(fns):
                yield Path(dp) / fn

    def _writable(self, p):
        if p == self.root or p.is_dir():
            raise FsError("that path is a folder, not a file")
        if ".git" in p.relative_to(self.root).parts:
            raise FsError("writing inside .git is not allowed")

    # ------------------------------------------------------------ read
    def read(self, path, offset=1, limit=200, max_chars=12000):
        p = self.path(path)
        if not p.exists():
            raise FsError("no such file; use filesystem.glob to find the right path")
        if p.is_dir():
            raise FsError("that is a folder; use filesystem.glob with a pattern like 'folder/*' to list it")
        size = p.stat().st_size
        if size > MAX_READ_BYTES:
            raise FsError(f"the file is too large to read ({size} bytes); use filesystem.grep to find the part you need")
        data = p.read_bytes()
        if b"\0" in data[:4096]:
            raise FsError("that file is binary")
        lines = data.decode("utf-8", "replace").splitlines()
        total = len(lines)
        offset = max(1, int(offset))
        if offset > max(total, 1):
            raise FsError(f"offset {offset} is past the end of the file ({total} lines)")
        out, chars, end = [], 0, offset - 1
        for i in range(offset - 1, min(total, offset - 1 + max(1, int(limit)))):
            s = f"{i + 1:>5} | {lines[i][:MAX_LINE]}"
            if out and chars + len(s) + 1 > max_chars:
                break
            out.append(s)
            chars += len(s) + 1
            end = i + 1
        return {"path": self.rel(p), "text": "\n".join(out), "start": offset, "end": end, "total": total, "truncated": end < total}

    # ------------------------------------------------------------ glob
    def glob(self, pattern, path="", limit=200):
        base = self.path(path or "")
        if not base.is_dir():
            raise FsError("the folder to search does not exist")
        rx = glob_regex(pattern)
        hits, more = [], False
        for p in self.walk(base):
            if not rx.match(p.relative_to(base).as_posix()) or not self._inside(p):
                continue
            if len(hits) >= limit:
                more = True
                break
            hits.append(self.rel(p))
        return {"files": hits, "truncated": more}

    # ------------------------------------------------------------ grep
    def grep(self, pattern, path="", glob="", regex=True, ignore_case=True, limit=40, context=0):
        if not pattern:
            raise FsError("the pattern is empty")
        if len(pattern) > 200:
            raise FsError("the pattern is too long")
        try:
            rx = re.compile(pattern if regex else re.escape(pattern), re.I if ignore_case else 0)
        except re.error as e:
            raise FsError(f"bad regular expression: {e} (set regex=false to search for the text literally)")
        base = self.path(path or "")
        if not base.exists():
            raise FsError("no such file or folder")
        gre = glob_regex(glob) if glob else None
        files = [base] if base.is_file() else self.walk(base)
        t0, scanned, nfiles, hits, trunc = time.monotonic(), 0, 0, [], False
        for p in files:
            if base.is_dir() and gre and not gre.match(p.relative_to(base).as_posix()):
                continue
            if p.suffix.lower() in self.binary or not self._inside(p):
                continue
            try:
                if p.stat().st_size > MAX_GREP_FILE:
                    continue
                data = p.read_bytes()
            except OSError:
                continue
            if b"\0" in data[:4096]:
                continue
            nfiles += 1
            scanned += len(data)
            lines = data.decode("utf-8", "replace").splitlines()
            for i, line in enumerate(lines):
                if rx.search(line[:1000]):
                    h = {"file": self.rel(p), "line": i + 1, "text": line[:300]}
                    if context:
                        h["before"] = [x[:300] for x in lines[max(0, i - context):i]]
                        h["after"] = [x[:300] for x in lines[i + 1:i + 1 + context]]
                    hits.append(h)
                    if len(hits) >= limit:
                        return {"hits": hits, "files": nfiles, "truncated": True}
            if scanned > MAX_GREP_TOTAL or time.monotonic() - t0 > GREP_SECONDS:
                trunc = True
                break
        return {"hits": hits, "files": nfiles, "truncated": trunc}

    # ------------------------------------------------------------ write
    def _atomic(self, p, data):
        try:
            p.parent.mkdir(parents=True, exist_ok=True)
            tmp = p.with_name(p.name + ".cressy-tmp")
            tmp.write_bytes(data)
            os.replace(tmp, p)
        except OSError as e:
            raise FsError(f"could not write: {e.strerror or e}")

    def write(self, path, content, dry_run=False):
        p = self.path(path)
        self._writable(p)
        data = content.encode("utf-8")
        if len(data) > self.max_write:
            raise FsError(f"the file is too large to write ({len(data)} bytes)")
        existed, old = p.exists(), b""
        if existed:
            old = p.read_bytes()
            if b"\0" in old[:4096]:
                raise FsError("refusing to overwrite a binary file")
            if b"\r\n" in old and b"\r\n" not in data:
                data = data.replace(b"\n", b"\r\n")
        info = {"path": self.rel(p), "created": not existed, "bytes": len(data), "old_bytes": len(old), "lines": _count_lines(content)}
        if existed:
            info["diff"] = _diff(old.decode("utf-8", "replace"), data.decode("utf-8", "replace"), info["path"]) or "(no change)"
            info["same"] = old == data
        else:
            head = content.splitlines()[:25]
            info["diff"] = "\n".join("+ " + ln[:200] for ln in head) + (f"\n... {info['lines'] - 25} more lines" if info["lines"] > 25 else "")
        if not dry_run:
            self._atomic(p, data)
        return info

    # ------------------------------------------------------------ edit
    def edit(self, path, old, new="", replace_all=False, dry_run=False):
        p = self.path(path)
        self._writable(p)
        if not p.exists():
            raise FsError("no such file; use filesystem.write to create it")
        if not old:
            raise FsError("'old' is empty; give the exact text to replace")
        raw = p.read_bytes()
        if b"\0" in raw[:4096]:
            raise FsError("refusing to edit a binary file")
        try:
            text = raw.decode("utf-8")
        except UnicodeDecodeError:
            raise FsError("the file is not valid UTF-8, so it is not edited")
        crlf = "\r\n" in text
        if crlf:
            text = text.replace("\r\n", "\n")
        old, new = old.replace("\r\n", "\n"), new.replace("\r\n", "\n")
        n = text.count(old)
        if n == 0 and all(NUMBERED.match(ln) for ln in old.split("\n") if ln.strip()):
            # the model copied the line numbers that filesystem.read prints; they are not part of the file
            old = "\n".join(NUMBERED.sub("", ln, count=1) for ln in old.split("\n"))
            new = "\n".join(NUMBERED.sub("", ln, count=1) for ln in new.split("\n"))
            n = text.count(old) if old else 0
        if n == 0:
            raise FsError("the text to replace was not found; read the file again and copy it exactly, including spaces and indentation")
        if old == new:
            raise FsError("'old' and 'new' are the same, so nothing would change")
        if n > 1 and not replace_all:
            raise FsError(f"the text to replace appears {n} times; include more surrounding lines to make it unique, or set replace_all=true")
        out = text.replace(old, new) if replace_all else text.replace(old, new, 1)
        data = (out.replace("\n", "\r\n") if crlf else out).encode("utf-8")
        if len(data) > self.max_write:
            raise FsError("the edited file would be too large")
        info = {"path": self.rel(p), "replacements": n if replace_all else 1, "bytes": len(data), "diff": _diff(text, out, self.rel(p))}
        if not dry_run:
            self._atomic(p, data)
        return info
