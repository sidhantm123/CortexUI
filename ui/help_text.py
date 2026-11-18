"""
What every editable setting means, in one place: the hover tooltips (explain()) and the
Help → Settings Guide window are both built from HELP, so they never disagree.

A new setting gets an entry here, a place in SECTIONS, and explain() on its widgets
(tests/test_help.py fails on an editable control without a tooltip).
"""
import html
from dataclasses import dataclass
from typing import Dict, List, Tuple

from PySide6.QtWidgets import QWidget


@dataclass(frozen=True)
class Help:
    title: str
    text: str
    default: str = ""


HELP: Dict[str, Help] = {
    # ── Channels tab ──────────────────────────────────────────────────────────
    "channels.mode": Help(
        "Channel Mode",
        "Single Channel shows one electrode. Overlay Channels draws up to 8 electrodes on top of "
        "each other, one colour each.",
        "Single Channel",
    ),
    "channels.electrode": Help(
        "Electrode",
        "The electrode shown in Single Channel mode. ↑/↓ on the keyboard step through electrodes. "
        "The number is the electrode number read from the channel label (elec1-10 → 10), or the "
        "channel's position in the recording (View → Number Channels By).",
    ),
    "channels.overlay": Help(
        "Overlay channels",
        "Pick an electrode and click + to add it to the overlay (at most 8). Select one in the list "
        "and click Remove Selected to take it out.",
    ),
    "channels.view_start": Help(
        "Start (s)",
        "Start of the time window shown, in seconds from the start of the recording. Type Start and "
        "End and click Apply View, or simply scroll and drag on the plot.",
    ),
    "channels.view_end": Help(
        "End (s)",
        "End of the time window shown, in seconds from the start of the recording.",
    ),
    "channels.apply_view": Help(
        "Apply View",
        "Shows the Start–End window. A window longer than Max length is shortened from Start.",
    ),
    "channels.reset_view": Help(
        "Reset",
        "Back to the first 10 s of the recording.",
    ),
    "channels.max_length": Help(
        "Max length (s)",
        "Longest time span the plots can show. Shorter spans load faster when you jump around the "
        "recording. With CAR/CMR or many overlay channels, memory can limit it further (a message "
        "says so). Remembered between sessions.",
        "20 s",
    ),
    "channels.scatter": Help(
        "Scatter / Dot Mode",
        "Draws each sample as a dot instead of joining samples with lines: useful to see individual "
        "samples when zoomed in far.",
        "off",
    ),
    "channels.refresh": Help(
        "Refresh Plot",
        "Redraws the plot, loading any data that is missing. Normally not needed: the plot updates "
        "by itself.",
    ),
    # ── Analog Ch. panel ──────────────────────────────────────────────────────
    "analog.sync": Help(
        "Sync X-axes",
        "Links the analog plot's time axis to the Time Series plot, so both always show the same "
        "time. Untick to zoom and pan the analog plot on its own.",
        "on",
    ),
    "analog.channel": Help(
        "Channel selection",
        "Which analog input the analog plot shows (1 = the stream's first analog input). The line "
        "below gives its label and what it is assigned to.",
    ),
    "analog.assign": Help(
        "Assign Channel",
        "Choose what the selected analog input marks (a stimulation modality) and how its events are "
        "detected.",
    ),
    "analog.edit_list": Help(
        "Edit List…",
        "Choose which channels of the stream are analog inputs. Analog inputs are shown here and left "
        "out of the electrode lists, CAR/CMR, Export and spike detection.",
    ),
    "analog.event": Help(
        "Event selection",
        "Jumps both plots to an event of the selected input's modality (the [ and ] keys step "
        "through events too).",
    ),
    "analog.detect": Help(
        "Detect Events",
        "Scans every assigned analog input over the whole recording and lists the events found. "
        "Detecting again replaces all events (it asks first if you edited any) and removes applied "
        "blanking, since the trial times may change.",
    ),
    "analog.table": Help(
        "Event table",
        "One row per event, numbered per modality in time order. Click a row to jump there. "
        "Double-click an Onset or Offset to correct it (in seconds, snapped to the nearest sample), "
        "or drag its dashed line on the analog plot. Right-click a row to reset an edited event.",
    ),
    # ── Assign Channel dialog ─────────────────────────────────────────────────
    "assign.modality": Help(
        "Modality",
        "What the line marks. A preset (ICMS, PNS, Tactor) fills in typical detection settings; "
        "“New…” adds your own name; “(none)” unassigns the line. Changing a line's modality removes "
        "its existing events.",
    ),
    "assign.polarity": Help(
        "Polarity",
        "Active high: the line rises during stimulation. Active low: the line drops during "
        "stimulation (e.g. a tactor trigger).",
        "Active high",
    ),
    "assign.threshold": Help(
        "Threshold",
        "Level in mV that separates idle from active. Auto puts it halfway between the idle level "
        "(the line's median) and the active level (its 50th most extreme sample), which suits clean "
        "trigger lines. Untick Auto to type your own.",
        "Auto",
    ),
    "assign.merge_gap": Help(
        "Merge gap",
        "Crossings that start closer than this (onset to onset) join into one event, so a pulse "
        "train counts as one trial. Use a gap longer than the spacing inside a train and shorter "
        "than the time between trials. Example: ICMS with 50 pulses 20 ms apart needs more than "
        "20 ms.",
        "20 ms (presets: 333 ms)",
    ),
    "assign.min_duration": Help(
        "Min duration",
        "Events shorter than this are dropped, e.g. glitches on the line. 0 keeps every event.",
        "0 ms",
    ),
    # ── Blanking tab ──────────────────────────────────────────────────────────
    "blanking.mode": Help(
        "Basic / Advanced",
        "Basic blanks fixed pads around every event on every electrode. Advanced blanks from the "
        "pre-pad until each electrode has recovered, measured per electrode. The mode used is the "
        "sub-tab that is showing when you click Apply Blanking.",
        "Basic",
    ),
    "blanking.pre": Help(
        "Pre-stim pad",
        "Blanking starts this long before each event's onset.",
        "1 ms",
    ),
    "blanking.post": Help(
        "Post-stim pad",
        "Blanking ends this long after each event's offset (Basic mode: the same for every "
        "electrode).",
        "0 ms",
    ),
    "blanking.window": Help(
        "Window size",
        "Recovery is measured in windows of this length: after the event, the RMS of each window "
        "in the spike band (250–5000 Hz) is compared with the electrode's baseline.",
        "50 ms",
    ),
    "blanking.n_windows": Help(
        "Num windows",
        "Blanking ends at the first run of this many quiet windows in a row after the offset "
        "(3 × 50 ms = 150 ms of quiet signal).",
        "3",
    ),
    "blanking.multiplier": Help(
        "Vrms multiple",
        "A window is quiet when its spike-band RMS is below this × the electrode's baseline RMS. "
        "The baseline is the median window RMS over up to 20 one-second stretches at least 3 s from "
        "any event. Lower values are stricter (longer blanking).",
        "1.5 ×",
    ),
    "blanking.min_post": Help(
        "Min post-pad",
        "Every electrode is blanked at least this long after the offset, even if it recovers sooner.",
        "100 ms",
    ),
    "blanking.max_post": Help(
        "Max post-pad",
        "Every electrode is blanked at most this long after the offset; electrodes that haven't "
        "recovered by then stop here (the status counts them as capped).",
        "5000 ms",
    ),
    "blanking.trials": Help(
        "Choose Trials",
        "Untick a trial to leave it unblanked (Advanced mode; Basic blanks every trial). Blank All "
        "Trials ticks them all again.",
        "all trials",
    ),
    "blanking.interpolation": Help(
        "Interpolation",
        "How blanked samples are filled: Linear joins the samples on either side with a straight "
        "line; Cubic draws a smooth curve through a few samples on either side. Blanked samples are "
        "always left out of the RMS, σ and spike detection.",
        "Linear",
    ),
    "blanking.apply": Help(
        "Apply / Remove Blanking",
        "Apply computes this modality's blanking (Advanced runs in the background) and uses it "
        "everywhere: plot, Analysis, Export and spike detection. Remove turns it off. When events "
        "or settings change afterwards, the blanking is marked out of date (amber) until you apply "
        "it again.",
    ),
    # ── Filters tab ───────────────────────────────────────────────────────────
    "filters.chain": Help(
        "Filter Chain",
        "The filters run in order, top to bottom, after blanking. Add, Edit (or double-click) and "
        "Remove change the list.",
    ),
    "filters.order": Help(
        "Up / Down",
        "Moves the selected filter earlier or later in the chain. Order matters, e.g. a high-pass "
        "before or after CAR.",
    ),
    "filters.bypass": Help(
        "Toggle Bypass",
        "Skips the selected filter (shown grey) without removing it.",
    ),
    "filters.bypass_all": Help(
        "Bypass All",
        "Shows unfiltered data without removing the filters.",
        "off",
    ),
    # ── Filter dialog ─────────────────────────────────────────────────────────
    "filter.type": Help(
        "Type",
        "Low-pass keeps frequencies below the cutoff, High-pass above it, Band-pass between Low and "
        "High; Notch removes a narrow band (e.g. mains hum). CAR / CMR subtract the mean / median "
        "of a group of electrodes from each of them, removing noise they share.",
    ),
    "filter.order": Help(
        "Order",
        "Steepness of the Butterworth filter. Filters run forward and backward (zero phase: no time "
        "shift), which doubles the effective order. 2–4 is typical.",
        "4",
    ),
    "filter.cutoff_low": Help(
        "Low Cutoff",
        "High-pass: frequencies below this are removed. Band-pass: the lower edge. Must be below "
        "half the sampling rate. Example: 250–5000 Hz isolates spikes.",
        "300 Hz",
    ),
    "filter.cutoff_high": Help(
        "High Cutoff",
        "Low-pass: frequencies above this are removed. Band-pass: the upper edge. Must be below "
        "half the sampling rate. Example: a 300 Hz low-pass keeps the LFP.",
        "6000 Hz",
    ),
    "filter.notch_freq": Help(
        "Notch Freq",
        "Centre of the band the notch removes: the mains frequency (60 Hz in the Americas, 50 Hz in "
        "most other countries).",
        "60 Hz",
    ),
    "filter.notch_q": Help(
        "Q Factor",
        "Sharpness of the notch: the band removed is about frequency / Q wide (60 Hz at Q 30 → about "
        "2 Hz). Higher Q = narrower notch.",
        "30",
    ),
    # ── CAR / CMR subgroups ───────────────────────────────────────────────────
    "car.grid": Help(
        "Electrode grid",
        "Click an electrode to put it in the selected subgroup (or take it out). Drag from one "
        "electrode to another to take every electrode between them, from other subgroups too. Start "
        "a drag on one of the subgroup's own electrodes to remove instead. Every electrode must be "
        "in a subgroup or excluded before OK.",
    ),
    "car.add_subgroup": Help(
        "+ Add Subgroup",
        "Adds a subgroup and selects it. Each subgroup is referenced on its own (e.g. one per "
        "array): CAR subtracts the subgroup's mean, CMR its median, from each of its electrodes.",
    ),
    "car.exclude": Help(
        "Exclude",
        "Leaves electrodes out of CAR/CMR, e.g. the stimulation electrode: they are not "
        "re-referenced and not part of any average. Select Exclude, then click or drag electrodes.",
    ),
    "car.deselect_all": Help(
        "Deselect All",
        "Takes every electrode out of its subgroup or exclusion. The subgroups themselves stay.",
    ),
    "car.auto_array": Help(
        "Auto-group by Array",
        "One subgroup per electrode array, read from the channel labels: the name before the number "
        "(elec1-*, elec2-*). Excluded electrodes stay excluded.",
    ),
    "car.auto_shank": Help(
        "Auto-group by Shank",
        "One subgroup per shank of the loaded probe map (File → Load Probe…). Excluded electrodes "
        "stay excluded.",
    ),
    # ── Spike Detection tab ───────────────────────────────────────────────────
    "spike.k": Help(
        "Threshold (k × σ)",
        "Spikes are crossings of k × σ, where σ = median(|x|) / 0.6745 of the filtered, blanked data "
        "in view (blanked samples excluded): a noise estimate that spikes barely affect, unlike the "
        "RMS in the plot header. Drag the dashed line on the plot to change k.",
        "4.75",
    ),
    "spike.polarity": Help(
        "Polarity",
        "Negative: a spike is a dip below −k·σ (typical extracellular spikes). Positive: a rise "
        "above +k·σ. Both: either.",
        "Negative",
    ),
    "spike.refractory": Help(
        "Refractory",
        "After a spike, crossings within this time are not counted, so one spike is counted once.",
        "2 ms",
    ),
    "spike.everywhere": Help(
        "Show threshold line on other tabs too",
        "Keeps the dashed threshold line on the Time Series plot while another tab is open "
        "(normally it shows only while this tab is open).",
        "off",
    ),
    "spike.detect": Help(
        "Detect Spikes (whole recording)",
        "Freezes each electrode's σ from the current view, then finds threshold crossings across the "
        "whole recording with the current blanking and filters. Pick a typical view of at least "
        "about 5 s first. Results appear in the Raster tab.",
    ),
    # ── Analysis tab ──────────────────────────────────────────────────────────
    "analysis.method": Help(
        "Method",
        "FFT Magnitude: one Fourier transform of the whole interval (Hann window), as amplitude: a "
        "sine of amplitude A reads A. PSD (Welch): power per Hz averaged over overlapping segments, "
        "smoother. Both are drawn in dB.",
        "FFT Magnitude",
    ),
    "analysis.select_on_plot": Help(
        "Select on plot",
        "Lets you drag on the Time Series plot to set the interval; Start and End follow.",
        "off",
    ),
    "analysis.interval": Help(
        "Start / End (s)",
        "The time interval analysed, with the current blanking and filters (CAR/CMR included). At "
        "most 30 million samples in total: about 1000 s of one electrode, or 125 s of 8, at 30 kHz.",
    ),
    "analysis.band": Help(
        "Frequency Range",
        "Lowest and highest frequency shown in the Spectrum plot. Changing them redraws the last "
        "result at once, no new analysis needed.",
        "0 Hz to half the sampling rate",
    ),
    "analysis.channels": Help(
        "Channels",
        "Use all displayed channels analyses the electrodes shown in the Time Series plot. Untick it "
        "to pick electrodes from the list (click to select several).",
        "on",
    ),
    "analysis.segment": Help(
        "Segment size",
        "PSD only: samples per segment. Frequency resolution = sampling rate / segment size (1024 at "
        "30 kHz → 29 Hz). Longer segments give finer resolution but a noisier estimate.",
        "1024",
    ),
    "analysis.overlap": Help(
        "Overlap",
        "PSD only: how much each segment overlaps the next (0.5 = half). More overlap gives a "
        "smoother estimate and takes longer.",
        "0.5",
    ),
    # ── Export dialog ─────────────────────────────────────────────────────────
    "export.range": Help(
        "Export Range",
        "Current view exports exactly the visible time window. Full recording exports everything, in "
        "chunks, so memory stays low.",
        "Current view",
    ),
    "export.blanking": Help(
        "Apply blanking",
        "Writes the data with the applied blanking interpolated across. Greyed out when no blanking "
        "is applied.",
        "on, if blanking is applied",
    ),
    "export.filters": Help(
        "Apply filter chain",
        "Runs the active filters (after blanking). Untick both boxes for raw data.",
        "on, if filters are active",
    ),
    "export.channels": Help(
        "Electrodes / Channels",
        "Type numbers and ranges in the current numbering, e.g. 1-24, 37-45. All Channels and "
        "Displayed fill the box for you. Written in ascending order; the preview below lists what "
        "you selected.",
    ),
    "export.scale": Help(
        "Scale Factor",
        "The data (in volts) is multiplied by this, rounded and stored as 16-bit integers "
        "(−32768 to 32767). 1,000,000 means 1 count = 1 µV. Values beyond that range are clipped; "
        "the summary file counts them.",
        "1,000,000",
    ),
    "export.layout": Help(
        "Sample layout",
        "Interlaced: sample 1 of every channel, then sample 2 of every channel, and so on (what "
        "Kilosort and most spike sorters expect). Channel-sequential: all samples of the first "
        "channel, then the next channel.",
        "Interlaced",
    ),
    "export.extension": Help(
        "File extension",
        ".i16 (TDT's convention) or .bin (Kilosort, Plexon Offline Sorter). Only the name differs; "
        "the contents are the same.",
        ".i16",
    ),
    # ── View menu, dialogs ────────────────────────────────────────────────────
    "view.numbering": Help(
        "Number Channels By",
        "Electrode: numbers read from the channel labels (elec1-10 → 10), used everywhere in the app. "
        "Channel: position in the recording (Ch 1 = the first channel in the file). Greyed out when "
        "the labels have no unique numbers.",
        "Electrode",
    ),
    "view.recording_panel": Help(
        "Show Recording Panel",
        "Shows or hides the left panel (Block Info and Streams).",
        "on",
    ),
    "view.shade_events": Help(
        "Shade Events on Time Series",
        "Shades detected events behind the Time Series traces, in their modality's colour.",
        "on",
    ),
    "info.all_fields": Help(
        "Show all fields",
        "Lists every channel setting stored in the .ccf file, not just the main ones.",
        "off",
    ),
    "probe.file": Help(
        "Probe file",
        "A probeinterface .json file describing the probe: contact positions, shanks and which "
        "recording channel each contact is wired to.",
    ),
    "probe.table": Help(
        "Probe table",
        "Ch: recording channel (1 = the first channel in the file; – = contact not connected). "
        "X / Y: position in µm. Shank ID: contacts with the same ID form a shank (Auto-group by "
        "Shank). Radius: contact radius in µm.",
    ),
}

