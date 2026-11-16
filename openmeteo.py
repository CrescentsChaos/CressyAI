"""Open-Meteo for CressyAI: forecast, air quality and a "is this normal?" climate comparison. Standard library only.

Open-Meteo needs no API key. Its free tier is for non-commercial use and the data is CC BY 4.0, so results are shown
with attribution. Every request goes to one of the four fixed hosts below (change them only for a self-hosted copy).
Coordinates are rounded to two decimals (about 1 km) before they leave this machine.
"""
import datetime as dt
import json, socket, time, urllib.error, urllib.parse, urllib.request

import geomath
from websearch import WebError

UA = "CressyAI/1.0 (local assistant) python-urllib"
FORECAST_URL = "https://api.open-meteo.com/v1/forecast"
GEOCODE_URL = "https://geocoding-api.open-meteo.com/v1/search"
AIR_URL = "https://air-quality-api.open-meteo.com/v1/air-quality"
ARCHIVE_URL = "https://archive-api.open-meteo.com/v1/archive"
ATTRIBUTION = "Weather data by Open-Meteo.com (CC BY 4.0)"

# WMO weather interpretation codes -> (text, day emoji, night emoji)
WMO = {0: ("Clear sky", "☀️", "🌙"), 1: ("Mainly clear", "🌤️", "🌙"), 2: ("Partly cloudy", "⛅", "☁️"), 3: ("Overcast", "☁️", "☁️"),
       45: ("Fog", "🌫️", "🌫️"), 48: ("Freezing fog", "🌫️", "🌫️"),
       51: ("Light drizzle", "🌦️", "🌧️"), 53: ("Drizzle", "🌦️", "🌧️"), 55: ("Heavy drizzle", "🌧️", "🌧️"),
       56: ("Freezing drizzle", "🌧️", "🌧️"), 57: ("Heavy freezing drizzle", "🌧️", "🌧️"),
       61: ("Light rain", "🌦️", "🌧️"), 63: ("Rain", "🌧️", "🌧️"), 65: ("Heavy rain", "🌧️", "🌧️"),
       66: ("Freezing rain", "🌧️", "🌧️"), 67: ("Heavy freezing rain", "🌧️", "🌧️"),
       71: ("Light snow", "🌨️", "🌨️"), 73: ("Snow", "🌨️", "🌨️"), 75: ("Heavy snow", "❄️", "❄️"), 77: ("Snow grains", "🌨️", "🌨️"),
       80: ("Light rain showers", "🌦️", "🌧️"), 81: ("Rain showers", "🌧️", "🌧️"), 82: ("Violent rain showers", "⛈️", "⛈️"),
       85: ("Snow showers", "🌨️", "🌨️"), 86: ("Heavy snow showers", "❄️", "❄️"),
       95: ("Thunderstorm", "⛈️", "⛈️"), 96: ("Thunderstorm with hail", "⛈️", "⛈️"), 99: ("Severe thunderstorm with hail", "⛈️", "⛈️")}
US_AQI = [(50, "Good", "Air quality is fine for everyone."),
          (100, "Moderate", "Acceptable; unusually sensitive people may notice it."),
          (150, "Unhealthy for sensitive groups", "Children, older adults and people with asthma or heart/lung conditions should limit long outdoor effort."),
          (200, "Unhealthy", "Everyone may feel effects; limit prolonged outdoor exertion, consider a mask outdoors."),
          (300, "Very unhealthy", "Avoid outdoor exertion; keep windows closed."),
          (10**9, "Hazardous", "Stay indoors if possible; emergency conditions.")]

_CACHE, _CACHE_MAX = {}, 128


def _cached(key, ttl, fn):
    hit = _CACHE.get(key)
    if hit and time.time() - hit[0] < ttl:
        return json.loads(json.dumps(hit[1]))
    val = fn()
    if len(_CACHE) >= _CACHE_MAX:
        _CACHE.pop(min(_CACHE, key=lambda k: _CACHE[k][0]), None)
    _CACHE[key] = (time.time(), json.loads(json.dumps(val)))
    return val


