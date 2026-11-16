"""Web access for CressyAI: a SearXNG client plus a safe page reader. Standard library only.

SearXNG is a self-hosted meta search engine. CressyAI asks it for results as JSON, optionally opens the top pages,
and hands the text to the model as numbered sources. The model itself never touches the network.
"""
import ipaddress, json, re, socket, urllib.error, urllib.parse, urllib.request
from concurrent.futures import ThreadPoolExecutor
from html.parser import HTMLParser

UA = "Mozilla/5.0 (compatible; CressyAI/1.0; local assistant)"
FORMS = {"day", "week", "month", "year"}
CATS = {"general", "news", "it", "science", "files", "images", "videos", "music", "social media", "map"}


class WebError(Exception):
    """A problem that can be shown to the user as is."""


def norm_base(url):
    url = (url or "").strip().rstrip("/")
    if url and "://" not in url:
        url = "http://" + url
    return url


def domain(url):
    host = urllib.parse.urlsplit(url).hostname or ""
    return host[4:] if host.startswith("www.") else host


def iri_to_uri(url):
    """Percent-encode non-ASCII characters so urllib can send the request."""
    u = urllib.parse.urlsplit(url)
    host = u.hostname or ""
    try:
        host = host.encode("idna").decode("ascii")
    except UnicodeError:
        pass
    netloc = host + (":" + str(u.port) if u.port else "")
    q = lambda s, safe: urllib.parse.quote(s, safe=safe)
    return urllib.parse.urlunsplit((u.scheme, netloc, q(u.path, "/%:@!$&'()*+,;=~-._"), q(u.query, "=&%:@!$'()*+,;/?~-._"), ""))


# ------------------------------------------------------------------ SearXNG
def _get_json(url, timeout):
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read(3_000_000).decode("utf-8", "replace"))


def searx_search(base, query, n=6, time_range="", categories="general", language="", timeout=15):
    """Ask SearXNG for results. Returns {query, results, answer, suggestions}. Raises WebError with a fix-it message."""
    base = norm_base(base)
    query = " ".join((query or "").split())
    if not query:
        raise WebError("There is nothing to search for.")
    params = {"q": query, "format": "json", "safesearch": "0"}
    if time_range in FORMS:
        params["time_range"] = time_range
    cats = ",".join(c for c in (categories or "").split(",") if c.strip() in CATS)
    if cats:
        params["categories"] = cats
    if language:
        params["language"] = language
    try:
        data = _get_json(base + "/search?" + urllib.parse.urlencode(params), timeout)
    except urllib.error.HTTPError as e:
        if e.code == 403:
            raise WebError(f"SearXNG at {base} refused the request (403). In its settings.yml, list json under "
                           "search: formats: (see searxng/settings.yml in this project), then restart it.")
        if e.code == 429:
            raise WebError("SearXNG says too many requests (429). Set server: limiter: false in its settings.yml for local use.")
        raise WebError(f"SearXNG answered with an error ({e.code}).")
    except urllib.error.URLError as e:
        raise WebError(f"Cannot reach SearXNG at {base}. Start it (see README: Web search) or pass --searxng-url. ({e.reason})")
    except (TimeoutError, socket.timeout):
        raise WebError(f"SearXNG at {base} took too long to answer.")
    except (ValueError, UnicodeError):
        raise WebError(f"SearXNG at {base} did not return JSON. Enable the json format in its settings.yml.")
    except OSError as e:
        raise WebError(f"Cannot reach SearXNG at {base}: {e}")

    seen, results = set(), []
    for r in data.get("results") or []:
        url = (r.get("url") or "").strip()
        if not url.startswith(("http://", "https://")):
            continue
        key = re.sub(r"^https?://(www\.)?", "", url).rstrip("/").lower()
        if key in seen:
            continue
        seen.add(key)
        engines = r.get("engines") or ([r["engine"]] if r.get("engine") else [])
        results.append({
            "title": " ".join((r.get("title") or url).split())[:200],
            "url": url,
            "domain": domain(url),
            "snippet": " ".join((r.get("content") or "").split())[:600],
            "engines": engines[:4],
            "published": (r.get("publishedDate") or "")[:10],
        })
        if len(results) >= max(1, min(int(n), 12)):
            break

    answer = ""
    for a in data.get("answers") or []:
        a = a.get("answer") if isinstance(a, dict) else a
        if a:
            answer = " ".join(str(a).split())[:500]
            break
    if not answer:
        for ib in data.get("infoboxes") or []:
            if ib.get("content"):
                answer = " ".join(str(ib["content"]).split())[:500]
                break
    sugg = [s for s in (data.get("suggestions") or []) if isinstance(s, str)][:5]
    return {"query": query, "results": results, "answer": answer, "suggestions": sugg}


