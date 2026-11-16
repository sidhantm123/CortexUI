# CLAUDE.md — Multi-Channel Neural Viewer

Working notes for this repo: current status, plans, decisions and gotchas.
**Keep this file under 500 lines.** When a step ships, collapse its plan into one line under "Done" and
delete details that the code or git history already records.

---

## 1. Status (update with every commit)

| | |
|---|---|
| Active branch | `NSP_Basic` (not pushed; branched from `main` @ `beb2da4`) |
| Last commit | `96b62ac` Detect spikes across the whole recording and show them as a raster |
| Tests | 109 passing: `tdtgui/bin/python -m unittest discover tests` |
| Next step | User tests UI-1 (dark theme); then UI-2: plot header + left card + status bar → toast (§4) |

### Done
1. Analog input viewer and event detection (`78664d0`).
2. Editable event onset/offset, by table cell or by dragging the line on the analog plot (`7b559ef`).
3. Blanking tab (`e60ac42`): Basic (fixed pads) and Advanced (adaptive per-channel post-pad), Apply/Remove, applied before filters
   everywhere, saved in the session.
4. (`de192d3`) Vrms is computed on the visible data with blanked samples excluded, and shown in the Time Series plot's top-right
   corner (moved out of the Channels panel). The zeroing Vrms-threshold filter is removed; old sessions drop it with a
   message (`REMOVED_FILTER_TYPES`).
5. (`3507af0`) Export at any stage: Apply blanking / Apply filter chain checkboxes; chunked full-recording export (`core/export.py`,
   `ExportWorker`); "current view" now exports exactly the view (it used to write the ~3× buffer).
6. (`50a7747`) Electrode numbering app-wide by default; View → Number Channels By → Electrode / Channel (saved in
   QSettings).
7. (`8722e70`) Export dialog: typed channel selection (`ui/channel_spec.py`, in the active numbering, written ascending) with All
   Channels / Displayed buttons and a live preview, plus a warning when the applied blanking is stale.
8. (`4b8836b`) Step 4a, Spike Detection tab:
   - k·σ threshold (σ = median(|x|)/0.6745 in view, blanked samples excluded), polarity, refractory.
   - A draggable threshold line (Single mode). The user then asked to remove the trace markers and the info line, and
     the label was renamed "Threshold (k × σ)": σ is not Vrms.
   - Hints for a missing HP/BP filter and short views.
9. (`96b62ac`) Step 4b:
   - Detect Spikes runs over the whole recording, with σ frozen from the view.
   - It uses the shared chunk pipeline `core.export.iter_processed_chunks`, and the results feed the Raster tab.
   - Channel filtering is parallelised.
10. UI-1:
    - Dark theme (`ui/theme.py`) on all plots: faint grid, no axis box, monospace ticks.
    - µV/mV axes, done by scaling tick labels only.
    - One font policy, text roles in place of inline styles, accent-filled primary buttons.

### Branches
- **`main`:** the TDT/Blackrock viewer with filters, FFT/PSD, probe map, export and `.ccf` info.
- **`NSP_Basic`:** the current work, a step-by-step rebuild that follows the user's mockup.
- **`nsp_pipeline`:** a **local archive only, never merge it**. It held an elaborate NSP preprocessing pipeline the user dropped
  as "too complicated". Its `nsp_pipeline/events.py` helpers were reused in `dsp/events.py`.

---

## 2. Goal and working rules

**Goal:** a generalized, easy viewer and preprocessing tool that any lab member can use on any Blackrock (and TDT) recording.

**The target layout** is the user's hand-drawn mockup `~/Desktop/AppLayout.pdf` (4 pages; rotate each 90° clockwise):
- **Left:** Block Info and Streams.
- **Center:** the main plot (Time Series | Spectrum tabs) above the analog plot.
- **Right:** the control tabs **Channels | Blanking | Filters | Spike Detection | Analysis | Probe Map**, with the
  **Analog Ch.** panel below.

**Rules the user confirmed:**
- **Electrode numbers are the default everywhere** (the user thinks in electrodes). Channel/row numbers are the
  opt-in alternative. Any new UI that shows or takes a channel number must use `StreamState.display_number` /
  `channel_for_number` / `ordered_neural_channels` / `channel_label`, never `index + 1`.
