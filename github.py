"""GitHub for CressyAI through the REST API (https://docs.github.com/rest). Standard library only.

Reads repositories (README, files, folders), issues and pull requests, releases and user profiles, and searches repositories.
Public data needs no login (60 requests per hour); a token (GITHUB_TOKEN / --github-token) raises that to 5,000 and lets it
read private repositories the token can see. The token is only ever sent to api.github.com and is never returned to the browser.
"""
import base64, json, re, socket, time, urllib.error, urllib.parse, urllib.request
from concurrent.futures import ThreadPoolExecutor

from websearch import WebError

API_HOST = "api.github.com"
API = "https://" + API_HOST
UA = "CressyAI/1.0 (local assistant)"
TOKEN = ""                      # set by server.py
RATE = {"limit": None, "remaining": None, "reset": 0}
OWNER_RE = re.compile(r"^[A-Za-z0-9](?:[A-Za-z0-9-]{0,38})$")
REPO_RE = re.compile(r"^(?!\.{1,2}$)[A-Za-z0-9._-]{1,100}$")
RESERVED = {"features", "topics", "orgs", "settings", "login", "logout", "explore", "marketplace", "sponsors", "about", "pricing",
            "search", "notifications", "pulls", "issues", "new", "join", "collections", "trending", "events", "apps", "enterprise",
            "customer-stories", "readme", "security", "site", "contact", "codespaces", "copilot", "gist"}
STOP = set("""github git hub repo repos repository repositories project projects on in from find me show search look up for a an the
some any please can you give list recommend suggest best top popular most starred trending good great open source that which is
are to i want need looking with using about of what there ones""".split())
RANK_WORDS = {"best", "top", "popular", "most", "starred", "trending"}

_CACHE, _CACHE_MAX, _CACHE_TTL = {}, 80, 300


class NotFound(WebError):
    pass


class _SameHost(urllib.request.HTTPRedirectHandler):
    """Never follow a redirect to another host, so the token cannot leak."""
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        if urllib.parse.urlsplit(newurl).hostname != API_HOST:
            raise WebError("GitHub redirected to another site; that is not followed.")
        return super().redirect_request(req, fp, code, msg, headers, newurl)


_OPENER = urllib.request.build_opener(_SameHost())


# ------------------------------------------------------------------ HTTP
def _note_rate(h):
    try:
        if h is not None and h.get("X-RateLimit-Remaining") is not None:
            RATE.update(limit=int(h.get("X-RateLimit-Limit", 0)), remaining=int(h["X-RateLimit-Remaining"]), reset=int(h.get("X-RateLimit-Reset", 0)))
    except (TypeError, ValueError):
        pass


def rate_message():
    when = time.strftime("%H:%M", time.localtime(RATE["reset"])) if RATE["reset"] else "later"
    hint = "" if TOKEN else " Set the GITHUB_TOKEN variable (or --github-token) to raise the limit from 60 to 5,000 requests per hour."
    return f"GitHub rate limit reached; it resets around {when}." + hint


def _get(path, params=None, raw=False, timeout=12, max_bytes=2_500_000):
    url = API + path + ("?" + urllib.parse.urlencode(params) if params else "")
    headers = {"User-Agent": UA, "X-GitHub-Api-Version": "2022-11-28",
               "Accept": "application/vnd.github.raw+json" if raw else "application/vnd.github+json"}
    if TOKEN:
        headers["Authorization"] = "Bearer " + TOKEN
    try:
        with _OPENER.open(urllib.request.Request(url, headers=headers), timeout=timeout) as r:
            _note_rate(r.headers)
            body = r.read(max_bytes + 1)
    except urllib.error.HTTPError as e:
        _note_rate(e.headers)
        if e.code in (403, 429):
            blob = b""
            try:
                blob = e.read(2000) or b""
            except Exception:
                pass
            if e.code == 429 or RATE["remaining"] == 0 or b"rate limit" in blob.lower():
                raise WebError(rate_message())
            raise WebError("GitHub refused the request (403).")
        if e.code == 401:
            raise WebError("GitHub rejected the access token. Check GITHUB_TOKEN.")
        if e.code in (404, 410):
            raise NotFound("Not found on GitHub (private repositories need a token that can see them).")
        raise WebError(f"GitHub answered with an error ({e.code}).")
    except urllib.error.URLError as e:
        raise WebError(f"Cannot reach GitHub. Check your internet connection. ({e.reason})")
    except (TimeoutError, socket.timeout):
        raise WebError("GitHub took too long to answer.")
    except OSError as e:
        raise WebError(f"Cannot reach GitHub: {e}")
    if len(body) > max_bytes:
        raise WebError("That GitHub response is too large to read here.")
    text = body.decode("utf-8", "replace")
    if raw:
        return text
    try:
        return json.loads(text)
    except ValueError:
        raise WebError("GitHub did not return valid data.")


