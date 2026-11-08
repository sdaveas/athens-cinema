#!/usr/bin/env python3
"""Fetch Athens cinema showtimes from athinorama.gr into data.js.

Three origins (see design doc):
  A  /cinema/guide/all/cinemas/   halls, films, cinema x film pairs, prose showtimes
  B  /cinema/guide/               film metadata (director, year, duration, poster)
  C  /cinema/halls/<slug>/        missing coordinates + exact ScreeningEvent.startDate

Run:  python3 fetch.py
Out:  data.js  (window.DATA = {...})
"""

import html as htmllib
import json
import re
import subprocess
import sys
import time
from datetime import datetime, timedelta, timezone

BASE = "https://www.athinorama.gr"
LISTING = BASE + "/cinema/guide/all/cinemas/"
GUIDE = BASE + "/cinema/guide/"
UA = "athina-cinema-guide/1.0 (personal, low-volume)"
PAUSE = 0.6

DAYS = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
DAYMAP = {
    "Δευτ": "Mon", "Τρ": "Tue", "Τετ": "Wed", "Πέμ": "Thu", "Πεμ": "Thu",
    "Παρ": "Fri", "Σάβ": "Sat", "Σαβ": "Sat", "Κυρ": "Sun",
}
ALL_DAYS = set(DAYS)

# --------------------------------------------------------------------------- http


def get(url: str) -> str:
    """curl, not urllib: this machine's python has no CA bundle."""
    r = subprocess.run(
        ["curl", "-sL", "--max-time", "60", "-A", UA, url],
        capture_output=True, text=True, encoding="utf-8",
    )
    if r.returncode != 0 or not r.stdout:
        sys.exit(f"fetch failed ({r.returncode}): {url}")
    return r.stdout


def ld_blocks(page: str):
    for raw in re.findall(r'<script type="application/ld\+json">(.*?)</script>', page, re.S):
        try:
            yield json.loads(raw)
        except json.JSONDecodeError:
            pass


def ld_objects(page: str, kind: str):
    """Yield every object of @type == kind, flattening ItemList wrappers."""
    for block in ld_blocks(page):
        items = block.get("itemListElement", [block])
        for item in items:
            obj = item.get("item", item) if isinstance(item, dict) else item
            if isinstance(obj, dict) and obj.get("@type") == kind:
                yield obj


# ------------------------------------------------------------------------ times


TIME_RE = re.compile(r"\d{1,2}\s*[.:]\s*\d{2}")
DAY_RE = re.compile(r"Δευτ|Τρ|Τετ|Πέμ|Πεμ|Παρ|Σάβ|Σαβ|Κυρ")
TOKEN_RE = re.compile(
    r"(?P<day>Δευτ|Τρ|Τετ|Πέμ|Πεμ|Παρ|Σάβ|Σαβ|Κυρ)"
    r"|(?P<time>\d{1,2}\s*[.:]\s*\d{2})"
    r"|(?P<lang>μεταγλ\w*|υπότιτλ\w*|υποτιτλ\w*)"
)


def norm_time(raw: str) -> str:
    h, m = re.split(r"[.:]", raw.strip())
    return f"{int(h):02d}:{m.strip()}"


def resolve_days(buf):
    """buf: [(day_name, gap_before)]. Ranges use '-' in the gap. Returns a day set."""
    out = set()
    i = 0
    while i < len(buf):
        name, _ = buf[i]
        if i + 1 < len(buf) and "-" in buf[i + 1][1]:
            start, end = DAYS.index(DAYMAP[name]), DAYS.index(DAYMAP[buf[i + 1][0]])
            d = start
            while True:
                out.add(DAYS[d])
                if d == end:
                    break
                d = (d + 1) % 7
            i += 2
        else:
            out.add(DAYMAP[name])
            i += 1
    return out or ALL_DAYS


