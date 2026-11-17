"""Filter design and application using SOS format."""
import os
from concurrent.futures import ThreadPoolExecutor
from typing import Dict, List, Optional, Tuple
import numpy as np
from scipy import signal

from models.filter_config import FilterConfig, FilterType
from .referencing import apply_car, apply_cmr
from .metrics import compute_vrms


_FILTER_THREADS = min(8, os.cpu_count() or 1)


def design_filter(config: FilterConfig, fs: float) -> Optional[np.ndarray]:
    """
    Design a filter and return SOS coefficients.

    Args:
        config: Filter configuration
        fs: Sampling frequency in Hz

    Returns:
        SOS coefficients array, or None if design fails
    """
    nyq = fs / 2.0

    try:
        if config.filter_type == FilterType.LOWPASS:
            if config.cutoff_high >= nyq:
                return None  # Invalid cutoff
            sos = signal.butter(
                config.order,
                config.cutoff_high / nyq,
                btype="low",
                output="sos",
            )

        elif config.filter_type == FilterType.HIGHPASS:
            if config.cutoff_low >= nyq or config.cutoff_low <= 0:
                return None
            sos = signal.butter(
                config.order,
                config.cutoff_low / nyq,
                btype="high",
                output="sos",
            )

        elif config.filter_type == FilterType.BANDPASS:
            if config.cutoff_low >= nyq or config.cutoff_high >= nyq:
                return None
            if config.cutoff_low >= config.cutoff_high:
                return None
            sos = signal.butter(
                config.order,
                [config.cutoff_low / nyq, config.cutoff_high / nyq],
                btype="band",
                output="sos",
            )

        elif config.filter_type == FilterType.NOTCH:
            if config.notch_freq >= nyq or config.notch_freq <= 0:
                return None
            # Design notch filter using iirnotch, then convert to SOS
            b, a = signal.iirnotch(config.notch_freq, config.notch_q, fs)
            # Convert to SOS for numerical stability
            sos = signal.tf2sos(b, a)

        else:
            return None

        return sos

    except Exception:
        return None


