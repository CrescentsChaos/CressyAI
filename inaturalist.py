"""iNaturalist lookups for CressyAI through the public API v1 (https://api.inaturalist.org/v1). Standard library only.

Features: species/taxon search, full taxon pages (summary, ancestry, children, conservation status, photos), places search,
what lives near a point or inside a place (species counts), recent observations, seasonality (month-of-year histogram),
similar species, observation detail, top observers, and photo identification (needs a JWT, see --inat-token).
Every function returns plain dicts and a compact `markdown` string that can be pasted into a chat as context.
The host is always api.inaturalist.org, so nothing else can be reached from here.
"""
import base64, json, re, socket, threading, time, urllib.error, urllib.parse, urllib.request, uuid
from html import unescape

from websearch import WebError

API = "https://api.inaturalist.org/v1"
UA = "CressyAI/1.0 (local assistant; python-urllib)"
LOCALE_RE = re.compile(r"^[a-z]{2,3}(?:-[A-Za-z]{2,8})?$")
ICONIC = {   # iconic taxon names the UI offers -> accepted by the API
    "Aves": "birds", "Mammalia": "mammals", "Reptilia": "reptiles", "Amphibia": "amphibians", "Actinopterygii": "ray-finned fishes",
    "Insecta": "insects", "Arachnida": "arachnids", "Mollusca": "molluscs", "Plantae": "plants", "Fungi": "fungi",
    "Animalia": "other animals", "Chromista": "chromista", "Protozoa": "protozoa",
}
STATUS_NAMES = {"EX": "Extinct", "EW": "Extinct in the wild", "CR": "Critically endangered", "EN": "Endangered", "VU": "Vulnerable",
                "NT": "Near threatened", "LC": "Least concern", "DD": "Data deficient", "NE": "Not evaluated"}
MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]

_CACHE, _CACHE_MAX, _CACHE_TTL = {}, 128, 600
_LOCK, _LAST = threading.Lock(), [0.0]
MIN_GAP = 0.7   # iNaturalist asks for roughly one request per second; cached answers cost nothing


def locale_ok(v):
    v = (v or "en").strip()
    return v if LOCALE_RE.match(v) else "en"


def _get_cached(key):
    hit = _CACHE.get(key)
    if hit and time.time() - hit[0] < _CACHE_TTL:
        return json.loads(json.dumps(hit[1]))
    return None


def _put_cached(key, val):
    if len(_CACHE) >= _CACHE_MAX:
        _CACHE.pop(min(_CACHE, key=lambda k: _CACHE[k][0]), None)
    _CACHE[key] = (time.time(), json.loads(json.dumps(val)))


def _request(req, timeout):
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.loads(r.read(3_000_000).decode("utf-8", "replace"))
    except urllib.error.HTTPError as e:
        if e.code == 429:
            raise WebError("iNaturalist says too many requests (429). Wait a minute and try again.")
        if e.code in (401, 403):
            raise WebError("iNaturalist refused the request (%d). The token for photo identification is missing, wrong or expired." % e.code)
        if e.code == 404:
            raise WebError("iNaturalist has no record with that id.")
        raise WebError(f"iNaturalist answered with an error ({e.code}).")
    except urllib.error.URLError as e:
        raise WebError(f"Cannot reach iNaturalist. Check your internet connection. ({e.reason})")
    except (TimeoutError, socket.timeout):
        raise WebError("iNaturalist took too long to answer.")
    except (ValueError, UnicodeError):
        raise WebError("iNaturalist did not return valid data.")
    except OSError as e:
        raise WebError(f"Cannot reach iNaturalist: {e}")


def _api(path, params=None, timeout=12):
    params = {k: v for k, v in (params or {}).items() if v not in (None, "", [])}
    for k, v in list(params.items()):
        if isinstance(v, bool):
            params[k] = "true" if v else "false"
        elif isinstance(v, (list, tuple)):
            params[k] = ",".join(str(x) for x in v)
    key = (path, tuple(sorted(params.items())))
    hit = _get_cached(key)
    if hit is not None:
        return hit
    with _LOCK:   # space real requests out
        wait = MIN_GAP - (time.time() - _LAST[0])
        if wait > 0:
            time.sleep(wait)
        _LAST[0] = time.time()
    url = API + path + ("?" + urllib.parse.urlencode(params) if params else "")
    data = _request(urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "application/json", "Accept-Encoding": "identity"}), timeout)
    _put_cached(key, data)
    return data


