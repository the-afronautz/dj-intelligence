"""Data sources: one function per external service, each returning plain dicts.

Split into two jobs on purpose:

  TRACKLIST  (album + artist -> ordered tracks)   -> MusicBrainz
  FEATURES   (track -> BPM + key)                 -> GetSongBPM, AcousticBrainz, Deezer

Spotify is deliberately absent. Its /audio-features endpoint was closed to new
apps in Nov 2024, and its album endpoints only duplicate what MusicBrainz gives
without OAuth.

Every function returns None / [] on failure rather than raising, so one dead
service never takes down a run.
"""
from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Any, Optional

from camelot import to_camelot, pretty_key

# Strings people paste from documentation instead of a real key.
PLACEHOLDER_KEYS = {"your_key", "your_key_here", "paste_your_real_key_here",
                    "YOUR_API_KEY", "xxxxxxxx", "changeme"}

UA = "DJIntelligence-CatalogLookup/0.1 ( https://github.com/the-afronautz )"
TIMEOUT = 20

# MusicBrainz asks for <=1 req/sec. This is the shared throttle.
_last_call: dict[str, float] = {}

# Per-run provider outcomes, so a run can explain *why* it found nothing
# rather than printing an undifferentiated "NO DATA" per track.
# provider -> {outcome: count}
STATS: dict[str, dict[str, int]] = {}
_last_status: dict[str, object] = {}


def reset_stats() -> None:
    STATS.clear()


def note(provider: str, outcome: str) -> None:
    STATS.setdefault(provider, {})
    STATS[provider][outcome] = STATS[provider].get(outcome, 0) + 1


def summarise() -> list[str]:
    """Human-readable one-liner per provider that was actually consulted."""
    labels = {
        "hit": "returned data",
        "not-in-db": "not in database (404)",
        "no-tempo": "matched, but no tempo on file",
        "no-match": "no match for the title",
        "no-key": "skipped - no API key",
        "bad-response": "unreadable response",
        "network-error": "network error",
    }
    out = []
    for prov in sorted(STATS):
        parts = [f"{n} {labels.get(k, k)}" for k, n in sorted(STATS[prov].items(), key=lambda kv: -kv[1])]
        out.append(f"{prov}: " + ", ".join(parts))
    return out


def _throttle(host: str, min_interval: float) -> None:
    prev = _last_call.get(host, 0.0)
    wait = min_interval - (time.time() - prev)
    if wait > 0:
        time.sleep(wait)
    _last_call[host] = time.time()


def _get_json(url: str, min_interval: float = 0.0) -> Optional[Any]:
    host = urllib.parse.urlparse(url).netloc
    if min_interval:
        _throttle(host, min_interval)
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "application/json"})
    _last_status["code"] = None
    _last_status["error"] = None
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
            _last_status["code"] = getattr(resp, "status", 200)
            return json.loads(resp.read().decode("utf-8", "replace"))
    except urllib.error.HTTPError as e:
        _last_status["code"] = e.code
    except Exception as e:
        _last_status["error"] = f"{type(e).__name__}: {e}"
    return None


# ---------------------------------------------------------------------------
# TRACKLIST: MusicBrainz
# ---------------------------------------------------------------------------

def find_release(artist: str, album: str, prefer_year: Optional[int] = None) -> Optional[dict]:
    """Find the best release MBID for an artist/album pair."""
    q = f'release:"{album}" AND artist:"{artist}"'
    url = ("https://musicbrainz.org/ws/2/release/?query="
           + urllib.parse.quote(q) + "&fmt=json&limit=25")
    data = _get_json(url, min_interval=1.1)
    if not data or not data.get("releases"):
        return None

    def score(r: dict) -> tuple:
        title_exact = r.get("title", "").strip().lower() == album.strip().lower()
        official = (r.get("status") or "").lower() == "official"
        year = int((r.get("date") or "0")[:4] or 0)
        year_match = (prefer_year is not None and year == prefer_year)
        # Prefer: exact title, official, year match, higher MB score, earlier date
        return (title_exact, official, year_match, r.get("score", 0), -year if year else 0)

    best = max(data["releases"], key=score)
    return {
        "mbid": best["id"],
        "title": best.get("title"),
        "date": best.get("date"),
        "status": best.get("status"),
        "artist": (best.get("artist-credit") or [{}])[0].get("name"),
        "track_count": best.get("track-count"),
        "candidates": [
            {"mbid": r["id"], "title": r.get("title"), "date": r.get("date"),
             "status": r.get("status"), "tracks": r.get("track-count")}
            for r in sorted(data["releases"], key=score, reverse=True)[:8]
        ],
    }


