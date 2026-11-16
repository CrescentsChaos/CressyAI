"""Places and weather for CressyAI: understands what is being asked, fetches Open-Meteo / OpenStreetMap data and
returns (a) a card for the Places tab and (b) compact text the model can answer from. Standard library only.

Nothing here runs unless the user types a /command or switches the Geo button on; every lookup states where it went.
"""
import datetime as dt
import re
from concurrent.futures import ThreadPoolExecutor

import geomath
import openmeteo
import openstreetmap as osm
from websearch import WebError


class NotFound(WebError):
    """The place name matched nothing."""


class NeedPlace(WebError):
    """No place was given and none is saved. The message is meant to be shown as is."""


# ---------------------------------------------------------------- understanding a message
CODE_HINT = re.compile(r"```|\b(?:def|class|import|function|const|var|return|select|npm|pip|traceback|exception|stack ?trace|compiler?|variable|"
                       r"top[_ -]?[pk]|llm|gguf|sampling|logits?|tokens?|prompt|api|json|regex|bug|error)\b|=>|[{};]\s*$|\btemperature\s*[=:]", re.I)
CREATIVE = re.compile(r"^\s*(?:write|compose|draft|translate|rewrite|summari[sz]e|explain|describe|generate|imagine|tell me a (?:story|joke)|poem|essay)\b", re.I)
WEATHER_STRONG = re.compile(r"\b(?:weather|forecast|umbrella|raining|snowing|will it (?:rain|snow|storm)|is it (?:going to )?(?:rain|snow|hot|cold|sunny|cloudy|windy|humid)|"
                            r"how (?:hot|cold|warm|humid|windy)|what (?:should i |to )wear|sunrise|sunset|uv index|heat ?wave|cold ?snap|air quality|aqi|pm ?2\.?5|smog)\b", re.I)
WEATHER_WEAK = re.compile(r"\b(?:temperature|humidity|rain|snow|wind|storm|pollution|haze)\b", re.I)
TIME_WORD = re.compile(r"\b(?:today|tonight|tomorrow|this (?:morning|afternoon|evening|weekend|week)|next (?:week|\d+ days)|right now|currently|outside|at the moment)\b", re.I)
CLIMATE = re.compile(r"\b(?:is (?:this|it|that|the weather) (?:normal|usual|typical|unusual)|unusually (?:hot|cold|warm|cool|wet|dry)|"
                     r"(?:hotter|colder|warmer|cooler|wetter|drier) than (?:usual|normal|average|last year)|compared (?:to|with) (?:the )?(?:average|normal|usual)|climate normals?)\b", re.I)
FIND_VERB = re.compile(r"^\s*(?:find|show|list|search|get|any|is there|are there|got any|recommend|where(?:'s| is| are| can| do))\b", re.I)
NEAR_WORD = re.compile(r"\b(?:nearest|closest|nearby|near(?: me| here| by)?|around(?: me| here)?|close to|within|walking distance|open now|where (?:can|do) i (?:find|get|buy)|any)\b", re.I)
TRIP = re.compile(r"\b(?:itinerary|trip|vacation|holiday|packing list|(?:what|where) (?:should i|to) (?:pack|do|see|visit)|things to do|travel(?:l?ing)? to|visit(?:ing)?|plan (?:a |my )?(?:\d+[- ]day |weekend )?(?:trip|visit|holiday))\b", re.I)
WHERE = re.compile(r"^\s*(?:where(?:'s| is| are| exactly is)|what(?:'s| is) the (?:location|address|coordinates?|gps (?:position|location)) (?:of|for)|(?:coordinates|location|address|gps) (?:of|for)|show me where)\s+(.+?)[?.!\s]*$", re.I)
WHERE_NOT = re.compile(r"\b(?:installed|stored|defined|declared|used|called|saved|running|hosted|deployed|located in (?:my|the) (?:code|file|repo|project)|config|folder|directory|file|path|module|package)\b", re.I)
DISTANCE = [re.compile(r"\b(?:how far|distance|how many (?:km|kilometers|kilometres|miles))\b.*?\bfrom\s+(.+?)\s+to\s+(.+?)[?.!\s]*$", re.I),
            re.compile(r"\b(?:how far|distance)\b.*?\bbetween\s+(.+?)\s+and\s+(.+?)[?.!\s]*$", re.I),
            re.compile(r"\bhow far is\s+(.+?)\s+from\s+(.+?)[?.!\s]*$", re.I)]