# ---------------------------------------------------------------- small helpers
def _txt(html, n=900):
    t = unescape(re.sub(r"<[^>]+>", " ", html or ""))
    t = " ".join(t.split())
    return t if len(t) <= n else t[:n].rsplit(" ", 1)[0] + "…"


def _photo(p, size="medium"):
    if not p:
        return ""
    u = p.get("medium_url") or p.get("url") or p.get("square_url") or ""
    return u.replace("/square.", f"/{size}.").replace("/medium.", f"/{size}.") if u else ""


def _status(t):
    cs = (t or {}).get("conservation_status") or {}
    code = (cs.get("iucn") and cs.get("status")) or cs.get("status") or ""
    code = str(code).upper()
    name = STATUS_NAMES.get(code) or cs.get("status_name") or ""
    return {"code": code, "name": name.capitalize() if name and name == name.lower() else name, "place": (cs.get("place") or {}).get("name", "")} if cs else {}


def _taxon_row(t):
    return {
        "id": t.get("id"), "name": t.get("name") or "", "common": t.get("preferred_common_name") or "", "rank": t.get("rank") or "",
        "iconic": t.get("iconic_taxon_name") or "", "observations": t.get("observations_count") or 0, "extinct": bool(t.get("extinct")),
        "photo": _photo(t.get("default_photo"), "medium"), "status": _status(t), "wikipedia": t.get("wikipedia_url") or "",
        "url": f"https://www.inaturalist.org/taxa/{t.get('id')}",
    }


def _label(r):
    return f"{r['common']} ({r['name']})" if r.get("common") else r.get("name", "?")


def _where(lat, lng, radius, place_id):
    if place_id:
        return {"place_id": int(place_id)}
    if lat is not None and lng is not None:
        return {"lat": round(float(lat), 5), "lng": round(float(lng), 5), "radius": max(1, min(float(radius or 10), 500))}
    return {}


def _filters(p):
    """Shared observation filters; every value is validated so nothing odd reaches the URL."""
    out = {}
    if p.get("taxon_id"):
        out["taxon_id"] = int(p["taxon_id"])
    ic = [x for x in (p.get("iconic") or []) if x in ICONIC]
    if ic:
        out["iconic_taxa"] = ic
    if p.get("quality") in ("research", "needs_id", "casual"):
        out["quality_grade"] = p["quality"]
    if p.get("days"):
        d = max(1, min(int(p["days"]), 3650))
        out["d1"] = time.strftime("%Y-%m-%d", time.gmtime(time.time() - d * 86400))
    if p.get("month"):
        out["month"] = ",".join(str(max(1, min(int(m), 12))) for m in str(p["month"]).split(",")[:12] if m.strip().isdigit())
    for k in ("threatened", "introduced", "native", "endemic", "captive", "verifiable"):
        if p.get(k):
            out[k] = True
    if p.get("photos"):
        out["photos"] = True
    if p.get("user"):
        out["user_id"] = re.sub(r"[^\w.-]", "", str(p["user"]))[:40]
    return out


# ---------------------------------------------------------------- features
def search(query, rank="", limit=8, locale="en"):
    """Find species/taxa by common or scientific name. rank: '' | species | genus | family | order | class | ..."""
    q = " ".join((query or "").split())[:200]
    if not q:
        raise WebError("There is nothing to look up.")
    limit = max(1, min(int(limit), 20))
    d = _api("/taxa", {"q": q, "per_page": limit, "locale": locale_ok(locale), "rank": rank if re.fullmatch(r"[a-z]{3,20}", rank or "") else "",
                       "is_active": True, "order_by": "observations_count", "order": "desc"})
    rows = [_taxon_row(t) for t in d.get("results", [])]
    md = "\n".join(f"- {_label(r)} — {r['rank']}" + (f", {r['iconic']}" if r["iconic"] else "") + f", {r['observations']:,} observations"
                   + (f", **{r['status']['name']}**" if r["status"].get("name") else "") for r in rows)
    return {"query": q, "results": rows, "total": d.get("total_results", len(rows)), "markdown": f"iNaturalist taxa matching “{q}”:\n{md}" if rows else ""}


