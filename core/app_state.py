"""Application state management."""
import json
import math
import os
from pathlib import Path
from typing import Dict, Optional, List, Callable, Tuple
from dataclasses import dataclass, field

from models import StreamState
from models.analog import AnalogAssignment, StimEvent, MODALITY_PRESETS, number_events
from models.blanking import BlankingSettings, BlankResult
from models.filter_config import FilterConfig, FilterType, REMOVED_FILTER_TYPES
from models.spikes import SpikeResult
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

    # Messages from the last load_session (shown to the user)
    load_messages: List[str] = field(default_factory=list)

    # Artifact blanking per modality: settings, and the intervals from "Apply Blanking"
    blanking_settings: Dict[str, BlankingSettings] = field(default_factory=dict)
    blanking_results: Dict[str, BlankResult] = field(default_factory=dict)

    # Whole-recording spike detection per stream
    spike_results: Dict[str, SpikeResult] = field(default_factory=dict)

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
            Various exceptions on failure; the current recording then stays loaded.
        """
        # Open the new recording first, with the reader that matches the folder's format:
        # if that fails, nothing has changed
        reader = open_reader(folder_path)
        block = reader.load_block(folder_path, headers_only=False)

        # The probe is loaded independently of the recording (the probe map keeps showing it)
        probe = self.probe
        self.close()
        self.reader, self.block, self.probe = reader, block, probe

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
        self.blanking_results.clear()
        self.spike_results.clear()

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

    def set_assignment(self, stream: str, channel: int, assignment: Optional[AnalogAssignment]) -> bool:
        """
        Assign (or with None, unassign) an analog channel. Its events are dropped when it is
        unassigned or moves to another modality: they were detected for the old one.

        Returns True if a blanking result was removed with them (the data must be re-processed).
        """
        old = self.analog_assignments.get((stream, channel))
        if assignment is None:
            self.analog_assignments.pop((stream, channel), None)
        else:
            self.analog_assignments[(stream, channel)] = assignment
            if assignment.modality not in self.modality_names():
                self.custom_modalities.append(assignment.modality)
        if old is not None and (assignment is None or assignment.modality != old.modality):
            return self._drop_events(lambda e: (e.stream, e.channel) == (stream, channel))
        return False

    def _renumber(self):
        """Renumber events (per modality, by onset); excluded blanking trials follow their events."""
        excluded = {
            id(e) for e in self.events
            if e.modality in self.blanking_settings
            and e.number in self.blanking_settings[e.modality].excluded_trials
        }
        self.events = number_events(self.events)  # renumbers the same objects
        for modality, settings in self.blanking_settings.items():
            settings.excluded_trials = sorted(
                e.number for e in self.events if e.modality == modality and id(e) in excluded
            )

    def _drop_events(self, drop: Callable[[StimEvent], bool]) -> bool:
        """
        Remove the events where drop(event) is True. A modality's blanking result goes with
        its last event (its Blanking tab disappears, so it could no longer be removed) and is
        marked stale otherwise. Returns True if a blanking result was removed.
        """
        gone = {e.modality for e in self.events if drop(e)}
        if not gone:
            return False
        self.events = [e for e in self.events if not drop(e)]
        self._renumber()
        removed = False
        for modality in gone:
            res = self.blanking_results.get(modality)
            if res is None:
                continue
            if any(e.modality == modality for e in self.events):
                res.stale = True
            else:
                del self.blanking_results[modality]
                removed = True
        return removed

    def set_event_times(self, event: StimEvent, onset: float, offset: float) -> Optional[str]:
        """
        Change an event's onset/offset (rounded to the nearest sample) and renumber.

        Returns an error message, or None when the change was applied.
        """
        if not (math.isfinite(onset) and math.isfinite(offset)):
            return "Enter a time in seconds"
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
        self._renumber()
        self.mark_blanking_stale(event.modality)
        return None

    def reset_event_times(self, event: StimEvent):
        """Restore an event's detected onset/offset."""
        event.onset, event.offset = event.detected_onset, event.detected_offset
        self._renumber()
        self.mark_blanking_stale(event.modality)

    def get_blanking_settings(self, modality: str) -> BlankingSettings:
        if modality not in self.blanking_settings:
            self.blanking_settings[modality] = BlankingSettings()
        return self.blanking_settings[modality]

    def mark_blanking_stale(self, modality: Optional[str] = None):
        """Events changed after blanking was computed (all modalities when None)."""
        for name, res in self.blanking_results.items():
            if modality is None or name == modality:
                res.stale = True

    def mark_spikes_stale(self, stream: Optional[str] = None):
        """Blanking, filters or threshold settings changed after spikes were detected."""
        for name, res in self.spike_results.items():
            if stream is None or name == stream:
                res.stale = True

    def active_blanking(self, stream: str) -> List[BlankResult]:
        """Applied blanking results for a stream."""
        return [r for r in self.blanking_results.values() if r.stream == stream]

    def edited_event_count(self) -> int:
        return sum(1 for e in self.events if e.edited)

    def set_analog_channels(self, stream: str, channels: List[int]) -> bool:
        """
        Change which channels of a stream are analog. Drops assignments (and their events)
        that no longer apply, and takes channels that became analog out of CAR/CMR groups.

        Returns True if a blanking result was removed (see _drop_events).
        """
        state = self.stream_states[stream]
        state.set_analog_channels(channels)
        for key in [k for k in self.analog_assignments if k[0] == stream]:
            if key[1] not in state.analog_channels:
                del self.analog_assignments[key]
        analog = set(state.analog_channels)
        for f in state.filters:
            if f.channel_groups:
                groups = [[c for c in g if c not in analog] for g in f.channel_groups]
                f.channel_groups = [g for g in groups if g]
        return self._drop_events(lambda e: (e.stream, e.channel) not in self.analog_assignments)

    def save_session(self, filepath: str):
        """
        Save current session to JSON file (written to a temporary file first, so a failed
        save never leaves a truncated session behind).

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
            "blanking_settings": {m: b.to_dict() for m, b in self.blanking_settings.items()},
            "blanking_results": [r.to_dict() for r in self.blanking_results.values()],
            "spike_results": [r.to_dict() for r in self.spike_results.values()],
        }

        tmp = filepath + ".tmp"
        try:
            with open(tmp, "w") as f:
                json.dump(session, f, indent=2)
            os.replace(tmp, filepath)
        finally:
            if os.path.exists(tmp):
                os.remove(tmp)

    @staticmethod
    def _parse_filters(filter_dicts: list, messages: List[str]) -> List[FilterConfig]:
        """Saved filters that still exist and are valid; the rest are noted in messages."""
        known = {t.value for t in FilterType}
        filters, n_removed, skipped = [], 0, []
        for d in filter_dicts:
            kind = d.get("filter_type")
            if kind in REMOVED_FILTER_TYPES:
                n_removed += 1
                continue
            try:
                if kind not in known:
                    raise ValueError("unknown type")
                filters.append(FilterConfig.from_dict(d))
            except (KeyError, TypeError, ValueError) as e:
                skipped.append(f"{kind or '?'} ({e})")
        if n_removed:
            messages.append(
                f"Removed {n_removed} Vrms threshold filter{'s' if n_removed != 1 else ''}: that filter "
                "no longer exists. Use the Spike Detection tab for thresholding."
            )
        if skipped:
            messages.append("Skipped filters this version can't use: " + ", ".join(skipped) + ".")
        return filters

    def load_session(self, filepath: str):
        """
        Load session from JSON file. The whole file is read and checked before the
        recording is opened, so a file that can't be used changes nothing.

        Args:
            filepath: Path to session file
        """
        with open(filepath, "r") as f:
            session = json.load(f)

        block_path = session.get("block_path")
        if not block_path or not Path(block_path).exists():
            raise ValueError(f"Block path not found: {block_path}")

        # Notes about the session for the user (e.g. settings that no longer exist)
        messages: List[str] = []
        saved_states = {}
        for name, state_dict in session.get("stream_states", {}).items():
            filters = self._parse_filters(state_dict.get("filters", []), messages)
            saved = StreamState.from_dict(dict(state_dict, filters=[]))
            saved.filters = filters
            saved_states[name] = (state_dict, saved)
        assignments = [AnalogAssignment.from_dict(d) for d in session.get("analog_assignments", [])]
        events = [StimEvent.from_dict(d) for d in session.get("events", [])]
        blanking_settings = {
            m: BlankingSettings.from_dict(d) for m, d in session.get("blanking_settings", {}).items()
        }
        blanking_results = [BlankResult.from_dict(d) for d in session.get("blanking_results", [])]
        spike_results = [SpikeResult.from_dict(d) for d in session.get("spike_results", [])]
        probe = None
        if session.get("probe"):
            try:
                probe = ProbeGeometry.from_probeinterface_dict(session["probe"])
            except Exception as e:
                messages.append(f"The saved probe geometry could not be read ({e}); it was not restored.")

        self.load_block(block_path)
        self.load_messages = messages

        # Restore stream states
        for name, (state_dict, saved) in saved_states.items():
            if name in self.stream_states:
                # Merge saved state into loaded state
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
                current.set_view_range(saved.view_start, saved.view_end)  # clamped to this recording
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
                current.spike_settings = saved.spike_settings

        # Restore analog assignments and detected events
        self.custom_modalities = list(session.get("custom_modalities", []))
        for a in assignments:
            state = self.stream_states.get(a.stream)
            if state is not None and a.channel in state.analog_channels:
                self.analog_assignments[(a.stream, a.channel)] = a
        self.events = [e for e in events if (e.stream, e.channel) in self.analog_assignments]

        # Restore blanking settings and results (results only for streams that still exist)
        self.blanking_settings = blanking_settings
        for res in blanking_results:
            if res.stream in self.stream_states:
                self.blanking_results[res.modality] = res

        for res in spike_results:
            if res.stream in self.stream_states:
                self.spike_results[res.stream] = res

        if probe is not None:
            self.probe = probe

        # Restore selection
        if session.get("current_stream") in self.stream_states:
            self.select_stream(session["current_stream"])
