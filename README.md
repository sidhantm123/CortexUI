# Multi-Channel Neural Viewer

A desktop app (Windows / macOS) for viewing and analyzing multi-channel neural recordings from **Tucker-Davis Technologies (TDT)** and **Blackrock Neurotech** systems.

## Features

- Single-channel or overlay (up to 8 channels) time-series plots, smooth on recordings of 2+ hours
- Filter chains: low-pass, high-pass, band-pass, notch, CAR and CMR, applied in any order
- Spike detection (k × σ threshold, draggable on the plot) over the whole recording, shown as a raster
- Live per-channel Vrms of the visible data (blanked samples excluded), shown in the plot header
- Analog input viewer (below the main plot) with stimulation event detection: assign analog lines to modalities (ICMS, PNS, Tactor or your own), detect events, browse them in a table and correct onset/offset times
- Stimulation artifact blanking per modality, with fixed or adaptive (per-channel recovery) padding, applied before filtering
- FFT and Welch PSD on a selected time interval
- Probe geometry map (probeinterface JSON; `device_channel_indices` are 0-based device channels, −1 = not connected)
- Export a stream to int16 binary (`.i16` / `.bin`) at any processing stage (raw, blanked, blanked + filtered), for the current view or the whole recording
- Session save/load (filters, view ranges, channel selections, analog assignments, events)

## Supported data

Open a recording folder via **File → Open Recording Folder…**, drag-and-drop, or `python main.py /path/to/folder`. The format is detected automatically.

| System | Files | Notes |
|---|---|---|
| TDT | `.tev`, `.tsq`, `.tdx`, `.sev`, … | The index is parsed with the `tdt` package; stream data is read from the `.tev` directly (`.sev` stores through `tdt`) |
| Blackrock | `.ns1`–`.ns6`, `.ccf` | Read with `neo`. Each `.nsX` file is one stream. Channel labels come from the `.ccf`, and **View → Recording Info** (Ctrl+I) shows its channel settings. Analog inputs (`ainp*`, or ID > 128 without an `elec`/`chan` label) are detected automatically. A damaged `.nsX` or `.ccf` is skipped with a note. `.nev` (spikes/events) is not loaded yet. |

## Setup

Requires Python 3.11+.

```bash
python3 -m venv tdtgui
source tdtgui/bin/activate        # Windows: tdtgui\Scripts\activate
pip install -r requirements.txt
python main.py
```

Run the tests:

```bash
python -m unittest discover tests
```

Build a standalone app (output in `dist/`):

```bash
pyinstaller --clean tdt_viewer.spec
```

## Usage

1. **Open** a recording folder (or drop it on the window). The left panel shows the block's name (hover for the full name and path), duration and stream count, and lists its streams with channel counts and sample rates; click a stream to display it. **View → Show Recording Panel** (⌘1 / Ctrl+1) hides or shows the panel. Messages (exports, detections, warnings) appear briefly at the bottom of the plots; click one to dismiss it.
2. **Numbering**: channels are numbered by **electrode** by default (the trailing number of each label, e.g. `elec1-10` → electrode 10), everywhere in the app. **View → Number Channels By → Channel** switches to recording order (Ch 1 = first channel in the file). If a recording's labels have no unique electrode numbers (e.g. TDT), channel numbers are used.
3. **Channels tab**: choose single or overlay mode, set the view range and toggle scatter (dot) mode. The header above the plot names the electrode (e.g. `Elec 10 · Ch 125 · elec1-10`), shows chips for the applied blanking and filters (amber = out of date or bypassed; hover for details), and the **RMS** of the visible data per trace (blanked samples excluded).
4. **Navigate**: scroll to zoom time, drag to pan; the amplitude axis always fits the visible data. **Ctrl/⌘ + scroll** zooms amplitude (e.g. to see small spikes next to an artifact); double-click fits it to the data again. On a **trackpad**, swipe sideways to pan and swipe up/down or pinch to zoom time. The view is at most **Max length** seconds long (Channels → View Range, default 20 s, remembered; with CAR/CMR memory limits it to about 12 s). Data around the view is loaded ahead of your panning in the background, so pans at up to about one screen per second stay smooth. Keys: **←/→** pan, **+/−** zoom, **↑/↓** next/previous electrode, **[ / ]** previous/next event (Help → Keyboard Shortcuts). Spin boxes and tables keep their own arrow keys while they have focus.
5. **Filters tab**: add filters, reorder them (they run top to bottom) and bypass them individually or all at once.
   - For **CAR/CMR**, create subgroups and assign electrodes to the selected one: click an electrode, or press on one and drag to another to take every electrode between them (e.g. 1 → 32 diagonally); drag again to add more. A subgroup takes electrodes over from other subgroups. **Exclude** leaves electrodes (e.g. the stimulation electrode) out: they are not re-referenced and not part of any average. Every electrode must be in a subgroup or excluded before you can confirm.
