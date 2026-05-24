"""Audio analysis for vinyl snippets.

Estimates BPM and musical key (and the Camelot wheel code) from a short
audio clip.  Designed to be called from a Flask endpoint with raw bytes
of an uploaded audio blob (webm/ogg/wav — librosa+soundfile handle most
container formats via the audioread/soundfile backend).

BPM:
    librosa.beat.beat_track on the percussive component of an HPSS split.
    Confidence is based on inter-beat-interval consistency: tighter
    spread of beat spacings -> higher confidence.

Key:
    Sha'ath profile key estimation (Krumhansl-Schmuckler-style correlation
    but with profiles tuned for electronic/popular music — meaningfully
    better than the original Kostka–Payne profiles for DJ content).
    Estimation is performed on overlapping windows across the clip and
    the votes are combined; the winning key's vote share is the
    confidence.  A runner-up candidate is also returned so the UI can
    show alternatives when the call was close.

Camelot:
    Static lookup from (pitch class, mode) -> Camelot code.
"""

from __future__ import annotations

import tempfile
from dataclasses import dataclass, field, asdict
from typing import Optional

import numpy as np
import librosa
import librosa.feature.rhythm  # ensure lazy-loaded submodule is bound
import scipy.signal
import scipy.stats


# ---------------------------------------------------------------------------
# Camelot wheel mapping
# ---------------------------------------------------------------------------

PITCH_CLASSES = ["C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B"]

CAMELOT: dict[tuple[str, str], str] = {
    # Major keys (B side of the wheel)
    ("C",  "major"): "8B",
    ("G",  "major"): "9B",
    ("D",  "major"): "10B",
    ("A",  "major"): "11B",
    ("E",  "major"): "12B",
    ("B",  "major"): "1B",
    ("F#", "major"): "2B",
    ("C#", "major"): "3B",
    ("G#", "major"): "4B",
    ("D#", "major"): "5B",
    ("A#", "major"): "6B",
    ("F",  "major"): "7B",
    # Minor keys (A side of the wheel)
    ("A",  "minor"): "8A",
    ("E",  "minor"): "9A",
    ("B",  "minor"): "10A",
    ("F#", "minor"): "11A",
    ("C#", "minor"): "12A",
    ("G#", "minor"): "1A",
    ("D#", "minor"): "2A",
    ("A#", "minor"): "3A",
    ("F",  "minor"): "4A",
    ("C",  "minor"): "5A",
    ("G",  "minor"): "6A",
    ("D",  "minor"): "7A",
}

# Sha'ath profiles — empirically derived from a corpus of popular music
# (Sha'ath, "Estimation of Key in Digital Music Recordings", 2011).
# These give materially better results than Krumhansl-Schmuckler on the
# kind of electronic / dance material DJs work with.
SHAATH_MAJOR = np.array(
    [6.6, 2.0, 3.5, 2.3, 4.6, 4.0, 2.5, 5.2, 2.4, 3.7, 2.3, 3.4]
)
SHAATH_MINOR = np.array(
    [6.5, 2.7, 3.5, 5.4, 2.6, 3.5, 2.5, 5.2, 4.0, 2.7, 4.3, 3.2]
)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

@dataclass
class AnalysisResult:
    bpm: float
    bpm_confidence: float          # 0-1
    key: str                       # e.g. "A minor"
    key_pitch_class: str
    key_mode: str                  # "major" | "minor"
    camelot: str                   # e.g. "8A"
    key_confidence: float          # 0-1, share of windows that voted for this key
    alt_key: Optional[str] = None        # runner-up key name, e.g. "F minor"
    alt_camelot: Optional[str] = None    # runner-up Camelot, e.g. "4A"
    alt_share: float = 0.0               # vote share for the runner-up
    bpm_candidates: list = field(default_factory=list)  # [{bpm, score, source}, ...]
    alt_bpm: Optional[float] = None       # runner-up BPM candidate
    alt_bpm_share: float = 0.0            # score of runner-up BPM
    duration_seconds: float = 0.0
    sample_rate: int = 0

    def to_dict(self) -> dict:
        return asdict(self)


def analyze_bytes(audio_bytes: bytes, suffix: str = ".webm") -> AnalysisResult:
    """Run BPM + key analysis on an in-memory audio blob."""
    with tempfile.NamedTemporaryFile(suffix=suffix, delete=True) as tmp:
        tmp.write(audio_bytes)
        tmp.flush()
        y, sr = librosa.load(tmp.name, sr=22050, mono=True)
    return analyze_samples(y, sr)


