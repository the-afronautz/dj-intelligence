# Contributing to DJ Intelligence

Thanks for your interest. This is a small project, so the process is light.

## Before your first pull request

Read the [CLA](./CLA.md) and send the signature block to the maintainer. You
keep copyright in everything you write; the agreement lets the project change
its license later without tracking down every contributor. Your entry goes in
[`contributors.md`](./contributors.md).

## What's in here

Two largely independent pieces share one repository and one SQLite file:

| Piece | What it is | Heavy dependencies? |
|---|---|---|
| `vinyl_analyzer/` (app) | Flask web app. Records from the mic, detects BPM and key with librosa, logs to SQLite. | Yes — librosa, numpy, scipy |
| `vinyl_analyzer/catalog/` | CLI. Turns "artist + album" into a BPM/Camelot track table from web sources. | No — stdlib + openpyxl |

They write to the same `data/tracks.db` but to different tables (`tracks` for
mic analysis, `catalog_albums` / `catalog_tracks` for lookups). They never
collide.

**If you're not sure where to start, start with the catalog module.** It needs
no audio hardware, no turntable, and no large dependency install, and its test
suite runs without network access.

## Setup

### Catalog module only

```bash
git clone https://github.com/the-afronautz/dj-intelligence.git
cd dj-intelligence
pip3 install openpyxl
```

That's the whole install. Everything else the catalog uses is in the standard
library.

Then get a free [GetSongBPM API key](https://getsongbpm.com/api) — registration
plus a backlink on any site that displays the data. Copy the example
environment file and add your key:

```bash
cp .env.example .env
# edit .env, set GETSONGBPM_API_KEY
```

Without a key the tool still runs, but it degrades to a tracklist generator
plus a gaps CSV. GetSongBPM is the main tempo source, not one of several
equals — see "Known constraints" below.

### Full app

```bash
pip3 install -r vinyl_analyzer/requirements.txt
```

This pulls librosa, numpy, scipy, and soundfile — roughly 300 MB on first
install. A virtual environment or conda environment is recommended.

```bash
cd vinyl_analyzer
python3 app.py
```

Open <http://127.0.0.1:5057/> and allow microphone access. The server binds to
`127.0.0.1` only.

### Docker

The deployed image is built from the `Dockerfile` at the repo root. Verify any
container change locally before pushing:

```bash
docker build -t vinyl-test .
docker run --rm -p 8000:8000 vinyl-test
```

On Apple Silicon, add `--platform linux/amd64` when running the *published*
image, which is amd64-only.

## Running the tests

```bash
python3 vinyl_analyzer/catalog/test_catalog.py
```

40 assertions covering Camelot conversion, source reconciliation, and an
end-to-end run. **No network required** — the sources layer is faked, so the
tests are deterministic and don't depend on a provider being up.

All tests must pass before a pull request is merged.

### When a real lookup returns nothing

The tests prove the reconciliation logic is right. They say nothing about
whether the live HTTP calls still work, because every provider is faked. For
that, use the prober:

```bash
python3 vinyl_analyzer/catalog/diagnose.py --artist "Young Thug" --album "JEFFERY"
```

It walks each stage separately and reports which provider returned what. Run
it before assuming a lookup failure is a bug in the code.

## Workflow

1. Branch off `main`: `git checkout -b feature/short-description`
2. Make your change; add tests when you add logic.
3. Run the test suite.
4. Push and open a pull request against `main`.
5. One approving review merges it.

`main` is protected, and every push to it rebuilds the production container
image. Please don't commit directly to it.

### Commit messages

A short summary line, then bullets if the change needs explanation. Say *why*,
not just *what* — the git history is the only place some of this reasoning
survives.

## Code style

No linter is enforced. Match what's already in the file you're editing:

- Standard library first, then third-party, then local imports.
- Type hints on new function signatures.
- Comments explain reasoning, not mechanics. The existing code notes things
  like *why* `init_db()` is called at import time — that kind of comment is
  welcome; `# increment counter` is not.
- Keep functions small enough to test on their own.

## Known constraints

Worth knowing before you file a bug:

- **`init_db()` must be called at module import time** in `app.py`, not inside
  `if __name__ == "__main__"`. Under gunicorn the main block never runs, the
  `tracks` table silently never gets created, and every `/api/tracks` call
  returns a 500.
- **AcousticBrainz coverage is erratic**, and per recording MBID. Whether data
  exists depends on whether a user ever submitted an analysis for that exact
  recording. One 2016 album can be fully covered while a 2015 album has
  nothing. Treat it as a bonus source, never a backbone.
- **Deezer's `bpm` field is sparsely populated** and often returns 0. It's a
  cross-check, not a source.
- **Spotify's audio-features endpoint is closed** to new applications as of
  November 2024. Don't propose it.
- **Tunebat has no public API.** It's supported only as a *manual* source
  through the gaps-CSV round trip.
- **MusicBrainz requires a User-Agent header and one request per second.**
  Respect that rate limit; it's their stated policy, not a suggestion.
- **SQLite `RETURNING` needs 3.35+.** macOS system Python can ship older, so
  use a follow-up `SELECT` instead.

## Data source terms

These bind anyone who redistributes or deploys this project:

- **GetSongBPM** requires a visible backlink to getsongbpm.com wherever its
  data is displayed. The footer in `templates/index.html` and the credit in
  the README exist for this reason — **don't remove them** while the provider
  is in use.
- **MusicBrainz** core data is public domain (CC0); some supplementary data is
  CC BY-NC-SA. Commercial use of the non-core data needs a license from the
  MetaBrainz Foundation.
- **AcousticBrainz** data is CC0.

## Questions

Open an issue. For anything about licensing, commercial use, or the CLA,
contact the maintainer directly.