HERE_WORDS = {"me", "here", "my location", "my area", "my place", "home", "my city", "where i am", "my position"}
STOPS = set("""today tonight tomorrow yesterday now this next on for please thanks thank and or then but if so because when while is are was were will be
should can could would do does did how what why who which with without at in to from by the a an my me us we i you it there here weather forecast
morning evening afternoon weekend week month later soon monday tuesday wednesday thursday friday saturday sunday right currently outside""".split())
LINKERS = {"de", "of", "the", "al", "el", "la", "le", "da", "do", "di", "van", "von", "bin", "du", "des", "del", "and", "upon", "on", "in", "-"}
ABBR = {"st", "mt", "ft", "dr", "pt", "saint"}
PREPS = ("in", "at", "for", "near", "around", "of", "to", "from", "by", "outside", "within", "close")
UNIT_AFTER = re.compile(r"^(?:days?|hours?|hrs?|km|kilomet\w+|mi|miles?|m|meters?|metres?|min|minutes?|weeks?|nights?|d)\b", re.I)
COORD_AT = re.compile(r"@\s*(-?\d{1,2}(?:\.\d+)?)\s*,\s*(-?\d{1,3}(?:\.\d+)?)")
COMMANDS = {"weather": "weather", "forecast": "weather", "aqi": "weather", "air": "weather", "climate": "climate", "where": "where", "find": "where",
            "near": "nearby", "nearby": "nearby", "distance": "distance", "trip": "trip"}


def split_coords(text):
    """'Dhaka @23.81,90.41' -> ('Dhaka', (23.81, 90.41)). Coordinates are only taken when valid."""
    m = COORD_AT.search(text or "")
    if not m:
        return (text or "").strip(), None
    lat, lon = float(m.group(1)), float(m.group(2))
    rest = (text[:m.start()] + " " + text[m.end():]).strip(" ,;")
    return rest, ((lat, lon) if geomath.valid(lat, lon) else None)


def _clean(tok):
    return tok.strip("?!.;:()\"“”‘")


def phrase_after(text, preps=PREPS):
    """The place-like words that follow a preposition ('in New York City this weekend' -> 'New York City'). Last match wins."""
    toks = re.findall(r"\S+", text or "")
    best = ""
    for i, t in enumerate(toks):
        if _clean(t).lower().rstrip(",") not in preps:
            continue
        out = []
        for j, t2 in enumerate(toks[i + 1:i + 8]):
            core = _clean(t2).rstrip(",")
            low = core.lower()
            if not core:
                break
            if low in STOPS or (low in LINKERS and not out):
                break
            if core.isdigit():
                nxt = toks[i + 2 + j] if i + 2 + j < len(toks) else ""
                if not out or UNIT_AFTER.match(_clean(nxt) or "") or len(core) > 4:
                    break
            out.append(t2.rstrip("?!;:,") if low in ABBR else _clean(t2))
            if t2[-1:] in "?!;" or (t2[-1:] == "." and low not in ABBR):
                break
        ph = " ".join(out).strip(" ,")
        while ph and ph.split()[-1].lower() in LINKERS:
            ph = " ".join(ph.split()[:-1]).strip(" ,")
        if ph:
            best = ph
    return best


def _is_here(ph):
    return not ph or ph.lower().strip() in HERE_WORDS


