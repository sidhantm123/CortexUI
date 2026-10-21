# CLAUDE.md — Multi-Channel Neural Viewer

Working notes for this repo: current status, plans, decisions and gotchas.
**Keep this file under 500 lines.** When a step ships, collapse its plan into one line under "Done" and
delete details that the code or git history already records.

---

## 1. Status (update with every commit)

| | |
|---|---|
| Active branch | `NSP_Basic` (not pushed; branched from `main` @ `beb2da4`) |
| Last commit | `e60ac42` Add Blanking tab for stimulation artifacts |
| Tests | 78 passing: `tdtgui/bin/python -m unittest discover tests` |
| Next step | **Step 3: Filters cleanup** (§4), after the user tests blanking |

### Done
1. Analog input viewer and event detection (`78664d0`).
2. Editable event onset/offset, by table cell or by dragging the line on the analog plot (`7b559ef`).
3. Blanking tab (`e60ac42`): Basic (fixed pads) and Advanced (adaptive per-channel post-pad), Apply/Remove, applied before filters
   everywhere, saved in the session.

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
- Write commit messages in the imperative, ending with the Co-Authored-By line. Don't push unless the user asks.

---

## 4. Roadmap

### Step 3: Filters cleanup (NEXT)
- Remove `VRMS_THRESHOLD` from the filter chain (`models/filter_config.py`, `dsp/filters.py`, `ui/filter_panel.py`) and
  migrate old sessions that use it.

### Step 4: Spike Detection tab
- Threshold = −k·σ with **σ = median(|x|)/0.6745** (the user's choice). k defaults to 4.75 and can be edited.

### Later / unscheduled
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
