# CLAUDE.md — Multi-Channel Neural Viewer

Working notes for this repo: current status, plans, decisions and gotchas.
**Keep this file under 500 lines.** When a step ships, collapse its plan into one line under "Done" and
delete details that the code or git history already records.

---

## 1. Status (update with every commit)

| | |
|---|---|
| Active branch | `release/v1.0` (from `main` @ `cb5b2dc`, after PR #5 merged the settings help) |
| Last commit | The user's logo (`assets/logo.png`) as the app icon |
| Tests | 202 passing: `tdtgui/bin/python -m tests` (exit code 0 checked) |
| Next step | PR `release/v1.0` → `main` (CI green); after the merge, tag `v1.0.0` with the user's OK (§4) |

### Done
1. Analog events (`78664d0`), editable onset/offset (`7b559ef`), Blanking tab Basic/Advanced (`e60ac42`).
4. (`de192d3`) Vrms on the visible data, blanked samples excluded, in the plot header; the Vrms-threshold filter removed
   (old sessions drop it with a message, `REMOVED_FILTER_TYPES`).
5. (`3507af0`, `8722e70`) Export at any stage, chunked; typed channel selection (`ui/channel_spec.py`) with a preview
   and a stale-blanking warning.
6. (`50a7747`) Electrode numbering app-wide; View → Number Channels By (QSettings).
8. (`4b8836b`, `96b62ac`) Spike Detection: k·σ line (Single mode; no markers/info line, at the user's request), Detect
   Spikes over the whole recording with σ frozen from the view, Raster tab.
10. (`ec903d7`, `9c7f5fc`, `7aa02ad`) UI: dark theme, µV/mV tick scaling, text roles, accent `#CC79A7` (user), thin
    progress bars, Time Series header (title, chips, RMS), hideable recording panel (the user kept the Block Info +
    Streams layout), toasts instead of a status bar, shortcuts, scrollable control tabs.
13. (`b332ee8`) Audit step 1 (filter chain + CAR memory):
    - **Bug:** any SOS filter placed *before* CAR/CMR was silently dropped (SOS ran on the displayed rows, CAR on the
      raw all-channel array, which then overwrote them). The chain now runs in order on the all-channel array.
    - **Bug:** adding CAR/CMR while the buffer covered the view never loaded the all-channel data (fast path).
    - **Memory:** per-edit transient on a CAR view 4.0× → 1.35× of the all-channel buffer (1175 → 395 MB on a 3 s
      view); the buffer is freed when no CAR/CMR needs it. Render path unchanged.
14. (`177bdc4`) Audit step 2 (buffer logic): view length capped by `MAX_BUFFER_SAMPLES` (zooming out used to load the
    whole recording); one disk load and one render per jump (was 2 + 2; the y auto-fit re-render is gone); views
    past the ends clamped before the buffer checks; `_apply_filters`/`_rerender_from_buffer` use the buffer's
    channels; CAR channel change re-slices instead of reloading; scatter toggle no longer re-filters; spin boxes
    fire once per typed number; stream switch sets the plot to the stream's view; the analog follower's range is no
    longer set while linked (10 s view came out as 7.52 s at load; the Sync drift known issue is gone).
15. (`e5bb0f2`) Zoom cap + smooth loading (user report: single-channel zoom-out wasn't capped and pans waited for data):
    - User "Max length" for the view (default 20 s, Channels → View Range, remembered), enforced for wheel,
      trackpad, pinch, drag and keys; memory may lower it (CAR → 12.3 s).
    - Sliding float32 buffer with direction-aware prefetch and incremental reads in the worker (blank + filter
      there too); direct sequential `.nsX` reads, ~7× faster than neo's memmap on a cold disk; analog plot reuses
      the main window's freshly cached pages; fixed a worker leak (~190 MB per pan).
    - Trackpad: horizontal swipe pans, vertical swipe and pinch zoom. Raster unlinked: zooms freely, syncs within
      the cap, row click opens the electrode at that time. CAR filters only the displayed rows (exact; 136× less).
16. (`8b1d43b`) Audit step 3 (long-job lifecycle: Detect Events, Advanced blanking, Detect Spikes, Export):
    - **Bug:** Cancel during event detection still replaced the events (and cleared blanking); a job cancelled before
      opening another recording could install its result there. Workers now check Cancel before every result, and
      `MainWindow._run_job` applies a result only if the job wasn't cancelled and the recording is the same.
    - One job of each kind at a time: a cancelled job finishes its current chunk (Detect Spikes ~0.2 s on RP01)
      and a restart meanwhile is refused with a toast (two exports to the same file used to delete each other).
    - `export_binary` deletes its file when cancelled or failed (also when quitting). Quit, Open and Load Session
      ask before stopping a running export; quit waits for the jobs (up to 10 s) before closing the reader.
    - Progress dialogs are freed (`deleteLater`); every closed one used to stay alive with its worker.
17. (`98f5b1f`) Audit step 4 (σ freeze): `freeze_sigma` works in 5 s chunks keeping only |x| (float32) and can be cancelled
    (0–10 % of Detect Spikes). The chunk pipeline frees each chunk before reading the next (two were alive at
    once). RP01, 128 ch, BP 250–5000: 20 s view + CAR peak 2.75 → 1.11 GB numpy, 10 s BP 0.87 → 0.61 GB; σ
    identical to 1e-7.
18–22. Audit steps 5–9 (`7899b48` render path: NaN RMS no longer freezes the trace, blank/event items reused; `3e49243`
    failed Open/session change nothing, `.tmp` + rename saves; `7658f42` unassigning a line drops its events, trials
    follow renumbering; `97d2973` Analyze through `iter_processed_chunks` with CAR, capped at `MAX_ANALYSIS_SAMPLES`;
    `7cb3027` small fixes: `_sample_index`, FFT window-sum amplitude, σ = 0 → NaN, `install_error_log`, `_only_nsx`).
23. (`4ef4e41`) Audit step 10 (TDT): **TDT viewing was broken** with numpy 2 (tdt 0.7.3's windowed reads wrap uint
    arithmetic). `TDTReader` parses the `.tsq` once (`headers=1`) and reads `.tev` records itself (`_TevStream`) at
    `floor(t*fs + 1e-6)`; `.sev` stores still go through tdt (t1 a quarter sample early): **untested, no `.sev`
    data**. `tests/test_tdt.py` writes synthetic `.tsq`/`.tev` that tdt itself reads back exactly.
24. (`4dbb788`) Audit step 11 (probe indices): `device_channel_indices` are 0-based (were taken as 1-based); −1 =
    not connected (`models.probe.UNWIRED`). Sessions write `probe_index_base: 0`; older ones still load. **Not
    checked against a lab probe file.**
25. (`ece5a7a`) CAR/CMR group dialog (user request): press and drag selects every electrode between the press and the cursor
    in reading order (`_ChannelGrid`: buttons are mouse-transparent, the nearest button centre wins, auto-scroll
    past the visible rows); drags add up; the selected subgroup takes electrodes over; an **Exclude** brush leaves
    electrodes in no group (`EXCLUDED`). A click still toggles. Editing a saved filter shows electrodes in no
    group as excluded.
26. (`926c09e`) Analysis → Frequency Range (user request): Min/Max Hz shown in the Spectrum plot. `SpectrumPlot` keeps the
    whole result and `set_band` crops what is drawn (y auto-fits the band), so changing the range needs no
    re-analysis; saved per stream (`fft_fmin`/`fft_fmax`, None = full).
27. (`605023b`) CAR/CMR **Auto-group by Array** (user request): one subgroup per array, named after it, from the labels
    (`models.stream_state.array_name`: the label before its trailing number, `elec1-10` → `elec1`; arrays of < 2
    electrodes are left out). Both auto-group buttons keep Exclude marks. The dialog says whether its numbers are
    electrode or channel numbers, and hovering an electrode shows its label and channel.
28. (`6b84a8f`) **Fix: crash or freeze at the end of a long job** (found while testing 29; intermittent, reproduced on RP01
    under load: segfault inside `QProgressDialog::setValue`, and a stall in `progress.close()`). For a modal
    dialog `setValue` processes events, so the job's result could be handled inside it and close (and, since
    audit step 3, `deleteLater`) the dialog under its own `setValue`. Job progress now goes to the bar
    (`theme.ProgressDialog.progress_bar().setValue`), which never processes events; 30 detections under
    load ran clean (the old code crashed by the second).
29. (`b23764e`) Event shading on Time Series (user request): faint modality-coloured bands (as the raster) behind the traces,
    under the blanking shade (`TimeSeriesPlot.set_event_spans`, items reused like the blank spans);
    `MainWindow._update_event_shading` runs on every render and every event change. View → Shade Events on Time
    Series (QSettings `shade_events`, on by default).
30. (`eb4f9f4`) **Fix (user report): a straight line at the right edge of both plots when zoomed in.** Item 18 turned the
    min/max decimation's leftover samples (up to `n_bins − 1`, e.g. 3,100 of an 83,100-sample view) into one bin
    drawn as two points. Bins are now `ceil(n / n_bins)` samples, so the leftover is shorter than one bin.
31. (`fd3a3b2`) CAR/CMR deselection (user request): a drag that starts on an electrode that already has the selected brush
    removes the brush from the electrodes in the run (other subgroups' left alone); elsewhere drags assign as
    before. **Deselect All** (status line) clears every electrode, exclusions included; the subgroups stay.
32. (`6bc089f`) Every editable setting explained (user request): `ui/help_text.py` holds one entry per setting (74; title,
    text, default) used for the tooltips (`explain(key, field, label)`) and **Help → Settings Guide** (F1;
    non-modal, searchable). `tests/test_help.py` fails on any editable control without a tooltip.
33. (`1c8e821`) Packaging step 1: the app is **CortexUI** (`version.py`: `APP_NAME`, `APP_VERSION`, `APP_URL`; title, splash,
    About). No organization name, so `error.log` is in `~/Library/Application Support/CortexUI/` (`%APPDATA%\CortexUI`);
    `QSettings("NSP", "MultiChannelNeuralViewer")` is kept on purpose (saved preferences carry over).
    `main.py --smoke-test FOLDER` opens the folder in the real window and exits 0 once the first processed buffer
    is in, or 1 on an error box, error toast, uncaught exception or 60 s; it prints the result, or (no console:
    the windowed Windows app) writes a failure to `error.log`. `tests/test_smoke.py` runs it in a subprocess.
34. (`2bec03c`) Packaging step 2: `packaging/make_icons.py` makes the icons from the **user's logo**
    `assets/logo.png` (pink action potential on near-black, 1254 px; replaced the drawn icon after step 4) as a
    rounded tile: `assets/icon.png`, `.ico` (PNG entries, no Pillow), `.icns` (iconutil, Apple's 824/1024 grid).
    Below 128 px the line is dilated to ≥ 1.6 px (it faded at 16–32 px). Window icon outside macOS. `cortexui.spec` (replaces `tdt_viewer.spec`; `upx=False`, `h5py` excluded,
    `LSMinimumSystemVersion` 14.0) and `packaging/make_dmg.sh` (ditto + Applications link, hdiutil UDZO, retries).
    Local Intel build: 181 MB app, 87 MB `.dmg`, ad-hoc signed, smoke test OK on RP01 (5 s warm, ~40 s first run).
35. (`a237f04`, fixes `f80f111` `71a5723` `e3b3191`) Packaging step 3: `.github/workflows/release.yml` + release notes.
    The first CI runs found: Windows can't delete a still-mapped `.nsX` (`tests.remove_dir`), and **the test process
    crashed at exit after "OK" on every platform** (see Conventions: exit via `os._exit`, `python -m tests`). Job
    logs need admin rights, so failures go to public annotations (`packaging/ci_annotate.py`). First green run
    44378241396 (6 min): artifacts Apple Silicon 73.6 MB, Intel 81.9 MB, Windows 103 MB.
36. Packaging step 4: README Download section (`…/releases/latest`), how to make a release, macOS first-launch help.

### Branches and repo
- **Repo:** `github.com/sidhantm123/CortexUI` (public; renamed from `tdtgui` on 2026-11-19, old URLs redirect).
- **`main`:** everything so far (PR #4 merged `NSP_Basic`, PR #5 the settings help). New work branches from it.
- **`release/v1.0`:** packaging (§4).
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
main.py               entry point (`--smoke-test FOLDER` checks a build);  version.py  app name + version
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
ui/stream_panel.py    left Block Info + Streams;  ui/toast.py  transient messages;  ui/theme.py  tokens, QSS, plot styling
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
  - Edits go through `AppState.set_event_times`, which validates, snaps to samples and renumbers (`_renumber`
    keeps excluded blanking trials on their events). Removing events goes through `_drop_events`.
- **Detection (`dsp/events.py`):**
  - Auto threshold = the midpoint of the median (idle) level and the 50th most extreme sample (active level).
  - Rising edges closer than the merge gap (onset-to-onset) join into one event.
  - Events shorter than the minimum duration are dropped.
  - `EventDetectWorker` reads assigned lines in 60 s chunks as float32.
- **Blanking (`models/blanking.py`, `dsp/blanking.py`, `BlankingWorker`):**
  - Settings are per modality (`AppState.blanking_settings`). Results are per modality (`AppState.blanking_results`),
    each holding the stream plus `start_s`/`end_s` of shape (trials × channels). Never store a sample mask.
  - The active results are interpolated before `apply_filter_chain` everywhere: the plot (`process_buffer`, display
    rows and the all-channel CAR buffer) and the chunked jobs (`iter_processed_chunks`: Analysis, Export, spikes). `apply_blanking` copies only when a span overlaps the buffer and
    otherwise returns the input itself; the raw buffer is never modified, so Remove is instant.
  - **Advanced:**
    - Baseline per channel = the median 50 ms window RMS of the **250–5000 Hz** band over 20 one-second windows at least
      3 s from any event.
    - The blank ends at the first run of N quiet windows after the offset, clamped to the min/max post-pad.
  - Editing events, changing settings or editing the analog list marks results `stale`; they are still applied but
    flagged. Re-detecting events clears them.
  - **RP01 check (ICMS, 1.5×):** median post-pad 1.65 s (notes: ~1.7 s), 2.1% at the 100 ms floor, none capped. The
    slowest channels are rows 125, 123, 121, 113, 111 (stim electrode first), matching the notes. A 300 Hz high-pass
    instead gave 1.35 s, which is why the 250–5000 Hz band is used.
- **Filter chain (`dsp/filters.py`, `dsp/referencing.py`):**
  - With `all_channel_data`, filters before a CAR/CMR must feed into it. **CAR** is the same linear combination of
    channels at every sample and every SOS filter is the same LTI filter on every channel, so they commute exactly:
    the chain references all channels first, then filters **only the displayed rows** (one CAR filter: `car_rows`
    subtracts each group's mean from the requested rows, no re-referenced copy of all channels; 136 ch × 15 s:
    0.07 s, 16 MB transient). **CMR** (a median) doesn't commute, so a chain with CMR runs in order on every channel.
  - A channel in no CAR/CMR group (the dialog's **Exclude**) is neither re-referenced nor part of any mean/median.
  - **Memory rules** (the all-channel buffer is ~1 GB for a 10 s view of 136 ch): the chain makes one working copy
    and then filters it in place; pass `inplace_ok=True` when the array you hand it is already scratch (a blanked
    copy, a pipeline chunk), never for `_raw_data`/`_raw_data_all`. `apply_car`/`apply_cmr` work row by row and take
    `copy=False`. `apply_filter` uses 2-row blocks because `sosfiltfilt` holds ~3× its input in temporaries (8 blocks
    of 17 rows peaked at 3–4× the array; 2-row blocks ~0.3×, for ~50 % more time). `with_vrms=False` skips the Vrms
    pass that chunked jobs and the plot never use.
  - `MainWindow._apply_filters` sets `_raw_data_all = None` as soon as no active CAR/CMR needs it; `_refresh_plot`'s
    fast path requires it when one does (`_chain_needs_all`).
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
- **Zoom (`TimeZoomViewBox` in `ui/plot_widget.py`, used by Time Series, analog and raster plots):**
  - **Gestures:** vertical wheel / two-finger vertical swipe zooms time; horizontal wheel / two-finger horizontal
    swipe pans (`pan_pixels`, `pixelDelta` for trackpads, same sign rule as Qt scroll areas so it follows macOS
    natural scrolling); pinch zooms time (`enable_pinch_zoom`: an event filter on the plot's viewport for
    `ZoomNativeGesture`). pyqtgraph alone zooms on any wheel orientation and ignores pinch.
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
    - `freeze_sigma` processes the view in 5 s chunks, keeping each channel's |x| as float32 for the median.
    - `detect_spikes` uses `iter_processed_chunks(left_overlap=1, with_mask=True)`. The 1-sample overlap lets a
      crossing exactly at a chunk start be seen, and the refractory `last` is carried across chunks.
  - `SpikeResult` lives in `AppState.spike_results[stream]` and is saved in the session as sample indices (RP01: 435k
    spikes ≈ 8 MB).
  - It goes stale on blanking apply/remove, filter changes, analog-list edits, re-detected events, and k/polarity/
    refractory changes.
  - `ui/raster_plot.py` draws one `PlotCurveItem(connect="pairs")` of the ticks within the view (not x-linked; see
    Plotting).
  - **Performance:**
    - `dsp.filters.apply_filter` filters small blocks of channels in threads (scipy releases the GIL; results are
      identical).
    - RP01 full detection with BP 250–5000: 472 s → 226 s, peak ~2.9 GB RSS before audit step 4.
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
  - **No `QGraphicsEffect`, animations or antialiasing.** Redraws are unchanged: ~11 ms, and 15 zoom steps take ~0.5 s
    (offscreen).
  - **Re-polish gotcha:** `set_role` on an already-polished label updates colours but **not** padding or border sizes.
    Roles that swap at runtime (chip ↔ chip-warning, toast variants) must share their box geometry, and a label's
    role must be set before its first polish (`Toast.__init__`, `set_chips`).
  - **Widgets:**
    - `theme.ElidedLabel` (middle elision; its `sizeHint` asks for the full text so it un-elides).
    - `theme.progress_dialog(text, parent, cancellable, maximum)`: always use it for progress dialogs.
    - `Toast.show_message(text, INFO|WARNING|ERROR)` for transient messages. Filter warnings toast only when they
      change.
  - **Plot header (`TimeSeriesPlot`):**
    - `set_title` is called only when the plotted channels change.
    - `set_vrms` runs on every render, so it only sets text when it has changed.
    - `set_chips` is fed by `MainWindow._update_plot_chips()`, which every blanking, filter or stale change must call.
  - **Shortcuts (`MainWindow._setup_shortcuts`):**
    - Window-wide `QShortcut`s: ←/→ pan, +/− zoom, ↑/↓ electrode, [ / ] event.
    - Spin boxes, tables and combos don't claim Up/Down, so when one of them (`_KEY_INPUTS`) has focus, `_on_plot_key`
      re-sends the key to it.
    - The shortcuts are disabled while it does this; otherwise the re-sent key triggers them again, recursing until
      a `SystemError`.
- **Plotting — sliding buffer (`core/view_buffer.py`, `BufferLoadWorker`, `MainWindow._refresh_plot`):**
  - **Why:** `.nsX` stores every channel side by side, so reading one channel for T s reads the whole file region.
    Cold reads through neo's memmap cost ~58 ms per recorded second (a page fault per 16 KB); that, not RAM, made
    zoomed-out views and pans wait.
  - **Direct reads:** `BlackrockReader` reads a segment's contiguous int16 block with plain file reads in 16 MB
    steps (`_NsxStream.file_layout/read_counts`): ~8 ms per recorded second cold, identical samples to neo. Falls
    back to neo when the segment isn't one plain block (e.g. per-sample timestamped files). Benefits every reader
    caller (export, detection, blanking). `get_stream_data(..., dtype=np.float32)` for plot buffers.
  - **View cap:** `min(user Max length, memory, duration)`. Max length is a QSettings preference (`max_view_s`,
    default 20 s, Channels → View Range). Memory cap = `MAX_BUFFER_SAMPLES` (1e8 float32 = 400 MB) / (2 × loaded
    channels × fs): 136 ch under CAR → 12.3 s. Applied with `TimeZoomViewBox.set_max_span` (`setLimits(maxXRange)`,
    which pyqtgraph enforces for every input) on the time and analog plots only; `_on_view_range_requested` clamps
    long requests from `start`. A clamp or a zoom-out stopped at it toasts (`_cap_message`).
  - **Window:** buffer = view + margins, (before, after) = 0.5/0.5 views when still, 0.5 behind / 1.5 ahead while
    panning (`_pan_dir`, set from view moves and arrow keys), scaled to fit `MAX_BUFFER_SAMPLES`.
  - **Loads** (`_start_buffer_load` → `BufferLoadWorker`): read only `missing_pieces` of the target (5 s sub-reads,
    cancel-checked), `stitch` to the old buffer's overlap (always a new, trimmed array), then blank + filter in the
    worker (`process_buffer`, the same steps as `_apply_filters`). A jump with no overlap first loads view ± 1 s
    (data in ~0.2 s), then `_maybe_prefetch` grows the margins. Prefetch starts when a third of a side's margin is
    used and reads at most `MAX_EXTEND_S` (10 s) per side so landings are frequent; after a miss the full lead is read.
  - **Never discard same-channel work:** an in-flight load covering the view is left to land; if a pan ran past it,
    `_reload_pending` extends again from its result. Only a jump elsewhere or a channel/stream change cancels
    (sequence check). Cancelling and re-reading from the older buffer is what made fast pans fall behind for good.
  - A result whose processing signature (`_processing_signature`: bypass, filter dicts, blanking result ids)
    changed while loading keeps the new raw samples and re-runs `_apply_filters`.
  - On a miss the loaded part of the view is rendered at once (partial trace, not the previous frame).
  - **Analog plot:** same worker and rules, one channel, no processing. While synced and on the main stream it
    loads exactly the main buffer's window right after each main load lands (`_sync_analog_with_main`): those pages
    are in the OS cache, ~1 ms per recorded second instead of a second cold read competing with the main one.
  - **Measured (RP01, one channel, BP, 20 s view, cold regions):** data after a jump in ~0.2 s (was 0.9–1.3 s);
    steady pans at 10, 20 and 30 s/s never ran past the loaded data. CAR (12.3 s view): 0/40 frames late at 6 s/s,
    4/40 at 12 s/s. App footprint stays ~400 MB single-channel, ~1.1–1.5 GB under CAR. Note macOS `ru_maxrss` also
    counts mapped file pages; use `footprint <pid>` for the app's own memory.
  - **Worker leak (fixed):** `WorkerManager.submit`'s cleanup closure held the worker inside a Qt connection on
    `worker.signals`, a cycle the GC can't see, so every finished worker and the buffer it referenced stayed alive
    (~190 MB per pan). The closure holds a weakref; `BufferLoadWorker` drops `old`/`process` when done.
  - **One load, one render per move:** `_on_view_range_requested` sets `_setting_range` so the synchronous
    `sigXRangeChanged` only updates the state, then calls `_refresh_plot` once. The plots listen to
    `sigXRangeChanged`, not `sigRangeChanged` (the y auto-fit after every render would trigger a second render).
  - `_apply_filters` and `_rerender_from_buffer` work on `_buffer_channels` (what `_raw_data` holds), never on the
    current selection, which may have moved on while a load is in flight. `_on_buffer_loaded` drops
    `_filtered_data` before swapping in the new buffer and leaves no buffer if processing fails.
  - Views are clamped to `[0, duration]` (`StreamState.set_view_range`) before any buffer check; the plot itself may
    show past the ends (Ctrl+R pads 2 %).
  - **Linked views push back:** pyqtgraph's `setXLink` is one-directional in storage but a follower's
    `setXRange` calls `linkedViewChanged` on the master with a range re-derived from pixel geometry. Never set the
    analog plot's range while Sync is on (that is how a 10 s view became 7.52 s at load and why the two plots used
    to drift by a few ms after jumps); link it and let it follow.
  - The analog plot keeps its own buffer and `WorkerManager`. Both plots use `LEFT_AXIS_WIDTH` so the linked time
    axes line up. Event markers are drawn only for events in view (`MAX_MARKERS`).
  - **Raster is not x-linked** (linking would cap it at the Time Series maximum): it opens on the whole recording
    and zooms freely. `_sync_raster_to_time` / `_on_raster_view_changed` keep the two in step only while the raster
    shows ≤ the cap. `RasterPlot.set_view` defers while the tab is hidden and nothing redraws off-screen. A row click
    emits (channel, time) and centres Time Series there.

### Conventions
- Channel indices are 0-based internally and shown 1-based. Times are seconds from stream start. Data is in volts.
- Serialize with `to_dict`/`from_dict`, and keep it backward compatible (default any missing field).
- Long jobs run in a `CancellableWorker` started with `MainWindow._run_job(kind, worker, text, on_done, ...)`,
  which owns the progress dialog, `_jobs[kind]`, the stale-result checks and error boxes.
  - Workers check `is_cancelled()` before emitting a result. Closing a `QProgressDialog` emits `canceled`, so check
    cancellation *before* `close()`; `QProgressDialog.cancel()` does **not** emit it (tests click the button).
  - They are **not** submitted to `_worker_manager`, because `cancel_all()` runs on every plot refresh.
  - Report progress with `progress_bar().setValue`, **never `QProgressDialog.setValue`** (it processes events for a
    modal dialog: a result handled inside it can close/delete the dialog under it → crash).
  - Anything that replaces the recording calls `_stop_jobs()` (bumps `_recording_gen`, cancels every job).
- **Every editable setting gets a `HELP` entry** (`ui/help_text.py`, in `SECTIONS`) and `explain(key, field,
  label)`; check the text against the code (defaults, units). `tests/test_help.py` enforces the tooltips.
- **Exit via `os._exit`** (end of `main.main`, `tests/__main__.py`): PySide's teardown at interpreter exit destroys
  Qt objects in no set order; freeing a pyqtgraph item can free a lambda holding the last reference to its own
  plot, which deletes the item again → crash after everything finished (SIGILL macOS, heap corruption Windows;
  `python -m unittest discover` exited 132 after "OK" since long before v1.0). Explicitly deleting pyqtgraph
  widgets (`shiboken6.delete`) crashed too (QGraphicsGridLayout wrapper). Anything that must persist is saved
  before (`QSettings.sync()`); `closeEvent` stops jobs. Run tests with `python -m tests`, never trust "OK" alone.
- Tests delete temp folders with `tests.remove_dir` (Windows can't delete a still-mapped `.nsX`).
- Don't rebuild a widget from inside its own signal (table `itemChanged`, line `sigPositionChangeFinished`); defer with
  `QTimer.singleShot(0, ...)`.
- Write commit messages in the imperative. **No Co-Authored-By or any other Claude attribution** (user rule,
  2026-11-17). Don't push unless the user asks.

---

## 4. Roadmap

### Package and release CortexUI v1.0 (approved 2026-11-18): steps 1–4 done (§1 items 33–36)
Left: PR `release/v1.0` → `main` (CI green on all three), then, with the user's OK at that moment, `git tag v1.0.0`
on `main` and push it; the workflow makes a draft release; the user tries the files and publishes it.

### Audit fixes (approved 2026-11-17): all 11 steps done (§1 items 13, 14, 16–24)
Untested for lack of data: TDT `.sev` stores, a real TDT block, a lab probeinterface file.

### Spike Detection tab: done (§1 item 8); still to come: waveform snippets (overlaid ~2 ms cut-outs)

### Later / unscheduled
- **Probe map takes priority (user request, 2026-10-27):** once probe-map import is fully built out, a loaded probe map
  overrides everything else: electrode numbering, array/shank grouping and positions. It wins over label parsing and
  over channel order.
- A "Plots" tab: the user intends to add it later. Don't build it yet.
- Loading `.nev` spikes/events.
- Blanking is computed for the stream that is current when Apply is pressed. Multi-stream recordings (e.g. ns5 + ns2)
  would need it applied per stream.
- Under CAR the buffer still holds every channel (~233–400 MB). It could hold only the displayed rows plus one
  reference row per group (computed per read piece), at the cost of handling blanking per piece.

### Known issues
- None open. (The Sync drift of a few ms after jumps was the linked-view push-back; fixed in audit step 2.)

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

- **Venv:** `tdtgui/` inside the repo (untracked; the README tells new setups to use `.venv`).
- **Run:** `tdtgui/bin/python main.py`.
- **Requirements:**
  - `requirements.txt` pins PySide6 6.10, pyqtgraph 0.14, neo 0.14.5, scipy, numpy and tdt.
  - `h5py` is installed in the venv but not required.
- **Headless UI checks:** `QT_QPA_PLATFORM=offscreen`, then `w.grab().save(...)` for screenshots.
- **Tools:** `gh` is not installed; the user merges PRs on GitHub.
- **Packaging:** `pyinstaller --clean --noconfirm cortexui.spec`, `packaging/make_dmg.sh`, `packaging/make_icons.py`;
  `QT_QPA_PLATFORM=offscreen dist/CortexUI.app/Contents/MacOS/CortexUI --smoke-test FOLDER`. Minimum macOS 14
  (numpy/scipy wheels). Release files come from the CI only (this Mac's Homebrew Python bundles libs for macOS 26).
- **CI status without signing in:** the anonymous API allows 60 requests/hour (poll ≥ 2 min; `ci_watch`-style
  monitors used it up); annotations are public (`/check-runs/<job>/annotations`); the run's web page
  (`github.com/sidhantm123/CortexUI/actions/runs/<id>`) is not rate-limited.
- **Machine:** 8 GB RAM. Avoid holding whole-file arrays of all channels.