def parse_times(text: str):
    """Parse one schedule-infos blob.

    Returns (showings, fully_resolved) where showings is a list of
    (day_set, "HH:MM", language).
    """
    text = re.sub(r"\s+", " ", text)
    ctx = ALL_DAYS            # bare leading times mean "every day"
    buf = []                  # day tokens awaiting a time
    run = []                  # (index into out) emitted since ctx last changed
    out = []
    prev_end = 0

    for m in TOKEN_RE.finditer(text):
        gap = text[prev_end:m.start()]
        prev_end = m.end()
        if m.group("day"):
            buf.append((m.group("day"), gap))
        elif m.group("time"):
            if buf:
                ctx = resolve_days(buf)
                buf = []
                run = []
            out.append([ctx, norm_time(m.group("time")), None])
            run.append(len(out) - 1)
        else:                                   # language qualifier
            lang = "dubbed" if m.group("lang").startswith("μεταγλ") else "subtitled"
            for idx in run:
                out[idx][2] = lang

    if buf:                                     # days with no time: unresolvable
        return [], False
    if len(out) != len(TIME_RE.findall(text)):
        return [], False
    return out, True


# ---------------------------------------------------------------------- listing


AREA_RE = re.compile(r'<h2><a href="/cinema/guide/([a-z_\-]+)/cinemas/">([^<]+)</a></h2>')
HALL_RE = re.compile(r'<h2 class="item-title"><a href="/cinema/halls/([^"]+)/">([^<]*)</a></h2>')
MOVIE_RE = re.compile(r'<h3><a href="/cinema/movie/([^"]+)/">([^<]*)</a></h3>')
STARS_RE = re.compile(r"--stars:\s*([\d.]+)")
GENRE_RE = re.compile(r'<div class="tags">\s*<ul>\s*<li><span>([^<]+)</span>')
INFO_RE = re.compile(r'<ul class="schedule-infos">(.*?)</ul>', re.S)


def parse_listing(page: str):
    hall_hits = list(HALL_RE.finditer(page))
    area_hits = [(m.start(), htmllib.unescape(m.group(2)).strip())
                 for m in AREA_RE.finditer(page)]

    # coordinates that the listing JSON-LD does carry, keyed by hall name
    # (location.url is often the neighbourhood route, not the hall route)
    listing_geo = {}
    for ev in ld_objects(page, "ScreeningEvent"):
        loc = ev.get("location") or {}
        geo = loc.get("geo") or {}
        name = htmllib.unescape(loc.get("name") or "").strip()
        if name and geo.get("latitude"):
            listing_geo[name] = (float(geo["latitude"]), float(geo["longitude"]))

    halls, films, parsed, raw_fallbacks = [], {}, [], []

    for i, hm in enumerate(hall_hits):
        body = page[hm.end(): hall_hits[i + 1].start() if i + 1 < len(hall_hits) else len(page)]
        head = body.split('<div class="grid schedule-grid">')[0]

        area = next((n for p, n in reversed(area_hits) if p < hm.start()), None)
        addr = re.search(r"<address>([^<]*)</address>", head)
        phone = re.search(r"τηλ\.?:\s*([\d\s,\-]{6,})", head)
        halls.append({
            "id": hm.group(1),
            "name": htmllib.unescape(hm.group(2)).strip(),
            "area": area,
            "address": htmllib.unescape(addr.group(1)).strip() if addr else None,
            "phone": re.sub(r"\s+", "", phone.group(1)) if phone else None,
            "url": f"{BASE}/cinema/halls/{hm.group(1)}/",
            "lat": listing_geo.get(htmllib.unescape(hm.group(2)).strip(), (None, None))[0],
            "lon": listing_geo.get(htmllib.unescape(hm.group(2)).strip(), (None, None))[1],
            "coordsFixed": None,
        })

        for chunk in body.split('class="item schedule-item"')[1:]:
            mv = MOVIE_RE.search(chunk)
            if not mv:
                continue
            mid, title = mv.group(1), htmllib.unescape(mv.group(2)).strip()
            films.setdefault(mid, {"id": mid, "title": title,
                                   "url": f"{BASE}/cinema/movie/{mid}/"})
            stars = STARS_RE.search(chunk)
            if stars:
                films[mid].setdefault("rating", float(stars.group(1)))
            genre = GENRE_RE.search(chunk)
            if genre:
                films[mid].setdefault("genre", htmllib.unescape(genre.group(1)).strip())

            info = INFO_RE.search(chunk)
            if not info:
                raw_fallbacks.append((mid, hm.group(1), ""))
                continue
            blob = "".join(re.findall(r'<span class="time">([^<]*)</span>', info.group(1)))
            got, ok = parse_times(blob)
            if not ok:
                raw_fallbacks.append((mid, hm.group(1), blob.strip()))
                continue
            for dayset, t, lang in got:
                parsed.append({
                    "hallId": hm.group(1), "filmId": mid,
                    "days": sorted(dayset), "time": t, "language": lang,
                    "raw": blob.strip(),
                })

    return halls, films, parsed, raw_fallbacks