def analyze_samples(y: np.ndarray, sr: int) -> AnalysisResult:
    if y.size == 0:
        raise ValueError("Empty audio buffer")

    duration = float(librosa.get_duration(y=y, sr=sr))

    # HPSS split: percussive for BPM, harmonic for key. Use a stronger
    # percussive margin to push tonal content out of the BPM signal —
    # important for sample-heavy hip-hop where pitched samples can fool
    # onset detection.
    y_harm, y_perc = librosa.effects.hpss(y, margin=(1.0, 4.0))

    # --- BPM -------------------------------------------------------------
    hop_length = 512
    bpm, bpm_conf, bpm_candidates = _detect_bpm(y_perc, sr, hop_length)

    alt_bpm = None
    alt_bpm_share = 0.0
    if len(bpm_candidates) > 1:
        alt_bpm = round(bpm_candidates[1]["bpm"], 2)
        alt_bpm_share = round(bpm_candidates[1]["score"], 3)

    # --- Key -------------------------------------------------------------
    # Constant-Q chroma on the harmonic component.  We use a moderately
    # large hop (4096 samples ≈ 186 ms at 22.05k) so each frame represents
    # ~half a beat at 130 BPM — plenty of harmonic information per frame.
    chroma = librosa.feature.chroma_cqt(y=y_harm, sr=sr, hop_length=2048)
    n_frames = chroma.shape[1]

    # Window-vote: split chroma into ~2-second windows with 50% overlap.
    win_seconds = 2.0
    win_frames = max(4, int(round(win_seconds * sr / 2048)))
    step_frames = max(2, win_frames // 2)

    votes: dict[tuple[int, str], int] = {}  # (pc, mode) -> count
    win_scores: dict[tuple[int, str], float] = {}  # accumulated correlation
    if n_frames < win_frames:
        # Clip shorter than a single window — analyse the whole thing.
        pc, mode, corr_best, corr_runner = _estimate_key(chroma.mean(axis=1))
        votes[(pc, mode)] = 1
        win_scores[(pc, mode)] = corr_best
    else:
        for start in range(0, n_frames - win_frames + 1, step_frames):
            chunk = chroma[:, start:start + win_frames].mean(axis=1)
            pc, mode, corr_best, _ = _estimate_key(chunk)
            votes[(pc, mode)] = votes.get((pc, mode), 0) + 1
            win_scores[(pc, mode)] = win_scores.get((pc, mode), 0.0) + corr_best

    total_votes = sum(votes.values()) or 1
    # Rank by (votes, then summed correlation) descending
    ranked = sorted(
        votes.items(),
        key=lambda kv: (kv[1], win_scores[kv[0]]),
        reverse=True,
    )
    (best_pc, best_mode), best_votes = ranked[0]
    best_name = PITCH_CLASSES[best_pc]
    best_camelot = CAMELOT[(best_name, best_mode)]
    key_confidence = best_votes / total_votes

    alt_key = None
    alt_camelot = None
    alt_share = 0.0
    if len(ranked) > 1:
        (alt_pc, alt_mode), alt_votes = ranked[1]
        alt_name = PITCH_CLASSES[alt_pc]
        alt_key = f"{alt_name} {alt_mode}"
        alt_camelot = CAMELOT[(alt_name, alt_mode)]
        alt_share = alt_votes / total_votes

    return AnalysisResult(
        bpm=round(bpm, 2),
        bpm_confidence=round(bpm_conf, 3),
        key=f"{best_name} {best_mode}",
        key_pitch_class=best_name,
        key_mode=best_mode,
        camelot=best_camelot,
        key_confidence=round(key_confidence, 3),
        alt_key=alt_key,
        alt_camelot=alt_camelot,
        alt_share=round(alt_share, 3),
        bpm_candidates=bpm_candidates,
        alt_bpm=alt_bpm,
        alt_bpm_share=alt_bpm_share,
        duration_seconds=round(duration, 2),
        sample_rate=sr,
    )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

# Tempo prior: log-normal centered on 120 BPM, broad enough to accommodate
# 80-180 BPM material with only mild penalty.  Used to bias both the
# windowed tempo estimator and the final candidate ranking.
_TEMPO_PRIOR = scipy.stats.lognorm(s=0.55, scale=120.0)


def _multiband_onset_env(y_perc: np.ndarray, sr: int, hop_length: int) -> np.ndarray:
    """Onset envelope blended from kick, snare and broadband percussion.

    Bandpassing the percussive component before onset detection focuses
    the detector on the actual beat carriers.  Hip-hop kicks live around
    40-200 Hz; snares around 200-800 Hz.  We blend the per-band onsets
    with the broadband envelope.
    """
    def bp(lo, hi):
        sos = scipy.signal.butter(4, [lo, hi], btype="band", fs=sr, output="sos")
        return scipy.signal.sosfiltfilt(sos, y_perc).astype(np.float32, copy=False)

    nyq = sr / 2.0 - 1.0
    y_kick  = bp(40, min(200, nyq))
    y_snare = bp(200, min(800, nyq))

    osd = lambda x: librosa.onset.onset_strength(y=x, sr=sr, hop_length=hop_length)
    e_kick  = osd(y_kick)
    e_snare = osd(y_snare)
    e_full  = osd(y_perc)

    # Normalise each then weight: kick is the most reliable beat carrier,
    # broadband helps when the kick is soft (sample-based production).
    def norm(x):
        m = x.max()
        return x / m if m > 0 else x

    return 0.5 * norm(e_kick) + 0.3 * norm(e_full) + 0.2 * norm(e_snare)


def _autocorr_strength(onset_env: np.ndarray, sr: int, hop_length: int, bpm: float) -> float:
    """Normalised autocorrelation peak of the onset envelope at the lag
    corresponding to ``bpm``.  0–1.
    """
    if bpm <= 0 or onset_env.size < 16:
        return 0.0
    frames_per_beat = (60.0 / bpm) * sr / hop_length
    if frames_per_beat <= 1 or frames_per_beat >= onset_env.size:
        return 0.0

    x = onset_env - onset_env.mean()
    if not np.any(x):
        return 0.0
    ac = np.correlate(x, x, mode="full")
    ac = ac[ac.size // 2:]
    ac /= ac[0] if ac[0] != 0 else 1.0

    lag = int(round(frames_per_beat))
    win = max(1, int(frames_per_beat * 0.05))
    lo, hi = max(1, lag - win), min(ac.size, lag + win + 1)
    if hi <= lo:
        return 0.0
    return float(max(0.0, min(1.0, ac[lo:hi].max())))


def _ibi_consistency(beat_frames: np.ndarray, sr: int, hop_length: int) -> float:
    """Score the consistency of inter-beat intervals (0-1)."""
    if beat_frames is None or len(beat_frames) < 4:
        return 0.0
    beat_times = librosa.frames_to_time(beat_frames, sr=sr, hop_length=hop_length)
    ibis = np.diff(beat_times)
    if ibis.size < 2 or ibis.mean() <= 0:
        return 0.0
    cv = float(np.std(ibis) / np.mean(ibis))
    cv_score = max(0.0, 1.0 - cv / 0.15)
    median_ibi = float(np.median(ibis))
    inlier_share = float(np.mean(np.abs(ibis - median_ibi) / median_ibi < 0.05))
    return float(max(0.0, min(1.0, 0.4 * cv_score + 0.6 * inlier_share)))


def _cluster_bpms(bpms: np.ndarray, rel_tol: float = 0.03) -> list[tuple[float, int]]:
    """Cluster a list of BPM estimates into groups by relative proximity.

    Returns ``[(median_bpm, count), ...]`` sorted by count desc.
    """
    if bpms is None or len(bpms) == 0:
        return []
    sorted_bpms = sorted(float(b) for b in bpms if b > 0)
    if not sorted_bpms:
        return []
    clusters: list[list[float]] = [[sorted_bpms[0]]]
    for b in sorted_bpms[1:]:
        ref = np.median(clusters[-1])
        if abs(b - ref) / ref < rel_tol:
            clusters[-1].append(b)
        else:
            clusters.append([b])
    out = [(float(np.median(c)), len(c)) for c in clusters]
    out.sort(key=lambda x: x[1], reverse=True)
    return out


def _detect_bpm(y_perc: np.ndarray, sr: int, hop_length: int = 512) -> tuple[float, float, list[dict]]:
    """Detect BPM with bandpass + multi-band onset + tempo prior + top-K candidates.

    Returns ``(primary_bpm, confidence_0_to_1, ranked_candidates)``.
    ``ranked_candidates`` is a list of ``{bpm, score, share, strength, source}``
    dicts in descending score order, top 4.
    """
    onset_env = _multiband_onset_env(y_perc, sr, hop_length)

    # Windowed tempo estimates (per-frame), biased by the prior.
    per_window = librosa.feature.rhythm.tempo(
        onset_envelope=onset_env,
        sr=sr,
        hop_length=hop_length,
        aggregate=None,
        prior=_TEMPO_PRIOR,
    )
    clusters = _cluster_bpms(per_window, rel_tol=0.03)
    total = sum(c[1] for c in clusters) or 1

    # Primary beat-track estimate (also benefits from the multi-band onset)
    try:
        bt_bpm, beat_frames = librosa.beat.beat_track(
            onset_envelope=onset_env, sr=sr, hop_length=hop_length, units="frames"
        )
        bt_bpm = float(np.asarray(bt_bpm).item())
    except Exception:
        bt_bpm, beat_frames = 0.0, np.array([])

    # Add beat_track's BPM as a candidate if not already represented
    candidates_bpms = [c[0] for c in clusters]
    if bt_bpm > 0 and not any(abs(bt_bpm - b) / b < 0.03 for b in candidates_bpms):
        clusters.append((bt_bpm, max(1, total // 10)))
        total += clusters[-1][1]
        candidates_bpms.append(bt_bpm)

    # Explicit octave alternates of the top-vote cluster, so half/double-time
    # is always a clickable alternative even when the per-window estimator
    # never produced it.
    if clusters:
        top_bpm = clusters[0][0]
        for factor in (0.5, 2.0):
            alt = top_bpm * factor
            if 60 <= alt <= 200 and not any(abs(alt - b) / b < 0.03 for b in candidates_bpms):
                clusters.append((alt, 0))
                candidates_bpms.append(alt)

    # Score each candidate
    ibi_score = _ibi_consistency(beat_frames, sr, hop_length)
    scored: list[dict] = []
    for bpm_c, votes in clusters:
        share = votes / total if total else 0.0
        strength = _autocorr_strength(onset_env, sr, hop_length, bpm_c)
        prior_w = float(_TEMPO_PRIOR.pdf(bpm_c) / _TEMPO_PRIOR.pdf(120.0))  # ~1 at 120
        prior_w = max(0.05, min(1.0, prior_w))
        # Weighted combination.  Share is the strongest signal (per-window
        # agreement) but beat strength is a critical tie-breaker for
        # syncopated material where multiple windows can disagree.
        score = 0.45 * share + 0.35 * strength + 0.20 * prior_w
        scored.append({
            "bpm": round(float(bpm_c), 2),
            "score": round(float(score), 3),
            "share": round(float(share), 3),
            "strength": round(float(strength), 3),
            "source": "octave_alt" if votes == 0 else "windowed",
        })

    scored.sort(key=lambda d: d["score"], reverse=True)
    scored = scored[:4]

    if not scored:
        return 0.0, 0.0, []

    primary_bpm = scored[0]["bpm"]
    # Confidence: blend top candidate's score with IBI consistency from
    # beat_track.  Both lie in [0,1].
    confidence = float(min(1.0, 0.6 * scored[0]["score"] + 0.4 * ibi_score))
    return primary_bpm, confidence, scored


def _estimate_key(chroma_mean: np.ndarray) -> tuple[int, str, float, float]:
    """Sha'ath profile key estimation on a single (12,)-vector.

    Returns ``(pitch_class_index, mode, best_correlation, runner_up_correlation)``.
    """
    if not np.any(chroma_mean):
        return 0, "minor", 0.0, 0.0

    chroma_mean = chroma_mean / (np.linalg.norm(chroma_mean) + 1e-9)

    scores: list[tuple[float, int, str]] = []
    for pc in range(12):
        major_rot = np.roll(SHAATH_MAJOR, pc)
        minor_rot = np.roll(SHAATH_MINOR, pc)
        scores.append((float(_pearson(chroma_mean, major_rot)), pc, "major"))
        scores.append((float(_pearson(chroma_mean, minor_rot)), pc, "minor"))

    scores.sort(key=lambda t: t[0], reverse=True)
    best_corr, best_pc, best_mode = scores[0]
    runner_corr = scores[1][0]
    return best_pc, best_mode, best_corr, runner_corr


def _pearson(a: np.ndarray, b: np.ndarray) -> float:
    a = a - a.mean()
    b = b - b.mean()
    denom = (np.linalg.norm(a) * np.linalg.norm(b)) + 1e-9
    return float(np.dot(a, b) / denom)


if __name__ == "__main__":  # pragma: no cover
    import sys
    path = sys.argv[1]
    with open(path, "rb") as f:
        data = f.read()
    suffix = "." + path.rsplit(".", 1)[-1]
    result = analyze_bytes(data, suffix=suffix)
    for k, v in result.to_dict().items():
        print(f"{k:>22}: {v}")
