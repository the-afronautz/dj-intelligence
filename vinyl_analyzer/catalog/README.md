# catalog — album → BPM + Camelot lookup

Give it an artist and an album; it returns every track in running order with
BPM, Camelot key, which source said so, and a flag on anything the sources
disagree about. Writes an .xlsx and a gaps CSV.

## Why it's built this way

The job splits into two problems that want different tools:

| | Problem | Solved by |
|---|---|---|
| 1 | album + artist → **ordered tracklist** | MusicBrainz |
| 2 | track → **BPM + key** | GetSongBPM → AcousticBrainz → Deezer |

Keeping them separate is the whole point. A BPM search page can tell you what
132 BPM belongs to a song called "Harambe"; it cannot tell you that Harambe is
track 7, or that the "Spider or Jeffery" in the results is a 2025 record. Album
identity has to come from a release database, not a features database.

**Spotify is deliberately not used.** Its `/audio-features` endpoint was closed
to new apps in November 2024, and its album endpoints only duplicate what
MusicBrainz gives without making you carry OAuth.

**Tunebat has no public API.** Their `/API` page points at Songstats, a paid
analytics partner — not a track-features endpoint. So Tunebat stays a *manual*
source here: the tool emits a CSV of unresolved tracks with a Tunebat search URL
per row, you (or Claude in Chrome) fill in the two columns, and `--merge-gaps`
folds them back in with `tunebat` recorded as the source.

## Sources

| Source | Auth | Gives | Notes |
|---|---|---|---|
| MusicBrainz | none | tracklist, order, recording MBIDs | 1 req/sec, User-Agent required |
| GetSongBPM | free key | BPM + key | 3,000/hr; a backlink to getsongbpm.com is required by their terms |
| AcousticBrainz | none | BPM + key | Essentia-derived, keyed on recording MBID. Frozen July 2022 — great for back catalogue, blank for anything newer |
| Deezer | none | BPM only | No key, but a free second opinion on tempo, which is exactly where octave errors hide |
| Tunebat | manual | BPM + key | via the gaps CSV round trip |

## Reconciliation

Sources are never averaged. Priority order is
`manual > tunebat > getsongbpm > acousticbrainz > deezer > librosa`,
and every source's raw answer is kept in the `Sources` sheet and in
`catalog_tracks.raw_sources`. Disagreements become flags:

- `bpm-octave:deezer=158` — sources agree modulo ×2/÷2. Beatmatching is fine; the number is a half-time reading.
- `half-time-suspect(x2=158)` — only one source, and it's under 90 BPM. This is the Webbie case.
- `key-adjacent:acousticbrainz=4B` — relative major/minor. Common detector ambiguity, harmonically compatible.
- `key-conflict:acousticbrainz=9A` — genuinely different keys. Don't trust either without checking.
- `no-data` — nothing found; goes in the gaps CSV.

Flagged rows are shaded yellow in the Excel output, missing rows red.

## Setup

```bash
# Free GetSongBPM key: https://getsongbpm.com/api  (registration + backlink)
export GETSONGBPM_API_KEY=xxxxxxxx
```

No new dependencies — stdlib `urllib` plus the `openpyxl` already in
`requirements.txt`.

## Use

```bash
cd vinyl_analyzer/catalog

python3 cli.py --artist "Young Thug" --album "Jeffery" --markdown

# ... fill the bpm/camelot columns in the gaps CSV from the Tunebat URLs ...
python3 cli.py --artist "Young Thug" --album "Jeffery" \
    --merge-gaps Young_Thug_Jeffery_keys_gaps.csv
```

Useful flags: `--year 2016` to disambiguate reissues, `--release-mbid` to skip
the search entirely, `--refresh` to ignore cached values, `--use deezer` to
limit sources.

Results cache into the same `data/tracks.db` the Flask app uses, in
`catalog_albums` / `catalog_tracks` — separate from the `tracks` table your
mic analysis writes, so the two never collide. Re-running is free.

## Tests

```bash
python3 test_catalog.py    # 40 assertions, no network
```

Fakes the network layer with the verified JEFFERY values and checks Camelot
conversion, every flag path, the cache round trip, the gap-fill merge, and the
xlsx output.

## The accuracy ceiling

GetSongBPM, AcousticBrainz and Tunebat are all algorithmic estimates, and two
of them ultimately trace back to similar analysis. Agreement between them is
weaker evidence than it looks. The only genuinely independent engines are
Mixed In Key, rekordbox, Serato — and `analyzer.py` one directory up, which
runs librosa on actual audio. For anything going into a set, this tool is for
narrowing the field; your own analysis is the answer.
