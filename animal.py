"""Animal profiles for CressyAI: iNaturalist + Wikipedia merged into one record, and multi-tool photo identification.
Standard library only; uses inaturalist.py and wikipedia.py, so only api.inaturalist.org and <lang>.wikipedia.org are contacted."""
import re, urllib.parse
from concurrent.futures import ThreadPoolExecutor

import inaturalist as inat
import wikipedia
from websearch import WebError

WANT = ("description", "appearance", "characteristics", "habitat", "distribution", "range", "diet", "feeding", "behaviour",
        "behavior", "ecology", "reproduction", "breeding", "life cycle", "conservation", "threats")


def _safe(fn, *a, **k):
    try:
        return fn(*a, **k)
    except WebError:
        return None


def _wiki_page(title, lang):
    """Intro, thumbnail and the most useful sections of one Wikipedia article (None if it cannot be read)."""
    try:
        pages = wikipedia._api(wikipedia.lang_ok(lang), {"action": "query", "prop": "extracts|pageimages|info", "explaintext": 1, "exchars": 9000,
                               "piprop": "thumbnail", "pithumbsize": 900, "inprop": "url", "titles": title, "redirects": 1}).get("query", {}).get("pages") or []
    except WebError:
        return None
    p = next((x for x in pages if x.get("extract")), None)
    if not p:
        return None
    intro, secs, cur, skip = [], {}, None, False
    for ln in p["extract"].replace("\r", "").split("\n"):
        m = re.match(r"^(={2,6})\s*(.+?)\s*\1\s*$", ln)
        if m:
            cur, skip = m.group(2).strip(), m.group(2).strip().lower() in wikipedia.TAIL
            if not skip:
                secs.setdefault(cur, [])
        elif ln.strip() and not skip:
            (secs[cur] if cur else intro).append(ln.strip())
    picked = []
    for key in WANT:
        h = next((h for h in secs if key in h.lower() and secs[h] and h not in [x["title"] for x in picked]), None)
        if h:
            txt = " ".join(secs[h])
            picked.append({"title": h, "text": txt if len(txt) <= 700 else txt[:700].rsplit(" ", 1)[0] + "…"})
    return {"title": p.get("title", title), "url": p.get("fullurl", ""), "thumb": (p.get("thumbnail") or {}).get("source", ""),
            "intro": " ".join(intro), "sections": picked[:5]}


def profile(taxon_id=None, query="", locale="en", wiki_lang="en", use_wiki=True, lat=None, lng=None, radius=50, place_id=None):
    """One animal, everything known: iNaturalist taxonomy/photos/status/season + Wikipedia text, merged."""
    if not taxon_id:
        rows = inat.search(query, limit=6, locale=locale)["results"]
        if not rows:
            raise WebError("No species found for that name.")
        taxon_id = next((r for r in rows if r["rank"] in ("species", "subspecies")), rows[0])["id"]
    t = inat.taxon(taxon_id, locale)
    title = urllib.parse.unquote(t["wikipedia"].rsplit("/", 1)[-1]).replace("_", " ") if t.get("wikipedia") else t["name"]
    with ThreadPoolExecutor(2) as ex:
        fw = ex.submit(lambda: _wiki_page(title, wiki_lang) if use_wiki else None)
        fs = ex.submit(_safe, inat.season, taxon_id, lat, lng, radius, place_id)
        w, s = fw.result(), fs.result()
    if use_wiki and not w and title != t["name"]:
        w = _wiki_page(t["name"], wiki_lang)
    photos = [p for p in t["photos"] if p["url"]]
    if w and w["thumb"]:
        photos.insert(0, {"url": w["thumb"], "credit": "Wikipedia / Wikimedia Commons"})
    summary = (w or {}).get("intro") or t["summary"]
    summary = summary if len(summary) <= 1100 else summary[:1100].rsplit(" ", 1)[0] + "…"
    facts = [("Scientific name", t["name"]), ("Rank", t["rank"]), ("Group", inat.ICONIC.get(t["iconic"], t["iconic"]))]
    if t["status"].get("name"):
        facts.append(("Conservation", t["status"]["name"] + (f" ({t['status']['place']})" if t["status"].get("place") else "")))
    facts.append(("iNaturalist records", f"{t['observations']:,}"))
    if s and s["best"]:
        facts.append(("Best months", ", ".join(s["best"])))
    out = {"id": t["id"], "name": t["name"], "common": t["common"], "rank": t["rank"], "iconic": t["iconic"], "status": t["status"],
           "classification": [{"id": a["id"], "name": a["name"], "rank": a["rank"]} for a in t["ancestors"][-7:]],
           "hero": photos[0]["url"] if photos else "", "photos": photos[:8], "summary": summary,
           "facts": [{"label": a, "value": b} for a, b in facts if b], "sections": (w or {}).get("sections", []),
           "season": s, "similar": t["similar"], "children": t["children"][:8],
           "links": {"inaturalist": t["url"], "wikipedia": (w or {}).get("url") or t["wikipedia"]},
           "sources": ["iNaturalist"] + (["Wikipedia"] if w else [])}
    md = [f"# {out['common'] or out['name']}" + (f" ({out['name']})" if out["common"] else ""), "; ".join(f"{f['label']}: {f['value']}" for f in out["facts"]), "", summary]
    md += [f"\n**{x['title']}:** {x['text']}" for x in out["sections"]]
    if out["similar"]:
        md.append("\nOften confused with: " + ", ".join(x["common"] or x["name"] for x in out["similar"][:4]))
    out["markdown"] = "\n".join(md) + "\n\nSources: " + ", ".join(out["sources"])
    return out