def taxon(tid, locale="en"):
    """Everything iNaturalist knows about one taxon: summary, ancestry, children, status, photos, similar species."""
    tid = int(tid)
    d = (_api(f"/taxa/{tid}", {"locale": locale_ok(locale)}).get("results") or [None])[0]
    if not d:
        raise WebError("iNaturalist has no taxon with that id.")
    row = _taxon_row(d)
    row.update({
        "summary": _txt(d.get("wikipedia_summary"), 1400),
        "ancestors": [{"id": a.get("id"), "name": a.get("name"), "common": a.get("preferred_common_name") or "", "rank": a.get("rank")} for a in d.get("ancestors", [])],
        "children": [_taxon_row(c) for c in (d.get("children") or [])[:20]],
        "photos": [{"url": _photo(tp.get("photo"), "medium"), "credit": (tp.get("photo") or {}).get("attribution", "")} for tp in (d.get("taxon_photos") or [])[:8]],
        "complete_species_count": d.get("complete_species_count"),
        "atlas": bool(d.get("atlas_id")),
    })
    try:
        sim = _api("/identifications/similar_species", {"taxon_id": tid, "locale": locale_ok(locale)}).get("results") or []
        row["similar"] = [{**_taxon_row(s["taxon"]), "count": s.get("count", 0)} for s in sim[:6] if s.get("taxon")]
    except WebError:
        row["similar"] = []
    lineage = " > ".join(a["name"] for a in row["ancestors"][-6:])
    lines = [f"# {_label(row)}", f"Rank: {row['rank']}" + (f" · Group: {row['iconic']}" if row["iconic"] else "") + f" · {row['observations']:,} observations on iNaturalist"]
    if lineage:
        lines.append(f"Classification: {lineage} > {row['name']}")
    if row["status"].get("name"):
        lines.append(f"Conservation status: {row['status']['name']}" + (f" ({row['status']['place']})" if row["status"].get("place") else ""))
    if row["extinct"]:
        lines.append("This taxon is extinct.")
    if row["summary"]:
        lines.append("\n" + row["summary"])
    if row["children"]:
        lines.append("\nIncludes: " + ", ".join(_label(c) for c in row["children"][:10]))
    if row["similar"]:
        lines.append("Often confused with: " + ", ".join(_label(s) for s in row["similar"][:5]))
    row["markdown"] = "\n".join(lines)
    return row


def places(query, limit=6):
    q = " ".join((query or "").split())[:120]
    if not q:
        raise WebError("There is nothing to look up.")
    d = _api("/places/autocomplete", {"q": q, "per_page": max(1, min(int(limit), 15))})
    rows = []
    for p in d.get("results", []):
        loc = (p.get("location") or "").split(",")
        rows.append({"id": p.get("id"), "name": p.get("display_name") or p.get("name") or "", "type": p.get("place_type"),
                     "lat": float(loc[0]) if len(loc) == 2 else None, "lng": float(loc[1]) if len(loc) == 2 else None})
    return {"query": q, "results": rows, "markdown": "\n".join(f"- {r['name']} (place id {r['id']})" for r in rows)}


