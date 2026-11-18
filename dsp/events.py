"""Stimulation event detection on TTL-like analog lines."""
from typing import List, Tuple
import numpy as np

from models.analog import DetectionSettings


def auto_threshold(x: np.ndarray, active_low: bool, n_trim: int = 50) -> Tuple[float, float]:
    """
    Midpoint between the line's idle level (median) and its active level.

    The active level is the n_trim-th most extreme sample in the active direction,
    so a few glitches don't set it. Returns (threshold, span between the levels).
    """
    x = x[~np.isnan(x)]
    if x.size == 0:
        return 0.0, 0.0
    step = max(1, x.size // 1_000_000)
    base = float(np.median(x[::step]))
    n_trim = min(n_trim, x.size)
    if active_low:
        act = float(np.partition(x, n_trim - 1)[n_trim - 1])
    else:
        act = float(np.partition(x, x.size - n_trim)[x.size - n_trim])
    return (base + act) / 2, abs(act - base)


def find_trains(
    x: np.ndarray, threshold: float, active_low: bool, gap_samples: int
) -> Tuple[np.ndarray, np.ndarray]:
    """
    Group threshold crossings into events.

    A new event starts when a rising edge is more than gap_samples after the previous
    rising edge, so a pulse train becomes one event. Returns (onset, offset) sample
    indices; offset is the falling edge of the event's last pulse.
    """
    with np.errstate(invalid="ignore"):
        active = x < threshold if active_low else x > threshold
    n = active.size
    rise = np.flatnonzero(active[1:] & ~active[:-1]) + 1
    fall = np.flatnonzero(~active[1:] & active[:-1]) + 1
    if n and active[0]:
        rise = np.concatenate(([0], rise))
    if n and active[-1]:
        fall = np.concatenate((fall, [n - 1]))
    if rise.size == 0:
        return np.zeros(0, np.int64), np.zeros(0, np.int64)

    starts = np.flatnonzero(np.concatenate(([True], np.diff(rise) > gap_samples)))
    last_rise = np.concatenate((starts[1:] - 1, [rise.size - 1]))
    onset = rise[starts]
    j = np.searchsorted(fall, rise[last_rise], side="right")
    offset = np.where(j < fall.size, fall[np.minimum(j, fall.size - 1)], n - 1)
    return onset.astype(np.int64), offset.astype(np.int64)


def detect_line_events(
    x: np.ndarray, fs: float, settings: DetectionSettings
) -> Tuple[np.ndarray, np.ndarray, float]:
    """
    Detect events on one analog line.

    Returns (onset_s, offset_s, threshold used). A flat line yields no events.
    """
    if settings.threshold is not None:
        threshold = float(settings.threshold)
    else:
        threshold, span = auto_threshold(x, settings.active_low)
        if span <= 0:
            return np.zeros(0), np.zeros(0), threshold

    gap = max(1, int(round(settings.merge_gap_ms * 1e-3 * fs)))
    onset, offset = find_trains(x, threshold, settings.active_low, gap)

    min_n = settings.min_duration_ms * 1e-3 * fs
    if min_n > 0:
        keep = (offset - onset) >= min_n
        onset, offset = onset[keep], offset[keep]

    return onset / fs, offset / fs, threshold