def units_ok(u):
    return "imperial" if str(u).lower() == "imperial" else "metric"


def _unit_params(units):
    if units == "imperial":
        return {"temperature_unit": "fahrenheit", "wind_speed_unit": "mph", "precipitation_unit": "inch"}
    return {}


def _get(base, params, timeout=12):
    url = base + "?" + urllib.parse.urlencode(params)
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "application/json", "Accept-Encoding": "identity"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            data = json.loads(r.read(6_000_000).decode("utf-8", "replace"))
    except urllib.error.HTTPError as e:
        reason = ""
        try:
            reason = str(json.loads(e.read(20000).decode("utf-8", "replace")).get("reason") or "")[:200]
        except Exception:
            pass
        finally:
            e.close()
        if e.code == 429:
            raise WebError("Open-Meteo says the daily or per-minute limit was reached (429). Try again later.")
        raise WebError(f"Open-Meteo answered with an error ({e.code}){': ' + reason if reason else ''}.")
    except urllib.error.URLError as e:
        raise WebError(f"Cannot reach Open-Meteo. Check your internet connection. ({e.reason})")
    except (TimeoutError, socket.timeout):
        raise WebError("Open-Meteo took too long to answer.")
    except (ValueError, UnicodeError):
        raise WebError("Open-Meteo did not return valid data.")
    except OSError as e:
        raise WebError(f"Cannot reach Open-Meteo: {e}")
    if isinstance(data, dict) and data.get("error"):
        raise WebError("Open-Meteo error: " + str(data.get("reason", "unknown"))[:200])
    return data


def r2(x):
    return round(float(x), 2)


def describe(code, is_day=True):
    t, d, n = WMO.get(int(code) if code is not None else -1, ("Unknown", "❔", "❔"))
    return t, (d if is_day else n)


def geocode(name, count=5, lang="en"):
    """Open-Meteo's place-name search. Handy as a fallback when Nominatim is busy."""
    name = " ".join((name or "").split())[:100]
    if not name:
        raise WebError("There is nothing to look up.")
    lang = lang if lang.isalpha() and 2 <= len(lang) <= 3 else "en"

    def go():
        data = _get(GEOCODE_URL, {"name": name, "count": max(1, min(int(count), 10)), "language": lang, "format": "json"})
        out = []
        for p in data.get("results") or []:
            lat, lon = p.get("latitude"), p.get("longitude")
            if not geomath.valid(lat, lon):
                continue
            bits = [p.get("name") or "", p.get("admin1") or "", p.get("country") or ""]
            label = ", ".join(dict.fromkeys(b for b in bits if b))
            out.append({"name": p.get("name") or label, "display_name": label, "lat": lat, "lon": lon, "country": p.get("country") or "",
                        "country_code": (p.get("country_code") or "").upper(), "timezone": p.get("timezone") or "",
                        "elevation": p.get("elevation"), "population": p.get("population"), "kind": p.get("feature_code") or "",
                        "source": "open-meteo"})
        return out
    return _cached(("geo", name.lower(), count, lang), 86400, go)


def forecast(lat, lon, units="metric", days=7):
    """Current conditions, the next 24 hours and a daily outlook, normalised for display and for the model."""
    if not geomath.valid(lat, lon):
        raise WebError("Those coordinates are not valid.")
    units, days = units_ok(units), max(1, min(int(days), 16))
    lat, lon = r2(lat), r2(lon)

    def go():
        d = _get(FORECAST_URL, {
            "latitude": lat, "longitude": lon, "timezone": "auto", "forecast_days": days, **_unit_params(units),
            "current": "temperature_2m,relative_humidity_2m,apparent_temperature,is_day,precipitation,weather_code,cloud_cover,pressure_msl,wind_speed_10m,wind_direction_10m,wind_gusts_10m",
            "hourly": "temperature_2m,precipitation_probability,weather_code,is_day",
            "daily": "weather_code,temperature_2m_max,temperature_2m_min,sunrise,sunset,uv_index_max,precipitation_sum,precipitation_probability_max,wind_speed_10m_max"})
        return _normalise_forecast(d, units)
    return _cached(("fc", lat, lon, units, days), 600, go)


