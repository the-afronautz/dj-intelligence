"""Key-string parsing and Camelot wheel conversion.

Web sources spell keys a dozen different ways: "Ab major", "A♭ major",
"G#m", "Am", "F minor", "Dbm", "open key 5d". This module normalises all of
them to (pitch_class, mode) and then to a Camelot code.

The CAMELOT table matches the one in analyzer.py so that values pulled from
the web and values from your own librosa analysis are directly comparable.
"""
from __future__ import annotations

import re
from typing import Optional, Tuple

# Canonical sharp spelling per pitch class
PITCH_NAMES = ["C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B"]

# Every spelling we might see -> pitch class index
_NOTE_TO_PC = {
    "C": 0, "B#": 0,
    "C#": 1, "DB": 1,
    "D": 2,
    "D#": 3, "EB": 3,
    "E": 4, "FB": 4,
    "F": 5, "E#": 5,
    "F#": 6, "GB": 6,
    "G": 7,
    "G#": 8, "AB": 8,
    "A": 9,
    "A#": 10, "BB": 10,
    "B": 11, "CB": 11,
}

# (pitch name, mode) -> Camelot
CAMELOT: dict[Tuple[str, str], str] = {
    ("C", "major"): "8B",  ("A", "minor"): "8A",
    ("G", "major"): "9B",  ("E", "minor"): "9A",
    ("D", "major"): "10B", ("B", "minor"): "10A",
    ("A", "major"): "11B", ("F#", "minor"): "11A",
    ("E", "major"): "12B", ("C#", "minor"): "12A",
    ("B", "major"): "1B",  ("G#", "minor"): "1A",
    ("F#", "major"): "2B", ("D#", "minor"): "2A",
    ("C#", "major"): "3B", ("A#", "minor"): "3A",
    ("G#", "major"): "4B", ("F", "minor"): "4A",
    ("D#", "major"): "5B", ("C", "minor"): "5A",
    ("A#", "major"): "6B", ("G", "minor"): "6A",
    ("F", "major"): "7B",  ("D", "minor"): "7A",
}

CAMELOT_TO_KEY = {v: k for k, v in CAMELOT.items()}

_MINOR_WORDS = {"minor", "min", "m", "moll", "aeolian"}
_MAJOR_WORDS = {"major", "maj", "dur", "ionian", ""}


def _clean(s: str) -> str:
    return (s.replace("♭", "b").replace("♯", "#")
             .replace("−", "-").strip())


def parse_key(text: Optional[str]) -> Optional[Tuple[str, str]]:
    """Parse a key string into (pitch_name, mode). Returns None if unparseable.

    >>> parse_key("A♭ major")
    ('G#', 'major')
    >>> parse_key("C#m")
    ('C#', 'minor')
    """
    if not text:
        return None
    t = _clean(str(text))
    if not t:
        return None

    # Already a Camelot code?
    m = re.fullmatch(r"\s*(\d{1,2})\s*([ABab])\s*", t)
    if m:
        code = f"{int(m.group(1))}{m.group(2).upper()}"
        return CAMELOT_TO_KEY.get(code)

    m = re.match(r"^\s*([A-Ga-g])\s*([#b]?)\s*(.*)$", t)
    if not m:
        return None
    note = (m.group(1) + m.group(2)).upper()
    rest = m.group(3).strip().lower().strip(" .-_")

    pc = _NOTE_TO_PC.get(note)
    if pc is None:
        return None

    first = rest.split()[0] if rest else ""
    if first in _MINOR_WORDS or rest in _MINOR_WORDS:
        mode = "minor"
    elif first in _MAJOR_WORDS or rest in _MAJOR_WORDS:
        mode = "major"
    else:
        return None

    return PITCH_NAMES[pc], mode


def to_camelot(key_text: Optional[str], mode_text: Optional[str] = None) -> Optional[str]:
    """Convert a key (and optional separate mode) to a Camelot code."""
    if key_text is None:
        return None
    combined = f"{key_text} {mode_text}" if mode_text else str(key_text)
    parsed = parse_key(combined)
    if parsed is None:
        parsed = parse_key(key_text)
    if parsed is None:
        return None
    return CAMELOT.get(parsed)


def pretty_key(key_text: Optional[str], mode_text: Optional[str] = None) -> Optional[str]:
    """Normalised human-readable key, e.g. 'G# major'."""
    combined = f"{key_text} {mode_text}" if mode_text else key_text
    parsed = parse_key(combined) or parse_key(key_text)
    if not parsed:
        return None
    return f"{parsed[0]} {parsed[1]}"


def camelot_distance(a: Optional[str], b: Optional[str]) -> Optional[int]:
    """Steps around the wheel between two Camelot codes (0 = same, 1 = compatible).

    A letter flip at the same number counts as 1 (relative major/minor).
    """
    if not a or not b:
        return None
    ma, mb = re.fullmatch(r"(\d{1,2})([AB])", a), re.fullmatch(r"(\d{1,2})([AB])", b)
    if not ma or not mb:
        return None
    na, la = int(ma.group(1)), ma.group(2)
    nb, lb = int(mb.group(1)), mb.group(2)
    ring = min((na - nb) % 12, (nb - na) % 12)
    return ring + (0 if la == lb else 1)
