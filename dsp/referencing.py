"""Common reference subtraction (CAR/CMR) for multi-channel data."""
from typing import List, Tuple
import numpy as np


def _valid_groups(channel_groups: List[List[int]], n_channels: int, name: str):
    """Yield (group index, unique valid channels) per group, warning about groups under 2."""
    warnings = []
    groups = []
    for group_idx, group in enumerate(channel_groups):
        valid = list(dict.fromkeys(ch for ch in group if 0 <= ch < n_channels))
        if len(valid) < 2:
            warnings.append(
                f"{name} group {group_idx + 1}: need >= 2 valid channels, got {len(valid)}"
            )
            continue
        groups.append(valid)
    return groups, warnings


def apply_car(
    data: np.ndarray,
    channel_groups: List[List[int]],
    copy: bool = True,
) -> Tuple[np.ndarray, List[str]]:
    """
    Apply Common Average Reference.

    For each group, subtracts the mean across group channels at each time point
    from every channel in that group.

    Args:
        data: Shape (n_channels, n_samples). All channels must be present.
        channel_groups: List of groups, each a list of 0-indexed channel indices.
                        Channels not in any group are left unchanged.
        copy: Work on a copy (default). With False, ``data`` is re-referenced in place.

    Returns:
        (re-referenced data, warnings)
    """
    result = data.copy() if copy else data
    groups, warnings = _valid_groups(channel_groups, data.shape[0], "CAR")
    acc_dtype = np.result_type(result.dtype, np.float64)
    for valid in groups:
        # Row by row: no group-sized temporaries (a group can be the whole array)
        mean = np.zeros(result.shape[1], dtype=acc_dtype)
        for ch in valid:
            mean += result[ch]
        mean /= len(valid)
        for ch in valid:
            result[ch] -= mean
    return result, warnings


def car_rows(
    data: np.ndarray,
    channel_groups: List[List[int]],
    rows: List[int],
) -> Tuple[np.ndarray, List[str]]:
    """
    apply_car(data, channel_groups)[0][rows], without re-referencing (or copying) the
    channels that aren't asked for: each group's mean is summed row by row and subtracted
    from the requested rows only.
    """
    groups, warnings = _valid_groups(channel_groups, data.shape[0], "CAR")
    out = np.array(data[rows], dtype=np.result_type(data.dtype, np.float64))
    acc_dtype = out.dtype
    for valid in groups:
        members = [i for i, r in enumerate(rows) if r in set(valid)]
        if not members:
            continue
        mean = np.zeros(data.shape[1], dtype=acc_dtype)
        for ch in valid:
            mean += data[ch]
        mean /= len(valid)
        for i in members:
            out[i] -= mean
    return out, warnings


def apply_cmr(
    data: np.ndarray,
    channel_groups: List[List[int]],
    copy: bool = True,
) -> Tuple[np.ndarray, List[str]]:
    """
    Apply Common Median Reference.

    Same as CAR but uses median instead of mean.

    Args:
        data: Shape (n_channels, n_samples). All channels must be present.
        channel_groups: List of groups, each a list of 0-indexed channel indices.
        copy: Work on a copy (default). With False, ``data`` is re-referenced in place.

    Returns:
        (re-referenced data, warnings)
    """
    result = data.copy() if copy else data
    groups, warnings = _valid_groups(channel_groups, data.shape[0], "CMR")
    for valid in groups:
        # The median needs the group stacked: one temporary, consumed by the median itself
        stacked = result[valid]
        median = np.median(stacked, axis=0, overwrite_input=True)
        del stacked
        for ch in valid:
            result[ch] -= median
    return result, warnings