- **Never force a workflow.** Tabs can be used in any order, and steps (blanking, filters) can be added or removed freely.
  A control whose prerequisite is missing is disabled (greyed out), not hidden behind a wizard.
- **Nothing rig-specific is hardcoded.** Use presets (e.g. ICMS/PNS/Tactor) that the user can edit or extend.
- **One step per commit.** The user tests each step before the next one starts.
- **Plan before editing.** Explain the next step and get approval first, and point out any inconsistencies in the mockup.
- **Keep it simple.** Over-engineering caused the restart. Prefer the smallest version that does the job.

---

## 3. Architecture

```
main.py               entry point
core/app_state.py     AppState: reader, block, per-stream StreamState, analog assignments, events, session JSON
core/workers.py       QRunnable workers (data load, filter, spectral, EventDetectWorker) + WorkerManager
core/reader_factory.py  open_reader(folder) -> TDTReader | BlackrockReader
tdt_io/, blackrock_io/  readers: load_block(), get_stream_data(stream, channels, t0, t1) -> (ch × samples volts, time)
models/               StreamInfo/RecordingBlock, StreamState, FilterConfig, ProbeGeometry, analog.py (events)
dsp/                  filters, CAR/CMR, Vrms, decimation, spectral, events.py — no Qt imports
ui/main_window.py     layout, signal wiring, plot buffering, detection/progress dialogs
ui/analog_plot.py     analog plot: draggable onset/offset lines, event highlight
ui/analog_panel.py    Analog Ch. panel + AssignChannelDialog + AnalogListDialog
ui/blanking_panel.py  Blanking tab: one ModalityBlankingPage (Basic | Advanced) per modality with events
tests/                unittest; test_blackrock.py has write_nsx_23() to build synthetic .nsX files
```

### Key concepts
- **Numbering:**
  - `StreamState.numbering` is a class-wide setting (`electrode` | `channel`), loaded from `QSettings` by the main
    window.
  - `electrode_numbers` is parsed from the trailing integer of each neural channel's label. It is None, with an
    `electrode_note`, when any label lacks a number or the numbers repeat; channel numbers are then used and the menu
    item is greyed out.
  - Internal state always stays as 0-based channel indices.
- **Analog vs neural channels:**
  - `StreamInfo.analog_channels` / `StreamState.analog_channels` hold the indices of analog inputs. They are auto-detected by
    `models.analog.is_analog_label` (label `ainp*` or electrode ID > 128), and the user can edit them via "Edit List…".
  - `StreamState.neural_channels()` drives every electrode list: the Channels spinbox, overlay, CAR/CMR groups, Analysis
    and Export.
  - The Channels spinbox position maps through this list, so it is not `index + 1` when analog channels are interleaved.
- **Events:**
  - `AnalogAssignment(stream, channel, modality, DetectionSettings)` and `StimEvent` are block-level and live in
    `AppState`.
  - Events are numbered **per modality** in onset order (`number_events`).
  - `detected_onset` / `detected_offset` keep the original times after a user edit.
  - Edits go through `AppState.set_event_times`, which validates, snaps to samples and renumbers.
- **Detection (`dsp/events.py`):**
  - Auto threshold = the midpoint of the median (idle) level and the 50th most extreme sample (active level).
  - Rising edges closer than the merge gap (onset-to-onset) join into one event.
  - Events shorter than the minimum duration are dropped.
  - `EventDetectWorker` reads assigned lines in 60 s chunks as float32.
- **Blanking (`models/blanking.py`, `dsp/blanking.py`, `BlankingWorker`):**
  - Settings are per modality (`AppState.blanking_settings`). Results are per modality (`AppState.blanking_results`),
    each holding the stream plus `start_s`/`end_s` of shape (trials × channels). Never store a sample mask.
  - `MainWindow._blank()` interpolates the active results on a copy of each buffer before `apply_filter_chain`: in the
    plot (display rows and the all-channel CAR buffer), Analysis and Export. The raw buffer is kept, so Remove is
    instant.
  - **Advanced:**
    - Baseline per channel = the median 50 ms window RMS of the **250–5000 Hz** band over 20 one-second windows at least
      3 s from any event.
    - The blank ends at the first run of N quiet windows after the offset, clamped to the min/max post-pad.
  - Editing events, changing settings or editing the analog list marks results `stale`; they are still applied but
    flagged. Re-detecting events clears them.
  - **RP01 check (ICMS, 1.5×):** median post-pad 1.65 s (notes: ~1.7 s), 2.1% at the 100 ms floor, none capped. The
    slowest channels are rows 125, 123, 121, 113, 111 (stim electrode first), matching the notes. A 300 Hz high-pass
    instead gave 1.35 s, which is why the 250–5000 Hz band is used.
