# Vinyl Analyzer

A small local web app: capture a snippet from your turntable speakers, get BPM
and Camelot key, log it to a queryable SQLite library, and export to Excel any
time.

## What it does

1. Click **Start recording** in the browser — your Mac's microphone listens
   to the speakers playing your record.
2. After 30 seconds (or when you stop), the clip is sent to a local Flask
   backend.
3. The backend uses **librosa** to estimate BPM (multi-band onset detection
   on the percussive component, with a tempo prior, ranked top-K candidates
   including half/double-time alternates) and key (Sha'ath profiles — tuned
   for electronic music — correlated against constant-Q chroma, then voted
   across overlapping 2-second windows), then maps the key to its
   **Camelot** code.
4. Review BPM and key. If detection is uncertain, pick the right BPM
   candidate, tap along to the beat, or type a manual BPM; pick the
   alternate key candidate if the runner-up looks right.
5. Fill in track name, artist, and notes; the row is saved to
   `data/tracks.db`.
6. Browse, search, sort, and **Export to Excel** whenever you want.

Confidence scores accompany both BPM and key:

- **BPM confidence** blends per-window vote share, beat-strength at the
  detected tempo, and inter-beat-interval consistency. Expect 85%+ on a
  clean recording.
- **Key confidence** is the share of analysis windows that voted for the
  winning key — when this falls below 60%, the UI surfaces the **runner-up
  candidate** so you can pick the one that matches your record before saving.

**Three escape hatches when BPM detection falters** (common on hip-hop with
swing or soft sample-based kicks):

1. **BPM candidate picker** — the top few candidates (including half- and
   double-time alternates) appear under the result panel; click the right one.
2. **Tap tempo** — click the "Tap tempo" button along with the beat (≥4 taps).
   BPM is calculated live from the median inter-tap interval.
3. **Manual BPM** — type the BPM directly (e.g. from Tunebat) into the manual
   input box.

Any override is preserved in the `bpm` column with the original detection
moved to `alt_bpm` for the audit trail, and a note is added describing how
the BPM was set.

## Setup (one-time)

```bash
cd "vinyl_analyzer"
pip3 install --user flask librosa openpyxl
```

(librosa pulls in numpy, scipy, soundfile, audioread, etc. — about ~300 MB of
deps the first time.)

## Run it

```bash
cd "vinyl_analyzer"
python3 app.py
```

Then open <http://127.0.0.1:5057/> in Safari or Chrome. The first time the
browser will ask for microphone permission — **allow it for this site**.

The server listens on `127.0.0.1` only, so nothing leaves your laptop.

## Tips for accurate detection

- **Volume matters more than fidelity.** Get a clean signal into the mic —
  speakers loud enough that the input meter sits in the green/yellow zone, not
  pinning red.
- **30 seconds is the sweet spot.** That's roughly 60 bars at 120 BPM —
  enough beats for stable BPM detection and enough harmonic content for the key
  estimator to settle. The recorder auto-stops at 30s, or you can stop early.
- **Pick a section with the full musical idea.** Intros and breakdowns with
  no kick can confuse BPM; an empty bar of "just hats" can confuse the key
  detector. The drop or a steady-state groove gives the best result.
- **Vinyl drift is real.** Even a calibrated 33⅓ deck wanders ±0.5%, and most
  in-club setups are nudged with the pitch fader. Expect detected BPM to land
  within a couple of percent of the label value, not exactly on it.
- **Disable noise suppression in the OS** if possible (System Settings →
  Sound) — aggressive denoising can chew up the transient information the
  beat tracker depends on. The web UI already disables echo cancellation,
  AGC, and noise suppression at the WebAudio level.

## How it was built

The whole app is intentionally small: one Python file for the server, one for
the audio analysis, and three files (HTML / CSS / JS) for the UI. No build
step, no frontend framework, no external services. The architecture in one
sentence: **browser captures mic → uploads blob → Flask runs librosa →
returns JSON → JS renders → SQLite stores**.

### Architectural choices

- **Local-only web app rather than a desktop GUI.** A browser already has a
  battle-tested API (`MediaRecorder`) for grabbing mic input, plus easy
  layout via HTML/CSS. The cost is one extra component (the local Flask
  server), the benefit is faster iteration and no platform-specific
  packaging.
- **Microphone capture in the browser, analysis on the backend.** Audio DSP
  in JavaScript is possible (Web Audio API + ML libraries) but `librosa` in
  Python is the standard reference implementation, with well-tested HPSS,
  chroma, beat-tracking, and tempogram primitives. Round-tripping a 30-second
  WebM blob to localhost takes ~1 second.
- **SQLite over a JSON file or direct-to-Excel writing.** SQLite is a single
  file, no server, queryable from any tool, and has free transactional
  guarantees. Excel is generated on demand from a SQL `SELECT`, so the spec
  of "log it" and "export it" stay decoupled.