def nearby(lat=None, lng=None, radius=10, place_id=None, limit=20, **flt):
    """Species recorded in a place or around a point, most-observed first (iNaturalist species_counts)."""
    where = _where(lat, lng, radius, place_id)
    if not where:
        raise WebError("Give a place or a location (latitude and longitude).")
    limit = max(1, min(int(limit), 50))
    params = {**where, **_filters(flt), "per_page": limit, "locale": locale_ok(flt.get("locale")), "quality_grade": flt.get("quality") or "research"}
    d = _api("/observations/species_counts", params)
    rows = []
    for r in d.get("results", []):
        t = r.get("taxon") or {}
        rows.append({**_taxon_row(t), "count": r.get("count", 0)})
    where_txt = f"place {place_id}" if place_id else f"within {where['radius']:g} km of {where['lat']}, {where['lng']}"
    md = "\n".join(f"- {_label(r)} — {r['count']} obs" + (f", **{r['status']['name']}**" if r["status"].get("name") and r["status"].get("code") not in ("LC", "NE") else "") for r in rows)
    return {"results": rows, "total_species": d.get("total_results", len(rows)),
            "markdown": f"Species recorded on iNaturalist {where_txt} ({d.get('total_results', len(rows))} species in total; top {len(rows)}):\n{md}" if rows else ""}


def observations(lat=None, lng=None, radius=10, place_id=None, limit=12, order="observed_on", **flt):
    where = _where(lat, lng, radius, place_id)
    limit = max(1, min(int(limit), 30))
    params = {**where, **_filters(flt), "per_page": limit, "locale": locale_ok(flt.get("locale")), "order_by": "votes" if order == "votes" else "observed_on", "order": "desc"}
    if not where and not params.get("taxon_id") and not params.get("iconic_taxa") and not params.get("user_id"):
        raise WebError("Give a species, a place, a location or an observer to look up.")
    d = _api("/observations", params)
    rows = []
    for o in d.get("results", []):
        t = o.get("taxon") or {}
        loc = (o.get("location") or "").split(",")
        rows.append({
            "id": o.get("id"), "taxon": t.get("preferred_common_name") or t.get("name") or "Unidentified", "scientific": t.get("name") or "",
            "taxon_id": t.get("id"), "observed": o.get("observed_on") or "", "place": o.get("place_guess") or "", "grade": o.get("quality_grade") or "",
            "observer": (o.get("user") or {}).get("login", ""), "photo": _photo((o.get("photos") or [None])[0], "medium"),
            "lat": float(loc[0]) if len(loc) == 2 else None, "lng": float(loc[1]) if len(loc) == 2 else None,
            "faves": o.get("faves_count") or 0, "url": o.get("uri") or f"https://www.inaturalist.org/observations/{o.get('id')}",
        })
    md = "\n".join(f"- {r['taxon']}" + (f" ({r['scientific']})" if r["scientific"] and r["scientific"] != r["taxon"] else "") + f" — {r['observed']}, {r['place'] or 'unknown place'}, {r['grade']}, {r['url']}" for r in rows)
    return {"results": rows, "total": d.get("total_results", len(rows)), "markdown": f"Recent iNaturalist observations ({d.get('total_results', len(rows)):,} match):\n{md}" if rows else ""}


def observation(oid):
    d = (_api(f"/observations/{int(oid)}").get("results") or [None])[0]
    if not d:
        raise WebError("iNaturalist has no observation with that id.")
    t = d.get("taxon") or {}
    ids = [{"user": (i.get("user") or {}).get("login", ""), "taxon": (i.get("taxon") or {}).get("preferred_common_name") or (i.get("taxon") or {}).get("name", ""), "current": bool(i.get("current"))}
           for i in d.get("identifications", []) if i.get("current")][:10]
    out = {"id": d.get("id"), "taxon": _taxon_row(t) if t else None, "description": _txt(d.get("description"), 600), "observed": d.get("observed_on_string") or d.get("observed_on") or "",
           "place": d.get("place_guess") or "", "grade": d.get("quality_grade"), "identifications": ids,
           "comments": [{"user": (c.get("user") or {}).get("login", ""), "body": _txt(c.get("body"), 300)} for c in (d.get("comments") or [])[:5]],
           "photos": [_photo(p, "large") for p in (d.get("photos") or [])[:6]], "url": d.get("uri", "")}
    lines = [f"Observation {out['id']}: {_label(out['taxon']) if out['taxon'] else 'Unidentified'} — {out['observed']}, {out['place']} ({out['grade']})"]
    if out["description"]:
        lines.append("Notes: " + out["description"])
    if ids:
        lines.append("Identifications: " + "; ".join(f"{i['taxon']} (by {i['user']})" for i in ids))
    out["markdown"] = "\n".join(lines)
    return out


