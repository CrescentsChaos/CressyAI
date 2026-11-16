"""Small geometry helpers shared by the Open-Meteo and OpenStreetMap modules. Standard library only."""
import math

EARTH_M = 6_371_008.8
POINTS = ["N", "NNE", "NE", "ENE", "E", "ESE", "SE", "SSE", "S", "SSW", "SW", "WSW", "W", "WNW", "NW", "NNW"]


def valid(lat, lon):
    return isinstance(lat, (int, float)) and isinstance(lon, (int, float)) and -90 <= lat <= 90 and -180 <= lon <= 180


def haversine_m(lat1, lon1, lat2, lon2):
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp, dl = p2 - p1, math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * EARTH_M * math.asin(min(1.0, math.sqrt(a)))


def bearing(lat1, lon1, lat2, lon2):
    """Initial compass bearing from point 1 to point 2, 0-360 degrees clockwise from north."""
    p1, p2, dl = math.radians(lat1), math.radians(lat2), math.radians(lon2 - lon1)
    y = math.sin(dl) * math.cos(p2)
    x = math.cos(p1) * math.sin(p2) - math.sin(p1) * math.cos(p2) * math.cos(dl)
    return (math.degrees(math.atan2(y, x)) + 360) % 360


def compass(deg):
    if deg is None:
        return ""
    return POINTS[int((deg % 360) / 22.5 + 0.5) % 16]


def fmt_dist(m, units="metric"):
    """'450 m', '2.3 km' (metric) or '0.3 mi', '1.4 mi' (imperial)."""
    if units == "imperial":
        mi = m / 1609.344
        return f"{round(m * 3.28084 / 10) * 10:.0f} ft" if mi < 0.1 else f"{mi:.1f} mi"
    if m < 1000:
        return f"{round(m / 10) * 10:.0f} m" if m >= 100 else f"{m:.0f} m"
    return f"{m / 1000:.1f} km" if m < 100_000 else f"{m / 1000:.0f} km"


def parse_coords(text):
    """'23.8103, 90.4125' or '23.8103 90.4125' -> (lat, lon), else None."""
    import re
    m = re.fullmatch(r"\s*(-?\d{1,2}(?:\.\d+)?)\s*[,;\s]\s*(-?\d{1,3}(?:\.\d+)?)\s*", text or "")
    if not m:
        return None
    lat, lon = float(m.group(1)), float(m.group(2))
    return (lat, lon) if valid(lat, lon) else None
