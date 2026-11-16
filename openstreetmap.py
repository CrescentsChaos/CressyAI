"""OpenStreetMap for CressyAI: place search (Nominatim) and "what is nearby" (Overpass). Standard library only.

Follows the public usage policies: an identifying User-Agent (set your e-mail or URL with --osm-contact), at most one
Nominatim request per second, cached results, and no bulk use. Overpass queries are built from a fixed list of categories,
so nothing typed by the user is ever placed into the query language. Data (c) OpenStreetMap contributors, ODbL.
"""
import json, re, socket, threading, time, urllib.error, urllib.parse, urllib.request

import geomath
from websearch import WebError

NOMINATIM = "https://nominatim.openstreetmap.org"
OVERPASS = "https://overpass-api.de/api/interpreter"
ATTRIBUTION = "© OpenStreetMap contributors (ODbL)"
CONTACT = ""
_THROTTLE, _LAST = threading.Lock(), [0.0]
MIN_GAP = 1.1          # seconds between Nominatim requests (policy: max 1 per second)
_CACHE, _CACHE_MAX = {}, 128


def configure(contact="", nominatim=None, overpass=None):
    """Called once at start-up from the command line (--osm-contact, --nominatim-url, --overpass-url)."""
    global CONTACT, NOMINATIM, OVERPASS
    for label, val in (("nominatim", nominatim), ("overpass", overpass)):
        if val and not re.match(r"^https?://", val):
            raise ValueError(f"--{label}-url must start with http:// or https://")
    CONTACT = (contact or "").strip()[:120]
    if nominatim:
        NOMINATIM = nominatim.rstrip("/")
    if overpass:
        OVERPASS = overpass.rstrip("/")


def _ua():
    return f"CressyAI/1.0 (local assistant; contact: {CONTACT or 'not set'}) python-urllib"


def _cached(key, ttl, fn):
    hit = _CACHE.get(key)
    if hit and time.time() - hit[0] < ttl:
        return json.loads(json.dumps(hit[1]))
    val = fn()
    if len(_CACHE) >= _CACHE_MAX:
        _CACHE.pop(min(_CACHE, key=lambda k: _CACHE[k][0]), None)
    _CACHE[key] = (time.time(), json.loads(json.dumps(val)))
    return val


def _http(req, timeout, what):
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.read(8_000_000).decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        e.close()
        if e.code == 429:
            raise WebError(f"{what} says too many requests (429). Wait a minute and try again.")
        if e.code in (403, 401):
            raise WebError(f"{what} refused the request ({e.code}). Set --osm-contact to your e-mail or a URL so requests are identifiable.")
        if e.code in (502, 503, 504):
            raise WebError(f"{what} is busy or down right now ({e.code}). Try again shortly.")
        raise WebError(f"{what} answered with an error ({e.code}).")
    except urllib.error.URLError as e:
        raise WebError(f"Cannot reach {what}. Check your internet connection. ({e.reason})")
    except (TimeoutError, socket.timeout):
        raise WebError(f"{what} took too long to answer.")
    except OSError as e:
        raise WebError(f"Cannot reach {what}: {e}")


def _nominatim(path, params, timeout=12):
    with _THROTTLE:                                   # one request at a time, at least MIN_GAP apart
        wait = MIN_GAP - (time.monotonic() - _LAST[0])
        if wait > 0:
            time.sleep(wait)
        try:
            url = f"{NOMINATIM}/{path}?" + urllib.parse.urlencode({**params, "format": "jsonv2"})
            body = _http(urllib.request.Request(url, headers={"User-Agent": _ua(), "Accept": "application/json", "Accept-Encoding": "identity"}),
                         timeout, "OpenStreetMap search (Nominatim)")
        finally:
            _LAST[0] = time.monotonic()
    try:
        return json.loads(body)
    except ValueError:
        raise WebError("OpenStreetMap search did not return valid data.")