- **Vrms:**
  - `MainWindow._rerender_from_buffer` computes `compute_vrms(view, exclude=blank mask)` on every pan or zoom.
  - The mask (`dsp.blanking.blank_mask`) is built per buffer in `_apply_filters`.
  - On RP01 elec1-45 over 124–129 s with a 250–5000 Hz band-pass: 115 µV raw, 7.5 µV with ICMS blanked, and 6.3 µV if
    the interpolated samples were counted (the bias the notes warn about).
- **Export (`core/export.py`):**
  - `export_binary` works in exact sample indices and passes `(i + 0.5)/fs` to readers, because `int(t*fs)` can drop a
    sample.
  - Chunks are 5 s, with 1 s of padding when filters are on (trimmed before writing).
  - Chunk boundaries avoid blank spans widened by the interpolation-anchor margin (`CUBIC_ANCHORS + 2`). Without that,
    a blank ending at a boundary loses its right anchor.
  - With CAR/CMR active, all channels are loaded.
  - On RP01 a full blanked export of 128 ch takes 141 s, writes 7.4 GB and peaks at ~1.5 GB RSS. macOS `ru_maxrss`
    is in bytes.
- **Zoom (`TimeZoomViewBox` in `ui/plot_widget.py`, used by Time Series and the analog plot):**
  - The wheel and drags act on x only (`setMouseEnabled(y=False)`).
  - y auto-fits to the samples inside the visible x-window (`setAutoVisible(y=True)`).
  - Ctrl/⌘+wheel scales y and turns y auto-fit off. On macOS, ⌘ is `ControlModifier` and Ctrl is `MetaModifier`.
  - A double-click (pyqtgraph sends it to `mouseClickEvent` with `ev.double()`) turns y auto-fit back on.
  - Previously the wheel zoomed y too, which permanently switched y auto-range off.
- **Spikes (4a):**
  - `dsp/spikes.py` (`robust_sigma`, `detect_crossings` with blank exclusion and a cross-chunk refractory `last`,
    `align_to_peak`).
  - `SpikeSettings` lives per stream in `StreamState` and is saved in the session.
  - `MainWindow._update_spike_overlay` runs in `_rerender_from_buffer` on the full-resolution visible slice, only while
    the Spike tab is current or "show everywhere" is on.
  - It computes σ for the selected channel only and places the line. **No markers or info line**: the user removed them.
    `detect_crossings`/`align_to_peak` remain for 4b.
  - **σ vs whole recording (explained to the user):** same formula and pipeline. The panel σ follows the view. In 4b,
    **decided (2026-11-03): σ per electrode is frozen from the current view at click time** (not from clean stretches or
    per chunk).
  - Threshold lines are `ignoreBounds` InfiniteLines, so they don't affect y auto-fit.
  - Line drags set k = |y|/σ, coalesced by a 30 ms timer. On RP01 Elec 10 (BP 250–5000, 10 s view): σ 7.7 µV, 19
    crossings.
- **Spikes (4b):**
  - `core/spike_detect.py`:
    - `freeze_sigma` processes the view range as one chunk.
    - `detect_spikes` uses `iter_processed_chunks(left_overlap=1, with_mask=True)`. The 1-sample overlap lets a
      crossing exactly at a chunk start be seen, and the refractory `last` is carried across chunks.
  - `SpikeResult` lives in `AppState.spike_results[stream]` and is saved in the session as sample indices (RP01: 435k
    spikes ≈ 8 MB).
  - It goes stale on blanking apply/remove, filter changes, analog-list edits, re-detected events, and k/polarity/
    refractory changes.
  - `ui/raster_plot.py` draws one `PlotCurveItem(connect="pairs")` of the ticks within the view, x-linked to Time Series.
  - **Performance:**
    - `dsp.filters.apply_filter` filters blocks of channels in threads (scipy releases the GIL; results are identical).
    - RP01 full detection with BP 250–5000: 472 s → 226 s, peak ~2.9 GB.
  - **RP01 finding:** with Basic ICMS blanking (post 500 ms), the raster shows a dense block on all electrodes for about
    1–3 s after each blank. That is the post-ICMS discharge, not spikes. Advanced blanking is the fix.
