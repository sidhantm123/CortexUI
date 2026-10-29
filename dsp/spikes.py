"""Spike threshold crossings: robust noise estimate, crossing detection, peak alignment."""
from typing import List, Optional, Sequence

import numpy as np

NEGATIVE = "negative"
POSITIVE = "positive"
BOTH = "both"
POLARITIES = (NEGATIVE, POSITIVE, BOTH)

# median(|x|) / 0.6745 estimates the noise standard deviation without being inflated by spikes
MAD_TO_SIGMA = 0.6745


def robust_sigma(x: np.ndarray, exclude: Optional[np.ndarray] = None) -> np.ndarray:
    """
    Per-row noise sigma = median(|x|) / 0.6745, leaving out excluded (e.g. blanked) samples.

    Returns an array with one value per row; NaN for rows with no usable samples.
    """
    x = np.atleast_2d(x)
    out = np.full(x.shape[0], np.nan)
    for i, row in enumerate(x):
        if exclude is not None:
            row = row[~np.atleast_2d(exclude)[i]]
        if row.size:
            out[i] = np.median(np.abs(row)) / MAD_TO_SIGMA
    return out


def _edges(row: np.ndarray, thr: float, polarity: str) -> np.ndarray:
    """Indices where the row first goes past the threshold (previous sample not past it)."""
    idx = []
    if polarity in (NEGATIVE, BOTH):
        below = row < -thr
        idx.append(np.flatnonzero(below[1:] & ~below[:-1]) + 1)
    if polarity in (POSITIVE, BOTH):
        above = row > thr
        idx.append(np.flatnonzero(above[1:] & ~above[:-1]) + 1)
    return np.unique(np.concatenate(idx)) if idx else np.zeros(0, np.int64)


def detect_crossings(
    x: np.ndarray,
    thresholds: Sequence[float],
    polarity: str = NEGATIVE,
    refractory_n: int = 0,
    exclude: Optional[np.ndarray] = None,
    last: Optional[Sequence[Optional[int]]] = None,
) -> List[np.ndarray]:
    """
    Threshold crossings per row.

    Args:
        thresholds: positive magnitude per row (k * sigma); crossings are below -thr
                    (negative), above +thr (positive) or either (both). NaN = no crossings.
        refractory_n: crossings closer than this many samples to the previous kept one are dropped.
        exclude: boolean mask like x; crossings on or right after excluded samples are dropped.
        last: index (relative to this array, may be negative) of each row's previous kept
              crossing, so the refractory period carries across chunks.

    Returns:
        One int64 array of sample indices per row.
    """
    x = np.atleast_2d(x)
    if exclude is not None:
        exclude = np.atleast_2d(exclude)
    result = []
    for i, row in enumerate(x):
        thr = thresholds[i]
        if not np.isfinite(thr) or row.size < 2:
            result.append(np.zeros(0, np.int64))
            continue
        idx = _edges(row, float(thr), polarity)
        if exclude is not None and idx.size:
            ex = exclude[i]
            idx = idx[~ex[idx] & ~ex[idx - 1]]
        if refractory_n > 0 and idx.size:
            kept = []
            prev = last[i] if last is not None and last[i] is not None else None
            for j in idx:
                if prev is None or j - prev >= refractory_n:
                    kept.append(j)
                    prev = j
            idx = np.array(kept, dtype=np.int64)
        result.append(idx.astype(np.int64))
    return result


def align_to_peak(row: np.ndarray, idx: np.ndarray, polarity: str, window_n: int) -> np.ndarray:
    """Index of the extremum within window_n samples from each crossing (trough for negative)."""
    if idx.size == 0:
        return idx
    window_n = max(1, window_n)
    ends = np.minimum(idx + window_n, row.size)
    peaks = np.empty_like(idx)
    for k, (a, b) in enumerate(zip(idx, ends)):
        seg = row[a:b]
        if polarity == NEGATIVE:
            peaks[k] = a + int(np.argmin(seg))
        elif polarity == POSITIVE:
            peaks[k] = a + int(np.argmax(seg))
        else:
            peaks[k] = a + int(np.argmax(np.abs(seg)))
    return peaks
