#!/usr/bin/env python3
"""Album -> BPM + Camelot key table.

    python cli.py --artist "Young Thug" --album "Jeffery"
    python cli.py --artist "Young Thug" --album "Jeffery" --merge-gaps gaps.csv

Sources, in priority order: manual/Tunebat entries you supply, then
GetSongBPM, then AcousticBrainz, then Deezer (tempo only). Whatever the
sources disagree about gets flagged instead of quietly averaged.
"""
from __future__ import annotations

import argparse
import csv
import os
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import export
import lookup
import sources

BASE_DIR = Path(__file__).resolve().parent.parent
DEFAULT_DB = Path(os.environ.get("VINYL_DATA_DIR", BASE_DIR / "data")) / "tracks.db"


def _slug(s: str) -> str:
    # Collapse runs, to match the djkeys shell wrapper's `tr -cs` behaviour --
    # otherwise --out defaults and wrapper-supplied paths disagree.
    return re.sub(r"_+", "_", "".join(
        c if c.isalnum() or c in "-_" else "_" for c in s)).strip("_")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--artist", required=True)
    ap.add_argument("--album", required=True)
    ap.add_argument("--year", type=int, help="Disambiguate reissues by release year")
    ap.add_argument("--release-mbid", help="Skip the search; use this MusicBrainz release")
    ap.add_argument("--use", default="getsongbpm,acousticbrainz,deezer",
                    help="Comma-separated source list")
    ap.add_argument("--refresh", action="store_true", help="Ignore cached track values")
    ap.add_argument("--out", type=Path, help="Output .xlsx path")
    ap.add_argument("--gaps", type=Path, help="Where to write the gaps CSV")
    ap.add_argument("--merge-gaps", type=Path,
                    help="Read a filled-in gaps CSV back into the cache first")
    ap.add_argument("--db", type=Path, default=DEFAULT_DB)
    ap.add_argument("--markdown", action="store_true", help="Also print a markdown table")
    args = ap.parse_args(argv)

    conn = lookup.open_cache(args.db)

    if args.merge_gaps:
        row = conn.execute("SELECT id FROM catalog_albums WHERE artist=? AND album=?",
                           (args.artist, args.album)).fetchone()
        if not row:
            print("No cached album to merge into. Run the lookup first.", file=sys.stderr)
            return 2
        album_id, n = row[0], 0
        with args.merge_gaps.open(encoding="utf-8") as fh:
            for r in csv.DictReader(fh):
                bpm, cam = (r.get("bpm") or "").strip(), (r.get("camelot") or "").strip()
                if not bpm and not cam:
                    continue
                if lookup.apply_manual(conn, album_id, int(r["position"]),
                                       float(bpm) if bpm else None, cam or None,
                                       source="tunebat"):
                    n += 1
        print(f"Merged {n} hand-filled track(s) as source 'tunebat'.")

    if "getsongbpm" in args.use:
        _k = os.environ.get("GETSONGBPM_API_KEY")
        if not _k:
            print("! GETSONGBPM_API_KEY not set — skipping GetSongBPM.\n"
                  "  It is the only provider with good coverage of post-2022 releases.\n"
                  "  Free key: https://getsongbpm.com/api (requires a backlink)\n",
                  file=sys.stderr)
        elif _k in sources.PLACEHOLDER_KEYS:
            print(f"! GETSONGBPM_API_KEY is set to the placeholder {_k!r}, not a real key.\n"
                  "  GetSongBPM will be skipped. Get a free key at "
                  "https://getsongbpm.com/api\n",
                  file=sys.stderr)

    artist, album = args.artist, args.album
    providers = [s.strip() for s in args.use.split(",") if s.strip()]

    def _run(a, b):
        return lookup.lookup_album(
            a, b, conn, year=args.year, release_mbid=args.release_mbid,
            use=providers, refresh=args.refresh,
        )

    print(f"\n{artist} — {album}")
    try:
        release, tracks = _run(artist, album)
    except LookupError as first_error:
        # Two positional arguments of the same shape are easy to transpose.
        # Before giving up, try them the other way round; if that resolves,
        # say so plainly rather than silently accepting the swap.
        if args.release_mbid:
            print(f"\n{first_error}", file=sys.stderr)
            return 1
        print("  No match. Retrying with the arguments swapped ...")
        try:
            release, tracks = _run(album, artist)
        except LookupError:
            print(f"\n{first_error}", file=sys.stderr)
            return 1
        artist, album = album, artist
        print(f"\n  Note: resolved with the arguments swapped — "
              f"artist={artist!r}, album={album!r}.\n"
              f"  The order is: djkeys \"ARTIST\" \"ALBUM\".\n")

    out = args.out or (BASE_DIR.parent / f"{_slug(artist)}_{_slug(album)}_keys.xlsx")
    export.write_xlsx(out, artist, album, release, tracks)

    gaps_path = args.gaps or out.with_name(out.stem + "_gaps.csv")
    _, n_gaps = export.write_gaps_csv(gaps_path, artist, tracks)

    resolved = sum(1 for t in tracks if t.chosen_source)
    print(f"\n{resolved}/{len(tracks)} tracks resolved.")

    summary = sources.summarise()
    if summary:
        print("\nWhy:")
        for line in summary:
            print(f"  {line}")
        if resolved == 0:
            print("\n  Nothing resolved. The usual cause is that GetSongBPM is not "
                  "configured:\n  it is the only provider here with broad coverage of "
                  "recent releases.\n  AcousticBrainz stopped accepting submissions in "
                  "2022 and Deezer\n  populates its tempo field only sparsely.")
    print(f"Excel : {out}")
    if n_gaps:
        print(f"Gaps  : {gaps_path}  ({n_gaps} track(s) need a look)")
        print("        Fill the bpm/camelot columns from the Tunebat URLs, then re-run")
        print(f"        with --merge-gaps {gaps_path.name}")
    else:
        gaps_path.unlink(missing_ok=True)

    if args.markdown:
        print("\n" + export.write_markdown(artist, album, tracks))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