- **No frontend framework.** The UI is one HTML page and one ~400-line JS
  file. Adding React/Vue would multiply the dependency footprint without
  meaningfully simplifying what is essentially three interactions (record,
  pick, save).

### BPM detection pipeline

1. **HPSS** split with a stronger percussive margin (`margin=(1.0, 4.0)`) to
   keep pitched samples out of the BPM signal — critical for sample-based
   hip-hop where pitched loops can fool the onset detector.
2. **Multi-band onset detection.** The percussive signal is bandpassed into
   a kick band (40–200 Hz) and a snare band (200–800 Hz). Per-band onset
   envelopes are blended with the broadband envelope (50 % kick + 30 %
   broadband + 20 % snare). Soft kicks that wouldn't trigger a broadband
   onset still register in the kick band.
3. **Windowed tempo estimates** are produced by `librosa.feature.rhythm.tempo`
   with a log-normal **tempo prior** centered on 120 BPM (σ = 0.55). This
   biases the search toward DJ-typical ranges without forbidding extremes.
4. **Cluster + rank.** Per-frame estimates are clustered by 3 % relative
   proximity; explicit 0.5× and 2× alternates of the top cluster are always
   added so half/double-time is a clickable option. Each candidate is scored
   `0.45 × vote_share + 0.35 × autocorr_strength + 0.20 × prior_weight`.
5. **Confidence** blends the top candidate's score (60 %) with the
   inter-beat-interval consistency from `beat_track` (40 %).

### Key detection pipeline

1. **Constant-Q chroma** (`librosa.feature.chroma_cqt`) on the harmonic
   component, with a moderately large hop length (2048 samples ≈ 93 ms) so
   each frame represents about half a beat at 130 BPM.
2. **Sha'ath profiles** — empirically derived major/minor key profiles tuned
   on a corpus of popular music. Materially better than the textbook
   Krumhansl–Schmuckler profiles at distinguishing related keys like 3A/4A.
3. **Overlapping 2-second windows** (50 % overlap). Each window runs the
   profile correlation and votes for a (pitch_class, mode) tuple.
4. **Winning key** is the most-voted key; the runner-up is also kept and
   surfaced in the UI when confidence < 60 %.
5. **Camelot mapping** is a static lookup from (pitch_class, mode) → code.

## File-by-file walkthrough

### `app.py` — Flask server, routes, SQLite, Excel export

The HTTP layer. About 200 lines. Defines:

- **Database setup** (`init_db`). Creates the `tracks` table on first run;
  also runs an idempotent migration that adds new columns to old databases
  so an upgrade never breaks an existing library.
- **Routes:**
  - `GET /` → serves the SPA from `templates/index.html`.
  - `POST /api/analyze` → accepts a multipart audio upload, hands the bytes
    to `analyzer.analyze_bytes`, returns the JSON result. Does **not**
    persist anything — analysis and save are decoupled so the user can
    review/override before committing.
  - `POST /api/tracks` → persists a row from JSON. Accepts all override
    fields (`alt_bpm`, `alt_key`, etc.) so the UI can record what the user
    actually chose vs. what was originally detected.
  - `GET /api/tracks` → lists rows with optional `?q=` search across track
    name / artist / key / camelot / notes, plus `?sort=` and `?order=`.
  - `DELETE /api/tracks/<id>` → removes a row.
  - `GET /api/export` → streams a freshly built `.xlsx` of the whole
    library (`openpyxl`, header styling, frozen top row).
- **Configuration:** binds to `127.0.0.1:5057` only (no LAN exposure), 50 MB
  upload cap (a 30 s WebM clip is ~400 KB), no debug mode.

### `analyzer.py` — BPM + key + Camelot detection

The DSP. About 290 lines, pure Python, depends on `librosa`, `numpy`, and
`scipy`. The two public entry points are `analyze_bytes(audio_bytes, suffix)`
and `analyze_samples(y, sr)`; both return an `AnalysisResult` dataclass.

Internals are organised as small helpers so each step is independently
testable:

- `_multiband_onset_env` — bandpass + per-band onset detection.
- `_detect_bpm` — full BPM pipeline; returns primary BPM, confidence, and
  the ranked candidate list.
- `_estimate_key` — Sha'ath profile correlation on a single chroma vector.
- `_cluster_bpms` — group windowed tempo estimates by relative proximity.
- `_autocorr_strength`, `_ibi_consistency` — confidence components.

The Camelot mapping table lives at the top of the file as a static dict.

The file is runnable as a CLI for ad-hoc testing:

```bash
python3 analyzer.py /path/to/clip.wav
```

### `templates/index.html` — Web UI markup

A single-page layout, no framework. Three sections stack vertically:

1. **Capture panel** — Start/Stop button, timer, live VU meter (canvas).
2. **Result card** (hidden until analysis returns) — BPM / Camelot / key
   confidence tiles; alt-key picker (appears when key confidence < 60 %);
   BPM candidate picker; tap tempo button + manual BPM input; save form
   (track name / artist / notes) and Save/Discard actions.