def searx_ping(base, timeout=6):
    """Cheap check used by the status endpoint. Returns (ok, why)."""
    try:
        searx_search(base, "cressy", n=1, timeout=timeout)
        return True, ""
    except WebError as e:
        return False, str(e)


# ------------------------------------------------------------------ page reader
def check_url(url, allow_private=False):
    u = urllib.parse.urlsplit(url)
    if u.scheme not in ("http", "https"):
        raise WebError("Only http and https links can be read.")
    if not u.hostname:
        raise WebError("That link has no address in it.")
    if u.username or u.password:
        raise WebError("Links with a login inside them are not read.")
    if allow_private:
        return
    try:
        infos = socket.getaddrinfo(u.hostname, u.port or (443 if u.scheme == "https" else 80), type=socket.SOCK_STREAM)
    except socket.gaierror:
        raise WebError("Could not find that site.")
    for info in infos:
        ip = ipaddress.ip_address(info[4][0].split("%")[0])
        if ip.version == 6 and ip.ipv4_mapped:
            ip = ip.ipv4_mapped
        if not ip.is_global:
            raise WebError("Links to private or local addresses are blocked (start with --web-allow-private to allow them).")


class _SafeRedirect(urllib.request.HTTPRedirectHandler):
    max_redirections = 4

    def __init__(self, allow_private):
        self.allow_private = allow_private

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        check_url(newurl, self.allow_private)
        return super().redirect_request(req, fp, code, msg, headers, newurl)


SKIP = {"script", "style", "noscript", "svg", "template", "iframe", "nav", "footer", "aside", "form", "button", "select",
        "dialog", "canvas", "video", "audio", "object", "head"}
VOID = {"br", "hr", "img", "input", "meta", "link", "area", "base", "col", "embed", "source", "track", "wbr"}
BLOCK = {"p", "div", "section", "article", "main", "ul", "ol", "tr", "table", "br", "h1", "h2", "h3", "h4", "h5", "h6",
         "pre", "blockquote", "figure", "figcaption", "dd", "dt", "dl", "hr", "details", "summary", "header", "li"}


class _Text(HTMLParser):
    def __init__(self, skip_noise=True):
        super().__init__(convert_charrefs=True)
        self.skip_noise, self.skip, self.in_title = skip_noise, 0, False
        self.title, self.desc, self.all, self.main, self.depth_main = "", "", [], [], 0

    def _put(self, s):
        self.all.append(s)
        if self.depth_main:
            self.main.append(s)

    def handle_starttag(self, tag, attrs):
        if tag == "title":
            self.in_title = True
        if tag == "meta":
            a = dict(attrs)
            if (a.get("name") or a.get("property") or "").lower() in ("description", "og:description") and not self.desc:
                self.desc = " ".join((a.get("content") or "").split())
        if self.skip_noise and tag in SKIP and tag not in VOID:
            self.skip += 1
            return
        if self.skip:
            return
        if tag in ("article", "main"):
            self.depth_main += 1
        if tag in BLOCK:
            self._put("\n")
        if tag in ("h1", "h2", "h3", "h4", "h5", "h6"):
            self._put("#" * int(tag[1]) + " ")
        elif tag == "li":
            self._put("- ")

    def handle_endtag(self, tag):
        if tag == "title":
            self.in_title = False
        if self.skip_noise and tag in SKIP:
            if self.skip:
                self.skip -= 1
            return
        if self.skip:
            return
        if tag in ("article", "main") and self.depth_main:
            self.depth_main -= 1
        if tag in ("td", "th"):
            self._put(" | ")
        if tag in BLOCK:
            self._put("\n")

    def handle_data(self, data):
        if self.in_title:
            self.title += data
        elif not self.skip:
            self._put(data)


def _tidy(parts):
    text = "".join(parts).replace("\u00a0", " ")
    lines, prev = [], None
    for ln in text.split("\n"):
        ln = re.sub(r"[ \t]+", " ", ln).strip(" |")
        if len(ln) < 2 or ln == prev:
            continue
        lines.append(ln)
        prev = ln
    return "\n".join(lines)


