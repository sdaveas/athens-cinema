#!/usr/bin/env python3
"""Query Athens cinema showtimes from the terminal (or from an agent).

Data comes from the published site; nothing is scraped here.

    python3 cinema.py --day today --after 20:00
    python3 cinema.py --day today --genre Δραματική --near 37.9838,23.7275 --radius 2
    python3 cinema.py --q Κωνσταντίνου --json

    python3 cinema.py --selftest
"""

import argparse
import json
import math
import re
import subprocess
import sys
from datetime import date, datetime, timedelta

DATA_URL = "https://sdaveas.github.io/athens-cinema/data.js"
UA = "athens-cinema-cli/1.0"


def fetch(url=DATA_URL):
    out = subprocess.run(["curl", "-sL", "--max-time", "30", "-A", UA, url],
                         capture_output=True, text=True, encoding="utf-8")
    if out.returncode or not out.stdout:
        sys.exit(f"cannot load data ({out.returncode}): {url}")
    return load(out.stdout)


def load(text):
    """data.js is `window.DATA = {...};`"""
    body = text[text.index("=") + 1:].strip().rstrip(";")
    return json.loads(body)


def resolve_day(spec):
    today = date.today()
    if spec in (None, "", "today", "σημερα", "σήμερα"):
        return today.isoformat() if spec != "all" else None
    if spec in ("tomorrow", "αυριο", "αύριο"):
        return (today + timedelta(days=1)).isoformat()
    if spec == "all":
        return None
    m = re.fullmatch(r"(\d{4})-(\d{2})-(\d{2})", spec)
    if not m:
        sys.exit(f"bad --day {spec!r}; use today|tomorrow|all|YYYY-MM-DD")
    return spec


def minutes(hm):
    return int(hm[:2]) * 60 + int(hm[3:5])


def km(a, b):
    r = 6371
    dlat = math.radians(b[0] - a[0])
    dlon = math.radians(b[1] - a[1])
    h = math.sin(dlat / 2) ** 2 + math.cos(math.radians(a[0])) * math.cos(math.radians(b[0])) * math.sin(dlon / 2) ** 2
    return 2 * r * math.asin(math.sqrt(h))


def search(data, day=None, after=None, before=None, genre=None, area=None,
           near=None, radius=None, q=None):
    halls = {h["id"]: h for h in data["halls"]}
    films = {f["id"]: f for f in data["films"]}
    lo = minutes(after) if after else 0
    hi = minutes(before) if before else 1440
    hits = {}

    for s in data["showings"]:
        f, h = films.get(s["filmId"]), halls.get(s["hallId"])
        if not f or not h:
            continue
        if day and s["date"] != day:
            continue
        if not (lo <= minutes(s["time"]) <= hi):
            continue
        if genre and genre.lower() not in (f.get("genre") or "").lower():
            continue
        if area and area.lower() not in (h.get("area") or "").lower():
            continue
        if q:
            hay = " ".join(filter(None, [f.get("title"), f.get("original"), f.get("director")])).lower()
            if q.lower() not in hay:
                continue
        dist = None
        if near:
            if h.get("lat") is None:
                continue
            dist = km(near, (h["lat"], h["lon"]))
            if radius is not None and dist > radius:
                continue
        hits.setdefault(f["id"], {"film": f, "venues": {}})
        hits[f["id"]]["venues"].setdefault(h["id"], {"hall": h, "dist": dist, "times": []})
        hits[f["id"]]["venues"][h["id"]]["times"].append(s)

    out = []
    for e in hits.values():
        venues = sorted(e["venues"].values(),
                        key=lambda v: (v["dist"] if v["dist"] is not None else 9e9,
                                       min(t["time"] for t in v["times"])))
        for v in venues:
            v["times"].sort(key=lambda t: t["time"])
        out.append({
            "title": e["film"]["title"],
            "original": e["film"].get("original"),
            "year": e["film"].get("year"),
            "duration": e["film"].get("duration"),
            "genre": e["film"].get("genre"),
            "rating": e["film"].get("rating"),
            "url": e["film"].get("url"),
            "showings": sum(len(v["times"]) for v in venues),
            "venues": [{
                "name": v["hall"]["name"],
                "area": v["hall"].get("area"),
                "address": v["hall"].get("address"),
                "distanceKm": round(v["dist"], 1) if v["dist"] is not None else None,
                "times": [{"time": t["time"], "date": t["date"], "language": t["language"]} for t in v["times"]],
            } for v in venues],
        })
    out.sort(key=lambda e: (e["venues"][0]["times"][0]["time"], -e["showings"]))
    return out