def ping(timeout=8):
    """Free check (the rate-limit endpoint does not count). Returns (ok, why)."""
    try:
        j = _get("/rate_limit", timeout=timeout)
        c = (j.get("resources") or {}).get("core") or j.get("rate") or {}
        if c:
            RATE.update(limit=c.get("limit"), remaining=c.get("remaining"), reset=c.get("reset", 0))
        return True, ""
    except WebError as e:
        return False, str(e)


# ------------------------------------------------------------------ text helpers
def _date(s):
    return (s or "")[:10]


def _clip(text, n):
    text = (text or "").strip()
    if len(text) <= n:
        return text
    cut = text[:n]
    k = max(cut.rfind("\n"), cut.rfind(". "))
    return (cut[:k + 1] if k > n * 0.6 else cut).rstrip() + "\n…"


def _clean_readme(md, n):
    md = re.sub(r"<!--.*?-->", "", md or "", flags=re.S)
    md = re.sub(r"\[!\[[^\]]*\]\([^)]*\)\]\([^)]*\)", "", md)           # badge links
    md = re.sub(r"!\[[^\]]*\]\([^)]*\)", "", md)                          # images
    md = re.sub(r"<img[^>]*>", "", md, flags=re.I)
    md = re.sub(r"</?(?:p|div|a|br|picture|source|center|details|summary|sup|sub|span|table|tr|td|th|thead|tbody)\b[^>]*>", "", md, flags=re.I)
    lines, blank = [], 0
    for ln in md.replace("\r", "").split("\n"):
        ln = ln.rstrip()
        blank = blank + 1 if not ln.strip() else 0
        if blank <= 1:
            lines.append(ln)
    return _clip("\n".join(lines), n)


def _repo_text(j):
    lic = j.get("license") or {}
    lines = [f"Repository: {j.get('full_name')}"]
    if j.get("description"):
        lines.append("Description: " + " ".join(str(j["description"]).split()))
    lines.append(" | ".join([
        f"Language: {j.get('language') or 'n/a'}", f"Stars: {j.get('stargazers_count', 0):,}", f"Forks: {j.get('forks_count', 0):,}",
        f"Open issues and PRs: {j.get('open_issues_count', 0):,}", f"License: {lic.get('spdx_id') or lic.get('name') or 'none listed'}",
        f"Default branch: {j.get('default_branch') or 'main'}"]))
    if j.get("topics"):
        lines.append("Topics: " + ", ".join(j["topics"][:12]))
    dates = [f"Created: {_date(j.get('created_at'))}", f"Last push: {_date(j.get('pushed_at'))}"]
    if j.get("homepage"):
        dates.append("Homepage: " + str(j["homepage"]))
    lines.append(" | ".join(dates))
    flags = []
    if j.get("archived"):
        flags.append("archived (read-only)")
    if j.get("fork") and (j.get("parent") or {}).get("full_name"):
        flags.append("fork of " + j["parent"]["full_name"])
    if j.get("private"):
        flags.append("private")
    if flags:
        lines.append("Note: " + ", ".join(flags))
    return "\n".join(lines)


def _item(title, url, snippet="", text="", **extra):
    r = {"title": title, "url": url, "domain": "github.com", "engines": ["github"], "published": "", "snippet": snippet[:600]}
    if text:
        r["text"] = text
    r.update(extra)
    return r


def _opt(fut):
    try:
        return fut.result()
    except WebError:
        return None


def _check(owner, repo=None):
    if not OWNER_RE.match(owner or "") or (repo is not None and not REPO_RE.match(repo or "")):
        raise WebError("That does not look like a valid GitHub name.")


