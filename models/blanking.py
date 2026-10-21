"""Stimulation artifact blanking: settings per modality and computed blank intervals."""
from dataclasses import dataclass, field, asdict
from typing import List

import numpy as np

BASIC = "basic"
ADVANCED = "advanced"
LINEAR = "linear"
CUBIC = "cubic"


@dataclass
class BlankingSettings:
    """How one modality's events are blanked."""

    mode: str = BASIC
    # Basic: fixed pads around every event
    pre_ms: float = 1.0
    post_ms: float = 0.0
    # Advanced: own pre-pad, then blank after offset until n_windows in a row are quiet
    adv_pre_ms: float = 1.0
    window_ms: float = 50.0
    n_windows: int = 3
    multiplier: float = 1.5  # quiet = spike-band RMS below multiplier x the channel's baseline RMS
    min_post_ms: float = 100.0
    max_post_ms: float = 5000.0
    interpolation: str = LINEAR
    excluded_trials: List[int] = field(default_factory=list)  # event numbers left unblanked

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> "BlankingSettings":
        return cls(**{k: v for k, v in data.items() if k in cls.__dataclass_fields__})


@dataclass
class BlankResult:
    """Blank intervals for one modality on one stream: one (start, end) per trial and channel."""

    modality: str
    stream: str
    channels: List[int]  # channel indices (columns of start_s / end_s)
    trials: List[int]  # event numbers (rows)
    start_s: np.ndarray  # (n_trials, n_channels)
    end_s: np.ndarray
    interpolation: str = LINEAR
    mode: str = BASIC
    n_capped: int = 0  # (trial, channel) pairs that hit the maximum post-pad
    median_post_s: float = 0.0  # Advanced: median blank length after offset
    stale: bool = False  # events changed after this was computed

    def summary(self) -> str:
        n_t, n_c = self.start_s.shape
        text = f"{n_c} ch × {n_t} trials"
        if self.mode == ADVANCED and self.end_s.size:
            text += f", median post-pad {self.median_post_s:.2f} s"
            text += f", {self.n_capped} hit the max"
        return text

    def to_dict(self) -> dict:
        return {
            "modality": self.modality,
            "stream": self.stream,
            "channels": list(self.channels),
            "trials": list(self.trials),
            "start_s": self.start_s.tolist(),
            "end_s": self.end_s.tolist(),
            "interpolation": self.interpolation,
            "mode": self.mode,
            "n_capped": self.n_capped,
            "median_post_s": self.median_post_s,
            "stale": self.stale,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "BlankResult":
        n_c = len(data["channels"])
        shape = (-1, n_c) if n_c else (0, 0)
        return cls(
            modality=data["modality"],
            stream=data["stream"],
            channels=list(data["channels"]),
            trials=list(data["trials"]),
            start_s=np.asarray(data["start_s"], dtype=float).reshape(shape),
            end_s=np.asarray(data["end_s"], dtype=float).reshape(shape),
            interpolation=data.get("interpolation", LINEAR),
            mode=data.get("mode", BASIC),
            n_capped=data.get("n_capped", 0),
            median_post_s=data.get("median_post_s", 0.0),
            stale=data.get("stale", False),
        )