def _names(text):
    """Pull candidate animal names out of a vision-model description."""
    found = []
    for m in re.finditer(r"(?im)^\W*(?:common|scientific|species|animal|likely|alt|alternatives?)\b[^:\n]*:\s*(.+)$", text or ""):
        found += [x.strip(" .*_`") for x in re.split(r"[,;/()]| or ", m.group(1))]
    found += re.findall(r"\b[A-Z][a-z]{2,}\s[a-z]{3,}\b", text or "")
    seen, out = set(), []
    for n in found:
        if 3 <= len(n) <= 60 and n.lower() not in seen:
            seen.add(n.lower()); out.append(n)
    return out[:6]


def identify(image, token, vision_text="", lat=None, lng=None, locale="en", wiki_lang="en", use_wiki=True):
    """Combine every signal: iNaturalist computer vision, the vision model's description, local sightings, similar species."""
    cands, notes = {}, []

    def add(row, score, why):
        c = cands.setdefault(row["id"], {**row, "score": 0.0, "evidence": []})
        if why.startswith("Photo"):
            c["score"] = max(c["score"], score)
        elif not any(e.startswith("Named") for e in c["evidence"]):   # count the vision model's opinion once per taxon
            c["score"] += score
        c["evidence"].append(why)

    if token:
        try:
            for r in inat.identify(image, token, lat, lng, locale)["results"]:
                add(r, r["score"], f"Photo match {r['score']:.0f}/100 (iNaturalist vision)")
        except WebError as e:
            notes.append(str(e))
    else:
        notes.append("No iNaturalist token, so the match relies on the vision model and name lookup. Add --inat-token for the strongest result.")
    for n in _names(vision_text):
        hit = _safe(inat.search, n, "", 2, locale)
        for i, r in enumerate((hit or {}).get("results", [])[:2]):
            if r["rank"] in ("species", "subspecies", "genus"):
                add(r, 25 if i == 0 else 8, f"Named in the photo description: {n}")
    if not cands:
        raise WebError("Could not narrow this down. " + " ".join(notes))
    top = sorted(cands.values(), key=lambda c: -c["score"])[:6]
    if lat is not None and lng is not None:   # local plausibility: has this taxon been recorded within 100 km?
        for c in top[:4]:
            d = _safe(inat._api, "/observations", {"taxon_id": c["id"], "lat": lat, "lng": lng, "radius": 100, "per_page": 0})
            n = (d or {}).get("total_results", 0)
            c["score"] += 8 if n else -5
            c["evidence"].append(f"{n:,} records within 100 km" if n else "No records within 100 km")
        top.sort(key=lambda c: -c["score"])
    for c in top:
        c["score"] = round(min(c["score"], 99), 1)
        c["confidence"] = "high" if c["score"] >= 75 else "medium" if c["score"] >= 45 else "low"
    best = _safe(profile, top[0]["id"], "", locale, wiki_lang, use_wiki, lat, lng)
    md = "Photo identification (combined iNaturalist + vision + local records):\n" + "\n".join(
        f"- {inat._label(c)} — {c['score']} ({c['confidence']}); " + "; ".join(c["evidence"]) for c in top)
    return {"results": top, "profile": best, "notes": notes, "markdown": md + ("\n\n" + best["markdown"] if best else "")}