# ------------------------------------------------------------------ resources
def repo_overview(owner, repo, max_chars=6000):
    _check(owner, repo)
    base = f"/repos/{owner}/{repo}"
    with ThreadPoolExecutor(max_workers=4) as ex:
        f_repo, f_readme = ex.submit(_get, base), ex.submit(_get, base + "/readme", None, True)
        f_tree, f_rel = ex.submit(_get, base + "/contents"), ex.submit(_get, base + "/releases/latest")
        j = f_repo.result()
        readme, tree, rel = _opt(f_readme), _opt(f_tree), _opt(f_rel)
    head = [_repo_text(j)]
    if isinstance(rel, dict) and rel.get("tag_name"):
        head.append(f"Latest release: {rel['tag_name']} ({_date(rel.get('published_at'))})")
    if isinstance(tree, list) and tree:
        names = [e["name"] + ("/" if e.get("type") == "dir" else "") for e in sorted(tree, key=lambda e: (e.get("type") != "dir", e.get("name", "").lower()))]
        head.append("Top-level files: " + ", ".join(names[:40]) + (" …" if len(names) > 40 else ""))
    meta = "\n".join(head)
    text = meta + (("\n\nREADME:\n" + _clean_readme(readme, max(800, max_chars - len(meta) - 20))) if readme else "\n\n(This repository has no README.)")
    return _item(j.get("full_name") or f"{owner}/{repo}", j.get("html_url") or f"https://github.com/{owner}/{repo}",
                 (j.get("description") or "") + f" (★{j.get('stargazers_count', 0):,})", text)


def contents(owner, repo, segs, kind, max_chars=6000):
    """A file or a folder. `segs` is the URL path after blob/tree: <ref>/<path...>; branch names with slashes are tried too."""
    _check(owner, repo)
    if any(s in ("", ".", "..") for s in segs):
        raise WebError("That path is not valid.")
    last = None
    for k in range(1, min(3, len(segs)) + 1):
        ref, path = "/".join(segs[:k]), "/".join(segs[k:])
        if kind == "blob" and not path:
            continue
        try:
            j = _get(f"/repos/{owner}/{repo}/contents" + ("/" + urllib.parse.quote(path, safe="/") if path else ""), {"ref": ref})
        except NotFound as e:
            last = e
            continue
        return _render_contents(owner, repo, ref, path, j, max_chars)
    raise last or NotFound("Not found on GitHub.")


def _render_contents(owner, repo, ref, path, j, max_chars):
    if isinstance(j, list):
        ents = sorted(j, key=lambda e: (e.get("type") != "dir", e.get("name", "").lower()))
        lines = [(e["name"] + ("/" if e.get("type") == "dir" else "")) for e in ents[:80]]
        text = f"Folder /{path} @ {ref} in {owner}/{repo}:\n" + "\n".join(lines) + ("\n…" if len(ents) > 80 else "")
        return _item(f"{owner}/{repo}/{path}".rstrip("/") + " (folder)", f"https://github.com/{owner}/{repo}/tree/{ref}/{path}".rstrip("/"),
                     f"{len(ents)} entries", text)
    if not isinstance(j, dict) or j.get("type") != "file":
        raise WebError("GitHub returned something that is not a file or folder.")
    if j.get("encoding") != "base64" or not j.get("content"):
        raise WebError("This file is larger than 1 MB (or empty), so it is not read here.")
    raw = base64.b64decode(j["content"])
    if b"\0" in raw[:8000]:
        raise WebError("That is a binary file, not text.")
    body = raw.decode("utf-8", "replace")
    text = f"File: {path} @ {ref} in {owner}/{repo} ({len(raw):,} bytes)\n\n" + _clip(body, max_chars)
    return _item(f"{owner}/{repo}/{path}", j.get("html_url") or f"https://github.com/{owner}/{repo}/blob/{ref}/{path}",
                 f"{len(raw):,} bytes", text)


def issue(owner, repo, num, max_chars=6000):
    _check(owner, repo)
    base = f"/repos/{owner}/{repo}"
    j = _get(f"{base}/issues/{int(num)}")
    is_pr = "pull_request" in j
    with ThreadPoolExecutor(max_workers=2) as ex:
        f_com = ex.submit(_get, f"{base}/issues/{int(num)}/comments", {"per_page": 8})
        f_pr = ex.submit(_get, f"{base}/pulls/{int(num)}") if is_pr else None
        coms, pr = _opt(f_com), (_opt(f_pr) if f_pr else None)
    state = j.get("state", "")
    if isinstance(pr, dict) and pr.get("merged"):
        state += " (merged)"
    labels = ", ".join(l.get("name", "") for l in j.get("labels") or [] if isinstance(l, dict))
    head = [f"{'Pull request' if is_pr else 'Issue'} #{j.get('number')} in {owner}/{repo}: {j.get('title')}",
            f"State: {state} | Author: {(j.get('user') or {}).get('login', '?')} | Created: {_date(j.get('created_at'))} | "
            f"Updated: {_date(j.get('updated_at'))} | Comments: {j.get('comments', 0)}" + (f" | Labels: {labels}" if labels else "")]
    if isinstance(pr, dict) and pr.get("base"):
        head.append(f"Branches: {pr['head']['label']} into {pr['base']['label']} | +{pr.get('additions', 0)} −{pr.get('deletions', 0)} in {pr.get('changed_files', 0)} file(s)")
    parts = ["\n".join(head), "Description:\n" + (_clip(j.get("body") or "(no description)", int(max_chars * 0.5)))]
    if isinstance(coms, list) and coms:
        parts.append("Comments:\n" + "\n".join(f"- {(c.get('user') or {}).get('login', '?')} ({_date(c.get('created_at'))}): {_clip(' '.join((c.get('body') or '').split()), 500)}" for c in coms[:8]))
    return _item(f"{owner}/{repo}#{j.get('number')} {j.get('title')}", j.get("html_url") or f"https://github.com/{owner}/{repo}/issues/{num}",
                 f"{state} · {j.get('comments', 0)} comments", _clip("\n\n".join(parts), max_chars))


