"""Whole-recording spike detection with thresholds frozen from one window (no Qt)."""
from typing import Callable, Dict, List, Optional

import numpy as np

from core.export import iter_processed_chunks
from dsp.spikes import MAD_TO_SIGMA, detect_crossings


def freeze_sigma(reader, stream: str, channels: List[int], i0: int, i1: int, fs: float,
                 num_channels: int, blank_results=(), filters=None,
                 progress: Optional[Callable[[int], None]] = None,
                 cancelled: Optional[Callable[[], bool]] = None,
                 chunk_s: float = 5.0, pad_s: float = 1.0) -> Optional[np.ndarray]:
    """
    Sigma per channel = median(|x|)/0.6745 of samples [i0, i1) after blanking and filtering,
    blanked samples excluded (the same numbers the threshold line uses for that view).

    The view is processed in chunks, keeping only each channel's |x| as float32: as one chunk,
    a 10 s view of every channel (CAR) took ~3 GB. Returns None if cancelled.
    """
    kept: List[List[np.ndarray]] = [[] for _ in channels]
    for k, n_chunks, _a, _b, data, mask in iter_processed_chunks(
        reader, stream, channels, i0, i1, fs, num_channels, blank_results, filters,
        chunk_s, pad_s, with_mask=True,
    ):
        if cancelled is not None and cancelled():
            return None
        for row, part in enumerate(kept):
            part.append(np.abs(data[row][~mask[row]]).astype(np.float32))
        del data, mask  # see iter_processed_chunks
        if progress is not None:
            progress(int(100 * (k + 1) / n_chunks))

    sigma = np.full(len(channels), np.nan)
    for row, parts in enumerate(kept):
        x = np.concatenate(parts) if parts else np.zeros(0, np.float32)
        kept[row] = None  # free each channel's samples once used
        if x.size:
            sigma[row] = np.median(x, overwrite_input=True) / MAD_TO_SIGMA
    return sigma


def detect_spikes(
    reader,
    stream: str,
    channels: List[int],
    i_start: int,
    i_stop: int,
    fs: float,
    num_channels: int,
    thresholds: np.ndarray,
    polarity: str,
    refractory_ms: float,
    blank_results=(),
    filters=None,
    progress: Optional[Callable[[int], None]] = None,
    cancelled: Optional[Callable[[], bool]] = None,
    chunk_s: float = 5.0,
    pad_s: float = 1.0,
) -> Optional[Dict[int, np.ndarray]]:
    """
    Threshold crossings over [i_start, i_stop), processed in chunks (blanking -> filters).

    thresholds: positive magnitude per channel (k * sigma), NaN = skip channel.
    Returns {channel: spike times in seconds}, or None if cancelled.
    """
    refractory_n = int(round(refractory_ms * 1e-3 * fs))
    last: List[Optional[int]] = [None] * len(channels)  # global sample of each channel's last spike
    found: List[List[np.ndarray]] = [[] for _ in channels]

    for k, n_chunks, a, b, data, mask in iter_processed_chunks(
        reader, stream, channels, i_start, i_stop, fs, num_channels, blank_results, filters,
        chunk_s, pad_s, left_overlap=1, with_mask=True,
    ):
        if cancelled is not None and cancelled():
            return None
        offset = a - (data.shape[1] - (b - a))  # global sample index of column 0
        last_rel = [None if l is None else l - offset for l in last]
        for row, idx in enumerate(
            detect_crossings(data, thresholds, polarity, refractory_n, mask, last_rel)
        ):
            idx = idx[idx + offset >= a]  # the overlap column only supplies the previous sample
            if idx.size:
                g = idx + offset
                found[row].append(g)
                last[row] = int(g[-1])
        del data, mask  # see iter_processed_chunks
        if progress is not None:
            progress(int(100 * (k + 1) / n_chunks))

    return {
        ch: (np.concatenate(f) / fs if f else np.zeros(0))
        for ch, f in zip(channels, found)
    }