# ------------------------------------------------------------------------ guide


CARD_RE = re.compile(r'<div class="item\s+horizontal card-item">')
YEAR_RE = re.compile(r"<li><span>((?:19|20)\d{2})</span></li>")
DUR_RE = re.compile(r"Διάρκεια:\s*(\d+)")


def parse_guide(page: str):
    meta = {}

    # structured: poster, original title, synopsis, director
    for movie in ld_objects(page, "Movie"):
        mid = movie.get("url", "").rstrip("/").split("/")[-1]
        if not mid:
            continue
        director = movie.get("director")
        meta[mid] = {
            "original": movie.get("alternateName"),
            "synopsis": movie.get("description"),
            "poster": movie.get("image"),
            "director": director.get("name") if isinstance(director, dict) else None,
        }

    # html card tags: year, duration (the trailing ' is the minutes marker)
    for chunk in CARD_RE.split(page)[1:]:
        mid = re.search(r'/cinema/movie/([^"]+)/', chunk)
        if not mid:
            continue
        entry = meta.setdefault(mid.group(1), {})
        year = YEAR_RE.search(chunk)
        if year:
            entry["year"] = int(year.group(1))
        dur = DUR_RE.search(chunk)
        if dur:
            entry["duration"] = int(dur.group(1))
    return meta


# -------------------------------------------------------------------- hall pages


COORD_RE = re.compile(r'"latitude":"(-?[\d.]+)","longitude":"(-?[\d.]+)"')


def coord_is_sane(lat, lon):
    return lat is not None and lon is not None and (36.0 < lat < 40.0 and 22.0 < lon < 24.5)


def fix_coords(lat, lon):
    """Attica sanity box. Returns (lat, lon, note)."""
    if lat is None or lon is None:
        return None, None, "dropped"
    if coord_is_sane(lat, lon):
        return lat, lon, None
    if coord_is_sane(lon, lat):                 # swapped
        return lon, lat, "swapped"
    return None, None, "dropped"                # e.g. 0,0


def parse_hall_page(page: str, hall_id: str):
    lat = lon = None
    m = COORD_RE.search(page)
    if m:
        lat, lon = float(m.group(1)), float(m.group(2))

    dates = {}
    for ev in ld_objects(page, "ScreeningEvent"):
        start = ev.get("startDate")
        if not start:
            continue
        mid = (ev.get("url") or "").rstrip("/").split("/")[-1]
        try:
            dt = datetime.fromisoformat(start)
        except ValueError:
            continue
        dates.setdefault((mid, dt.strftime("%H:%M")), set()).add(dt.date().isoformat())
    return lat, lon, dates


# ------------------------------------------------------------------------- build


