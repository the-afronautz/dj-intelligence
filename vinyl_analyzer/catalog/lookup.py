"""Orchestration: tracklist -> provider chain -> reconciliation -> cache.

The design principle here is that disagreement between sources is the
product, not a nuisance. Every source's answer is stored; the "chosen"
value is just the highest-priority one that exists, and anything the other
sources dispute gets flagged rather than silently overwritten.
"""
from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

import sources
from camelot import camelot_distance

# Highest priority first. A source only wins a field if every source above it
# left that field empty.
PRIORITY = ["manual", "tunebat", "getsongbpm", "acousticbrainz", "deezer", "librosa"]

BPM_TOLERANCE = 0.02      # 2% -- covers rounding between services
LOW_BPM_FLOOR = 90.0      # below this, suspect a half-time reading
HIGH_BPM_CEIL = 180.0     # above this, suspect a double-time reading


# ---------------------------------------------------------------------------
# Cache
# ---------------------------------------------------------------------------

SCHEMA = """
CREATE TABLE IF NOT EXISTS catalog_albums (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    artist         TEXT NOT NULL,
    album          TEXT NOT NULL,
    release_mbid   TEXT,
    release_date   TEXT,
    fetched_at     TEXT NOT NULL,
    UNIQUE(artist, album)
);
CREATE TABLE IF NOT EXISTS catalog_tracks (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    album_id       INTEGER NOT NULL REFERENCES catalog_albums(id) ON DELETE CASCADE,
    position       INTEGER,
    title          TEXT,
    credited       TEXT,
    recording_mbid TEXT,
    bpm            REAL,
    key            TEXT,
    camelot        TEXT,
    chosen_source  TEXT,
    flags          TEXT,
    raw_sources    TEXT,
    updated_at     TEXT,
    UNIQUE(album_id, position)
);
CREATE INDEX IF NOT EXISTS idx_catalog_tracks_album ON catalog_tracks(album_id);
"""


def open_cache(db_path: Path) -> sqlite3.Connection:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    try:
        conn = sqlite3.connect(db_path)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        conn.executescript(SCHEMA)
        conn.commit()
        return conn
    except sqlite3.OperationalError as e:
        # SQLite needs real file locking. Network shares, some FUSE mounts and
        # sync folders don't provide it and fail here with "disk I/O error".
        raise SystemExit(
            f"Could not open the cache at {db_path}\n"
            f"  sqlite3: {e}\n\n"
            "SQLite needs a filesystem that supports file locking. If this path is "
            "on a network share, a mounted volume or a sync folder, point the cache "
            "at local disk instead:\n"
            f"    --db ~/.vinyl_analyzer/tracks.db"
        ) from e


# ---------------------------------------------------------------------------
# Reconciliation
# ---------------------------------------------------------------------------

@dataclass
class TrackResult:
    position: int
    title: str
    credited: Optional[str] = None
    recording_mbid: Optional[str] = None
    bpm: Optional[float] = None
    key: Optional[str] = None
    camelot: Optional[str] = None
    chosen_source: Optional[str] = None
    flags: list[str] = field(default_factory=list)
    raw: list[dict] = field(default_factory=list)

    def to_row(self) -> dict:
        d = asdict(self)
        d["flags"] = ",".join(self.flags)
        d["raw"] = json.dumps(self.raw)
        return d


def _bpm_agree(a: float, b: float) -> tuple[bool, bool]:
    """(agree, only_after_octave_shift)"""
    if abs(a - b) <= a * BPM_TOLERANCE:
        return True, False
    for mult in (2.0, 0.5):
        if abs(a * mult - b) <= (a * mult) * BPM_TOLERANCE:
            return True, True
    return False, False