# ---------------------------------------------------------------- places
def osm_url(osm_type, osm_id):
    t = {"N": "node", "W": "way", "R": "relation"}.get(str(osm_type)[:1].upper(), str(osm_type))
    return f"https://www.openstreetmap.org/{t}/{osm_id}" if t in ("node", "way", "relation") and osm_id else ""


def map_url(lat, lon, zoom=16):
    return f"https://www.openstreetmap.org/?mlat={lat:.5f}&mlon={lon:.5f}#map={zoom}/{lat:.5f}/{lon:.5f}"


def _wiki_url(tag):
    m = re.match(r"^([a-z]{2,3}):(.+)$", tag or "")
    return f"https://{m.group(1)}.wikipedia.org/wiki/" + urllib.parse.quote(m.group(2).replace(" ", "_")) if m else ""


def _short(addr):
    """A short human label from Nominatim's address parts."""
    road = " ".join(x for x in (addr.get("house_number"), addr.get("road") or addr.get("pedestrian") or addr.get("footway")) if x)
    area = addr.get("suburb") or addr.get("neighbourhood") or addr.get("quarter") or addr.get("city_district") or ""
    city = addr.get("city") or addr.get("town") or addr.get("village") or addr.get("municipality") or addr.get("county") or ""
    return ", ".join(dict.fromkeys(x for x in (road, area, city) if x))


def _place(p):
    try:
        lat, lon = float(p["lat"]), float(p["lon"])
    except (KeyError, TypeError, ValueError):
        return None
    if not geomath.valid(lat, lon):
        return None
    addr = p.get("address") or {}
    ex = p.get("extratags") or {}
    try:
        bb = [float(x) for x in (p.get("boundingbox") or [])][:4]
    except ValueError:
        bb = []
    display = p.get("display_name") or ""
    return {"name": p.get("name") or display.split(",")[0].strip() or "Unnamed place", "display_name": display, "lat": lat, "lon": lon,
            "kind": " / ".join(x for x in (p.get("category"), p.get("type")) if x), "address": _short(addr),
            "city": addr.get("city") or addr.get("town") or addr.get("village") or addr.get("state_district") or "", "state": addr.get("state") or "",
            "postcode": addr.get("postcode") or "", "country": addr.get("country") or "", "country_code": (addr.get("country_code") or "").upper(),
            "bbox": bb, "importance": p.get("importance"), "osm_url": osm_url(p.get("osm_type"), p.get("osm_id")), "map_url": map_url(lat, lon),
            "wikipedia": _wiki_url(ex.get("wikipedia")), "website": ex.get("website") or ex.get("contact:website") or "",
            "population": ex.get("population") or "", "source": "openstreetmap"}


def search(query, limit=5, lang="en"):
    """Find places by name or address. Returns a list of normalised places, best match first."""
    query = " ".join((query or "").split())[:200]
    if not query:
        raise WebError("There is nothing to look up.")
    c = geomath.parse_coords(query)
    if c:
        return [reverse(c[0], c[1], lang=lang)]
    lang = lang if re.fullmatch(r"[a-z]{2,3}(-[A-Za-z]{2,8})?", lang or "") else "en"

    def go():
        data = _nominatim("search", {"q": query, "limit": max(1, min(int(limit), 10)), "addressdetails": 1, "extratags": 1, "dedupe": 1,
                                     "accept-language": lang})
        return [x for x in (_place(p) for p in (data if isinstance(data, list) else [])) if x]
    return _cached(("s", query.lower(), limit, lang), 3600, go)