def get_tracklist(release_mbid: str) -> list[dict]:
    """Ordered tracks for a release, with recording MBIDs."""
    url = (f"https://musicbrainz.org/ws/2/release/{release_mbid}"
           "?inc=recordings+artist-credits&fmt=json")
    data = _get_json(url, min_interval=1.1)
    if not data:
        return []
    out: list[dict] = []
    n = 0
    for medium in data.get("media", []):
        for t in medium.get("tracks", []):
            n += 1
            rec = t.get("recording", {})
            credits = rec.get("artist-credit") or t.get("artist-credit") or []
            feats = "".join(
                (c.get("name", "") if isinstance(c, dict) else str(c)) + (c.get("joinphrase", "") if isinstance(c, dict) else "")
                for c in credits
            )
            out.append({
                "position": n,
                "disc": medium.get("position", 1),
                "title": t.get("title") or rec.get("title"),
                "recording_mbid": rec.get("id"),
                "length_ms": t.get("length") or rec.get("length"),
                "credited_artists": feats or None,
            })
    return out


# ---------------------------------------------------------------------------
# FEATURES
# ---------------------------------------------------------------------------

def from_acousticbrainz(recording_mbid: Optional[str]) -> Optional[dict]:
    """Free, keyed on MusicBrainz recording MBID. Essentia-derived.

    Submissions stopped in 2022, so nothing newer than that has values --
    but back-catalogue coverage is excellent, which suits crate digging.
    """
    if not recording_mbid:
        note("acousticbrainz", "no-match")
        return None
    url = f"https://acousticbrainz.org/api/v1/{recording_mbid}/low-level"
    data = _get_json(url)
    if not isinstance(data, dict):
        code, err = _last_status.get("code"), _last_status.get("error")
        note("acousticbrainz",
             "not-in-db" if code == 404 else "network-error" if err else "bad-response")
        return None
    bpm = (data.get("rhythm") or {}).get("bpm")
    tonal = data.get("tonal") or {}
    key_key, key_scale = tonal.get("key_key"), tonal.get("key_scale")
    if bpm is None and not key_key:
        note("acousticbrainz", "bad-response")
        return None
    key_str = f"{key_key} {key_scale}" if key_key and key_scale else None
    note("acousticbrainz", "hit")
    return {
        "source": "acousticbrainz",
        "bpm": round(float(bpm), 1) if bpm else None,
        "key": pretty_key(key_str) if key_str else None,
        "camelot": to_camelot(key_str) if key_str else None,
        "confidence": tonal.get("key_strength"),
    }


def from_getsongbpm(title: str, artist: str, api_key: Optional[str] = None) -> Optional[dict]:
    """Free API. Requires registration + a backlink to getsongbpm.com.

    Set GETSONGBPM_API_KEY in the environment. 3,000 requests/hour.
    """
    api_key = api_key or os.environ.get("GETSONGBPM_API_KEY")
    if not api_key or api_key in PLACEHOLDER_KEYS:
        note("getsongbpm", "no-key")
        return None
    lookup = urllib.parse.quote(f"song:{title} artist:{artist}")
    url = (f"https://api.getsongbpm.com/search/?api_key={api_key}"
           f"&type=both&lookup={lookup}&limit=5")
    data = _get_json(url, min_interval=0.5)
    if not isinstance(data, dict):
        note("getsongbpm", "network-error" if _last_status.get("error") else "bad-response")
        return None
    results = data.get("search")
    if not isinstance(results, list) or not results:
        note("getsongbpm", "no-match")
        return None
    note("getsongbpm", "hit")
    hit = results[0]
    tempo = hit.get("tempo")
    key_of = hit.get("key_of")
    return {
        "source": "getsongbpm",
        "bpm": float(tempo) if tempo else None,
        "key": pretty_key(key_of) if key_of else None,
        "camelot": to_camelot(key_of) if key_of else None,
        "matched_title": hit.get("title"),
        "matched_artist": (hit.get("artist") or {}).get("name"),
    }


def from_deezer(title: str, artist: str) -> Optional[dict]:
    """No auth, no key required. BPM only -- useful purely as a second opinion
    on tempo, which is exactly where half/double-time errors show up.
    """
    q = urllib.parse.quote(f'artist:"{artist}" track:"{title}"')
    data = _get_json(f"https://api.deezer.com/search?q={q}&limit=5", min_interval=0.3)
    if not isinstance(data, dict) or not data.get("data"):
        note("deezer", "no-match")
        return None
    track_id = data["data"][0].get("id")
    if not track_id:
        note("deezer", "no-match")
        return None
    full = _get_json(f"https://api.deezer.com/track/{track_id}", min_interval=0.3)
    if not isinstance(full, dict):
        note("deezer", "bad-response")
        return None
    bpm = full.get("bpm")
    if not bpm:  # Deezer returns 0 when unknown -- very common
        note("deezer", "no-tempo")
        return None
    note("deezer", "hit")
    return {
        "source": "deezer",
        "bpm": round(float(bpm), 1),
        "key": None,
        "camelot": None,
        "matched_title": full.get("title"),
    }


def tunebat_search_url(title: str, artist: str) -> str:
    """Tunebat has no public API -- their /API page points at Songstats, a paid
    analytics partner, not a track-features endpoint. So this returns a search
    URL for a human (or Claude in Chrome) to open.
    """
    return "https://tunebat.com/Search?q=" + urllib.parse.quote(f"{artist} {title}")