def _days(text, default=None):
    m = re.search(r"\b(\d{1,2})[- ]?(?:day|days|d)\b", text or "", re.I) or re.search(r"\bnext (\d{1,2}) days\b", text or "", re.I)
    if m:
        return max(1, min(int(m.group(1)), 16))
    low = (text or "").lower()
    if re.search(r"\b(?:week|7[- ]day|weekend)\b", low):
        return 7
    if "tomorrow" in low:
        return 2
    return default


def _when(text):
    low = (text or "").lower()
    for w in ("tonight", "tomorrow", "weekend", "today"):
        if w in low:
            return w
    return ""


def parse_message(text, have_default=False):
    """What does a plain chat message ask for? None when it is not clearly about weather or places.
    Deliberately conservative: coding and writing requests are never treated as lookups."""
    t = (text or "").strip()
    if not t or len(t) > 1200 or t.startswith("/") or CODE_HINT.search(t) or CREATIVE.match(t):
        return None
    for rx in DISTANCE:
        m = rx.search(t)
        if m:
            a, b = (m.group(1), m.group(2))
            if a.lower() != b.lower() and len(a) < 80 and len(b) < 80:
                return {"kind": "distance", "place": a.strip(" ,"), "place2": b.strip(" ,"), "source": "auto"}
    kind_word = osm.kind_from_text(t)
    ph = phrase_after(t)
    if TRIP.search(t) and (ph or (have_default and re.search(r"\b(?:pack|wear)\b", t, re.I))):
        return {"kind": "trip", "place": ph, "days": _days(t, 3), "source": "auto"}
    if kind_word and (NEAR_WORD.search(t) or FIND_VERB.match(t) or (ph and len(t.split()) <= 5)) and not WEATHER_STRONG.search(t):
        return {"kind": "nearby", "thing": kind_word, "place": "" if _is_here(ph) else ph, "source": "auto"}
    if CLIMATE.search(t):
        return {"kind": "climate", "place": "" if _is_here(ph) else ph, "days": _days(t, 5), "source": "auto"}
    strong = bool(WEATHER_STRONG.search(t))
    weak = bool(WEATHER_WEAK.search(t)) and (bool(TIME_WORD.search(t)) or bool(ph))
    if strong or weak:
        if not strong and re.search(r"\b(?:rain|snow|wind|storm)\b", t, re.I) and not (TIME_WORD.search(t) or ph):
            return None
        focus = "aqi" if re.search(r"\b(?:air quality|aqi|pm ?2\.?5|smog|pollution|haze)\b", t, re.I) else ""
        return {"kind": "weather", "place": "" if _is_here(ph) else ph, "days": _days(t, None), "when": _when(t), "focus": focus, "source": "auto"}
    m = WHERE.match(t)
    if m and not WHERE_NOT.search(t):
        target = re.sub(r"^(?:the|a|an)\s+", "", m.group(1).strip(), flags=re.I)
        caps = re.findall(r"\b[A-Z][\w'’-]+", m.group(1))
        if (caps or "," in target or geomath.parse_coords(target)) and len(target) < 100 and not re.match(r"^(?:my|this|that|it|there|i|you)\b", target, re.I):
            return {"kind": "where", "place": target, "source": "auto"}
    return None