def reverse(lat, lon, zoom=14, lang="en"):
    """The place at a coordinate. Raises WebError when there is nothing there (open sea, for example)."""
    if not geomath.valid(lat, lon):
        raise WebError("Those coordinates are not valid.")
    lat, lon = round(lat, 5), round(lon, 5)
    lang = lang if re.fullmatch(r"[a-z]{2,3}(-[A-Za-z]{2,8})?", lang or "") else "en"

    def go():
        d = _nominatim("reverse", {"lat": lat, "lon": lon, "zoom": max(3, min(int(zoom), 18)), "addressdetails": 1, "extratags": 1, "accept-language": lang})
        if not isinstance(d, dict) or d.get("error"):
            return {"name": f"{lat:.4f}, {lon:.4f}", "display_name": f"{lat:.4f}, {lon:.4f}", "lat": lat, "lon": lon, "kind": "", "address": "",
                    "city": "", "state": "", "postcode": "", "country": "", "country_code": "", "bbox": [], "importance": None, "osm_url": "",
                    "map_url": map_url(lat, lon), "wikipedia": "", "website": "", "population": "", "source": "coordinates"}
        p = _place(d)
        if not p:
            raise WebError("OpenStreetMap did not return a usable place.")
        p["lat"], p["lon"] = lat, lon
        return p
    return _cached(("r", lat, lon, zoom, lang), 3600, go)


def ping(timeout=6):
    try:
        body = _http(urllib.request.Request(f"{NOMINATIM}/status.php?format=json", headers={"User-Agent": _ua(), "Accept-Encoding": "identity"}),
                     timeout, "OpenStreetMap search (Nominatim)")
        try:
            if json.loads(body).get("status") not in (0, None):
                return False, "Nominatim reports a problem: " + str(json.loads(body).get("message", ""))[:120]
        except ValueError:
            if body.strip() not in ("OK", ""):
                return False, "Nominatim answered unexpectedly."
        return True, ""
    except WebError as e:
        return False, str(e)


# ---------------------------------------------------------------- what is nearby (Overpass)
# id -> label, emoji, Overpass tag filters, words that mention it in a question, whether unnamed objects are still useful
KINDS = {
    "pharmacy": ("Pharmacies", "💊", ['["amenity"="pharmacy"]'], r"pharmac(?:y|ies)|chemists?|drug ?stores?", False),
    "hospital": ("Hospitals & clinics", "🏥", ['["amenity"~"^(hospital|clinic|doctors)$"]'], r"hospitals?|clinics?|doctors?|emergency rooms?|medical centers?|medical centres?", False),
    "restaurant": ("Restaurants", "🍽️", ['["amenity"~"^(restaurant|fast_food|food_court)$"]'], r"restaurants?|places? to eat|eateries|diners?|fast ?food|somewhere to eat|food courts?", False),
    "cafe": ("Cafés", "☕", ['["amenity"="cafe"]'], r"caf[eé]s?|coffee (?:shops?|places?|bars?|houses?|spots?)|tea (?:shops?|stalls?)", False),
    "bakery": ("Bakeries", "🥐", ['["shop"="bakery"]'], r"bakery|bakeries", False),
    "atm": ("ATMs & banks", "🏧", ['["amenity"~"^(atm|bank)$"]'], r"atms?|cash machines?|cashpoints?|banks?", False),
    "supermarket": ("Supermarkets & groceries", "🛒", ['["shop"~"^(supermarket|convenience|greengrocer|grocery)$"]'], r"supermarkets?|grocer(?:y|ies|s)|convenience stores?|mini ?marts?|grocery stores?", False),
    "fuel": ("Fuel stations", "⛽", ['["amenity"="fuel"]'], r"petrol (?:pumps?|stations?)|gas stations?|fuel stations?|filling stations?|cng stations?|petrol pumps?", False),
    "charging": ("EV charging", "🔌", ['["amenity"="charging_station"]'], r"(?:ev |electric )?charging (?:stations?|points?)|ev chargers?", False),
    "parking": ("Parking", "🅿️", ['["amenity"="parking"]'], r"parking(?: lots?| spots?| places?)?|car parks?", True),
    "toilets": ("Toilets", "🚻", ['["amenity"="toilets"]'], r"toilets?|restrooms?|washrooms?|loos?|bathrooms?", True),
    "water": ("Drinking water", "🚰", ['["amenity"="drinking_water"]'], r"drinking water|water fountains?|water points?|refill stations?", True),
    "park": ("Parks & gardens", "🌳", ['["leisure"~"^(park|garden|playground)$"]'], r"parks?|gardens?|playgrounds?|green spaces?", False),
    "hotel": ("Hotels & stays", "🏨", ['["tourism"~"^(hotel|hostel|guest_house|motel|apartment)$"]'], r"hotels?|hostels?|guest ?houses?|places? to stay|accommodations?|motels?", False),
    "attraction": ("Sights & museums", "🏛️", ['["tourism"~"^(attraction|museum|gallery|viewpoint|zoo|theme_park)$"]', '["historic"~"^(monument|castle|memorial|archaeological_site|ruins)$"]'],
                   r"attractions?|museums?|sights?|sightseeing|landmarks?|tourist (?:spots?|attractions?)|things to (?:see|do)|viewpoints?|galleries|monuments?", False),
    "transit": ("Transit stops", "🚌", ['["highway"="bus_stop"]', '["railway"~"^(station|halt|tram_stop|subway_entrance)$"]', '["public_transport"="station"]'],
                r"bus (?:stops?|stands?|stations?)|(?:train|railway|metro|subway|tram) (?:stations?|stops?)|transit stops?", True),
    "library": ("Libraries", "📚", ['["amenity"="library"]'], r"librar(?:y|ies)", False),
    "school": ("Schools & universities", "🎓", ['["amenity"~"^(school|university|college|kindergarten)$"]'], r"schools?|universit(?:y|ies)|colleges?|kindergartens?", False),
    "post": ("Post offices", "📮", ['["amenity"="post_office"]'], r"post offices?|courier offices?", False),
    "police": ("Police", "👮", ['["amenity"="police"]'], r"police(?: stations?)?", False),
    "worship": ("Places of worship", "🕌", ['["amenity"="place_of_worship"]'], r"mosques?|masjids?|churches|church|temples?|places? of worship|mandirs?|pagodas?", False),
    "gym": ("Gyms & sports", "🏋️", ['["leisure"~"^(fitness_centre|sports_centre|stadium|swimming_pool)$"]'], r"gyms?|fitness (?:centers?|centres?)|sports (?:centers?|centres?)|swimming pools?", False),
    "cinema": ("Cinemas & theatres", "🎬", ['["amenity"~"^(cinema|theatre)$"]'], r"cinemas?|movie theat(?:er|re)s?|theat(?:er|re)s?", False),
    "market": ("Markets", "🧺", ['["amenity"="marketplace"]'], r"markets?|bazaars?|marketplaces?", False),
}
KIND_RX = [(k, re.compile(r"\b(?:" + v[3] + r")\b", re.I)) for k, v in KINDS.items()]