# Order of the Settings Guide: (section, keys)
SECTIONS: List[Tuple[str, List[str]]] = [
    ("Channels tab", ["channels.mode", "channels.electrode", "channels.overlay", "channels.view_start",
                      "channels.view_end", "channels.apply_view", "channels.reset_view",
                      "channels.max_length", "channels.scatter", "channels.refresh"]),
    ("Analog Ch. panel", ["analog.sync", "analog.channel", "analog.assign", "analog.edit_list",
                          "analog.event", "analog.detect", "analog.table"]),
    ("Assign Channel dialog", ["assign.modality", "assign.polarity", "assign.threshold",
                               "assign.merge_gap", "assign.min_duration"]),
    ("Blanking tab", ["blanking.mode", "blanking.pre", "blanking.post", "blanking.window",
                      "blanking.n_windows", "blanking.multiplier", "blanking.min_post",
                      "blanking.max_post", "blanking.trials", "blanking.interpolation",
                      "blanking.apply"]),
    ("Filters tab", ["filters.chain", "filters.order", "filters.bypass", "filters.bypass_all"]),
    ("Add / Edit Filter dialog", ["filter.type", "filter.order", "filter.cutoff_low",
                                  "filter.cutoff_high", "filter.notch_freq", "filter.notch_q"]),
    ("CAR / CMR subgroups", ["car.grid", "car.add_subgroup", "car.exclude", "car.deselect_all",
                             "car.auto_array", "car.auto_shank"]),
    ("Spike Detection tab", ["spike.k", "spike.polarity", "spike.refractory", "spike.everywhere",
                             "spike.detect"]),
    ("Analysis tab", ["analysis.method", "analysis.select_on_plot", "analysis.interval",
                      "analysis.band", "analysis.channels", "analysis.segment", "analysis.overlap"]),
    ("Export dialog", ["export.range", "export.blanking", "export.filters", "export.channels",
                       "export.scale", "export.layout", "export.extension"]),
    ("View menu and other dialogs", ["view.numbering", "view.recording_panel", "view.shade_events",
                                     "info.all_fields", "probe.file", "probe.table"]),
]


def tooltip(key: str) -> str:
    """Rich-text tooltip: the title in bold, the explanation, then the default."""
    h = HELP[key]
    text = f"<b>{html.escape(h.title)}</b><br>{html.escape(h.text)}"
    if h.default:
        text += f"<br><i>Default: {html.escape(h.default)}</i>"
    return text


def explain(key: str, *widgets: QWidget):
    """Give each widget (a field and its label) the explanation of setting `key`."""
    tip = tooltip(key)
    for widget in widgets:
        if widget is not None:
            widget.setToolTip(tip)
