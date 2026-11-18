"""
Sliding plot buffer (no Qt): which samples to hold around a view, which of them still have
to be read, stitching old and new pieces, and the blank -> filter step every buffer goes
through.

Ranges are [start, stop) sample indices of the stream.
"""
from typing import List, Optional, Sequence, Tuple

import numpy as np

from dsp import apply_filter_chain
from dsp.blanking import apply_blanking, blank_mask

Range = Tuple[int, int]


def window_target(v0: int, v1: int, n_total: int, margin: int,
                  margin_after: Optional[int] = None) -> Range:
    """
    Samples to hold for the view [v0, v1): `margin` before it and `margin_after` (default:
    the same) after it, clamped to the recording.
    """
    if margin_after is None:
        margin_after = margin
    v0 = max(0, min(v0, n_total))
    v1 = max(v0, min(v1, n_total))
    return max(0, v0 - margin), min(n_total, v1 + margin_after)


def missing_pieces(have: Optional[Range], want: Range) -> List[Range]:
    """The 0-2 parts of `want` that `have` doesn't hold (left part first)."""
    w0, w1 = want
    if w1 <= w0:
        return []
    if have is None or have[1] <= have[0] or have[1] <= w0 or have[0] >= w1:
        return [(w0, w1)]
    pieces = []
    if w0 < have[0]:
        pieces.append((w0, have[0]))
    if have[1] < w1:
        pieces.append((have[1], w1))
    return pieces


def stitch(old: Optional[np.ndarray], old_range: Optional[Range],
           pieces: Sequence[Tuple[Range, np.ndarray]], want: Range) -> np.ndarray:
    """
    One (rows x samples) array covering `want`, from the old buffer's overlap with `want`
    plus newly read pieces. Always a new array: a trimmed view would keep the whole old
    buffer alive.
    """
    parts = list(pieces)
    if old is not None and old_range is not None:
        o0, o1 = max(old_range[0], want[0]), min(old_range[1], want[1])
        if o1 > o0:
            parts.append(((o0, o1), old[:, o0 - old_range[0]: o1 - old_range[0]]))
    parts.sort(key=lambda p: p[0][0])
    pos = want[0]
    for (a, b), arr in parts:
        if a != pos or arr.shape[1] != b - a:
            raise ValueError(f"Buffer pieces don't line up: expected sample {pos}, got [{a}, {b}) "
                             f"holding {arr.shape[1]} samples")
        pos = b
    if pos != want[1]:
        raise ValueError(f"Buffer pieces end at sample {pos}, not {want[1]}")
    return np.concatenate([arr for _, arr in parts], axis=1)


def process_buffer(raw: np.ndarray, raw_all: Optional[np.ndarray], time_axis: np.ndarray,
                   channels: Sequence[int], num_channels: int, filters, global_bypass: bool,
                   fs: float, blank_results=()):
    """
    Blank, then filter a plot buffer (the same steps as export and spike detection).

    Args:
        raw: displayed rows (rows = channels). Never modified.
        raw_all: every channel of the stream, when an active CAR/CMR needs it, else None.

    Returns:
        (processed float32 rows x samples, blanked-sample mask or None, filter warnings)
    """
    blank_results = list(blank_results or [])
    blanked = apply_blanking(raw, time_axis, channels, blank_results) if blank_results else raw
    blanked_all = raw_all
    if raw_all is not None and blank_results:
        blanked_all = apply_blanking(raw_all, time_axis, list(range(num_channels)), blank_results)
    data, warnings, _ = apply_filter_chain(
        blanked, filters, fs, global_bypass,
        all_channel_data=blanked_all,
        channel_indices=list(channels),
        with_vrms=False,  # the plot computes the view's RMS itself, blanked samples excluded
        # A blanked copy is scratch; the raw buffers themselves must stay untouched
        inplace_ok=(blanked_all is not raw_all) if raw_all is not None else (blanked is not raw),
    )
    del blanked, blanked_all
    mask = blank_mask(time_axis, list(channels), blank_results) if blank_results else None
    return np.asarray(data, dtype=np.float32), mask, warnings