def kind_from_text(text):
    """First category mentioned in a sentence ('any pharmacy near me' -> 'pharmacy'), else None."""
    best = None
    for k, rx in KIND_RX:
        m = rx.search(text or "")
        if m and (best is None or m.start() < best[0]):
            best = (m.start(), k)
    return best[1] if best else None


def kinds_public():
    return [{"id": k, "label": v[0], "emoji": v[1]} for k, v in KINDS.items()]


def _ql(lat, lon, kind, radius, cap):
    parts = "".join(f"nwr(around:{int(radius)},{lat:.5f},{lon:.5f}){f};" for f in KINDS[kind][2])
    return f"[out:json][timeout:25];({parts});out center tags {int(cap)};"


def _poi(el, kind, lat0, lon0):
    tags = el.get("tags") or {}
    lat = el.get("lat", (el.get("center") or {}).get("lat"))
    lon = el.get("lon", (el.get("center") or {}).get("lon"))
    if not geomath.valid(lat, lon):
        return None
    label = KINDS[kind][0]
    name = (tags.get("name:en") or tags.get("name") or tags.get("brand") or tags.get("operator") or "").strip()
    if not name and not KINDS[kind][4]:
        return None
    street = " ".join(x for x in (tags.get("addr:housenumber"), tags.get("addr:street")) if x)
    where = ", ".join(x for x in (street, tags.get("addr:suburb") or tags.get("addr:neighbourhood") or tags.get("addr:city") or "") if x)
    d = geomath.haversine_m(lat0, lon0, lat, lon)
    b = geomath.bearing(lat0, lon0, lat, lon)
    return {"name": name or f"Unnamed {label.lower().rstrip('s')}", "named": bool(name), "kind": kind, "lat": lat, "lon": lon, "distance_m": round(d),
            "bearing": round(b), "compass": geomath.compass(b), "address": where, "opening_hours": tags.get("opening_hours") or "",
            "phone": tags.get("phone") or tags.get("contact:phone") or "", "website": tags.get("website") or tags.get("contact:website") or "",
            "cuisine": (tags.get("cuisine") or "").replace(";", ", ").replace("_", " "), "brand": tags.get("brand") or "",
            "wheelchair": tags.get("wheelchair") or "", "osm_url": osm_url(el.get("type", "node")[:1], el.get("id"))}


