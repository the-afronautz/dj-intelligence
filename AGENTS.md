# AGENTS.md

Guidance for AI coding agents working in this repository. Humans should read
[CONTRIBUTING.md](./CONTRIBUTING.md) instead — it covers the same ground with
more context.

## What this project is

Tools for DJ workflows: BPM and musical key detection, and harmonic mixing
metadata. Two pieces share one repo and one SQLite file:

- `vinyl_analyzer/` — Flask app. Mic capture, librosa analysis, SQLite log.
- `vinyl_analyzer/catalog/` — CLI. Album lookup against web music databases.

They use different tables in `data/tracks.db` (`tracks` versus
`catalog_albums` / `catalog_tracks`) and must stay decoupled.

## Before you change anything

Run the tests. They're fast and need no network:

```bash
python3 vinyl_analyzer/catalog/test_catalog.py
```

The catalog module imports only the standard library plus `openpyxl`. **Do not
add dependencies to it** without a clear reason — its light footprint is
deliberate, and it's what lets contributors work without the ~300 MB audio
stack.

## Rules that are not style preferences

Breaking any of these produces silent, hard-to-diagnose failures:

1. **`init_db()` is called at module import time in `app.py`.** Do not move it
   into `if __name__ == "__main__"`. Under gunicorn that block never runs, the
   `tracks` table is never created, and every `/api/tracks` call 500s.
2. **The GetSongBPM backlink is mandatory.** The footer in
   `templates/index.html` and the credit in `README.md` are conditions of that
   provider's free API terms. Do not remove or hide them.
3. **The GitHub Actions workflow needs `provenance: false`, `sbom: false`, and
   `platforms: linux/amd64`.** The default multi-entry manifest from
   `docker/build-push-action` is rejected by some Azure hosts.
4. **MusicBrainz requires a User-Agent header and a 1 request/second limit.**
5. **Avoid SQLite `RETURNING`** — it needs 3.35+, and macOS system Python can
   ship older. Use a follow-up `SELECT`.

## Things that do not work — do not retry them

- **Spotify `/audio-features`** — closed to new applications since November
  2024.
- **Tunebat scraping** — Cloudflare-fronted and against their terms. Tunebat is
  a *manual* source only, via the gaps-CSV round trip.
- **AcousticBrainz as a primary source** — coverage is per recording MBID and
  erratic. It is a bonus, never a backbone.
- **Azure App Service with the Oryx Python runtime** — Oryx overrides custom
  startup scripts and produces unfixable `ModuleNotFoundError: numpy`. The
  project deploys as a Docker image to Azure Container Apps instead.

## Diagnosing a failed lookup

`test_catalog.py` fakes the sources layer, so it verifies reconciliation logic
but not live HTTP. When a real lookup returns nothing, use the prober rather
than reading the reconciliation code:

```bash
python3 vinyl_analyzer/catalog/diagnose.py --artist "..." --album "..."
```

It reports per-stage results and distinguishes not-in-database, no-tempo,
no-match, and no-API-key.

## Verify containers locally

Cloud log archaeology is slow. A local run takes seconds and has caught real
bugs the platform logs obscured:

```bash
docker build -t vinyl-test . && docker run --rm -p 8000:8000 vinyl-test
```

## Do not commit

- `app.yaml` / `app.yaml.bak` — Azure exports containing subscription IDs
- Anything under `Keys/` — generated lookup output
- Business or client documents
- API keys. `.env` is gitignored; `.env.example` holds placeholders only.

## Workflow

Branch off `main`, run the tests, open a pull request. `main` is protected and
every push to it rebuilds the production image.