def render(results, limit):
    if not results:
        print("Καμία προβολή με αυτά τα κριτήρια.")
        return
    for e in results[:limit]:
        bits = [str(e["year"]) if e["year"] else None,
                f"{e['duration']}′" if e["duration"] else None,
                e["genre"], f"★{e['rating']}" if e["rating"] else None]
        head = " · ".join(b for b in bits if b)
        print(f"\n{e['title']}" + (f" ({head})" if head else ""))
        for v in e["venues"]:
            d = f" — {v['distanceKm']} χλμ" if v["distanceKm"] is not None else ""
            times = ", ".join(t["time"] for t in v["times"])
            print(f"  {v['name']}: {times}{d}")
    print(f"\n{len(results)} ταινίες" + (f" (δείχνω {limit})" if len(results) > limit else ""))


def selftest():
    data = {
        "halls": [
            {"id": "a", "name": "Άλφα", "area": "ΚΕΝΤΡΟ", "lat": 37.98, "lon": 23.73, "address": "x"},
            {"id": "b", "name": "Βήτα", "area": "ΝΟΤΙΑ", "lat": 37.90, "lon": 23.75, "address": "y"},
            {"id": "c", "name": "Χωρίς Στίγμα", "area": "ΝΟΤΙΑ", "lat": None, "lon": None, "address": "z"},
        ],
        "films": [
            {"id": "f1", "title": "Δράμα", "genre": "Δραματική", "year": 2025, "duration": 100},
            {"id": "f2", "title": "Κωμωδία", "genre": "Κωμωδία", "year": 2020, "duration": 90},
        ],
        "showings": [
            {"hallId": "a", "filmId": "f1", "date": "2026-01-01", "time": "19:00", "language": None},
            {"hallId": "a", "filmId": "f1", "date": "2026-01-01", "time": "22:00", "language": "dubbed"},
            {"hallId": "b", "filmId": "f2", "date": "2026-01-01", "time": "20:30", "language": None},
            {"hallId": "c", "filmId": "f2", "date": "2026-01-01", "time": "21:00", "language": None},
            {"hallId": "b", "filmId": "f2", "date": "2026-01-02", "time": "18:00", "language": None},
        ],
    }
    d = "2026-01-01"
    assert len(search(data, day=d)) == 2, "day filter"
    assert len(search(data, day=d, after="21:00")) == 2, "after filter"
    assert [e["title"] for e in search(data, day=d, after="21:30")] == ["Δράμα"], "late shows"
    assert search(data, day=d, genre="δραματ")[0]["title"] == "Δράμα", "genre is case/accent tolerant"
    near = (37.98, 23.73)
    tight = search(data, day=d, near=near, radius=1)
    assert all(v["name"] == "Άλφα" for e in tight for v in e["venues"]), "radius excludes far halls"
    assert not any(v["name"] == "Χωρίς Στίγμα" for e in search(data, day=d, near=near) for v in e["venues"]), \
        "halls without coordinates are skipped when a location is given"
    assert search(data, day="2026-01-02")[0]["showings"] == 1, "date filter"
    assert resolve_day("all") is None and resolve_day("today") == date.today().isoformat(), "day words"
    print("selftest ok")


def main():
    p = argparse.ArgumentParser(description="Athens cinema showtimes")
    p.add_argument("--day", default="today", help="today|tomorrow|all|YYYY-MM-DD")
    p.add_argument("--after", help="HH:MM")
    p.add_argument("--before", help="HH:MM")
    p.add_argument("--genre")
    p.add_argument("--area", help="π.χ. ΚΕΝΤΡΟ - ΚΟΛΩΝΑΚΙ")
    p.add_argument("--near", help="lat,lon")
    p.add_argument("--radius", type=float, help="km, needs --near")
    p.add_argument("--q", help="title / original title / director")
    p.add_argument("--limit", type=int, default=12)
    p.add_argument("--json", action="store_true")
    p.add_argument("--url", default=DATA_URL)
    p.add_argument("--selftest", action="store_true")
    a = p.parse_args()

    if a.selftest:
        return selftest()

    near = None
    if a.near:
        try:
            near = tuple(float(x) for x in a.near.split(","))
        except ValueError:
            sys.exit("--near wants lat,lon")
    if a.radius and not near:
        sys.exit("--radius needs --near")

    results = search(fetch(a.url), day=resolve_day(a.day), after=a.after, before=a.before,
                     genre=a.genre, area=a.area, near=near, radius=a.radius, q=a.q)
    if a.json:
        print(json.dumps(results[:a.limit], ensure_ascii=False, indent=1))
    else:
        render(results, a.limit)


if __name__ == "__main__":
    main()