- **Theme (`ui/theme.py`):**
  - `apply_theme(app)` is called once in `main.py`: Fusion style, dark `QPalette`, one QSS and the UI font.
  - `style_plot()` is applied to every pyqtgraph plot. Axis lines are transparent; grid lines use the tick pen at
    `GRID_ALPHA`.
  - `left_scale` relabels the ticks: 1e6 → µV for the neural plot, 1e3 → mV for analog. Data stays in volts.
  - **Rules:**
    - Use the colour tokens, `set_role(label, "secondary"|"warning"|"error"|"success"|"title"|"mono")` and
      `set_primary(button)`.
    - **Never inline colours or font styles.** Numbers use the monospace font, and QSS already covers spinboxes and
      tables.
  - The checkmark lives in `ui/assets/check.svg`, bundled via the spec.
  - `ui/__init__.py` is lazy, so `ui.theme` loads without pyqtgraph and the splash shows immediately.
  - **No `QGraphicsEffect`, animations or antialiasing.** Redraws are unchanged: ~12 ms, and 15 zoom steps take ~0.5 s
    (offscreen).
  - **Planned UI-2:**
    - a plot header with the title and an RMS chip, plus blanking/filter chips;
    - the left panel as a compact, collapsible card with middle-elided names;
    - the status bar replaced by a toast;
    - empty states and keyboard shortcuts.
- **Plotting:**
  - Only a buffer of about 3× the view is read from disk, and it is min/max-decimated to ≤ 8000 points.
  - The analog plot keeps its own buffer and `WorkerManager`. It is x-linked to the main plot when Sync is on.
  - Both plots use `LEFT_AXIS_WIDTH` so the linked time axes line up.
  - Event markers are drawn only for events in view (`MAX_MARKERS`).

### Conventions
- Channel indices are 0-based internally and shown 1-based. Times are seconds from stream start. Data is in volts.
- Serialize with `to_dict`/`from_dict`, and keep it backward compatible (default any missing field).
- Long jobs run in a `CancellableWorker` with a `QProgressDialog`.
  - A job that must survive plot refreshes (e.g. detection) is **not** submitted to `_worker_manager`, because
    `cancel_all()` runs on every refresh. Keep its own reference so it can be cancelled on close.
- Don't rebuild a widget from inside its own signal (table `itemChanged`, line `sigPositionChangeFinished`); defer with
  `QTimer.singleShot(0, ...)`.
- Write commit messages in the imperative. **No Co-Authored-By or any other Claude attribution** (user rule,
  2026-11-17). Don't push unless the user asks.

---

## 4. Roadmap

### Step 4: Spike Detection tab (DONE: 4a + 4b; design notes below kept for reference)
- **Threshold:**
  - −k·σ with σ = median(|x|)/0.6745 on the filtered, blanked data **in the current view**, blanked samples excluded.
  - k defaults to 4.75 and can be edited. Polarity is negative, positive or both; the refractory period is 2 ms.
  - Warn when the chain has no high-pass or band-pass.
- **Draggable horizontal threshold line** on the Time Series plot, in **Single Channel mode only**:
  - Dragging sets k = |y|/σ for that channel.
  - k is shared, and each channel's threshold is k × its own σ.
- **Markers:** dots at crossings within the loaded buffer, updated live.
- **Detect Spikes** runs over the whole recording:
  - σ per channel is frozen from the current view at click time.
  - A worker reads 10 s chunks of all neural channels, then applies blanking, the filter chain (with CAR) and crossing
    detection, and stores only spike times.
  - Show a hint if the view is under ~5 s.
- **Raster tab** (Time Series | Raster | Spectrum):
  - One row per channel with a `|` tick per spike.
  - x-linked with the analog plot, with events shaded. Clicking a row selects that channel.