def html_to_text(html):
    """Return (title, text) with the page's readable content. Falls back to a looser pass if the page is unusual."""
    out = None
    for strict in (True, False):
        p = _Text(skip_noise=strict)
        try:
            p.feed(html)
            p.close()
        except Exception:
            pass
        main = _tidy(p.main)
        body = main if len(main) >= 600 else _tidy(p.all)
        out = (" ".join(p.title.split()), body or p.desc)
        if len(body) >= 200:
            break
    return out


def fetch_page(url, max_bytes=700_000, max_chars=6000, timeout=10, allow_private=False):
    """Download one page and return {url, title, text, truncated}. Raises WebError."""
    check_url(url, allow_private)
    opener = urllib.request.build_opener(_SafeRedirect(allow_private))
    req = urllib.request.Request(iri_to_uri(url), headers={"User-Agent": UA, "Accept": "text/html,application/xhtml+xml,text/plain;q=0.9,*/*;q=0.1",
                                                            "Accept-Language": "en;q=0.9,*;q=0.5"})
    try:
        with opener.open(req, timeout=timeout) as r:
            ctype = r.headers.get_content_type()
            if ctype not in ("text/html", "application/xhtml+xml", "text/plain", "text/markdown", "application/json", "text/xml", "application/xml"):
                raise WebError(f"Skipped a {ctype} file (only web pages and text are read).")
            raw = r.read(max_bytes)
            final = r.geturl()
            charset = r.headers.get_content_charset()
    except WebError:
        raise
    except urllib.error.HTTPError as e:
        raise WebError(f"The site answered {e.code}.")
    except urllib.error.URLError as e:
        raise WebError(f"Could not open the page ({e.reason}).")
    except (TimeoutError, socket.timeout):
        raise WebError("The page took too long to answer.")
    except OSError as e:
        raise WebError(f"Could not open the page ({e}).")
    if not charset:
        m = re.search(rb"<meta[^>]+charset=[\"']?\s*([\w-]+)", raw[:4096], re.I)
        charset = m.group(1).decode("ascii", "ignore") if m else "utf-8"
    try:
        text = raw.decode(charset, "replace")
    except LookupError:
        text = raw.decode("utf-8", "replace")
    title = ""
    if ctype in ("text/html", "application/xhtml+xml"):
        title, text = html_to_text(text)
    else:
        text = _tidy([text])
    if not text.strip():
        raise WebError("The page has no readable text (it may need JavaScript).")
    cut = len(text) > max_chars
    if cut:
        text = text[:max_chars]
        k = max(text.rfind("\n"), text.rfind(". "))
        if k > max_chars * 0.6:
            text = text[:k + 1]
    return {"url": final, "title": title, "text": text.strip(), "truncated": cut}


def read_pages(urls, max_chars=6000, timeout=10, allow_private=False):
    """Fetch several pages at once. Returns a list of {url, ok, title, text | error} in the same order."""
    def one(u):
        try:
            return {"ok": True, **fetch_page(u, max_chars=max_chars, timeout=timeout, allow_private=allow_private)}
        except WebError as e:
            return {"ok": False, "url": u, "error": str(e)}
        except Exception as e:   # never let one bad page break the batch
            return {"ok": False, "url": u, "error": f"Could not read the page ({e.__class__.__name__})."}
    urls = list(urls)[:6]
    if not urls:
        return []
    with ThreadPoolExecutor(max_workers=min(4, len(urls))) as ex:
        return list(ex.map(one, urls))


def research(base, query, n=6, read=2, time_range="", categories="general", language="", max_chars=6000, allow_private=False):
    """Search, then open the top `read` results. Each opened result gets `text`; failures get `read_error`."""
    out = searx_search(base, query, n=n, time_range=time_range, categories=categories, language=language)
    top = out["results"][:max(0, min(int(read), 5))]
    for res, page in zip(top, read_pages([r["url"] for r in top], max_chars=max_chars, allow_private=allow_private)):
        if page["ok"]:
            res["text"] = page["text"]
            if page.get("title") and len(page["title"]) > 3:
                res["page_title"] = page["title"][:200]
        else:
            res["read_error"] = page["error"]
    out["read"] = sum(1 for r in top if r.get("text"))
    return out