def releases(owner, repo, tag=None, listing=False, max_chars=6000):
    _check(owner, repo)
    base = f"/repos/{owner}/{repo}/releases"
    items = _get(base, {"per_page": 5}) if listing else [_get(base + (f"/tags/{urllib.parse.quote(tag, safe='')}" if tag else "/latest"))]
    if not items:
        raise NotFound("This repository has no releases.")
    blocks = [f"Release {r.get('tag_name')} — {r.get('name') or ''} ({_date(r.get('published_at'))})" + (" [pre-release]" if r.get("prerelease") else "")
              + "\n" + _clip(r.get("body") or "(no notes)", 1500) for r in items]
    first = items[0]
    return _item(f"{owner}/{repo} releases", first.get("html_url") or f"https://github.com/{owner}/{repo}/releases",
                 f"latest: {first.get('tag_name')}", _clip("\n\n".join(blocks), max_chars))


def user(login, max_chars=6000):
    _check(login)
    with ThreadPoolExecutor(max_workers=2) as ex:
        f_u, f_r = ex.submit(_get, f"/users/{login}"), ex.submit(_get, f"/users/{login}/repos", {"sort": "pushed", "per_page": 8})
        j = f_u.result()
        repos = _opt(f_r)
    lines = [f"{j.get('type', 'User')}: {j.get('login')}" + (f" ({j['name']})" if j.get("name") else "")]
    if j.get("bio"):
        lines.append("Bio: " + " ".join(str(j["bio"]).split()))
    lines.append(f"Public repos: {j.get('public_repos', 0)} | Followers: {j.get('followers', 0):,} | Joined: {_date(j.get('created_at'))}"
                 + (f" | Website: {j['blog']}" if j.get("blog") else ""))
    if isinstance(repos, list) and repos:
        lines.append("Recently active repositories:")
        lines += [f"- {r['name']}: {' '.join((r.get('description') or '').split())[:120]} (★{r.get('stargazers_count', 0):,}, {r.get('language') or 'n/a'})" for r in repos[:8]]
    return _item(j.get("login") or login, j.get("html_url") or f"https://github.com/{login}", (j.get("bio") or j.get("type") or "")[:200], _clip("\n".join(lines), max_chars))


def _keywords(q):
    words = re.findall(r"[\w.+#-]+", q or "", re.UNICODE)
    return [w for w in words if w.lower() not in STOP][:8]


def search_repos(query, n=5, read=1, max_chars=6000):
    kw = _keywords(query)
    if not kw:
        raise WebError("Say what kind of repository to look for.")
    params = {"q": " ".join(kw), "per_page": max(1, min(int(n), 10))}
    if RANK_WORDS & {w.lower() for w in re.findall(r"\w+", query or "")}:
        params.update(sort="stars", order="desc")
    j = _get("/search/repositories", params)
    items = [i for i in (j.get("items") or []) if isinstance(i, dict) and i.get("full_name")]
    out = []
    for i in items:
        bits = [f"★{i.get('stargazers_count', 0):,}", i.get("language") or "", "updated " + _date(i.get("pushed_at"))]
        if i.get("archived"):
            bits.append("archived")
        out.append(_item(i["full_name"], i.get("html_url") or "https://github.com/" + i["full_name"],
                         " ".join((i.get("description") or "").split())[:300] + " — " + " · ".join(b for b in bits if b), _repo_text(i)))
    top = [r for r, i in zip(out, items) if not i.get("archived")][:max(0, min(int(read), 3))] or []
    def readme(r):
        try:
            return _get(f"/repos/{r['title']}/readme", None, True)
        except WebError:
            return ""
    if top:
        with ThreadPoolExecutor(max_workers=min(3, len(top))) as ex:
            for r, md in zip(top, ex.map(readme, top)):
                if md:
                    r["text"] = r["text"] + "\n\nREADME:\n" + _clean_readme(md, max(800, max_chars - len(r["text"]) - 20))
    return out