def parse_command(cmd, args):
    """/weather Dhaka 5 days · /near pharmacy near Gulshan · /distance Paris to Rome · /trip Kyoto 4 days · /where Eiffel Tower"""
    cmd = (cmd or "").lower().lstrip("/")
    kind = COMMANDS.get(cmd)
    if not kind:
        raise WebError("Unknown command.")
    args = (args or "").strip()
    rest, coords = split_coords(args)
    days = _days(rest, None) if re.search(r"\b\d{1,2}[- ]?(?:days?|d)\b", rest, re.I) else None
    rest = re.sub(r"\b(?:for |next )?\d{1,2}[- ]?(?:days?|d)\b", " ", rest, flags=re.I).strip(" ,")
    base = {"kind": kind, "days": days, "source": "command", "coords": coords, "focus": "aqi" if cmd in ("aqi", "air") else ""}
    if kind == "distance":
        parts = re.split(r"\s+(?:to|and|->|→|vs\.?)\s+|\s*(?:->|→)\s*", rest, maxsplit=1, flags=re.I)
        parts = [p.strip(" ,") for p in parts if p.strip(" ,")]
        if len(parts) < 2:
            raise WebError("Give two places: /distance Paris to Rome")
        return {**base, "place": parts[0], "place2": parts[1], "coords": None}
    if kind == "nearby":
        tag = re.search(r"\bkind:(\w+)", rest)
        thing = tag.group(1) if tag and tag.group(1) in osm.KINDS else osm.kind_from_text(rest)
        rest = re.sub(r"\bkind:\w+", " ", rest).strip(" ,")
        if not thing:
            raise WebError("Say what to look for, for example: /near pharmacy, /near cafe in Lisbon. Categories: "
                           + ", ".join(k for k in osm.KINDS) + ".")
        ph = phrase_after(rest, ("near", "in", "around", "at", "by", "close"))
        return {**base, "thing": thing, "place": "" if _is_here(ph) else ph}
    ph = rest
    if kind in ("weather", "climate", "trip"):
        m = re.match(r"^(?:in|for|at|near)\s+(.*)$", rest, re.I)
        ph = m.group(1) if m else rest
    return {**base, "place": "" if _is_here(ph) else ph.strip(" ,")}


# ---------------------------------------------------------------- resolving places
def resolve(spec, default=None, coords=None, lang="en", osm_ok=True):
    """Text (or coordinates) -> {'name','lat','lon',...}. With no text, the saved place is used."""
    spec = (spec or "").strip()
    if coords:
        if spec:
            return {"name": spec, "display_name": spec, "lat": coords[0], "lon": coords[1], "source": "given"}
        return osm.reverse(coords[0], coords[1], lang=lang) if osm_ok else {"name": f"{coords[0]:.4f}, {coords[1]:.4f}", "lat": coords[0], "lon": coords[1]}
    if not spec:
        if default and geomath.valid(default.get("lat"), default.get("lon")):
            return {**default, "name": str(default.get("name") or "your place"), "source": "saved"}
        raise NeedPlace("No place given. Name one (for example “weather in Lisbon”) or save yours in the Places tab.")
    c = geomath.parse_coords(spec)
    if c:
        return resolve("", None, c, lang, osm_ok)
    first = None
    if osm_ok:
        try:
            res = osm.search(spec, limit=3, lang=lang)
            if res:
                return {**res[0], "alternatives": [x["display_name"] for x in res[1:3]]}
        except WebError as e:
            first = e
    try:
        res = openmeteo.geocode(spec, count=3, lang=lang)
        if res:
            return {**res[0], "alternatives": [x["display_name"] for x in res[1:3]]}
    except WebError as e:
        first = first or e
    if first:
        raise first
    raise NotFound(f"I could not find a place called “{spec[:60]}”. Try adding the country or a nearby city.")


# ---------------------------------------------------------------- formatting
def _n(x, d=0, unit=""):
    if x is None:
        return "n/a"
    s = f"{x:.{d}f}"
    if "." in s:
        s = s.rstrip("0").rstrip(".")
    return s + unit


def _label(place):
    return place.get("display_name") or place.get("name") or "the place"


def _short_label(place):
    parts = [p.strip() for p in (place.get("display_name") or place.get("name") or "").split(",") if p.strip()]
    if len(parts) > 3:
        parts = [parts[0]] + parts[-2:]
    return ", ".join(dict.fromkeys(parts)) or "the place"