def main():
    print("→ origin A: listing")
    page_a = get(LISTING)
    halls, films, parsed, raw_fallbacks = parse_listing(page_a)
    if len(halls) < 20 or len(films) < 40:
        sys.exit(f"listing looks truncated: {len(halls)} halls, {len(films)} films")
    print(f"  {len(halls)} halls, {len(films)} films, {len(parsed)} showtime groups, "
          f"{len(raw_fallbacks)} unparsed")

    print("→ origin B: film metadata")
    page_b = get(GUIDE)
    meta = parse_guide(page_b)
    merged = 0
    for mid, film in films.items():
        extra = meta.get(mid, {})
        for key in ("original", "synopsis", "poster", "director", "year", "duration"):
            if extra.get(key) is not None:
                film[key] = extra[key]
        merged += 1 if extra else 0
    print(f"  metadata for {merged}/{len(films)} films")
    if merged < 60:
        sys.exit(f"metadata coverage too low: {merged}/{len(films)}")

    print("→ origin C: hall pages (coordinates + exact dates)")
    exact = {}
    repairs = []
    for n, hall in enumerate(halls, 1):
        time.sleep(PAUSE)
        try:
            lat, lon, dates = parse_hall_page(get(hall["url"]), hall["id"])
        except SystemExit:
            print(f"  ! {hall['name']}: page fetch failed, keeping listing data")
            continue
        lat, lon, note = fix_coords(lat, lon)
        if lat is not None:
            hall["lat"], hall["lon"], hall["coordsFixed"] = lat, lon, note
            if note:
                repairs.append(f"{hall['name']} ({note})")
        elif not coord_is_sane(hall["lat"], hall["lon"]):
            hall["lat"] = hall["lon"] = None
            hall["coordsFixed"] = "dropped"
            repairs.append(f"{hall['name']} (dropped)")
        for (mid, t), ds in dates.items():
            for d in ds:
                exact.setdefault((hall["id"], mid, t), set()).add(d)
        if n % 10 == 0:
            print(f"  {n}/{len(halls)}")

    # overlay exact dates where origin C had them
    showings = []
    dated = 0
    for p in parsed:
        key = (p["hallId"], p["filmId"], p["time"])
        if key in exact:
            for d in sorted(exact[key]):
                dt = datetime.fromisoformat(d)
                showings.append({"hallId": p["hallId"], "filmId": p["filmId"],
                                 "day": DAYS[dt.weekday()], "date": d, "time": p["time"],
                                 "language": p["language"]})
            dated += 1
        else:
            for day in p["days"]:
                showings.append({"hallId": p["hallId"], "filmId": p["filmId"],
                                 "day": day, "date": None, "time": p["time"],
                                 "language": p["language"]})

    # overlapping day groups emit the same slot twice (e.g. "Πέμ. Σαβ.-Τετ. 20.00"),
    # so collapse to one row per hall/film/date/time, keeping any language tag
    uniq = {}
    for s in showings:
        key = (s["hallId"], s["filmId"], s["date"], s["time"])
        if key in uniq:
            if uniq[key]["language"] is None:
                uniq[key]["language"] = s["language"]
        else:
            uniq[key] = s
    showings = list(uniq.values())

    showings.sort(key=lambda s: (s["date"] or "9999", s["time"]))
    placed = sum(1 for h in halls if h["lat"] is not None)
    blobs = {(p["hallId"], p["filmId"], p["raw"]) for p in parsed}
    tokens = sum(len(TIME_RE.findall(b[2])) for b in blobs)
    fallback_tokens = sum(len(TIME_RE.findall(r[2])) for r in raw_fallbacks)
    total_tokens = tokens + fallback_tokens

    # ------------------------------------------------------- self check
    assert len(halls) == 38, f"expected 38 halls, got {len(halls)}"
    assert len(films) >= 70, f"too few films: {len(films)}"
    assert 2000 <= len(showings) <= 4500, f"showing count out of range: {len(showings)}"
    assert parse_times("Πέμ.-Τετ. : 20.00")[0][0][0] == ALL_DAYS, "Πέμ.-Τετ. != all 7 days"
    assert parse_times("Κυρ., Δευτ. 17.20")[0][0][0] == {"Sun", "Mon"}, "comma day list broken"
    assert parse_times("Πέμ. Δευτ. 20.00")[0][0][0] == {"Thu", "Mon"}, "space day list broken"
    bad = [h["name"] for h in halls if h["lat"] is not None and not coord_is_sane(h["lat"], h["lon"])]
    assert not bad, f"insane coordinates survived: {bad}"

    data = {
        "fetchedAt": datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds"),
        "halls": halls,
        "films": sorted(films.values(), key=lambda f: f["title"]),
        "showings": showings,
        "stats": {"exactDateGroups": dated, "parsedGroups": len(parsed),
                  "unparsedGroups": len(raw_fallbacks)},
    }

    with open("data.js", "w", encoding="utf-8") as fh:
        fh.write("window.DATA = ")
        json.dump(data, fh, ensure_ascii=False, separators=(",", ":"))
        fh.write(";\n")

    print(f"\n✓ {tokens}/{total_tokens} time tokens parsed "
          f"({fallback_tokens} kept as raw fallback in {len(raw_fallbacks)} groups)")
    print(f"✓ {placed}/{len(halls)} halls placed · {len(showings)} showings "
          f"({dated} groups with exact dates)")
    print(f"✓ {len(films)} films · data.js {len(open('data.js', encoding='utf-8').read())//1024} KB")
    if repairs:
        print("! repaired coordinates: " + ", ".join(repairs))
    if raw_fallbacks:
        print("! unparsed labels (rendered verbatim):")
        for mid, hid, blob in raw_fallbacks[:5]:
            print(f"    {hid} {mid}: {blob[:90]}")


if __name__ == "__main__":
    main()