def season(taxon_id, lat=None, lng=None, radius=50, place_id=None):
    """When is this species seen? Observations per month of the year."""
    params = {"taxon_id": int(taxon_id), "interval": "month_of_year", "date_field": "observed", **_where(lat, lng, radius, place_id)}
    d = _api("/observations/histogram", params)
    raw = (d.get("results") or {}).get("month_of_year") or {}
    counts = [int(raw.get(str(m), 0)) for m in range(1, 13)]
    total, peak = sum(counts), max(counts) if counts else 0
    best = [MONTHS[i] for i, c in enumerate(counts) if peak and c >= peak * 0.6]
    md = "Observations by month: " + ", ".join(f"{m} {c}" for m, c in zip(MONTHS, counts)) + (f". Best months: {', '.join(best)}." if best else "")
    return {"months": MONTHS, "counts": counts, "total": total, "best": best, "markdown": md if total else ""}


def observers(lat=None, lng=None, radius=10, place_id=None, limit=8, **flt):
    where = _where(lat, lng, radius, place_id)
    if not where:
        raise WebError("Give a place or a location.")
    d = _api("/observations/observers", {**where, **_filters(flt), "per_page": max(1, min(int(limit), 20))})
    rows = [{"user": (r.get("user") or {}).get("login", ""), "observations": r.get("observation_count", 0), "species": r.get("species_count", 0)} for r in d.get("results", [])]
    return {"results": rows, "markdown": "Top iNaturalist observers here:\n" + "\n".join(f"- {r['user']}: {r['observations']} obs, {r['species']} species" for r in rows) if rows else ""}


def identify(image_data_url, token, lat=None, lng=None, locale="en"):
    """Photo identification (computer vision). Needs a JWT from https://www.inaturalist.org/users/api_token (valid 24 h)."""
    if not token:
        raise WebError("Photo identification needs an iNaturalist API token. Start the server with --inat-token or set INAT_TOKEN (see README).")
    m = re.match(r"^data:image/(jpeg|png|webp);base64,(.+)$", image_data_url or "", re.S)
    if not m:
        raise WebError("Send a JPEG, PNG or WebP image.")
    raw = base64.b64decode(m.group(2), validate=False)
    if len(raw) > 6_000_000:
        raise WebError("That image is too large (6 MB max).")
    ext = "jpg" if m.group(1) == "jpeg" else m.group(1)
    bd = uuid.uuid4().hex
    parts = [(f'--{bd}\r\nContent-Disposition: form-data; name="image"; filename="photo.{ext}"\r\nContent-Type: image/{m.group(1)}\r\n\r\n').encode(), raw, b"\r\n"]
    for k, v in (("lat", lat), ("lng", lng), ("locale", locale_ok(locale))):
        if v not in (None, ""):
            parts.append(f'--{bd}\r\nContent-Disposition: form-data; name="{k}"\r\n\r\n{v}\r\n'.encode())
    parts.append(f"--{bd}--\r\n".encode())
    req = urllib.request.Request(API + "/computervision/score_image", data=b"".join(parts), method="POST",
                                 headers={"User-Agent": UA, "Authorization": token.strip(), "Content-Type": f"multipart/form-data; boundary={bd}", "Accept": "application/json"})
    d = _request(req, 40)
    rows = []
    for r in (d.get("results") or [])[:8]:
        t = r.get("taxon") or {}
        rows.append({**_taxon_row(t), "score": round(float(r.get("combined_score") or r.get("vision_score") or 0), 1)})
    md = "Photo identification suggestions (iNaturalist computer vision, score out of 100):\n" + "\n".join(f"- {_label(r)} — {r['score']}" for r in rows)
    return {"results": rows, "markdown": md if rows else ""}


def ping(timeout=6):
    try:
        _api("/taxa/1", timeout=timeout)
        return True, ""
    except WebError as e:
        return False, str(e)
