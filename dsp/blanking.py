"""Blank stimulation artifacts: compute blank intervals and interpolate across them."""
from typing import Iterable, List, Sequence, Tuple

import numpy as np
from scipy.interpolate import CubicSpline

from models.blanking import BlankResult, BlankingSettings, CUBIC
from models.filter_config import FilterConfig, FilterType
from .filters import design_filter, apply_filter

# Band used only to measure artifact recovery (the NSP pipeline's spike band)
SPIKE_BAND_HZ = (250.0, 5000.0)
CUBIC_ANCHORS = 4  # samples on each side of a gap used by the cubic spline


def spike_band(data: np.ndarray, fs: float) -> np.ndarray:
    """Zero-phase 4th-order Butterworth band-pass over SPIKE_BAND_HZ (upper edge kept below Nyquist)."""
    low, high = SPIKE_BAND_HZ
    high = min(high, 0.45 * fs)
    sos = design_filter(
        FilterConfig(FilterType.BANDPASS, order=4, cutoff_low=low, cutoff_high=high), fs
    )
    if sos is None:
        raise ValueError(f"Sample rate {fs:g} Hz is too low to measure the {low:g} Hz+ spike band")
    return apply_filter(data, sos)


def window_rms(data: np.ndarray, window_n: int) -> np.ndarray:
    """RMS of consecutive non-overlapping windows along the last axis: (..., n_windows)."""
    n_win = data.shape[-1] // window_n
    trimmed = data[..., : n_win * window_n]
    shaped = trimmed.reshape(trimmed.shape[:-1] + (n_win, window_n))
    return np.sqrt(np.mean(shaped ** 2, axis=-1))


def clean_windows(
    intervals: Iterable[Tuple[float, float]],
    duration: float,
    n: int = 20,
    guard_s: float = 3.0,
    length_s: float = 1.0,
) -> List[float]:
    """
    Start times of up to n windows of length_s, at least guard_s from every interval,
    spread evenly over the recording.
    """
    blocked = [(a - guard_s, b + guard_s) for a, b in intervals]
    candidates = [
        t for t in np.arange(0.0, duration - length_s, length_s)
        if all(t + length_s <= a or t >= b for a, b in blocked)
    ]
    if len(candidates) <= n:
        return [float(t) for t in candidates]
    picks = np.linspace(0, len(candidates) - 1, n).round().astype(int)
    return [float(candidates[i]) for i in picks]


def adaptive_end_windows(
    below: np.ndarray, n_windows: int, min_win: int, max_win: int
) -> Tuple[np.ndarray, np.ndarray]:
    """
    Per channel, the first window index k >= min_win where windows k..k+n_windows-1 are
    all quiet (below[ch, k] True). Channels with no such k (before max_win) get max_win.

    Args:
        below: (n_channels, n_total_windows) quiet flags, window 0 starting at the offset.

    Returns:
        (end window index per channel, capped flag per channel)
    """
    n_ch, n_tot = below.shape
    n_windows = max(1, n_windows)
    if n_tot < n_windows:
        return np.full(n_ch, max_win), np.ones(n_ch, bool)
    # run[ch, k] = True when windows k .. k+n_windows-1 are all quiet
    kernel = np.ones(n_windows, int)
    counts = np.array([np.convolve(row.astype(int), kernel, mode="valid") for row in below])
    run = counts == n_windows
    run[:, :min_win] = False
    run[:, max_win + 1:] = False
    found = run.any(axis=1)
    first = np.where(found, run.argmax(axis=1), max_win)
    return np.minimum(first, max_win), ~found


def basic_result(modality: str, stream: str, events, channels: Sequence[int],
                 settings: BlankingSettings) -> BlankResult:
    """Same fixed-pad interval on every channel for every event (Basic mode blanks all trials)."""
    trials = list(events)
    starts = np.array([e.onset - settings.pre_ms * 1e-3 for e in trials])
    ends = np.array([e.offset + settings.post_ms * 1e-3 for e in trials])
    n_c = len(channels)
    return BlankResult(
        modality=modality,
        stream=stream,
        channels=list(channels),
        trials=[e.number for e in trials],
        start_s=np.repeat(np.maximum(starts, 0.0)[:, None], n_c, axis=1).reshape(len(trials), n_c),
        end_s=np.repeat(ends[:, None], n_c, axis=1).reshape(len(trials), n_c),
        interpolation=settings.interpolation,
        mode=settings.mode,
    )