def _utc_off(s):
    h, m = divmod(abs(int(s)) // 60, 60)
    return f"UTC{'-' if s < 0 else '+'}{h}" + (f":{m:02d}" if m else "")


def weather_text(place, fc, air=None, clim=None, focus="", days=None, when=""):
    u, now, pl = fc["units"], fc["now"], fc["place"]
    T = u["temp"]
    lines = [f"Place: {_short_label(place)} ({place['lat']:.3f}, {place['lon']:.3f}); timezone {pl.get('timezone') or 'unknown'} ({_utc_off(pl.get('utc_offset_s') or 0)})"
             + (f"; elevation {_n(pl.get('elevation'))} m" if pl.get("elevation") is not None else "")]
    if place.get("alternatives"):
        lines.append("Other places with that name (not used): " + "; ".join(place["alternatives"]))
    t = (now.get("time") or "")[11:16]
    wind = f"{_n(now.get('wind'), 0)} {u['wind']}" + (f" from {now['wind_dir_txt']}" if now.get("wind_dir_txt") else "") + (f" (gusts {_n(now.get('gust'), 0)})" if now.get("gust") is not None else "")
    lines.append(f"Now (local {t}): {_n(now.get('temp'), 1, T)}, feels like {_n(now.get('feels'), 1, T)}, {now.get('text', '').lower()}, humidity {_n(now.get('humidity'), 0, '%')}, "
                 f"wind {wind}, cloud cover {_n(now.get('cloud'), 0, '%')}, rain now {_n(now.get('precip'), 1)} {u['precip']}, pressure {_n(now.get('pressure'), 0)} {u['pressure']}")
    if air and (air.get("us_aqi") is not None or air.get("pm2_5") is not None):
        lines.append(f"Air quality now: US AQI {_n(air.get('us_aqi'))} ({air.get('category') or 'n/a'}), PM2.5 {_n(air.get('pm2_5'), 1)} {air.get('unit', '')}, PM10 {_n(air.get('pm10'), 1)}, "
                     f"ozone {_n(air.get('ozone'), 0)}" + (f". {air['advice']}" if air.get("advice") else ""))
    hours = fc["hourly"][::3][:8]
    if hours:
        lines.append("Next 24 hours (every 3 h): " + "; ".join(f"{h['time'][11:16]} {_n(h['temp'], 0, T)} {h['text'].lower()}" + (f", rain {_n(h['pop'], 0, '%')}" if h.get("pop") is not None else "") for h in hours))
    n = days or len(fc["daily"])
    lines.append("Daily outlook:")
    for d in fc["daily"][:n]:
        try:
            nice = dt.date.fromisoformat(d["date"]).strftime("%a %d %b")
        except ValueError:
            nice = d["date"]
        lines.append(f"- {nice}: {d['text']}, {_n(d['tmin'], 0)}–{_n(d['tmax'], 0, T)}, rain chance {_n(d['pop'], 0, '%')} ({_n(d['rain'], 1)} {u['precip']}), "
                     f"wind up to {_n(d['wind_max'], 0)} {u['wind']}, UV {_n(d['uv'], 0)}, sun {d['sunrise']}–{d['sunset']}")
    if clim:
        lines.append(f"Compared with normal ({clim['years']} average for the same dates): highs {_n(clim['tmax'], 1)} vs {_n(clim['tmax_normal'], 1)}{clim['temp']} "
                     f"({'+' if clim['tmax_diff'] >= 0 else ''}{_n(clim['tmax_diff'], 1)}), lows {_n(clim['tmin'], 1)} vs {_n(clim['tmin_normal'], 1)}{clim['temp']} "
                     f"({'+' if clim['tmin_diff'] >= 0 else ''}{_n(clim['tmin_diff'], 1)}): {clim['label']} over the next {clim['days']} day(s).")
    if when:
        lines.append(f"The question is about: {when}.")
    return "\n".join(lines)


def nearby_text(place, kind, items, radius, units="metric"):
    label = osm.KINDS[kind][0].lower()
    head = f"{osm.KINDS[kind][0]} within {geomath.fmt_dist(radius, units)} of {_short_label(place)} (OpenStreetMap, nearest first)."
    if not items:
        return head + "\nNone found in OpenStreetMap within that distance. That can mean the area is not well mapped, so say it may be incomplete."
    rows = []
    for i, p in enumerate(items, 1):
        bits = [f"{geomath.fmt_dist(p['distance_m'], units)} {p['compass']}"]
        for k in ("address", "cuisine", "opening_hours", "phone", "website"):
            if p.get(k):
                bits.append(f"hours {p[k]}" if k == "opening_hours" else p[k])
        rows.append(f"{i}. {p['name']} — " + " · ".join(bits))
    return head + "\n" + "\n".join(rows) + f"\nOpenStreetMap is made by volunteers: {label} may be missing, closed or moved, and opening hours are only what contributors entered."


def where_text(place, fc=None, units="metric"):
    lines = [f"Name: {_label(place)}", f"Coordinates: {place['lat']:.5f}, {place['lon']:.5f}"]
    for k, lab in (("kind", "OSM type"), ("country", "Country"), ("state", "Region"), ("postcode", "Postcode"), ("population", "Population"),
                   ("wikipedia", "Wikipedia"), ("website", "Website"), ("osm_url", "OpenStreetMap")):
        if place.get(k):
            lines.append(f"{lab}: {place[k]}")
    if place.get("alternatives"):
        lines.append("Other matches (not used): " + "; ".join(place["alternatives"]))
    if fc:
        pl = fc["place"]
        lines.append(f"Local time: {(fc['now'].get('time') or '')[11:16]} ({pl.get('timezone') or ''}, {_utc_off(pl.get('utc_offset_s') or 0)}); elevation {_n(pl.get('elevation'))} m; "
                     f"weather now: {_n(fc['now'].get('temp'), 0, fc['units']['temp'])}, {fc['now'].get('text', '').lower()}")
    return "\n".join(lines)


def distance_text(a, b, d, units="metric"):
    return (f"From {_short_label(a)} ({a['lat']:.3f}, {a['lon']:.3f}) to {_short_label(b)} ({b['lat']:.3f}, {b['lon']:.3f}):\n"
            f"Straight-line distance: {d['km']} km ({d['miles']} mi), direction {d['compass']} ({d['bearing']}° from north).\n"
            "This is the great-circle distance, not a driving or walking route; real routes are longer and travel time depends on the mode and roads.")


def wrap(body, sources):
    now = dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    return (f'<live-data sources="{", ".join(sources)}" fetched="{now}">\n'
            "Use this data to answer the question above. Quote numbers with their units exactly as given. If something you need is not here, say so; "
            "do not invent places, opening hours or values. End with one short line naming the source.\n"
            f"{body}\n</live-data>")


# ---------------------------------------------------------------- doing it
def _opt(fn, notes, what):
    try:
        return fn()
    except WebError as e:
        notes.append(f"{what} unavailable: {e}")
        return None


def handle(req, osm_ok=True, weather_ok=True):
    """req: {message | cmd+args, default, units, days, radius, climate, lang, auto}. Returns a dict with 'used'; see below.
    used=False: nothing to look up. used=True: 'text' (for the model), 'card' (for the Places tab), 'notes', 'sources'."""
    default = req.get("default") if isinstance(req.get("default"), dict) else None
    units = openmeteo.units_ok(req.get("units"))
    lang = str(req.get("lang") or "en")[:5]
    radius = max(200, min(int(req.get("radius") or 1000), 10000))
    if req.get("cmd"):
        intent = parse_command(req["cmd"], req.get("args") or "")
    else:
        intent = parse_message(str(req.get("message") or ""), bool(default))
        if not intent:
            return {"used": False}
    kind = intent["kind"]
    if kind in ("weather", "climate", "trip") and not weather_ok:
        raise WebError("Weather lookups are turned off (--no-geo or --no-weather).")
    if kind in ("nearby", "distance", "where") and not osm_ok:
        raise WebError("Place lookups are turned off (--no-geo or --no-osm).")
    days = intent.get("days") or max(1, min(int(req.get("days") or 7), 16))
    notes, sources = [], []
    place = resolve(intent.get("place"), default, intent.get("coords"), lang, osm_ok)
    card = {"type": kind, "place": place, "units": units}

    if kind in ("weather", "climate", "trip"):
        sources.append("Open-Meteo")
        with ThreadPoolExecutor(max_workers=2) as ex:
            f_fc = ex.submit(openmeteo.forecast, place["lat"], place["lon"], units, max(days, 7) if kind == "climate" else days)
            f_aq = ex.submit(lambda: _opt(lambda: openmeteo.air_quality(place["lat"], place["lon"]), notes, "Air quality"))
            fc, air = f_fc.result(), f_aq.result()
        clim = None
        if kind in ("climate", "trip") or req.get("climate"):
            clim = _opt(lambda: openmeteo.climate_check(place["lat"], place["lon"], fc["daily"], units), notes, "Climate comparison")
        card.update(weather=fc, air=air, climate=clim, days=days, focus=intent.get("focus") or "", when=intent.get("when") or "")
        body = weather_text(place, fc, air, clim, intent.get("focus") or "", days, intent.get("when") or "")
        if kind == "trip":
            sources.append("OpenStreetMap")
            r_trip = max(radius, 3000)
            with ThreadPoolExecutor(max_workers=2) as ex:
                f_a = ex.submit(lambda: _opt(lambda: osm.nearby(place["lat"], place["lon"], "attraction", r_trip, 8), notes, "Sights"))
                f_f = ex.submit(lambda: _opt(lambda: osm.nearby(place["lat"], place["lon"], "restaurant", min(r_trip, 1500), 6), notes, "Restaurants"))
                sights, food = f_a.result() or [], f_f.result() or []
            card.update(sights=sights, food=food, radius=r_trip)
            if sights:
                body += "\n\n" + nearby_text(place, "attraction", sights, r_trip, units)
            if food:
                body += "\n\n" + nearby_text(place, "restaurant", food, min(r_trip, 1500), units)
            body += f"\n\nTrip length: {days} day(s). Build the plan around the weather above (put outdoor items on the better days) and mention air quality if it is poor."
    elif kind == "nearby":
        sources.append("OpenStreetMap")
        thing = intent["thing"]
        items = osm.nearby(place["lat"], place["lon"], thing, radius, 12)
        card.update(kind=thing, label=osm.KINDS[thing][0], emoji=osm.KINDS[thing][1], radius=radius, items=items)
        body = f"Place: {_short_label(place)} ({place['lat']:.3f}, {place['lon']:.3f})\n" + nearby_text(place, thing, items, radius, units)
    elif kind == "where":
        sources.append("OpenStreetMap")
        fc = None
        if weather_ok:
            fc = _opt(lambda: openmeteo.forecast(place["lat"], place["lon"], units, 1), notes, "Local time and weather")
            if fc:
                sources.append("Open-Meteo")
        card.update(weather=fc)
        body = where_text(place, fc, units)
    else:   # distance
        sources.append("OpenStreetMap")
        place2 = resolve(intent.get("place2"), None, None, lang, osm_ok)
        d = osm.between(place, place2, units)
        card.update(place2=place2, distance=d)
        body = distance_text(place, place2, d, units)
    if notes:
        body += "\nNote: " + " ".join(notes)
    return {"used": True, "kind": kind, "intent": {k: v for k, v in intent.items() if k != "coords"}, "text": wrap(body, sources), "card": card,
            "notes": notes, "sources": sources, "attribution": [openmeteo.ATTRIBUTION if "Open-Meteo" in sources else "", osm.ATTRIBUTION if "OpenStreetMap" in sources else ""]}
