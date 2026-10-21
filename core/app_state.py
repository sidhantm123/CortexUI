"""Application state management."""
import json
from pathlib import Path
from typing import Dict, Optional, List, Callable, Tuple
from dataclasses import dataclass, field

from models import StreamState
from models.analog import AnalogAssignment, StimEvent, MODALITY_PRESETS, number_events
from models.probe import ProbeGeometry
from models.recording import RecordingBlock
from tdt_io import TDTReader
from .reader_factory import open_reader


@dataclass
class AppState:
    """
    Global application state.

    Manages:
    - Current recording (TDT block or Blackrock file set)
    - Stream states (one per stream)
    - Current selection
    """

    # Recording data; reader is replaced on each load to match the folder's format
    reader: object = field(default_factory=TDTReader)
    block: Optional[RecordingBlock] = None

    # Probe geometry (optional; loaded independently of the recording)
    probe: Optional[ProbeGeometry] = None

    # Per-stream state
    stream_states: Dict[str, StreamState] = field(default_factory=dict)

    # Current selection
    current_stream: Optional[str] = None

    # Analog channel -> modality assignments, keyed by (stream, channel index)
    analog_assignments: Dict[Tuple[str, int], AnalogAssignment] = field(default_factory=dict)
    # Modality names the user added beyond the presets
    custom_modalities: List[str] = field(default_factory=list)
    # Detected stimulation events (numbered per modality)
    events: List[StimEvent] = field(default_factory=list)

    # Callbacks for state changes
    _on_block_loaded: List[Callable] = field(default_factory=list)
    _on_stream_changed: List[Callable] = field(default_factory=list)
    _on_state_updated: List[Callable] = field(default_factory=list)

    def load_block(self, folder_path: str) -> RecordingBlock:
        """
        Load a recording (TDT or Blackrock) from folder.

        Args:
            folder_path: Path to recording folder

        Returns:
            RecordingBlock with metadata

        Raises:
            Various exceptions on failure
        """
        # Clear previous state
        self.close()

        # Load new block with the reader that matches the folder's format
        self.reader = open_reader(folder_path)
        self.block = self.reader.load_block(folder_path, headers_only=False)

        # Create stream states
        for name, info in self.block.streams.items():
            self.stream_states[name] = StreamState(
                stream_name=name,
                num_channels=info.num_channels,
                sample_rate=info.sample_rate,
                duration=info.duration,
                channel_labels=info.channel_labels,
                analog_channels=list(info.analog_channels),
            )

        # Select first stream by default
        if self.block.streams:
            self.current_stream = list(self.block.streams.keys())[0]

        # Notify listeners
        for callback in self._on_block_loaded:
            callback(self.block)

        return self.block

    def select_stream(self, stream_name: str):
        """Select a stream as current."""
        if stream_name not in self.stream_states:
            raise ValueError(f"Unknown stream: {stream_name}")

        self.current_stream = stream_name

        # Notify listeners
        for callback in self._on_stream_changed:
            callback(stream_name)

    def get_current_state(self) -> Optional[StreamState]:
        """Get state for currently selected stream."""
        if self.current_stream is None:
            return None
        return self.stream_states.get(self.current_stream)

    def get_stream_state(self, stream_name: str) -> Optional[StreamState]:
        """Get state for a specific stream."""
        return self.stream_states.get(stream_name)

    def notify_state_updated(self):
        """Notify listeners that state has changed."""
        for callback in self._on_state_updated:
            callback()

    def on_block_loaded(self, callback: Callable):
        """Register callback for block loaded event."""
        self._on_block_loaded.append(callback)

    def on_stream_changed(self, callback: Callable):
        """Register callback for stream selection change."""
        self._on_stream_changed.append(callback)

    def on_state_updated(self, callback: Callable):
        """Register callback for state updates."""
        self._on_state_updated.append(callback)

    def set_probe(self, geometry: Optional[ProbeGeometry]):
        """Store probe geometry and notify listeners."""
        self.probe = geometry
        for callback in self._on_state_updated:
            callback()

    def close(self):
        """Close current block and clear state."""
        self.reader.close()
        self.block = None
        self.probe = None
        self.stream_states.clear()
        self.current_stream = None
        self.analog_assignments.clear()
        self.events = []

    def modality_names(self) -> List[str]:
        """Preset modalities followed by the user's own."""
        names = list(MODALITY_PRESETS)
        names += [m for m in self.custom_modalities if m not in names]
        return names

    def analog_channel_list(self) -> List[Tuple[str, int]]:
        """Every analog channel in the recording as (stream, channel index)."""
        return [
            (name, ch)
            for name, state in self.stream_states.items()
            for ch in state.analog_channels
        ]

    def set_assignment(self, stream: str, channel: int, assignment: Optional[AnalogAssignment]):
        """Assign (or with None, unassign) an analog channel."""
        if assignment is None:
            self.analog_assignments.pop((stream, channel), None)
        else:
            self.analog_assignments[(stream, channel)] = assignment
            if assignment.modality not in self.modality_names():
                self.custom_modalities.append(assignment.modality)

    def set_event_times(self, event: StimEvent, onset: float, offset: float) -> Optional[str]:
        """
        Change an event's onset/offset (rounded to the nearest sample) and renumber.

        Returns an error message, or None when the change was applied.
        """
        state = self.stream_states.get(event.stream)
        fs = state.sample_rate if state else None
        if fs:
            onset = round(onset * fs) / fs
            offset = round(offset * fs) / fs
        duration = state.duration if state else float("inf")
        if onset < 0:
            return "Onset can't be before the start of the recording"
        if offset > duration:
            return f"Offset can't be after the end of the recording ({duration:.4f} s)"
        if onset >= offset:
            return "Onset must be before offset"

        event.onset, event.offset = onset, offset
        self.events = number_events(self.events)
        return None

    def reset_event_times(self, event: StimEvent):
        """Restore an event's detected onset/offset."""
        event.onset, event.offset = event.detected_onset, event.detected_offset
        self.events = number_events(self.events)

    def edited_event_count(self) -> int:
        return sum(1 for e in self.events if e.edited)

    def set_analog_channels(self, stream: str, channels: List[int]):
        """Change which channels of a stream are analog; drops assignments that no longer apply."""
        state = self.stream_states[stream]
        state.set_analog_channels(channels)
        for key in [k for k in self.analog_assignments if k[0] == stream]:
            if key[1] not in state.analog_channels:
                del self.analog_assignments[key]
        self.events = [
            e for e in self.events
            if (e.stream, e.channel) in self.analog_assignments
        ]

    def save_session(self, filepath: str):
        """
        Save current session to JSON file.

        Args:
            filepath: Path to save session file
        """
        if self.block is None:
            raise ValueError("No block loaded to save")

        session = {
            "block_path": self.block.path,
            "current_stream": self.current_stream,
            "probe": self.probe.to_probeinterface_dict() if self.probe else None,
            "stream_states": {
                name: state.to_dict()
                for name, state in self.stream_states.items()
            },
            "custom_modalities": self.custom_modalities,
            "analog_assignments": [a.to_dict() for a in self.analog_assignments.values()],
            "events": [e.to_dict() for e in self.events],
        }

        with open(filepath, "w") as f:
            json.dump(session, f, indent=2)

    def load_session(self, filepath: str):
        """
        Load session from JSON file.

        Args:
            filepath: Path to session file
        """
        with open(filepath, "r") as f:
            session = json.load(f)

        # Load the block first
        block_path = session.get("block_path")
        if not block_path or not Path(block_path).exists():
            raise ValueError(f"Block path not found: {block_path}")

        self.load_block(block_path)

        # Restore stream states
        saved_states = session.get("stream_states", {})
        for name, state_dict in saved_states.items():
            if name in self.stream_states:
                # Merge saved state into loaded state
                saved = StreamState.from_dict(state_dict)
                current = self.stream_states[name]

                # Copy over user settings, keep hardware info from actual data
                if "analog_channels" in state_dict:
                    current.set_analog_channels(saved.analog_channels)
                current.channel_mode = saved.channel_mode
                current.selected_channel = min(
                    saved.selected_channel, current.num_channels - 1
                )
                current.overlay_channels = [
                    c for c in saved.overlay_channels
                    if c < current.num_channels
                ]
                current.set_analog_channels(current.analog_channels)
                current.view_start = saved.view_start
                current.view_end = min(saved.view_end, current.duration)
                current.filters = saved.filters
                current.global_bypass = saved.global_bypass
                current.scatter_mode = saved.scatter_mode
                current.fft_start = saved.fft_start
                current.fft_end = saved.fft_end
                current.fft_channels = [
                    c for c in saved.fft_channels
                    if c < current.num_channels
                ]
                current.psd_nperseg = saved.psd_nperseg
                current.psd_overlap = saved.psd_overlap

        # Restore analog assignments and detected events
        self.custom_modalities = list(session.get("custom_modalities", []))
        for a_dict in session.get("analog_assignments", []):
            a = AnalogAssignment.from_dict(a_dict)
            state = self.stream_states.get(a.stream)
            if state is not None and a.channel in state.analog_channels:
                self.analog_assignments[(a.stream, a.channel)] = a
        self.events = [
            e for e in (StimEvent.from_dict(d) for d in session.get("events", []))
            if (e.stream, e.channel) in self.analog_assignments
        ]

        # Restore probe geometry (optional — missing key or None is fine)
        probe_dict = session.get("probe")
        if probe_dict:
            try:
                self.probe = ProbeGeometry.from_probeinterface_dict(probe_dict)
            except Exception:
                self.probe = None

        # Restore selection
        if session.get("current_stream") in self.stream_states:
            self.select_stream(session["current_stream"])