def reconcile(readings: list[dict]) -> dict:
    """Fold several source readings into one chosen value plus flags."""
    readings = [r for r in readings if r]
    flags: list[str] = []
    ordered = sorted(
        readings,
        key=lambda r: PRIORITY.index(r["source"]) if r.get("source") in PRIORITY else 99,
    )

    bpm = key = camelot = chosen = None
    for r in ordered:
        if bpm is None and r.get("bpm"):
            bpm, chosen = float(r["bpm"]), r["source"]
        if camelot is None and r.get("camelot"):
            camelot, key = r["camelot"], r.get("key")
            if chosen is None:
                chosen = r["source"]

    # --- cross-source BPM disagreement
    bpms = [(r["source"], float(r["bpm"])) for r in ordered if r.get("bpm")]
    if bpm is not None:
        for src, other in bpms:
            if src == chosen:
                continue
            agree, octave = _bpm_agree(bpm, other)
            if not agree:
                flags.append(f"bpm-conflict:{src}={other:g}")
            elif octave:
                flags.append(f"bpm-octave:{src}={other:g}")

    # --- cross-source key disagreement
    cams = [(r["source"], r["camelot"]) for r in ordered if r.get("camelot")]
    for src, other in cams:
        if other == camelot:
            continue
        dist = camelot_distance(camelot, other)
        if dist == 1:
            flags.append(f"key-adjacent:{src}={other}")
        else:
            flags.append(f"key-conflict:{src}={other}")

    # --- single-source octave suspicion (the Webbie 79 BPM case)
    if bpm is not None and len(bpms) == 1:
        if bpm < LOW_BPM_FLOOR:
            flags.append(f"half-time-suspect(x2={bpm * 2:g})")
        elif bpm > HIGH_BPM_CEIL:
            flags.append(f"double-time-suspect(x0.5={bpm / 2:g})")

    if not readings:
        flags.append("no-data")

    return {
        "bpm": bpm, "key": key, "camelot": camelot,
        "chosen_source": chosen, "flags": flags, "raw": ordered,
    }


# ---------------------------------------------------------------------------
# Orchestration
# ---------------------------------------------------------------------------

def lookup_album(
    artist: str,
    album: str,
    conn: sqlite3.Connection,
    year: Optional[int] = None,
    release_mbid: Optional[str] = None,
    use: Optional[list[str]] = None,
    refresh: bool = False,
    log=print,
) -> tuple[dict, list[TrackResult]]:
    use = use or ["getsongbpm", "acousticbrainz", "deezer"]
    sources.reset_stats()

    if release_mbid:
        release = {"mbid": release_mbid, "title": album, "date": None,
                   "artist": artist, "candidates": []}
    else:
        log(f"  MusicBrainz: searching for {artist!r} / {album!r} ...")
        release = sources.find_release(artist, album, prefer_year=year)
        if not release:
            raise LookupError(
                f"No MusicBrainz release found.\n"
                f"    artist = {artist!r}\n"
                f"    album  = {album!r}\n"
                "  The order is: djkeys \"ARTIST\" \"ALBUM\".\n"
                "  If those look swapped, swap them. Otherwise check the spelling "
                "against musicbrainz.org, or pass --release-mbid directly."
            )
        log(f"  MusicBrainz: {release['title']} ({release.get('date')}) "
            f"[{release.get('status')}] {release['mbid']}")

    tracks = sources.get_tracklist(release["mbid"])
    if not tracks:
        raise LookupError(f"Release {release['mbid']} returned no tracks.")
    log(f"  Tracklist: {len(tracks)} tracks")

    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    # NB: avoid RETURNING -- it needs SQLite 3.35+, and macOS system Python
    # can ship older. A follow-up SELECT works everywhere.
    conn.execute(
        "INSERT INTO catalog_albums (artist, album, release_mbid, release_date, fetched_at) "
        "VALUES (?,?,?,?,?) ON CONFLICT(artist, album) DO UPDATE SET "
        "release_mbid=excluded.release_mbid, release_date=excluded.release_date, "
        "fetched_at=excluded.fetched_at",
        (artist, album, release["mbid"], release.get("date"), now),
    )
    album_id = conn.execute(
        "SELECT id FROM catalog_albums WHERE artist=? AND album=?", (artist, album)
    ).fetchone()[0]

    results: list[TrackResult] = []
    for t in tracks:
        cached = conn.execute(
            "SELECT * FROM catalog_tracks WHERE album_id=? AND position=?",
            (album_id, t["position"]),
        ).fetchone()
        if cached and not refresh and cached["chosen_source"]:
            tr = TrackResult(
                position=t["position"], title=t["title"],
                credited=t.get("credited_artists"), recording_mbid=t.get("recording_mbid"),
                bpm=cached["bpm"], key=cached["key"], camelot=cached["camelot"],
                chosen_source=cached["chosen_source"],
                flags=[f for f in (cached["flags"] or "").split(",") if f],
                raw=json.loads(cached["raw_sources"] or "[]"),
            )
            results.append(tr)
            log(f"  {t['position']:>2}. {t['title'][:44]:<44} (cached)")
            continue

        readings: list[dict] = []
        if "acousticbrainz" in use:
            readings.append(sources.from_acousticbrainz(t.get("recording_mbid")))
        if "getsongbpm" in use:
            readings.append(sources.from_getsongbpm(t["title"], artist))
        if "deezer" in use:
            readings.append(sources.from_deezer(t["title"], artist))

        rec = reconcile(readings)
        tr = TrackResult(
            position=t["position"], title=t["title"],
            credited=t.get("credited_artists"), recording_mbid=t.get("recording_mbid"),
            bpm=rec["bpm"], key=rec["key"], camelot=rec["camelot"],
            chosen_source=rec["chosen_source"], flags=rec["flags"], raw=rec["raw"],
        )
        results.append(tr)
        save_track(conn, album_id, tr)
        mark = tr.camelot or "--"
        log(f"  {t['position']:>2}. {t['title'][:44]:<44} "
            f"{(f'{tr.bpm:g}' if tr.bpm else '--'):>6} {mark:>4}  "
            f"{tr.chosen_source or 'NO DATA'}"
            + (f"  [{'; '.join(tr.flags)}]" if tr.flags else ""))

    conn.commit()
    return {"album_id": album_id, **release}, results


