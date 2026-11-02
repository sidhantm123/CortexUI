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


@dataclass
class SpikeResult:
    """Spike times from whole-recording detection on one stream."""

    stream: str
    fs: float
    channels: list  # channel indices, in the order detected
    times: dict  # channel index -> np.ndarray of spike times (s)
    sigma: dict  # channel index -> frozen sigma (V); NaN if fully blanked in the view
    k: float
    polarity: str
    refractory_ms: float
    sigma_window: tuple  # (start_s, end_s) of the view sigma was frozen from
    processing: str = ""  # description of the blanking and filters applied
    duration: float = 0.0  # seconds of recording scanned
    stale: bool = False

    def total(self) -> int:
        return int(sum(len(t) for t in self.times.values()))

    def rate(self, ch: int) -> float:
        return len(self.times.get(ch, ())) / self.duration if self.duration else 0.0

    def to_dict(self) -> dict:
        import numpy as np

        return {
            "stream": self.stream,
            "fs": self.fs,
            "channels": list(self.channels),
            # Stored as sample indices: exact and much smaller than float seconds
            "samples": {str(c): np.rint(np.asarray(t) * self.fs).astype(np.int64).tolist()
                        for c, t in self.times.items()},
            "sigma": {str(c): (None if s != s else float(s)) for c, s in self.sigma.items()},
            "k": self.k,
            "polarity": self.polarity,
            "refractory_ms": self.refractory_ms,
            "sigma_window": list(self.sigma_window),
            "processing": self.processing,
            "duration": self.duration,
            "stale": self.stale,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "SpikeResult":
        import numpy as np

        fs = float(data["fs"])
        return cls(
            stream=data["stream"],
            fs=fs,
            channels=list(data["channels"]),
            times={int(c): np.asarray(s, dtype=np.int64) / fs for c, s in data["samples"].items()},
            sigma={int(c): (float("nan") if s is None else float(s)) for c, s in data["sigma"].items()},
            k=data["k"],
            polarity=data["polarity"],
            refractory_ms=data["refractory_ms"],
            sigma_window=tuple(data["sigma_window"]),
            processing=data.get("processing", ""),
            duration=data.get("duration", 0.0),
            stale=data.get("stale", False),
        )
