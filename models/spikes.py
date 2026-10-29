"""Spike detection settings."""
from dataclasses import dataclass, asdict


@dataclass
class SpikeSettings:
    """Threshold k x sigma (sigma = median(|x|)/0.6745), shared by all channels of a stream."""

    k: float = 4.75
    polarity: str = "negative"  # negative | positive | both
    refractory_ms: float = 2.0
    show_everywhere: bool = False  # keep the threshold line visible outside the Spike Detection tab

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> "SpikeSettings":
        return cls(**{k: v for k, v in (data or {}).items() if k in cls.__dataclass_fields__})