def _col(block, key, i):
    v = (block.get(key) or [])
    return v[i] if i < len(v) else None


def _normalise_forecast(d, units):
    cur, hourly, daily = d.get("current") or {}, d.get("hourly") or {}, d.get("daily") or {}
    cu = d.get("current_units") or {}
    day = bool(cur.get("is_day", 1))
    text, emoji = describe(cur.get("weather_code"), day)
    out = {
        "place": {"lat": d.get("latitude"), "lon": d.get("longitude"), "elevation": d.get("elevation"), "timezone": d.get("timezone") or "",
                  "tz_abbr": d.get("timezone_abbreviation") or "", "utc_offset_s": d.get("utc_offset_seconds") or 0},
        "units": {"system": units, "temp": cu.get("temperature_2m") or ("°F" if units == "imperial" else "°C"),
                  "wind": cu.get("wind_speed_10m") or ("mph" if units == "imperial" else "km/h"),
                  "precip": cu.get("precipitation") or ("inch" if units == "imperial" else "mm"), "pressure": cu.get("pressure_msl") or "hPa"},
        "now": {"time": cur.get("time") or "", "temp": cur.get("temperature_2m"), "feels": cur.get("apparent_temperature"),
                "humidity": cur.get("relative_humidity_2m"), "precip": cur.get("precipitation"), "code": cur.get("weather_code"),
                "text": text, "emoji": emoji, "is_day": day, "cloud": cur.get("cloud_cover"), "pressure": cur.get("pressure_msl"),
                "wind": cur.get("wind_speed_10m"), "wind_dir": cur.get("wind_direction_10m"),
                "wind_dir_txt": geomath.compass(cur.get("wind_direction_10m")), "gust": cur.get("wind_gusts_10m")},
        "hourly": [], "daily": [],
    }
    times = hourly.get("time") or []
    start = 0
    now_h = (cur.get("time") or "")[:13]
    for i, t in enumerate(times):
        if t[:13] >= now_h:
            start = i
            break
    for i in range(start, min(start + 24, len(times))):
        code = _col(hourly, "weather_code", i)
        t_, e_ = describe(code, bool(_col(hourly, "is_day", i) if _col(hourly, "is_day", i) is not None else 1))
        out["hourly"].append({"time": times[i], "temp": _col(hourly, "temperature_2m", i), "pop": _col(hourly, "precipitation_probability", i),
                              "code": code, "text": t_, "emoji": e_})
    for i, date in enumerate(daily.get("time") or []):
        code = _col(daily, "weather_code", i)
        t_, e_ = describe(code, True)
        try:
            wd = dt.date.fromisoformat(date).strftime("%a")
        except ValueError:
            wd = ""
        out["daily"].append({"date": date, "weekday": wd, "code": code, "text": t_, "emoji": e_, "tmax": _col(daily, "temperature_2m_max", i),
                             "tmin": _col(daily, "temperature_2m_min", i), "rain": _col(daily, "precipitation_sum", i),
                             "pop": _col(daily, "precipitation_probability_max", i), "wind_max": _col(daily, "wind_speed_10m_max", i),
                             "uv": _col(daily, "uv_index_max", i), "sunrise": (_col(daily, "sunrise", i) or "")[11:16],
                             "sunset": (_col(daily, "sunset", i) or "")[11:16]})
    return out


def aqi_category(us_aqi):
    if us_aqi is None:
        return "", ""
    for top, name, advice in US_AQI:
        if us_aqi <= top:
            return name, advice
    return "", ""


