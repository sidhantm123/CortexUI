"""Signal metrics (Vrms, etc.)."""
from typing import Dict, List, Optional
import numpy as np


def compute_vrms(
    data: np.ndarray,
    channel_indices: List[int],
    exclude: Optional[np.ndarray] = None,
) -> Dict[int, float]:
    """
    Compute Vrms for each channel.

    Args:
        data: Shape (n_channels, n_samples) or (n_samples,)
        channel_indices: Original channel indices (used as dict keys).
        exclude: Optional boolean mask, same shape as data; True samples (e.g. blanked)
                 are left out. A channel with every sample excluded gets NaN.

    Returns:
        Dict mapping channel index -> Vrms value.
    """
    if data.ndim == 1:
        data = data.reshape(1, -1)
    if exclude is not None and exclude.ndim == 1:
        exclude = exclude.reshape(1, -1)

    result = {}
    for i, ch_idx in enumerate(channel_indices):
        if i >= data.shape[0]:
            continue
        row = data[i] if exclude is None else data[i][~exclude[i]]
        result[ch_idx] = float(np.sqrt(np.mean(row ** 2))) if row.size else float("nan")
    return result