def save_track(conn: sqlite3.Connection, album_id: int, tr: TrackResult) -> None:
    conn.execute(
        """INSERT INTO catalog_tracks
           (album_id, position, title, credited, recording_mbid, bpm, key, camelot,
            chosen_source, flags, raw_sources, updated_at)
           VALUES (?,?,?,?,?,?,?,?,?,?,?,?)
           ON CONFLICT(album_id, position) DO UPDATE SET
             title=excluded.title, credited=excluded.credited,
             recording_mbid=excluded.recording_mbid, bpm=excluded.bpm,
             key=excluded.key, camelot=excluded.camelot,
             chosen_source=excluded.chosen_source, flags=excluded.flags,
             raw_sources=excluded.raw_sources, updated_at=excluded.updated_at""",
        (album_id, tr.position, tr.title, tr.credited, tr.recording_mbid,
         tr.bpm, tr.key, tr.camelot, tr.chosen_source, ",".join(tr.flags),
         json.dumps(tr.raw), datetime.now(timezone.utc).isoformat(timespec="seconds")),
    )


def apply_manual(conn: sqlite3.Connection, album_id: int, position: int,
                 bpm: Optional[float], camelot: Optional[str],
                 key: Optional[str] = None, source: str = "manual") -> bool:
    """Fold a hand-supplied (or Tunebat-sourced) value into a cached track,
    re-running reconciliation so conflicts against the API sources still show.
    """
    row = conn.execute(
        "SELECT * FROM catalog_tracks WHERE album_id=? AND position=?",
        (album_id, position),
    ).fetchone()
    if not row:
        return False
    raw = json.loads(row["raw_sources"] or "[]")
    raw = [r for r in raw if r.get("source") != source]
    raw.append({"source": source, "bpm": float(bpm) if bpm else None,
                "key": key, "camelot": camelot})
    rec = reconcile(raw)
    tr = TrackResult(
        position=position, title=row["title"], credited=row["credited"],
        recording_mbid=row["recording_mbid"], bpm=rec["bpm"], key=rec["key"],
        camelot=rec["camelot"], chosen_source=rec["chosen_source"],
        flags=rec["flags"], raw=rec["raw"],
    )
    save_track(conn, album_id, tr)
    conn.commit()
    return True
