# Multi-Channel Neural Viewer

A desktop app (Windows / macOS) for viewing and analyzing multi-channel neural recordings from **Tucker-Davis Technologies (TDT)** and **Blackrock Neurotech** systems.

## Features

- Single-channel or overlay (up to 8 channels) time-series plots, smooth on recordings of 2+ hours
- Filter chains: low-pass, high-pass, band-pass, notch, CAR and CMR, applied in any order
- Live per-channel Vrms of the visible data (blanked samples excluded), shown in the plot corner
- Analog input viewer (below the main plot) with stimulation event detection: assign analog lines to modalities (ICMS, PNS, Tactor or your own), detect events, browse them in a table and correct onset/offset times
- Stimulation artifact blanking per modality, with fixed or adaptive (per-channel recovery) padding, applied before filtering
- FFT and Welch PSD on a selected time interval
- Probe geometry map (probeinterface JSON)
- Export a stream to int16 binary (`.i16` / `.bin`) at any processing stage (raw, blanked, blanked + filtered), for the current view or the whole recording
- Session save/load (filters, view ranges, channel selections, analog assignments, events)

## Supported data

Open a recording folder via **File → Open Recording Folder…**, drag-and-drop, or `python main.py /path/to/folder`. The format is detected automatically.

| System | Files | Notes |
|---|---|---|
| TDT | `.tev`, `.tsq`, `.tdx`, `.sev`, … | Read with the `tdt` package |
| Blackrock | `.ns1`–`.ns6`, `.ccf` | Read with `neo`. Each `.nsX` file is one stream. Channel labels come from the `.ccf`, and **View → Recording Info** (Ctrl+I) shows its channel settings. Analog inputs (`ainp*`, ID > 128) are detected automatically. `.nev` (spikes/events) is not loaded yet. |

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

1. **Open** a recording folder. Streams are listed on the left; click one to display it.
2. **Channels tab**: choose single or overlay mode, set the view range and toggle scatter (dot) mode. Each displayed channel's Vrms (of the visible data, blanked samples excluded) is shown in the top-right corner of the plot.
3. **Navigate**: scroll to zoom, drag to pan, double-click to reset.
4. **Filters tab**: add filters, reorder them (they run top to bottom) and bypass them individually or all at once.
   - For **CAR/CMR**, create subgroups and click channels to assign them. Every channel must be assigned before you can confirm.
5. **Analog Ch. panel** (bottom right) and analog plot (below the main plot):
   - **Channel selection** picks the analog input to show. **Edit List…** changes which channels count as analog; those are left out of the electrode lists and CAR/CMR.
   - **Assign Channel** maps the input to a modality and sets its polarity (active high/low), threshold (Auto = midpoint of idle and active levels), merge gap (pulses closer than this form one event) and minimum duration.
   - **Detect Events** scans every assigned input. Events are numbered per modality and listed in the table.
   - **Event selection** or clicking a row jumps to that event. **Sync to Raw Data Viewer** links the two plots' time axes.
   - **Correct an event** by double-clicking its Onset/Offset cell or dragging its dashed line on the analog plot. Edited times are bold; right-click a row to reset.
6. **Blanking tab** (needs detected events): one sub-tab per modality.
   - **Basic** blanks every electrode from onset − pre-pad to offset + post-pad and fills the gap linearly.
   - **Advanced** blanks from onset − pre-pad until, per channel, *N* consecutive windows have spike-band (250–5000 Hz) RMS below *k* × that channel's baseline (measured on clean stretches ≥ 3 s from any event), within the min/max post-pad. Choose trials and linear or cubic interpolation.
   - **Apply Blanking** / **Remove Blanking**. Blanking runs before the filter chain in the plot, Analysis and Export; blanked spans are shaded grey.
7. **Analysis tab**: pick FFT or PSD, choose an interval (type it in or drag on the plot), then click **Analyze**. Results appear in the Spectrum tab.
8. **File → Export → Stream as Binary**: pick the range (current view or full recording), channels, scale (default ×1e6, i.e. µV per count), layout and extension. **Processing** chooses the stage: tick **Apply blanking** and/or **Apply filter chain** (untick both for raw). Full recordings are exported in chunks, so memory stays around 1.5 GB even for 8 GB files. A `.txt` summary next to the file records the range, channels, scale, clipped samples and exactly which blanking and filters were applied.
9. **File → Save/Load Session** stores settings as JSON. The recording must still exist at its original path to reload a session.

## Project structure

```
main.py              Entry point
core/                AppState, background workers, reader_factory (picks TDT or Blackrock)
tdt_io/              TDT reader
blackrock_io/        Blackrock .nsX reader and .ccf parser
models/              Data classes: recording metadata, per-stream state, filters, probe, analog assignments/events
dsp/                 Filtering, CAR/CMR, Vrms, decimation, FFT/PSD, event detection, blanking (no UI dependencies)
ui/                  PySide6 windows, panels and plots
tests/               Unit tests
```

Every reader exposes `load_block()` and `get_stream_data(stream, channels, start, end)`, which returns `(channels × samples)` data in volts. Only the visible time window is read from disk, and plots are min/max decimated for display.

The Spike Detection tab is a placeholder for a feature in progress (see `CLAUDE.md`).

## Troubleshooting

- **Filter warnings**: cutoff frequencies must be below half the sample rate (the Nyquist frequency).
- **macOS "app is damaged"** (unsigned build): run `xattr -cr dist/TDT_Viewer.app`.

## License

MIT. Built with [tdt](https://www.tdt.com/docs/sdk/offline-data-analysis/offline-data-python/), [neo](https://neo.readthedocs.io/), [PySide6](https://www.qt.io/qt-for-python), [pyqtgraph](https://www.pyqtgraph.org/) and [SciPy](https://scipy.org/).