def _fill_gap(row: np.ndarray, i0: int, i1: int, method: str):
    """Replace row[i0:i1] by interpolating between the samples just outside it."""
    n = row.size
    has_left, has_right = i0 > 0, i1 < n
    if not has_left and not has_right:
        row[i0:i1] = 0.0
        return
    if not has_left:
        row[i0:i1] = row[i1]
        return
    if not has_right:
        row[i0:i1] = row[i0 - 1]
        return

    xs = np.arange(i0, i1)
    if method == CUBIC and i0 >= CUBIC_ANCHORS and i1 + CUBIC_ANCHORS <= n:
        anchors = np.r_[np.arange(i0 - CUBIC_ANCHORS, i0), np.arange(i1, i1 + CUBIC_ANCHORS)]
        row[i0:i1] = CubicSpline(anchors, row[anchors])(xs)
    else:
        row[i0:i1] = np.interp(xs, [i0 - 1, i1], [row[i0 - 1], row[i1]])


def apply_blanking(
    data: np.ndarray,
    time_axis: np.ndarray,
    channel_indices: Sequence[int],
    results: Iterable[BlankResult],
) -> np.ndarray:
    """
    Return a float copy of data (rows = channel_indices) with every blank interval that
    overlaps this buffer interpolated across. Intervals cut by the buffer edge hold the
    edge value. When no interval overlaps, ``data`` itself is returned (no copy).
    """
    data = np.asarray(data)
    n_samples = data.shape[-1] if data.ndim else 0
    if n_samples == 0:
        return data

    # Collect the gaps first, so a buffer with nothing to blank costs no copy
    t0, t1 = time_axis[0], time_axis[-1]
    row_of = {ch: i for i, ch in enumerate(channel_indices)}
    gaps = []  # (row, i0, i1, interpolation)
    for res in results:
        if res.start_s.size == 0:
            continue
        for col, ch in enumerate(res.channels):
            row = row_of.get(ch)
            if row is None:
                continue
            starts, ends = res.start_s[:, col], res.end_s[:, col]
            keep = (ends >= t0) & (starts <= t1)
            for s, e in zip(starts[keep], ends[keep]):
                i0 = int(np.searchsorted(time_axis, s, side="left"))
                i1 = int(np.searchsorted(time_axis, e, side="right"))
                if i1 > i0:
                    gaps.append((row, i0, i1, res.interpolation))
    if not gaps:
        return data

    out = np.array(data, dtype=float, copy=True)
    was_1d = out.ndim == 1
    if was_1d:
        out = out.reshape(1, -1)
    for row, i0, i1, interpolation in gaps:
        _fill_gap(out[row], i0, i1, interpolation)
    return out[0] if was_1d else out


def blank_mask(
    time_axis: np.ndarray, channel_indices: Sequence[int], results: Iterable[BlankResult]
) -> np.ndarray:
    """Boolean (rows x samples) mask of blanked samples, for one buffer only (never a whole recording)."""
    mask = np.zeros((len(channel_indices), time_axis.size), dtype=bool)
    if time_axis.size == 0:
        return mask
    t0, t1 = time_axis[0], time_axis[-1]
    row_of = {ch: i for i, ch in enumerate(channel_indices)}
    for res in results:
        if res.start_s.size == 0:
            continue
        for col, ch in enumerate(res.channels):
            row = row_of.get(ch)
            if row is None:
                continue
            starts, ends = res.start_s[:, col], res.end_s[:, col]
            keep = (ends >= t0) & (starts <= t1)
            for s, e in zip(starts[keep], ends[keep]):
                i0 = int(np.searchsorted(time_axis, s, side="left"))
                i1 = int(np.searchsorted(time_axis, e, side="right"))
                mask[row, i0:i1] = True
    return mask


def blank_spans(results: Iterable[BlankResult], channels: Sequence[int],
                start: float, end: float) -> List[Tuple[float, float]]:
    """Merged time spans in [start, end] blanked on any of the given channels (for shading)."""
    spans = []
    wanted = set(channels)
    for res in results:
        cols = [i for i, ch in enumerate(res.channels) if ch in wanted]
        if not cols or res.start_s.size == 0:
            continue
        s = res.start_s[:, cols].min(axis=1)
        e = res.end_s[:, cols].max(axis=1)
        keep = (e >= start) & (s <= end)
        spans += list(zip(s[keep], e[keep]))
    spans.sort()
    merged = []
    for s, e in spans:
        if merged and s <= merged[-1][1]:
            merged[-1] = (merged[-1][0], max(merged[-1][1], e))
        else:
            merged.append((float(s), float(e)))
    return merged
