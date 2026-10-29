"""Stream state model for per-stream persistence."""
import re
from dataclasses import dataclass, field
from enum import Enum
from typing import ClassVar, Dict, List, Optional, Tuple
from .filter_config import FilterConfig, REMOVED_FILTER_TYPES
from .spikes import SpikeSettings


class ChannelMode(Enum):
    SINGLE = "single"
    OVERLAY = "overlay"


# How channels are numbered in the UI
NUMBER_BY_ELECTRODE = "electrode"  # trailing number of the label, e.g. elec1-10 -> 10
NUMBER_BY_CHANNEL = "channel"  # recording order, 1-based

_TRAILING_INT = re.compile(r"(\d+)\s*$")


def parse_electrode_numbers(labels: Optional[List[str]], channels: List[int]) -> Tuple[Optional[Dict[int, int]], str]:
    """
    Electrode number of each channel from the trailing integer of its label.

    Returns (channel index -> electrode number, "") when every channel has one and they are
    unique, otherwise (None, reason).
    """
    if not channels:
        return None, "no electrode channels"
    if not labels:
        return None, "the recording has no channel labels"
    numbers: Dict[int, int] = {}
    for ch in channels:
        label = labels[ch] if ch < len(labels) else ""
        m = _TRAILING_INT.search(label or "")
        if not m:
            return None, f"label '{label}' has no electrode number"
        numbers[ch] = int(m.group(1))
    if len(set(numbers.values())) != len(numbers):
        return None, "electrode numbers in the labels are not unique"
    return numbers, ""


