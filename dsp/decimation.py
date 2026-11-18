"""Decimation utilities for efficient plotting."""
from typing import Tuple
import numpy as np
from scipy import signal


def decimate_for_display(
    data: np.ndarray,
    time_axis: np.ndarray,
    target_points: int = 4000,
    method: str = "minmax",
) -> Tuple[np.ndarray, np.ndarray]:
    """
    Decimate data for display while preserving visual features.

    For time series visualization, we want to preserve peaks and troughs
    so the visual appearance matches the full-resolution data.

    Args:
        data: Input data, shape (n_channels, n_samples) or (n_samples,)
        time_axis: Time values for each sample
        target_points: Target number of points for display
        method: 'minmax' preserves peaks (recommended), 'decimate' uses scipy

    Returns:
        Tuple of (decimated_data, decimated_time)
    """
    if data.ndim == 1:
        data = data.reshape(1, -1)
        was_1d = True
    else:
        was_1d = False

    n_channels, n_samples = data.shape

    if n_samples <= target_points:
        # No decimation needed
        if was_1d:
            return data[0], time_axis
        return data, time_axis

    if method == "minmax":
        # Vectorised MinMax decimation — no Python loop.
        # Each bin contributes two output points (min then max) so that peaks
        # and troughs are preserved in the rendered waveform.
        n_bins = max(2, target_points // 2)

        # Bins of spb = ceil(n / n_bins) samples cover all but fewer than spb samples (the end of
        # the view), which form one last, shorter bin. With spb = n // n_bins the leftover could
        # be thousands of samples: dropped, or drawn as one straight line.
        spb = -(-n_samples // n_bins)      # samples per bin
        n_bins = n_samples // spb          # full bins
        n_complete = n_bins * spb
        tail = n_samples > n_complete
        n_out = n_bins + int(tail)

        # Reshape → (n_channels, n_bins, spb), then min/max along last axis
        data_bins = data[:, :n_complete].reshape(n_channels, n_bins, spb)
        min_vals = np.empty((n_channels, n_out), dtype=data.dtype)
        max_vals = np.empty((n_channels, n_out), dtype=data.dtype)
        data_bins.min(axis=2, out=min_vals[:, :n_bins])
        data_bins.max(axis=2, out=max_vals[:, :n_bins])
        if tail:
            min_vals[:, -1] = data[:, n_complete:].min(axis=1)
            max_vals[:, -1] = data[:, n_complete:].max(axis=1)

        # Interleave: column 0 = min of bin 0, column 1 = max of bin 0, ...
        out_data = np.empty((n_channels, n_out * 2), dtype=data.dtype)
        out_data[:, 0::2] = min_vals
        out_data[:, 1::2] = max_vals

        # Time: start and end sample of each bin
        time_bins = time_axis[:n_complete].reshape(n_bins, spb)
        out_time = np.empty(n_out * 2, dtype=time_axis.dtype)
        out_time[0:2 * n_bins:2] = time_bins[:, 0]
        out_time[1:2 * n_bins:2] = time_bins[:, -1]
        if tail:
            out_time[-2:] = time_axis[n_complete], time_axis[n_samples - 1]

        if was_1d:
            return out_data[0], out_time
        return out_data, out_time

    elif method == "decimate":
        # Standard scipy decimation with anti-aliasing
        factor = max(1, n_samples // target_points)

        if factor == 1:
            if was_1d:
                return data[0], time_axis
            return data, time_axis

        out_data = np.zeros(
            (n_channels, n_samples // factor), dtype=np.float64
        )

        for i in range(n_channels):
            # Use FIR filter for stability
            out_data[i] = signal.decimate(
                data[i].astype(np.float64), factor, ftype="fir", zero_phase=True
            )

        out_time = time_axis[::factor][: out_data.shape[1]]

        if was_1d:
            return out_data[0], out_time
        return out_data, out_time

    else:
        # Simple subsampling (not recommended but fast)
        factor = max(1, n_samples // target_points)
        out_data = data[:, ::factor]
        out_time = time_axis[::factor]

        if was_1d:
            return out_data[0], out_time
        return out_data, out_time


def calculate_decimation_factor(
    n_samples: int, display_width_pixels: int = 2000
) -> int:
    """
    Calculate appropriate decimation factor based on display constraints.

    Args:
        n_samples: Number of samples in the data
        display_width_pixels: Approximate width of display area in pixels

    Returns:
        Decimation factor (1 = no decimation needed)
    """
    # We want roughly 2-4 samples per pixel for good visual quality
    target_samples = display_width_pixels * 4
    factor = max(1, n_samples // target_samples)
    return factor