def air_quality(lat, lon):
    if not geomath.valid(lat, lon):
        raise WebError("Those coordinates are not valid.")
    lat, lon = r2(lat), r2(lon)

    def go():
        d = _get(AIR_URL, {"latitude": lat, "longitude": lon, "timezone": "auto",
                           "current": "us_aqi,european_aqi,pm10,pm2_5,ozone,nitrogen_dioxide,sulphur_dioxide,carbon_monoxide,uv_index"})
        c = d.get("current") or {}
        cat, advice = aqi_category(c.get("us_aqi"))
        return {"time": c.get("time") or "", "us_aqi": c.get("us_aqi"), "eu_aqi": c.get("european_aqi"), "pm2_5": c.get("pm2_5"),
                "pm10": c.get("pm10"), "ozone": c.get("ozone"), "no2": c.get("nitrogen_dioxide"), "so2": c.get("sulphur_dioxide"),
                "co": c.get("carbon_monoxide"), "uv": c.get("uv_index"), "category": cat, "advice": advice,
                "unit": (d.get("current_units") or {}).get("pm2_5") or "μg/m³"}
    return _cached(("aq", lat, lon), 600, go)


def _norm_date(y, month, day):
    try:
        return dt.date(y, month, day)
    except ValueError:      # 29 February in a non-leap year
        return dt.date(y, month, 28)


def climate_check(lat, lon, daily, units="metric", years=10, window=3):
    """Compare the forecast highs/lows with the average for the same dates over the last `years` complete years."""
    if not geomath.valid(lat, lon):
        raise WebError("Those coordinates are not valid.")
    units, lat, lon = units_ok(units), r2(lat), r2(lon)
    days = [x for x in (daily or []) if x.get("tmax") is not None and x.get("tmin") is not None][:7]
    if not days:
        return None
    this_year = dt.date.fromisoformat(days[0]["date"]).year
    y1, y0 = this_year - 1, this_year - max(3, min(int(years), 30))

    def go():
        d = _get(ARCHIVE_URL, {"latitude": lat, "longitude": lon, "timezone": "auto", "start_date": f"{y0}-01-01", "end_date": f"{y1}-12-31",
                               "daily": "temperature_2m_max,temperature_2m_min", **_unit_params(units)}, timeout=25)
        dd = d.get("daily") or {}
        return {t: (a, b) for t, a, b in zip(dd.get("time") or [], dd.get("temperature_2m_max") or [], dd.get("temperature_2m_min") or [])}
    hist = _cached(("arc", lat, lon, units, y0, y1), 86400, go)
    hi, lo = [], []
    for x in days:
        d0 = dt.date.fromisoformat(x["date"])
        hv, lv = [], []
        for y in range(y0, y1 + 1):
            base = _norm_date(y, d0.month, d0.day)
            for k in range(-window, window + 1):
                row = hist.get((base + dt.timedelta(days=k)).isoformat())
                if row and row[0] is not None and row[1] is not None:
                    hv.append(row[0]); lv.append(row[1])
        if len(hv) < 10:
            continue
        hi.append((x["tmax"], sum(hv) / len(hv)))
        lo.append((x["tmin"], sum(lv) / len(lv)))
    if not hi:
        return None
    avg = lambda pairs, i: sum(p[i] for p in pairs) / len(pairs)
    dh, dl = avg(hi, 0) - avg(hi, 1), avg(lo, 0) - avg(lo, 1)
    scale = 1.8 if units == "imperial" else 1.0
    mean = (dh + dl) / 2 / scale     # in °C-equivalent
    label = ("much warmer than usual" if mean >= 4 else "warmer than usual" if mean >= 1.5 else "about normal" if mean > -1.5
             else "cooler than usual" if mean > -4 else "much cooler than usual")
    return {"years": f"{y0}-{y1}", "days": len(hi), "tmax": round(avg(hi, 0), 1), "tmax_normal": round(avg(hi, 1), 1), "tmax_diff": round(dh, 1),
            "tmin": round(avg(lo, 0), 1), "tmin_normal": round(avg(lo, 1), 1), "tmin_diff": round(dl, 1), "label": label,
            "temp": "°F" if units == "imperial" else "°C"}


def ping(timeout=6):
    """Cheap reachability check. Returns (ok, why)."""
    try:
        _get(GEOCODE_URL, {"name": "London", "count": 1, "format": "json"}, timeout=timeout)
        return True, ""
    except WebError as e:
        return False, str(e)
