#!/usr/bin/env python3
"""Verbose provider diagnostic. Shows what sources._get_json hides.

    python3 diagnose.py f9d39d1d-6e38-4db5-b06f-ef7da13c37b4 "The Weeknd"

Prints, for each stage: the exact URL, the HTTP status or the real exception,
and the fields that were parsed out of the response.
"""
from __future__ import annotations
import json, os, sys, urllib.error, urllib.parse, urllib.request

UA = "DJIntelligence-CatalogLookup/0.1 ( https://github.com/the-afronautz )"


def fetch(url, label):
    print(f"\n  {label}")
    print(f"    URL    {url[:150]}")
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=25) as r:
            raw = r.read().decode("utf-8", "replace")
            print(f"    STATUS {r.status}  ({len(raw)} bytes)")
            try:
                return json.loads(raw)
            except Exception as e:
                print(f"    PARSE  FAILED: {e}")
                print(f"    BODY   {raw[:200]}")
                return None
    except urllib.error.HTTPError as e:
        print(f"    STATUS {e.code} {e.reason}")
        try:
            print(f"    BODY   {e.read().decode('utf-8','replace')[:200]}")
        except Exception:
            pass
    except Exception as e:
        print(f"    ERROR  {type(e).__name__}: {e}")
    return None


def main():
    release_mbid = sys.argv[1] if len(sys.argv) > 1 else "f9d39d1d-6e38-4db5-b06f-ef7da13c37b4"
    artist = sys.argv[2] if len(sys.argv) > 2 else "The Weeknd"

    print("=" * 72)
    print("ENVIRONMENT")
    key = os.environ.get("GETSONGBPM_API_KEY")
    if not key:
        print("  GETSONGBPM_API_KEY  not set")
    elif key in ("your_key", "paste_your_real_key_here", "your_key_here"):
        print(f"  GETSONGBPM_API_KEY  set to the PLACEHOLDER {key!r} — not a real key")
    else:
        print(f"  GETSONGBPM_API_KEY  set ({len(key)} chars, starts {key[:4]}...)")

    print("\n" + "=" * 72)
    print("STAGE 1 — MusicBrainz tracklist: are recording MBIDs coming through?")
    data = fetch(
        f"https://musicbrainz.org/ws/2/release/{release_mbid}?inc=recordings+artist-credits&fmt=json",
        "MusicBrainz release")
    tracks = []
    if data:
        for medium in data.get("media", []):
            for t in medium.get("tracks", []):
                rec = t.get("recording", {})
                tracks.append({"title": t.get("title") or rec.get("title"),
                               "mbid": rec.get("id")})
        print(f"\n    parsed {len(tracks)} tracks")
        for t in tracks[:4]:
            print(f"      {t['title'][:38]:<40} recording_mbid = {t['mbid']}")
        missing = sum(1 for t in tracks if not t["mbid"])
        print(f"\n    >>> tracks WITHOUT a recording MBID: {missing} of {len(tracks)}")
        if missing:
            print("    >>> This alone would make AcousticBrainz return nothing for them.")

    if not tracks:
        print("\n  Cannot continue without a tracklist.")
        return 1

    probe = next((t for t in tracks if t["mbid"]), tracks[0])

    print("\n" + "=" * 72)
    print(f"STAGE 2 — AcousticBrainz for {probe['title']!r}")
    if probe["mbid"]:
        ab = fetch(f"https://acousticbrainz.org/api/v1/{probe['mbid']}/low-level", "AcousticBrainz low-level")
        if isinstance(ab, dict):
            print(f"    top-level keys: {sorted(ab.keys())[:8]}")
            rhythm, tonal = ab.get("rhythm") or {}, ab.get("tonal") or {}
            print(f"    rhythm.bpm      = {rhythm.get('bpm')}")
            print(f"    tonal.key_key   = {tonal.get('key_key')}")
            print(f"    tonal.key_scale = {tonal.get('key_scale')}")
            if not rhythm and not tonal:
                print("    >>> Neither key present — response shape differs from what sources.py expects.")
    else:
        print("    skipped — no recording MBID")

    print("\n" + "=" * 72)
    print(f"STAGE 3 — Deezer for {probe['title']!r}")
    q = urllib.parse.quote(f'artist:"{artist}" track:"{probe["title"]}"')
    dz = fetch(f"https://api.deezer.com/search?q={q}&limit=5", "Deezer search (quoted syntax)")
    if isinstance(dz, dict):
        hits = dz.get("data") or []
        print(f"    hits: {len(hits)}  error: {dz.get('error')}")
        if hits:
            tid = hits[0]["id"]
            full = fetch(f"https://api.deezer.com/track/{tid}", "Deezer track detail")
            if isinstance(full, dict):
                print(f"    title = {full.get('title')!r}   bpm = {full.get('bpm')!r}")
                if not full.get("bpm"):
                    print("    >>> bpm is 0/absent — Deezer has no tempo for this track.")
        else:
            print("    >>> No hits. Trying the plain (unquoted) query as a control:")
            q2 = urllib.parse.quote(f'{artist} {probe["title"]}')
            dz2 = fetch(f"https://api.deezer.com/search?q={q2}&limit=3", "Deezer search (plain)")
            if isinstance(dz2, dict) and dz2.get("data"):
                print(f"    >>> Plain query returned {len(dz2['data'])} hits — the quoted syntax is the problem.")
                t0 = dz2["data"][0]
                print(f"        first hit: {t0.get('title')!r} by {(t0.get('artist') or {}).get('name')!r}")

    print("\n" + "=" * 72)
    print("Done. Paste this whole output back.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