6. **Analog Ch. panel** (bottom right) and analog plot (below the main plot):
   - **Channel selection** picks the analog input to show. **Edit List…** changes which channels count as analog; those are left out of the electrode lists and CAR/CMR.
   - **Assign Channel** maps the input to a modality and sets its polarity (active high/low), threshold (Auto = midpoint of idle and active levels), merge gap (pulses closer than this form one event) and minimum duration.
   - **Detect Events** scans every assigned input. Events are numbered per modality and listed in the table.
   - **Event selection** or clicking a row jumps to that event. **Sync X-axes** links the two plots' time axes.
   - **Correct an event** by double-clicking its Onset/Offset cell or dragging its dashed line on the analog plot. Edited times are bold; right-click a row to reset.
7. **Blanking tab** (needs detected events): one sub-tab per modality.
   - **Basic** blanks every electrode from onset − pre-pad to offset + post-pad and fills the gap linearly.
   - **Advanced** blanks from onset − pre-pad until, per channel, *N* consecutive windows have spike-band (250–5000 Hz) RMS below *k* × that channel's baseline (measured on clean stretches ≥ 3 s from any event), within the min/max post-pad. Choose trials and linear or cubic interpolation.
   - **Apply Blanking** / **Remove Blanking**. Blanking runs before the filter chain in the plot, Analysis and Export; blanked spans are shaded grey.
8. **Spike Detection tab**: threshold = *k* × σ (default 4.75), with σ = median(|x|)/0.6745 of the filtered, blanked data in view (blanked samples excluded). Choose polarity (negative, positive, both) and a refractory period (2 ms). While the tab is open, the Time Series plot shows the threshold as a red dashed line (Single Channel mode; drag it to change *k*). σ is a spike-robust noise estimate, not the RMS shown in the plot header. Add a high-pass or band-pass first (e.g. 250–5000 Hz); the tab warns if there is none.
   - **Detect Spikes (whole recording)** freezes each electrode's σ from the current view (pick a representative view of at least ~5 s), then runs through the whole recording with the current blanking and filters (a spike = the first sample past the threshold; crossings on blanked samples are ignored; refractory period applied). About 4 minutes for a 16-minute, 128-channel recording.
   - Results appear in the **Raster** tab (one row per electrode, a tick per spike, stimulus events shaded). The raster opens on the whole recording and zooms freely; while it shows no more than the Max length it stays in step with Time Series. Click a row to open that electrode in Time Series at the clicked time; hover for its spike count and rate. Results are saved with the session and marked out of date if blanking, filters or threshold settings change.
9. **Analysis tab**: pick FFT or PSD, choose an interval (type it in or drag on the plot), then click **Analyze**. It runs in the background (with Cancel) on the data as the plot shows it: applied blanking, then the filter chain, CAR/CMR included. An interval holds at most 30 million samples (about 1000 s of one channel, or 125 s of 8, at 30 kHz). Results appear in the Spectrum tab.
10. **File → Export → Stream as Binary**: pick the range (current view or full recording), then type the channels in the current numbering (e.g. `1-24, 37-45`, `1,2,3` or `33`; **All Channels** / **Displayed** fill the box; a preview lists what you selected and errors show in red). Channels are written in ascending order. Set the scale (default ×1e6, i.e. µV per count), layout and extension. Export writes the data as it currently is: every applied blanking, then the active filter chain. **Processing** lets you pick an earlier stage: untick **Apply blanking** and/or **Apply filter chain** (both for raw). If an applied blanking is out of date (events or settings changed since Apply), the dialog warns you. Full recordings are exported in chunks, so memory stays around 1.5 GB even for 8 GB files. A `.txt` summary next to the file records the range, channels, scale, clipped samples and exactly which blanking and filters were applied (including any filter that could not run, e.g. a cutoff above the Nyquist frequency). Cancelling an export (or quitting during one, which asks first) deletes the unfinished file.
11. **File → Save/Load Session** stores settings as JSON. The recording must still exist at its original path to reload a session.

## Project structure

```
main.py              Entry point
core/                AppState, background workers, reader_factory, chunked processing (export, spike detection)
tdt_io/              TDT reader
blackrock_io/        Blackrock .nsX reader and .ccf parser
models/              Data classes: recording metadata, per-stream state, filters, probe, analog assignments/events
dsp/                 Filtering, CAR/CMR, Vrms, decimation, FFT/PSD, event detection, blanking (no UI dependencies)
ui/                  PySide6 windows, panels and plots
tests/               Unit tests
```

Every reader exposes `load_block()` and `get_stream_data(stream, channels, start, end)`, which returns `(channels × samples)` data in volts. Only the visible time window is read from disk, and plots are min/max decimated for display.

See `CLAUDE.md` for what's in progress.

## Troubleshooting

- **Filter warnings**: cutoff frequencies must be below half the sample rate (the Nyquist frequency).
- **macOS "app is damaged"** (unsigned build): run `xattr -cr dist/TDT_Viewer.app`.
- **Unexpected errors** are shown once and written to `error.log` in the app's data folder (macOS: `~/Library/Application Support/TDT Viewer/Multi-Channel Neural Viewer/`). Please include it when reporting a problem.

## License

MIT. Built with [tdt](https://www.tdt.com/docs/sdk/offline-data-analysis/offline-data-python/), [neo](https://neo.readthedocs.io/), [PySide6](https://www.qt.io/qt-for-python), [pyqtgraph](https://www.pyqtgraph.org/) and [SciPy](https://scipy.org/).