3. **Library** — search box, Export to Excel link, sortable table of all
   saved rows.

Loads `static/style.css` and `static/app.js` via Flask's `url_for`.

### `static/style.css` — Styling

Dark theme tuned for late-night DJ workflows. CSS custom properties at the
top define the palette and component tokens; everything below uses them. No
external CSS framework. About 300 lines.

### `static/app.js` — Recording, upload, library rendering

The frontend logic, ~500 lines, no framework. Organised in one IIFE so
nothing leaks to the global scope. Responsibilities:

- **Recording lifecycle.** `getUserMedia` → `MediaRecorder` → blob → upload.
  Disables `echoCancellation`, `noiseSuppression`, and `autoGainControl`
  on the audio constraints to preserve transients for the beat tracker.
- **VU meter.** WebAudio AnalyserNode reads time-domain samples in a
  `requestAnimationFrame` loop and draws a gradient bar on a canvas.
- **Result rendering.** Displays BPM / Camelot / key with confidence
  pills; conditionally shows the alt-key panel and the BPM candidates panel.
- **Override handling.** Tap tempo uses `performance.now()` timestamps and
  computes BPM from a rolling window of the most recent 8 taps. Candidate
  pick, tap, and manual input are mutually exclusive — choosing one clears
  the others. The save payload swaps user choices into the primary fields
  and demotes the original detection to the `alt_*` fields.
- **Library.** Async fetch + render. Sortable headers (`th.sortable` on
  click toggles sort key/direction). Debounced search box (180 ms).

### `data/tracks.db` — SQLite library

Auto-created on first run. A single table; see Database schema below. Plain
SQLite file — open it with the `sqlite3` CLI, DB Browser for SQLite, or any
other tool that speaks SQLite.

### `data/` directory

Everything in `data/` is user data. Safe to back up by copying the folder;
safe to wipe to start fresh.

## How to recreate from scratch

If you ever lose the source or want to rebuild from notes:

1. **Make the folder layout:**

   ```
   vinyl_analyzer/
   ├── app.py
   ├── analyzer.py
   ├── README.md
   ├── data/                  (auto-created on first run)
   ├── static/
   │   ├── app.js
   │   └── style.css
   └── templates/
       └── index.html
   ```

2. **Install dependencies:**

   ```bash
   pip3 install --user flask librosa openpyxl
   ```

3. **Write `analyzer.py` first.** It has no Flask dependency, so you can
   verify it on a known WAV file (`python3 analyzer.py clip.wav`) before
   you have any HTTP layer. Get this working before moving on — it's the
   only piece that's genuinely hard.

4. **Write `app.py` second.** Start with just `GET /` returning a hello
   page and `POST /api/analyze` accepting a multipart upload and returning
   the analyzer's JSON. Add the SQLite layer and `/api/tracks` next, then
   `/api/export` last.

5. **Write the UI last.** Mock the recording flow with a static WAV file
   first (a file input that POSTs to `/api/analyze`) so you can iterate on
   the result panel without touching `MediaRecorder`. Wire up `MediaRecorder`
   once the rest works.

6. **Start the server:** `python3 app.py`, open <http://127.0.0.1:5057/>,
   grant mic permission, and capture a test clip.

## Database schema

```
tracks(
    id              INTEGER PRIMARY KEY,
    captured_at     TEXT,
    track_name      TEXT,
    artist          TEXT,
    bpm             REAL,
    bpm_confidence  REAL,
    key             TEXT,
    camelot         TEXT,
    key_confidence  REAL,
    alt_key         TEXT,        -- runner-up key (or original if user overrode)
    alt_camelot     TEXT,
    alt_share       REAL,
    alt_bpm         REAL,        -- runner-up BPM (or original if user overrode)
    alt_bpm_share   REAL,
    duration_s      REAL,
    notes           TEXT
)
```

The database is a plain SQLite file — query it directly with any SQLite tool:

```bash
sqlite3 data/tracks.db "SELECT camelot, COUNT(*) FROM tracks GROUP BY camelot;"
```

## Camelot wheel reference

| Key | Camelot | Key | Camelot |
|---|---|---|---|
| C major | 8B | A minor | 8A |
| G major | 9B | E minor | 9A |
| D major | 10B | B minor | 10A |
| A major | 11B | F# minor | 11A |
| E major | 12B | C# minor | 12A |
| B major | 1B | G# minor | 1A |
| F# major | 2B | D# minor | 2A |
| C# major | 3B | A# minor | 3A |
| G# major | 4B | F minor | 4A |
| D# major | 5B | C minor | 5A |
| A# major | 6B | G minor | 6A |
| F major | 7B | D minor | 7A |

For harmonic mixing, the next track should be the same code, one number up,
one number down, or the same number with the letter flipped (relative
major/minor).