def apply_filter(data: np.ndarray, sos: np.ndarray, inplace: bool = False) -> np.ndarray:
    """
    Apply SOS filter using zero-phase filtering.

    Args:
        data: Input data (1D or 2D with channels as rows)
        sos: SOS coefficients
        inplace: Write the result back into ``data`` (float64 only) instead of allocating
                 another full-size array; the filter chain uses this on arrays it owns.

    Returns:
        Filtered data with same shape as input
    """
    inplace = inplace and data.dtype == np.float64
    if data.ndim == 1:
        out = signal.sosfiltfilt(sos, data)
        if inplace:
            data[...] = out
            return data
        return out
    # scipy's filtering releases the GIL, so blocks of channels run in parallel threads;
    # results are identical to filtering each channel on its own.
    n_threads = min(_FILTER_THREADS, data.shape[0])
    if n_threads <= 1 or data.size < 200_000:
        out = signal.sosfiltfilt(sos, data, axis=-1)
        if inplace:
            data[...] = out
            return data
        return out
    result = data if inplace else np.empty(data.shape, dtype=np.result_type(data.dtype, np.float64))

    def run(rows: slice):
        # Each block is filtered into a temporary first, so writing back into `data` is safe
        result[rows] = signal.sosfiltfilt(sos, data[rows], axis=-1)  # slices are views, not copies

    # Small blocks: sosfiltfilt holds about 3 temporaries of its input, so the blocks in
    # flight (n_threads at a time) set the peak memory. 2-row blocks on 136 channels keep it
    # near 0.3x the array instead of 3x, for ~50% more time (0.23 s vs 0.15 s on a 30 s buffer).
    rows_per_block = max(1, data.shape[0] // (n_threads * 8))
    blocks = [slice(a, min(a + rows_per_block, data.shape[0]))
              for a in range(0, data.shape[0], rows_per_block)]
    with ThreadPoolExecutor(n_threads) as pool:
        list(pool.map(run, blocks))
    return result


def apply_filter_chain(
    data: np.ndarray,
    filters: List[FilterConfig],
    fs: float,
    global_bypass: bool = False,
    all_channel_data: Optional[np.ndarray] = None,
    channel_indices: Optional[List[int]] = None,
    with_vrms: bool = True,
    inplace_ok: bool = False,
) -> Tuple[np.ndarray, List[str], Dict[int, float]]:
    """
    Apply ordered filter chain to data.

    Args:
        data: Input data (1D or 2D with channels as rows) — displayed channels.
        filters: List of filter configurations in order.
        fs: Sampling frequency.
        global_bypass: If True, skip all filtering.
        all_channel_data: Full array with ALL channels loaded (required for CAR/CMR).
                          Shape (total_n_channels, n_samples). When given, the whole chain
                          runs on it, so filters placed before a CAR/CMR feed into it and
                          filters after it see the re-referenced data.
        channel_indices: 0-indexed channel indices corresponding to rows of ``data``
                         (and the rows of ``all_channel_data`` to return).
        with_vrms: Also compute Vrms of the output per channel (skip in chunked jobs).
        inplace_ok: The array the chain starts from (``all_channel_data`` if given, else
                    ``data``) is a scratch copy the caller no longer needs, so the chain may
                    filter it in place instead of making its own copy.

    Returns:
        Tuple of (filtered_data, warnings, vrms_dict). The inputs are not modified unless
        ``inplace_ok`` is set.
    """
    warnings: List[str] = []

    if data.ndim == 1:
        data = data.reshape(1, -1)
        was_1d = True
    else:
        was_1d = False

    if channel_indices is None:
        channel_indices = list(range(data.shape[0]))

    active = [] if global_bypass else [f for f in filters if not f.bypassed]
    if not active:
        out = data.copy()
        vrms = compute_vrms(out, channel_indices) if with_vrms else {}
        return (out[0] if was_1d else out), warnings, vrms

    # `work` starts as the caller's array; the first filter that runs makes our own copy
    # (owned), and every later filter modifies that copy in place.
    uses_all = all_channel_data is not None
    work = all_channel_data if uses_all else data
    owned = inplace_ok

    for filt in active:
        if filt.filter_type in (FilterType.CAR, FilterType.CMR):
            if not uses_all:
                warnings.append(
                    f"{filt.get_display_name()}: requires all channels loaded"
                )
                continue
            reference = apply_car if filt.filter_type == FilterType.CAR else apply_cmr
            work, w = reference(work, filt.channel_groups, copy=not owned)
            warnings.extend(w)
            owned = True

        else:
            # Standard SOS filter
            sos = design_filter(filt, fs)
            if sos is None:
                warnings.append(
                    f"Filter '{filt.get_display_name()}' could not be designed "
                    f"(invalid parameters for fs={fs:.0f} Hz)"
                )
                continue

            try:
                work = apply_filter(work, sos, inplace=owned)
                owned = True
            except Exception as e:
                warnings.append(f"Filter '{filt.get_display_name()}' failed: {e}")

    if uses_all:
        # Pick the displayed rows out of the all-channel result
        rows = [c for c in channel_indices if 0 <= c < work.shape[0]]
        if len(rows) == len(channel_indices):
            result = work[rows]
        else:
            warnings.append("Some displayed channels are outside the loaded data")
            result = data.copy()
            for i, c in enumerate(channel_indices):
                if 0 <= c < work.shape[0]:
                    result[i] = work[c]
    else:
        result = work if owned else data.copy()

    vrms_dict = compute_vrms(result, channel_indices) if with_vrms else {}

    out = result[0] if was_1d else result
    return out, warnings, vrms_dict


def get_filter_response(
    config: FilterConfig, fs: float, n_points: int = 512
) -> Tuple[Optional[np.ndarray], Optional[np.ndarray]]:
    """
    Get frequency response of a filter.

    Args:
        config: Filter configuration
        fs: Sampling frequency
        n_points: Number of frequency points

    Returns:
        Tuple of (frequencies, magnitude in dB), or (None, None) if design fails
    """
    sos = design_filter(config, fs)
    if sos is None:
        return None, None

    try:
        w, h = signal.sosfreqz(sos, worN=n_points, fs=fs)
        magnitude_db = 20 * np.log10(np.maximum(np.abs(h), 1e-10))
        return w, magnitude_db
    except Exception:
        return None, None