def nearby(lat, lon, kind, radius=1000, limit=10):
    """Places of one category around a point, nearest first. Raises WebError on a failed or overloaded service."""
    if not geomath.valid(lat, lon):
        raise WebError("Those coordinates are not valid.")
    if kind not in KINDS:
        raise WebError("Unknown category.")
    radius, limit = max(100, min(int(radius), 10000)), max(1, min(int(limit), 30))
    lat, lon = round(lat, 4), round(lon, 4)

    def go():
        body = urllib.parse.urlencode({"data": _ql(lat, lon, kind, radius, 300)}).encode()
        req = urllib.request.Request(OVERPASS, data=body, headers={"User-Agent": _ua(), "Accept": "application/json", "Accept-Encoding": "identity",
                                                                    "Content-Type": "application/x-www-form-urlencoded"})
        text = _http(req, 40, "OpenStreetMap data (Overpass)")
        try:
            data = json.loads(text)
        except ValueError:
            raise WebError("OpenStreetMap data (Overpass) is overloaded or returned something unreadable. Try again shortly.")
        if "runtime error" in str(data.get("remark", "")).lower():
            raise WebError("The OpenStreetMap data query timed out. Try a smaller radius.")
        seen, items = set(), []
        for el in data.get("elements") or []:
            p = _poi(el, kind, lat, lon)
            if not p:
                continue
            key = (p["name"].lower(), round(p["lat"], 4), round(p["lon"], 4))
            if key in seen:
                continue
            seen.add(key)
            items.append(p)
        items.sort(key=lambda p: (not p["named"], p["distance_m"]) if KINDS[kind][4] else p["distance_m"])
        return items
    return _cached(("n", lat, lon, kind, radius), 900, go)[:limit]


# ---------------------------------------------------------------- distance between two places
def between(a, b, units="metric"):
    """Straight-line (great-circle) distance and direction from place a to place b. Not a driving distance."""
    d = geomath.haversine_m(a["lat"], a["lon"], b["lat"], b["lon"])
    br = geomath.bearing(a["lat"], a["lon"], b["lat"], b["lon"])
    return {"meters": round(d), "km": round(d / 1000, 1), "miles": round(d / 1609.344, 1), "bearing": round(br), "compass": geomath.compass(br),
            "text": geomath.fmt_dist(d, units)}


def gpx(points, name="CressyAI places"):
    """A small GPX file (waypoints) that opens in most map apps."""
    from xml.sax.saxutils import escape
    rows = "".join(f'<wpt lat="{p["lat"]:.6f}" lon="{p["lon"]:.6f}"><name>{escape(str(p.get("name", "")))}</name>'
                   f'<desc>{escape(str(p.get("address", "")))}</desc></wpt>' for p in points if geomath.valid(p.get("lat"), p.get("lon")))
    return ('<?xml version="1.0" encoding="UTF-8"?><gpx version="1.1" creator="CressyAI" xmlns="http://www.topografix.com/GPX/1/1">'
            f'<metadata><name>{escape(name)}</name><copyright author="OpenStreetMap contributors"><license>https://opendatacommons.org/licenses/odbl/</license></copyright></metadata>'
            + rows + "</gpx>")