- **Session and stale results:** spikes are saved in the session and go stale when blanking, filters or events change.
- **Later step:** waveform snippets (overlaid ~2 ms cut-outs).

### Later / unscheduled
- **Analysis tab: frequency range for FFT/PSD (user request, 2026-11-17):** let the user set a min/max frequency (Hz)
  before Analyze; the Spectrum plot then shows only that band. The time-interval selection already exists.
- **CAR/CMR grouping by array (user request, 2026-10-27):**
  - Add "Auto-group by array" to the CAR/CMR subgroup panel, e.g. SG1 = lateral (elec 1–64, `elec1-*`) and SG2 =
    medial (elec 65–128, `elec2-*`). Derive the arrays from the label bank (`elecA-NN` → A), not hardcoded ranges.
  - The panel must say whether its numbers are electrode or channel numbers, following View → Number Channels By.
- **Probe map takes priority (user request, 2026-10-27):** once probe-map import is fully built out, a loaded probe map
  overrides everything else: electrode numbering, array/shank grouping and positions. It wins over label parsing and
  over channel order.
- A "Plots" tab: the user intends to add it later. Don't build it yet.
- Loading `.nev` spikes/events.
- Shading events on the main plot (currently only on the analog plot).
- Blanking is computed for the stream that is current when Apply is pressed. Multi-stream recordings (e.g. ns5 + ns2)
  would need it applied per stream.
- Faster full-file reads: detection takes about 50 s on 8 GB through neo's memmap. A direct sequential `.nsX` reader exists
  in the archive (`nsp_pipeline:blackrock_io/nsx_header.py`) if it's ever needed.

### Known issues
- With Sync on, after jumping to an event the two plots' time ranges can differ by a few ms (~0.3% of the window). Toggling
  Sync realigns them.

---

## 5. Reference dataset

`~/Developer/NSP/Data/RawData/RP01_Multimodal_Quality_2023-11-08-15-09-07-066_NSP1001.{ns5,ccf,nev}`
- **Recording:** one stream `ns5`, 136 channels at 30 kHz, 966.6 s, about 8 GB.
- **Channels:** rows 0–127 are `elecA-NN` (µV); rows 128–135 are `ainp1`–`8` (mV).
- **Full domain notes:** `~/Developer/NSP/CLAUDE.md`. §2.2 has the analog map and §9.6/§9.11 the blanking measurements.

**Analog map:**

| ainp | Signal | Use |
|---|---|---|
| 1 | Second ICMS line (protocol says unassigned) | ignore; it is **not** PNS |
| **2** | ICMS trigger (CereStim sync) | active-high, 50 pulses at 20 ms spacing per trial |
| 3 | CereStim monitor out | ignore |
| **4** | PNS trigger (UECU on/off) | active-high, one 1 s level per trial |
| 5 | UECU UDP clock, 50 Hz, always on | ignore; it marks nothing |
| **6** | Tactor trigger | **active-low**, 1 s drop per trial |
| 7 | Tactor position | continuous |
| 8 | Unassigned | flat |

**Expected detection with presets** (merge gap 333 ms): ICMS 9 trains of 0.984 s, PNS 9 of ~1 s, Tactor 12 (9 + 3 catch).
- PNS and Tactor have no crossing chatter (one crossing per trial) and ≥ 22 s between trials, so any gap from about 1.5 s to
  20 s works.
- ICMS needs a gap above 20 ms.

**Other facts:**
- The stim electrode is 10 (`elec1-10`, row 125). Bad electrodes are 7 and 49.
- Post-ICMS recovery is per channel: median ~0.75 s at 3× and ~1.7 s at 1.5×.

---

## 6. Environment

- **Venv:** `tdtgui/` inside the repo. It was rebuilt after the repo moved from `~/Desktop/GUI`.
- **Run:** `tdtgui/bin/python main.py`.
- **Requirements:**
  - `requirements.txt` pins PySide6 6.10, pyqtgraph 0.14, neo 0.14.5, scipy, numpy and tdt.
  - `h5py` is installed in the venv but not required.
- **Headless UI checks:** `QT_QPA_PLATFORM=offscreen`, then `w.grab().save(...)` for screenshots.
- **Tools:** `gh` is not installed; the user merges PRs on GitHub.
- **Machine:** 8 GB RAM. Avoid holding whole-file arrays of all channels.
