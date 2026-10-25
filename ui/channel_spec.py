"""Parse and format typed channel selections such as "1-24, 37-45" (no Qt)."""
import re
from typing import Dict, Iterable, List, Optional, Tuple

_ITEM = re.compile(r"^(\d+)(?:\s*[-–]\s*(\d+))?$")


def parse_channel_spec(
    text: str, valid: Iterable[int], reasons: Optional[Dict[int, str]] = None, noun: str = "channel"
) -> Tuple[List[int], str]:
    """
    Parse comma/space separated numbers and ranges ("1-24, 37-45", "1,2,3", "33").

    Args:
        valid: numbers that may be selected.
        reasons: why particular numbers are not selectable (e.g. {130: "an analog channel"}).
        noun: what the numbers are ("electrode" / "channel"), for messages.

    Returns:
        (sorted unique numbers, "") or ([], error message).
    """
    valid = set(valid)
    reasons = reasons or {}
    # Allow spaces around range dashes ("1 - 24"), then split on commas/whitespace
    joined = re.sub(r"\s*([-–])\s*", r"\1", text.strip())
    items = [p for p in re.split(r"[,\s]+", joined) if p]
    if not items:
        return [], f"Type {noun} numbers, e.g. 1-24, 37-45"

    numbers = set()
    for item in items:
        m = _ITEM.match(item)
        if not m:
            return [], f"'{item}' isn't a number or range"
        a = int(m.group(1))
        b = int(m.group(2)) if m.group(2) else a
        if b < a:
            return [], f"'{item}': range goes backwards"
        for n in range(a, b + 1):
            if n not in valid:
                if n in reasons:
                    return [], f"{n} is {reasons[n]}"
                lo, hi = (min(valid), max(valid)) if valid else (0, 0)
                if valid and lo <= n <= hi:
                    return [], f"There is no {noun} {n} in this recording"
                return [], f"{n} is out of range ({lo}–{hi})"
            numbers.add(n)
    return sorted(numbers), ""


def format_channel_spec(numbers: Iterable[int]) -> str:
    """Compact ranges: [1, 2, 3, 6, 8, 9] -> "1-3, 6, 8-9"."""
    nums = sorted(set(numbers))
    parts = []
    i = 0
    while i < len(nums):
        j = i
        while j + 1 < len(nums) and nums[j + 1] == nums[j] + 1:
            j += 1
        parts.append(str(nums[i]) if i == j else f"{nums[i]}-{nums[j]}")
        i = j + 1
    return ", ".join(parts)
