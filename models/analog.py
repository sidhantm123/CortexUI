"""Analog input channels, their stimulus-modality assignments, and detected events."""
from dataclasses import dataclass, field, asdict
from typing import Dict, List, Optional


@dataclass
class DetectionSettings:
    """How events are found on one analog line."""

    active_low: bool = False  # True when the line drops during stimulation (e.g. tactor)
    threshold: Optional[float] = None  # volts; None = automatic (midpoint of idle and active levels)
    merge_gap_ms: float = 20.0  # crossings closer than this join into one event (pulse trains)
    min_duration_ms: float = 0.0  # shorter events are dropped

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> "DetectionSettings":
        return cls(**{k: v for k, v in data.items() if k in cls.__dataclass_fields__})


# Preset modalities: name -> default detection settings (the user can add more).
# The 333 ms merge gap groups pulse trains of 3 Hz and faster into one event.
MODALITY_PRESETS: Dict[str, DetectionSettings] = {
    "ICMS": DetectionSettings(active_low=False, merge_gap_ms=333.0),
    "PNS": DetectionSettings(active_low=False, merge_gap_ms=333.0),
    "Tactor": DetectionSettings(active_low=True, merge_gap_ms=333.0),
}


@dataclass
class AnalogAssignment:
    """An analog channel mapped to a stimulus modality."""

    stream: str
    channel: int  # 0-based index within the stream
    modality: str
    settings: DetectionSettings = field(default_factory=DetectionSettings)

    def to_dict(self) -> dict:
        return {
            "stream": self.stream,
            "channel": self.channel,
            "modality": self.modality,
            "settings": self.settings.to_dict(),
        }

    @classmethod
    def from_dict(cls, data: dict) -> "AnalogAssignment":
        return cls(
            stream=data["stream"],
            channel=data["channel"],
            modality=data["modality"],
            settings=DetectionSettings.from_dict(data.get("settings", {})),
        )


@dataclass
class StimEvent:
    """One detected stimulation event."""

    modality: str
    number: int  # 1-based, counted separately for each modality
    onset: float  # seconds from stream start
    offset: float
    stream: str
    channel: int
    label: str  # analog channel label, e.g. "ainp2"

    @property
    def duration(self) -> float:
        return self.offset - self.onset

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> "StimEvent":
        return cls(**{k: v for k, v in data.items() if k in cls.__dataclass_fields__})


def number_events(events: List[StimEvent]) -> List[StimEvent]:
    """Sort by modality then onset, numbering events from 1 within each modality."""
    events = sorted(events, key=lambda e: (e.modality, e.onset))
    counts: Dict[str, int] = {}
    for e in events:
        counts[e.modality] = counts.get(e.modality, 0) + 1
        e.number = counts[e.modality]
    return events


def is_analog_label(label: str, channel_id: Optional[int] = None) -> bool:
    """Blackrock analog inputs are labeled ainpN and have electrode IDs above 128."""
    if label and label.strip().lower().startswith("ainp"):
        return True
    return channel_id is not None and channel_id > 128
