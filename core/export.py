"""Chunked int16 binary export of a stream, optionally blanked and/or filtered."""
from typing import Callable, List, Optional, Sequence, Tuple

import numpy as np

from dsp import apply_filter_chain
from dsp.blanking import apply_blanking, CUBIC_ANCHORS
from models.filter_config import FilterType

INT16_MIN, INT16_MAX = -32768, 32767


def _t(i: int, fs: float) -> float:
    """Time for sample i that readers' int(t * fs) maps back to exactly i."""
    return (i + 0.5) / fs


def blank_sample_spans(results, fs: float) -> List[Tuple[int, int]]:
    """
    Merged [start, stop) sample spans blanked on any channel by any result, widened by the
    samples interpolation uses as anchors on each side (so a chunk never splits them).
    """
    margin = CUBIC_ANCHORS + 2
    spans = []
    for res in results:
        if res.start_s.size == 0:
            continue
        s = res.start_s.min(axis=1)
        e = res.end_s.max(axis=1)
        spans += [
            (int(np.floor(a * fs)) - margin, int(np.ceil(b * fs)) + 1 + margin) for a, b in zip(s, e)
        ]
    spans.sort()
    merged: List[Tuple[int, int]] = []
    for a, b in spans:
        if merged and a <= merged[-1][1]:
            merged[-1] = (merged[-1][0], max(merged[-1][1], b))
        else:
            merged.append((a, b))
    return merged


def chunk_bounds(i_start: int, i_stop: int, chunk_n: int,
                 avoid: Sequence[Tuple[int, int]] = ()) -> List[Tuple[int, int]]:
    """
    Split [i_start, i_stop) into chunks of about chunk_n samples. A boundary that would
    fall inside an avoid span moves to the end of that span, so blank interpolation
    always sees real samples on both sides.
    """
    bounds = []
    a = i_start
    while a < i_stop:
        b = min(i_stop, a + chunk_n)
        for s, e in avoid:
            if s < b < e:
                b = min(i_stop, e)
        bounds.append((a, b))
        a = b
    return bounds


def export_binary(
    reader,
    stream: str,
    channels: List[int],
    i_start: int,
    i_stop: int,
    fs: float,
    num_channels: int,
    filepath: str,
    scale: float,
    layout: str = "interlaced",
    blank_results=(),
    filters=None,
    chunk_s: float = 5.0,
    pad_s: float = 1.0,
    progress: Optional[Callable[[int], None]] = None,
    cancelled: Optional[Callable[[], bool]] = None,
) -> dict:
    """
    Write samples [i_start, i_stop) of the given channels as int16.

    Processing per chunk: blanking (if blank_results) -> filter chain (if filters)
    -> x scale -> clip -> int16. Each chunk is read with pad_s of extra data on each side
    (trimmed before writing) so zero-phase filters have no edge effects at chunk joins.

    Returns:
        {"n_samples": samples per channel written, "n_clipped": samples clipped to int16,
         "cancelled": bool}
    """
    blank_results = list(blank_results or [])
    filters = [f for f in (filters or []) if not f.bypassed]
    needs_all = any(f.filter_type in (FilterType.CAR, FilterType.CMR) for f in filters)
    load_channels = list(range(num_channels)) if needs_all else list(channels)
    rows = [load_channels.index(c) for c in channels]

    n_total = i_stop - i_start
    n_ch = len(channels)
    pad_n = int(round(pad_s * fs)) if filters else 0
    bounds = chunk_bounds(
        i_start, i_stop, max(1, int(round(chunk_s * fs))), blank_sample_spans(blank_results, fs)
    )

    sequential = None
    interlaced = None
    if layout == "interlaced":
        interlaced = open(filepath, "wb")
    else:
        sequential = np.memmap(filepath, dtype=np.int16, mode="w+", shape=(n_ch, max(1, n_total)))

    n_clipped = 0
    try:
        for k, (a, b) in enumerate(bounds):
            if cancelled is not None and cancelled():
                return {"n_samples": a - i_start, "n_clipped": n_clipped, "cancelled": True}

            r0, r1 = max(0, a - pad_n), b + pad_n
            data, time_axis = reader.get_stream_data(
                stream, channels=load_channels, start_time=_t(r0, fs), end_time=_t(r1, fs)
            )
            data = np.atleast_2d(data)
            if blank_results:
                data = apply_blanking(data, time_axis, load_channels, blank_results)
            if filters:
                out, _, _ = apply_filter_chain(
                    data[rows], filters, fs,
                    all_channel_data=data if needs_all else None,
                    channel_indices=channels,
                )
            else:
                out = data[rows]

            out = np.atleast_2d(out)[:, a - r0: a - r0 + (b - a)] * scale
            n_clipped += int(np.count_nonzero((out < INT16_MIN) | (out > INT16_MAX)))
            chunk = np.clip(np.rint(out), INT16_MIN, INT16_MAX).astype(np.int16)

            if interlaced is not None:
                interlaced.write(np.ascontiguousarray(chunk.T).tobytes())
            else:
                sequential[:, a - i_start: a - i_start + chunk.shape[1]] = chunk

            if progress is not None:
                progress(int(100 * (k + 1) / len(bounds)))
    finally:
        if interlaced is not None:
            interlaced.close()
        if sequential is not None:
            sequential.flush()
            del sequential

    return {"n_samples": n_total, "n_clipped": n_clipped, "cancelled": False}