# ------------------------------------------------------------------ targets: what did the user point at?
def parse_target(t):
    """Returns (kind, args...) or None. Understands github.com links, raw.githubusercontent.com links and 'owner/repo'."""
    t = (t or "").strip().strip("<>\"'`")
    if re.match(r"^https?://", t, re.I):
        u = urllib.parse.urlsplit(t)
        host = (u.hostname or "").lower()
        segs = [urllib.parse.unquote(s) for s in u.path.split("/") if s]
        if host == "raw.githubusercontent.com" and len(segs) >= 4:
            return ("blob", segs[0], segs[1], segs[2:])
        if host not in ("github.com", "www.github.com") or not segs:
            return None
        if len(segs) == 1:
            return None if segs[0].lower() in RESERVED else ("user", segs[0])
        if segs[0].lower() in RESERVED:
            return None
        owner, repo = segs[0], re.sub(r"\.git$", "", segs[1])
        rest = segs[2:]
        if rest[:1] in (["blob"], ["tree"]) and len(rest) >= 2:
            return (rest[0], owner, repo, rest[1:])
        if rest[:1] in (["issues"], ["pull"]) and len(rest) >= 2 and rest[1].isdigit():
            return ("issue", owner, repo, int(rest[1]))
        if rest[:1] == ["releases"]:
            if len(rest) >= 3 and rest[1] == "tag":
                return ("release", owner, repo, "/".join(rest[2:]))
            return ("release", owner, repo, "latest" if rest[1:2] == ["latest"] else None)
        return ("repo", owner, repo)
    m = re.match(r"^([A-Za-z0-9][A-Za-z0-9-]{0,38})/([A-Za-z0-9._-]{1,100})$", t)
    if m and OWNER_RE.match(m.group(1)):
        return ("repo", m.group(1), re.sub(r"\.git$", "", m.group(2)))
    return None


def _run(target, max_chars):
    p = parse_target(target)
    if not p:
        raise WebError("I cannot read that GitHub address. Give a repository, file, issue, pull request, release or user link.")
    k = p[0]
    if k == "repo":
        return repo_overview(p[1], p[2], max_chars)
    if k in ("blob", "tree"):
        return contents(p[1], p[2], p[3], k, max_chars)
    if k == "issue":
        return issue(p[1], p[2], p[3], max_chars)
    if k == "release":
        return releases(p[1], p[2], tag=None if p[3] in (None, "latest") else p[3], listing=p[3] is None, max_chars=max_chars)
    return user(p[1], max_chars)


def lookup(targets=None, query="", n=5, read=1, max_chars=6000):
    """Read the given targets (links or owner/repo) and/or search repositories for `query`.
    Returns {results, read, answer, rate}. A target that fails becomes a result with read_error; if everything fails, WebError is raised."""
    targets = [t for t in (targets or []) if isinstance(t, str) and t.strip()][:3]
    query = " ".join((query or "").split())[:300]
    if not targets and not query:
        raise WebError("There is nothing to look up on GitHub.")
    key = (tuple(targets), query.lower(), int(n), int(read), int(max_chars), bool(TOKEN))
    hit = _CACHE.get(key)
    if hit and time.time() - hit[0] < _CACHE_TTL:
        return json.loads(json.dumps(hit[1]))
    results, errors = [], []
    for t in targets:
        try:
            results.append(_run(t, max_chars))
        except WebError as e:
            errors.append(str(e))
            url = t if t.startswith("http") else "https://github.com/" + t
            results.append(_item(t, url, "", "", read_error=str(e)))
    if query and not targets:
        try:
            results += search_repos(query, n, read, max_chars)
        except WebError as e:
            errors.append(str(e))
    if errors and not any(r.get("text") or r.get("snippet") for r in results):
        raise WebError(errors[0])
    out = {"query": query, "results": results, "read": sum(1 for r in results if r.get("text")), "answer": "",
           "rate": {"remaining": RATE["remaining"], "limit": RATE["limit"]}}
    if len(_CACHE) >= _CACHE_MAX:
        _CACHE.pop(min(_CACHE, key=lambda k: _CACHE[k][0]), None)
    _CACHE[key] = (time.time(), json.loads(json.dumps(out)))
    return out
