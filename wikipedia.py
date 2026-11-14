"""Wikipedia lookups for CressyAI through the public MediaWiki API. Standard library only.

One request finds the best matching articles and returns their intro text; the top few can then be read in full.
The host is always <lang>.wikipedia.org (the language code is validated), so nothing else can be reached from here.
"""
import json, re, socket, time, urllib.error, urllib.parse, urllib.request
from concurrent.futures import ThreadPoolExecutor

from websearch import WebError

UA = "CressyAI/1.0 (local assistant; https://github.com/; contact: local user) python-urllib"
LANG_RE = re.compile(r"^[a-z]{2,3}(?:-[a-z]{2,8})?$")
# sections that add tokens but no knowledge
TAIL = {"see also", "references", "external links", "further reading", "notes", "bibliography", "sources",
        "footnotes", "citations", "explanatory notes", "gallery", "works cited"}
STOP = set("""a an the is are was were be been am do does did of in on at to for from by with about and or as that this these those it
its who whom whose what which when where why how tell me please can could would will you your yours i my explain define
meaning mean history overview facts fact give some know""".split())

_CACHE, _CACHE_MAX, _CACHE_TTL = {}, 64, 600


def lang_ok(lang):
    lang = (lang or "en").strip().lower()
    return lang if LANG_RE.match(lang) else "en"


def _api(lang, params, timeout=10):
    url = f"https://{lang}.wikipedia.org/w/api.php?" + urllib.parse.urlencode({**params, "format": "json", "formatversion": "2"})
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "application/json", "Accept-Encoding": "identity"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            data = json.loads(r.read(2_500_000).decode("utf-8", "replace"))
    except urllib.error.HTTPError as e:
        if e.code == 429:
            raise WebError("Wikipedia says too many requests (429). Wait a moment and try again.")
        raise WebError(f"Wikipedia answered with an error ({e.code}).")
    except urllib.error.URLError as e:
        raise WebError(f"Cannot reach Wikipedia. Check your internet connection. ({e.reason})")
    except (TimeoutError, socket.timeout):
        raise WebError("Wikipedia took too long to answer.")
    except (ValueError, UnicodeError):
        raise WebError("Wikipedia did not return valid data.")
    except OSError as e:
        raise WebError(f"Cannot reach Wikipedia: {e}")
    if isinstance(data, dict) and data.get("error"):
        raise WebError("Wikipedia error: " + str(data["error"].get("info", "unknown"))[:200])
    return data


def _is_disamb(p):
    return "disambiguation" in (p.get("pageprops") or {})


def _keywords(q):
    words = [w for w in re.findall(r"[\w'-]+", q, re.UNICODE) if w.lower() not in STOP]
    return " ".join(words[:8])


def _clean_extract(text, max_chars):
    """Turn MediaWiki's plain-text extract into compact markdown and drop reference-style tail sections."""
    out = []
    for ln in (text or "").replace("\r", "").split("\n"):
        m = re.match(r"^(={2,6})\s*(.+?)\s*\1\s*$", ln)
        if m:
            if m.group(2).strip().lower() in TAIL:
                break
            out.append("#" * min(len(m.group(1)), 6) + " " + m.group(2))
        elif ln.strip():
            out.append(ln.strip())
    body = "\n".join(out)
    if len(body) > max_chars:
        body = body[:max_chars]
        k = max(body.rfind("\n"), body.rfind(". "))
        if k > max_chars * 0.6:
            body = body[:k + 1]
    return body.strip()


def _search(lang, query, n):
    return _api(lang, {
        "action": "query", "generator": "search", "gsrsearch": query, "gsrlimit": n, "gsrnamespace": 0,
        "prop": "extracts|info|pageprops", "exintro": 1, "explaintext": 1, "exlimit": "max", "exchars": 800,
        "inprop": "url", "ppprop": "disambiguation", "redirects": 1,
    }).get("query", {}).get("pages") or []


def _full(lang, title, max_chars):
    """Read one article's text (no intro-only limit). Returns '' when the page cannot be read."""
    try:
        pages = _api(lang, {"action": "query", "prop": "extracts", "explaintext": 1, "exchars": max(2000, int(max_chars * 1.6)),
                            "titles": title, "redirects": 1}).get("query", {}).get("pages") or []
    except WebError:
        return ""
    for p in pages:
        if p.get("extract"):
            return _clean_extract(p["extract"], max_chars)
    return ""


def lookup(query, n=3, read=1, lang="en", max_chars=6000):
    """Search Wikipedia. Returns {query, lang, results, read}. Each result has title, url, domain, snippet and, for the
    top `read` articles, the article text. Raises WebError with a message that can be shown as is."""
    lang = lang_ok(lang)
    query = " ".join((query or "").split())[:300]
    if not query:
        raise WebError("There is nothing to look up.")
    n, read = max(1, min(int(n), 6)), max(0, min(int(read), 3))
    key = (lang, query.lower(), n, read, max_chars)
    hit = _CACHE.get(key)
    if hit and time.time() - hit[0] < _CACHE_TTL:
        return json.loads(json.dumps(hit[1]))

    pages = _search(lang, query, n)
    if not pages:   # natural questions sometimes match nothing: retry with just the keywords
        kw = _keywords(query)
        if kw and kw.lower() != query.lower():
            pages = _search(lang, kw, n)
    pages = sorted(pages, key=lambda p: p.get("index", 99))
    pages.sort(key=lambda p: _is_disamb(p))   # disambiguation pages go last

    results = []
    for p in pages:
        title = p.get("title") or ""
        if not title:
            continue
        url = p.get("fullurl") or f"https://{lang}.wikipedia.org/wiki/" + urllib.parse.quote(title.replace(" ", "_"))
        results.append({
            "title": title, "url": url, "domain": f"{lang}.wikipedia.org", "engines": ["wikipedia"], "published": "",
            "snippet": " ".join((p.get("extract") or "").split())[:600],
            "disambiguation": _is_disamb(p),
        })
    top = [r for r in results if not r["disambiguation"]][:read]
    if top:
        with ThreadPoolExecutor(max_workers=min(3, len(top))) as ex:
            for r, text in zip(top, ex.map(lambda r: _full(lang, r["title"], max_chars), top)):
                if text:
                    r["text"] = text
                else:
                    r["text"] = r["snippet"]   # at least the intro
    out = {"query": query, "lang": lang, "results": results, "read": sum(1 for r in results if r.get("text")), "answer": ""}
    if len(_CACHE) >= _CACHE_MAX:
        _CACHE.pop(min(_CACHE, key=lambda k: _CACHE[k][0]), None)
    _CACHE[key] = (time.time(), json.loads(json.dumps(out)))
    return out


def ping(lang="en", timeout=6):
    """Cheap reachability check. Returns (ok, why)."""
    try:
        _api(lang_ok(lang), {"action": "query", "meta": "siteinfo", "siprop": "general"}, timeout=timeout)
        return True, ""
    except WebError as e:
        return False, str(e)