@dataclass
class StreamState:
    """State for a single stream, persisted when switching streams."""

    stream_name: str
    num_channels: int
    sample_rate: float
    duration: float  # seconds

    # Hardware channel names (from the recording; not persisted)
    channel_labels: Optional[List[str]] = None

    # Analog input channels (stimulus lines etc.); excluded from neural channel lists and CAR/CMR
    analog_channels: List[int] = field(default_factory=list)

    # Channel selection
    channel_mode: ChannelMode = ChannelMode.SINGLE
    selected_channel: int = 0  # For single mode
    overlay_channels: List[int] = field(default_factory=list)  # For overlay mode
    max_overlay_channels: int = 8

    # View state
    view_start: float = 0.0  # seconds
    view_end: float = 10.0  # seconds (default 10s window)

    # Filter chain (ordered)
    filters: List[FilterConfig] = field(default_factory=list)
    global_bypass: bool = False

    # Display mode
    scatter_mode: bool = False

    # Vrms (transient, not persisted)
    vrms_values: Dict[int, float] = field(default_factory=dict)

    # FFT/PSD settings
    fft_start: Optional[float] = None
    fft_end: Optional[float] = None
    fft_channels: List[int] = field(default_factory=list)
    psd_nperseg: int = 1024
    psd_overlap: float = 0.5

    # Spike threshold settings
    spike_settings: SpikeSettings = field(default_factory=SpikeSettings)

    # App-wide numbering preference (set by the main window from user settings)
    numbering: ClassVar[str] = NUMBER_BY_ELECTRODE

    def __post_init__(self):
        """Initialize view_end based on duration if needed."""
        if self.view_end > self.duration:
            self.view_end = min(10.0, self.duration)
        self._update_electrode_numbers()
        if self.selected_channel == 0 and self.neural_channels():
            self.selected_channel = self.ordered_neural_channels()[0]
            self.overlay_channels = [self.selected_channel]
        self._drop_analog_from_selection()

    # ------------------------------------------------------------------ numbering

    def _update_electrode_numbers(self):
        self.electrode_numbers, self.electrode_note = parse_electrode_numbers(
            self.channel_labels, self.neural_channels()
        )

    @property
    def uses_electrode_numbers(self) -> bool:
        """True when channels are shown by electrode number (preferred and available)."""
        return StreamState.numbering == NUMBER_BY_ELECTRODE and self.electrode_numbers is not None

    def display_number(self, channel: int) -> int:
        """Number shown for a channel: electrode number, or 1-based channel number."""
        if self.uses_electrode_numbers and channel in self.electrode_numbers:
            return self.electrode_numbers[channel]
        return channel + 1

    def channel_for_number(self, number: int) -> Optional[int]:
        """Neural channel index shown as this number, or None."""
        for ch in self.neural_channels():
            if self.display_number(ch) == number:
                return ch
        return None

    def ordered_neural_channels(self) -> List[int]:
        """Neural channel indices sorted by their displayed number."""
        return sorted(self.neural_channels(), key=self.display_number)

    def neural_channels(self) -> List[int]:
        """Channel indices that are electrodes, i.e. everything except analog inputs."""
        analog = set(self.analog_channels)
        return [c for c in range(self.num_channels) if c not in analog]

    def set_analog_channels(self, channels: List[int]):
        """Replace the analog channel list and keep the neural selection valid."""
        self.analog_channels = sorted(c for c in set(channels) if 0 <= c < self.num_channels)
        self._update_electrode_numbers()
        self._drop_analog_from_selection()

    def _drop_analog_from_selection(self):
        neural = self.neural_channels()
        first = self.ordered_neural_channels()[0] if neural else 0
        if self.selected_channel not in neural:
            self.selected_channel = first
        self.overlay_channels = [c for c in self.overlay_channels if c in neural]
        if not self.overlay_channels and self.num_channels > 0:
            self.overlay_channels = [first]

    def channel_label(self, channel: int, prefix: str = "Ch") -> str:
        """
        Display name for a 0-based channel.

        Electrode numbering: "Elec 10 (Ch 125)". Channel numbering (or no electrode
        number, e.g. analog inputs): "Ch 125 (elec1-10)".
        """
        if self.uses_electrode_numbers and channel in self.electrode_numbers:
            return f"Elec {self.electrode_numbers[channel]} (Ch {channel + 1})"
        name = f"{prefix} {channel + 1}"
        if self.channel_labels and 0 <= channel < len(self.channel_labels):
            label = self.channel_labels[channel]
            if label and label != name:
                name = f"{name} ({label})"
        return name

    def get_active_channels(self) -> List[int]:
        """Return list of currently active channel indices."""
        if self.channel_mode == ChannelMode.SINGLE:
            return [self.selected_channel]
        return self.overlay_channels.copy()

    def add_overlay_channel(self, channel: int) -> bool:
        """Add channel to overlay. Returns True if added."""
        if channel in self.overlay_channels:
            return False
        if len(self.overlay_channels) >= self.max_overlay_channels:
            return False
        if channel < 0 or channel >= self.num_channels:
            return False
        self.overlay_channels.append(channel)
        return True

    def remove_overlay_channel(self, channel: int) -> bool:
        """Remove channel from overlay. Returns True if removed."""
        if channel in self.overlay_channels:
            self.overlay_channels.remove(channel)
            return True
        return False

    def move_filter(self, from_idx: int, to_idx: int):
        """Move a filter in the chain."""
        if 0 <= from_idx < len(self.filters) and 0 <= to_idx < len(self.filters):
            filt = self.filters.pop(from_idx)
            self.filters.insert(to_idx, filt)

    def add_filter(self, filt: FilterConfig):
        """Add a filter to the end of the chain."""
        self.filters.append(filt)

    def remove_filter(self, uid: str):
        """Remove a filter by its UID."""
        self.filters = [f for f in self.filters if f.uid != uid]

    def get_view_range(self) -> Tuple[float, float]:
        """Return current view range in seconds."""
        return (self.view_start, self.view_end)

    def set_view_range(self, start: float, end: float):
        """Set view range, clamping to valid bounds."""
        self.view_start = max(0.0, min(start, self.duration))
        self.view_end = max(self.view_start + 0.001, min(end, self.duration))

    def to_dict(self) -> dict:
        """Serialize to dictionary for JSON export."""
        return {
            "stream_name": self.stream_name,
            "num_channels": self.num_channels,
            "sample_rate": self.sample_rate,
            "duration": self.duration,
            "analog_channels": self.analog_channels,
            "channel_mode": self.channel_mode.value,
            "selected_channel": self.selected_channel,
            "overlay_channels": self.overlay_channels,
            "view_start": self.view_start,
            "view_end": self.view_end,
            "filters": [f.to_dict() for f in self.filters],
            "global_bypass": self.global_bypass,
            "scatter_mode": self.scatter_mode,
            "fft_start": self.fft_start,
            "fft_end": self.fft_end,
            "fft_channels": self.fft_channels,
            "psd_nperseg": self.psd_nperseg,
            "psd_overlap": self.psd_overlap,
            "spike_settings": self.spike_settings.to_dict(),
        }

    @classmethod
    def from_dict(cls, data: dict) -> "StreamState":
        """Deserialize from dictionary."""
        state = cls(
            stream_name=data["stream_name"],
            num_channels=data["num_channels"],
            sample_rate=data["sample_rate"],
            duration=data["duration"],
        )
        state.analog_channels = data.get("analog_channels", [])
        state.channel_mode = ChannelMode(data.get("channel_mode", "single"))
        state.selected_channel = data.get("selected_channel", 0)
        state.overlay_channels = data.get("overlay_channels", [0])
        state.view_start = data.get("view_start", 0.0)
        state.view_end = data.get("view_end", 10.0)
        state.filters = [
            FilterConfig.from_dict(f) for f in data.get("filters", [])
            if f.get("filter_type") not in REMOVED_FILTER_TYPES
        ]
        state.global_bypass = data.get("global_bypass", False)
        state.scatter_mode = data.get("scatter_mode", False)
        state.fft_start = data.get("fft_start")
        state.fft_end = data.get("fft_end")
        state.fft_channels = data.get("fft_channels", [])
        state.psd_nperseg = data.get("psd_nperseg", 1024)
        state.psd_overlap = data.get("psd_overlap", 0.5)
        state.spike_settings = SpikeSettings.from_dict(data.get("spike_settings"))
        return state
