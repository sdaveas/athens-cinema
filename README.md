# Σινεμά Αθήνα

Static showtimes browser for Athens cinemas. No backend, no build step.

- `index.html` — the whole UI (vanilla JS + Leaflet from CDN)
- `data.js` — generated dataset, `window.DATA`
- `fetch.py` — scraper that rebuilds `data.js`

## Refresh the data

```sh
python3 fetch.py
```

Hits three pages of [athinorama.gr](https://www.athinorama.gr/cinema/guide/all/cinemas/):
the cinema listing (halls, films, prose showtimes), the film guide (metadata) and
38 hall pages (coordinates, exact screening dates). ~40 requests, ~0.6 s apart.

Self-checks abort the write if the upstream markup changes shape, so a broken scrape
can't overwrite good data.

## Local preview

```sh
python3 -m http.server 8000
```

Data and attribution belong to athinorama.gr. Personal use.
