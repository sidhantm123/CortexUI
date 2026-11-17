"""Worker threads for background processing."""
import weakref
from typing import List, Optional, Tuple
import numpy as np

from PySide6.QtCore import QObject, QRunnable, Signal, Slot, QThreadPool

from dsp import compute_fft, compute_psd


class WorkerSignals(QObject):
    """Signals for worker threads."""

    started = Signal()
    finished = Signal()
    error = Signal(str)
    progress = Signal(int)  # 0-100
    result = Signal(object)  # Generic result
    cancelled = Signal()


class CancellableWorker(QRunnable):
    """Base class for cancellable workers."""

    def __init__(self):
        super().__init__()
        self.signals = WorkerSignals()
        self._cancelled = False

    def cancel(self):
        """Request cancellation."""
        self._cancelled = True

    def is_cancelled(self) -> bool:
        """Check if cancellation was requested."""
        return self._cancelled


class BufferLoadWorker(CancellableWorker):
    """
    Bring a plot buffer to the sample range `want`.

    Only the parts the previous buffer doesn't hold are read (the file stores every
    channel side by side, so reading costs ~28 ms per second of recording whatever the
    channel count); they are stitched to the overlap of the previous buffer and, when
    `process` is given, blanked and filtered here rather than on the GUI thread.

    Emits result: dict with
        range: (start, stop) samples held; time: float64 seconds per sample
        data: float32 (len(load_channels) x samples) — every loaded row
        processed, mask, warnings: see core.view_buffer.process_buffer (None without `process`)
        tag: passed through unchanged
    """

    # Pieces are read in sub-reads of this length, checking for cancellation in between, so
    # a superseded load stops within ~0.15 s instead of tying up the disk
    READ_STEP_S = 5.0

    def __init__(self, reader, stream: str, load_channels: List[int], want, fs: float,
                 old: Optional[np.ndarray] = None, old_range=None, process: Optional[dict] = None,
                 tag=None):
        """
        Args:
            old, old_range: the previous buffer of the same rows (read only), or None.
            process: None, or dict(rows, channels, num_channels, filters, global_bypass,
                     blank_results, all_channels) — `rows` picks the displayed channels out of
                     the loaded ones; `all_channels` says the loaded rows are every channel
                     (CAR/CMR reference).
        """
        super().__init__()
        self.reader = reader
        self.stream = stream
        self.load_channels = list(load_channels)
        self.want = (int(want[0]), int(want[1]))
        self.fs = fs
        self.old = old
        self.old_range = old_range
        self.process = process
        self.tag = tag

    @Slot()
    def run(self):
        from core.export import _t
        from core.view_buffer import missing_pieces, stitch, process_buffer

        self.signals.started.emit()
        try:
            w0, w1 = self.want
            have = self.old_range if self.old is not None else None
            step = max(1, int(self.READ_STEP_S * self.fs))
            pieces = []
            for p0, p1 in missing_pieces(have, (w0, w1)):
                for a in range(p0, p1, step):
                    b = min(p1, a + step)
                    if self.is_cancelled():
                        self.signals.cancelled.emit()
                        return
                    data, _ = self.reader.get_stream_data(
                        self.stream, channels=self.load_channels,
                        start_time=_t(a, self.fs), end_time=_t(b, self.fs), dtype=np.float32,
                    )
                    data = np.atleast_2d(data)
                    if data.shape[1] > b - a:
                        data = data[:, : b - a]
                    elif data.shape[1] < b - a:
                        if b != w1:  # only the recording's very end may come back short
                            raise ValueError(f"Read {data.shape[1]} samples of [{a}, {b})")
                        b = w1 = a + data.shape[1]
                    pieces.append(((a, b), data))
                    if b == w1 and b < p1:
                        break
            buffer = stitch(self.old, have, pieces, (w0, w1))
            del pieces
            self.old = None  # never keep the previous buffer alive longer than needed
            time_axis = (w0 + np.arange(w1 - w0)) / self.fs

            processed = mask = None
            warnings: List[str] = []
            p, self.process = self.process, None
            if p is not None:
                if self.is_cancelled():
                    self.signals.cancelled.emit()
                    return
                raw = buffer[p["rows"]] if p["all_channels"] else buffer
                processed, mask, warnings = process_buffer(
                    raw, buffer if p["all_channels"] else None, time_axis, p["channels"],
                    p["num_channels"], p["filters"], p["global_bypass"], self.fs, p["blank_results"],
                )

            if self.is_cancelled():
                self.signals.cancelled.emit()
                return
            self.signals.result.emit({
                "range": (w0, w1), "time": time_axis, "data": buffer,
                "processed": processed, "mask": mask, "warnings": warnings, "tag": self.tag,
            })
            self.signals.finished.emit()
        except Exception as e:
            self.signals.error.emit(str(e))
        finally:
            self.old = self.process = None


class SpectralWorker(CancellableWorker):
    """
    FFT or Welch PSD of samples [i_start, i_stop) of some channels, after blanking and the
    filter chain (CAR/CMR included), read in chunks the same way as Export.

    Emits result as dict with:
        - 'freqs': frequency array
        - 'magnitude': (channels x freqs) FFT amplitude or PSD
        - 'method': 'fft' or 'psd'
        - 'channels': list of channel indices
        - 'warnings': filter chain warnings (e.g. a skipped filter)
    """

    def __init__(self, reader, stream: str, channels: List[int], i_start: int, i_stop: int,
                 fs: float, num_channels: int, blank_results=(), filters=None,
                 method: str = "psd", nperseg: int = 1024, overlap: float = 0.5):
        super().__init__()
        self.reader = reader
        self.stream = stream
        self.channels = list(channels)
        self.i_start = i_start
        self.i_stop = i_stop
        self.fs = fs
        self.num_channels = num_channels
        self.blank_results = list(blank_results)
        self.filters = filters
        self.method = method
        self.nperseg = nperseg
        self.overlap = overlap

    @Slot()
    def run(self):
        from core.export import iter_processed_chunks

        self.signals.started.emit()
        try:
            data = np.empty((len(self.channels), self.i_stop - self.i_start))
            warnings: List[str] = []
            for k, n_chunks, a, b, out, _ in iter_processed_chunks(
                self.reader, self.stream, self.channels, self.i_start, self.i_stop, self.fs,
                self.num_channels, self.blank_results, self.filters, warnings=warnings,
            ):
                if self.is_cancelled():
                    self.signals.cancelled.emit()
                    return
                data[:, a - self.i_start: b - self.i_start] = out
                del out  # see iter_processed_chunks
                self.signals.progress.emit(int(80 * (k + 1) / n_chunks))

            # One channel at a time keeps the FFT's temporaries small
            spectra = []
            for i, row in enumerate(data):
                if self.is_cancelled():
                    self.signals.cancelled.emit()
                    return
                if self.method == "fft":
                    freqs, magnitude = compute_fft(row, self.fs)
                else:
                    freqs, magnitude = compute_psd(row, self.fs, nperseg=self.nperseg, overlap=self.overlap)
                spectra.append(magnitude)
                self.signals.progress.emit(80 + int(20 * (i + 1) / len(data)))

            if self.is_cancelled():
                self.signals.cancelled.emit()
                return
            self.signals.result.emit({
                "freqs": freqs,
                "magnitude": np.vstack(spectra),
                "method": self.method,
                "channels": self.channels,
                "warnings": warnings,
            })
            self.signals.finished.emit()

        except Exception as e:
            self.signals.error.emit(str(e))


class EventDetectWorker(CancellableWorker):
    """
    Detect stimulation events on assigned analog channels.

    Each stream's assigned lines are read together, in sequential chunks, so the
    file is scanned once per stream.

    Emits result as dict with:
        - 'events': list of StimEvent (numbered per modality)
        - 'summary': list of (label, modality, threshold in volts, event count)
    """

    CHUNK_SECONDS = 60.0

    def __init__(self, reader, assignments, stream_info: dict, labels: dict):
        """
        Args:
            reader: Recording reader with get_stream_data().
            assignments: List of AnalogAssignment.
            stream_info: stream name -> (sample_rate, duration).
            labels: (stream, channel) -> display label.
        """
        super().__init__()
        self.reader = reader
        self.assignments = list(assignments)
        self.stream_info = stream_info
        self.labels = labels

    @Slot()
    def run(self):
        from dsp.events import detect_line_events
        from models.analog import StimEvent, number_events

        self.signals.started.emit()
        try:
            by_stream = {}
            for a in self.assignments:
                by_stream.setdefault(a.stream, []).append(a)

            total = sum(self.stream_info[s][1] for s in by_stream) or 1.0
            done = 0.0
            events, summary = [], []

            for stream, items in by_stream.items():
                fs, duration = self.stream_info[stream]
                channels = [a.channel for a in items]
                # The lines are filled in place (a chunk list plus its concatenation held each
                # line twice); readers may return a sample less than asked at the very end
                lines = np.empty((len(channels), int(round(duration * fs))), dtype=np.float32)
                n = 0
                t = 0.0
                while t < duration:
                    if self.is_cancelled():
                        self.signals.cancelled.emit()
                        return
                    t_end = min(duration, t + self.CHUNK_SECONDS)
                    data, _ = self.reader.get_stream_data(
                        stream, channels=channels, start_time=t, end_time=t_end, dtype=np.float32
                    )
                    data = np.atleast_2d(data)[:, : lines.shape[1] - n]
                    lines[:, n: n + data.shape[1]] = data
                    n += data.shape[1]
                    del data
                    done += t_end - t
                    t = t_end
                    self.signals.progress.emit(int(90 * done / total))
                lines = lines[:, :n]

                for row, a in enumerate(items):
                    if self.is_cancelled():
                        self.signals.cancelled.emit()
                        return
                    label = self.labels.get((stream, a.channel), f"Ch {a.channel + 1}")
                    onsets, offsets, thr = detect_line_events(lines[row], fs, a.settings)
                    summary.append((label, a.modality, thr, len(onsets)))
                    events += [
                        StimEvent(a.modality, 0, float(on), float(off), stream, a.channel, label)
                        for on, off in zip(onsets, offsets)
                    ]
                del lines

            if self.is_cancelled():
                self.signals.cancelled.emit()
                return
            self.signals.progress.emit(100)
            self.signals.result.emit({"events": number_events(events), "summary": summary})
            self.signals.finished.emit()

        except Exception as e:
            self.signals.error.emit(str(e))


class BlankingWorker(CancellableWorker):
    """
    Compute adaptive blank intervals for one modality.

    Each channel's baseline is the median spike-band RMS (window_ms windows) over clean
    one-second windows far from any event. After each event's offset the blank extends
    until n_windows consecutive windows are below multiplier x baseline.

    Emits result: BlankResult.
    """

    MARGIN_S = 0.1  # extra data read on each side and discarded (filter edge effects)

    def __init__(self, reader, stream: str, fs: float, duration: float, channels,
                 modality: str, events, all_events, settings):
        """
        Args:
            channels: neural channel indices to blank.
            events: this modality's StimEvents.
            all_events: every StimEvent (clean windows avoid all of them).
            settings: BlankingSettings (mode must be advanced).
        """
        super().__init__()
        self.reader = reader
        self.stream = stream
        self.fs = fs
        self.duration = duration
        self.channels = list(channels)
        self.modality = modality
        self.events = [e for e in events if e.number not in settings.excluded_trials]
        self.all_events = list(all_events)
        self.settings = settings

    def _read(self, t0: float, t1: float) -> np.ndarray:
        data, _ = self.reader.get_stream_data(
            self.stream, channels=self.channels, start_time=t0, end_time=t1
        )
        return np.atleast_2d(data)

    def _spike_rms(self, t0: float, t1: float, window_n: int) -> np.ndarray:
        """Spike-band window RMS of [t0, t1), reading a margin on each side."""
        from dsp.blanking import spike_band, window_rms

        r0 = max(0.0, t0 - self.MARGIN_S)
        r1 = min(self.duration, t1 + self.MARGIN_S)
        data = spike_band(self._read(r0, r1), self.fs)
        i0 = int(round((t0 - r0) * self.fs))
        i1 = i0 + int(round((t1 - t0) * self.fs))
        return window_rms(data[:, i0:i1], window_n)

    @Slot()
    def run(self):
        from dsp.blanking import clean_windows, adaptive_end_windows
        from models.blanking import BlankResult

        self.signals.started.emit()
        try:
            st = self.settings
            w_s = st.window_ms * 1e-3
            window_n = max(1, int(round(w_s * self.fs)))
            min_win = int(np.ceil(st.min_post_ms * 1e-3 / w_s - 1e-9))
            max_win = int(np.floor(st.max_post_ms * 1e-3 / w_s + 1e-9))
            n_steps = 20 + len(self.events)

            # Baseline: median window RMS per channel over clean stretches
            starts = clean_windows(
                [(e.onset, e.offset) for e in self.all_events], self.duration
            )
            if not starts:
                raise ValueError(
                    "No stretch of the recording is at least 3 s away from every event, "
                    "so there is no clean data to measure the baseline."
                )
            rms = []
            for i, t in enumerate(starts):
                if self.is_cancelled():
                    self.signals.cancelled.emit()
                    return
                rms.append(self._spike_rms(t, t + 1.0, window_n))
                self.signals.progress.emit(int(100 * (i + 1) / n_steps))
            baseline = np.median(np.concatenate(rms, axis=1), axis=1)

            n_c = len(self.channels)
            start_s = np.zeros((len(self.events), n_c))
            end_s = np.zeros((len(self.events), n_c))
            n_capped = 0
            n_total = max_win + max(1, st.n_windows)
            for i, e in enumerate(self.events):
                if self.is_cancelled():
                    self.signals.cancelled.emit()
                    return
                t1 = min(self.duration, e.offset + n_total * w_s)
                below = self._spike_rms(e.offset, t1, window_n) < st.multiplier * baseline[:, None]
                k, capped = adaptive_end_windows(below, st.n_windows, min_win, max_win)
                post = np.clip(k * w_s, st.min_post_ms * 1e-3, st.max_post_ms * 1e-3)
                post[capped] = st.max_post_ms * 1e-3
                start_s[i] = max(0.0, e.onset - st.adv_pre_ms * 1e-3)
                end_s[i] = np.minimum(e.offset + post, self.duration)
                n_capped += int(capped.sum())
                self.signals.progress.emit(int(100 * (20 + i + 1) / n_steps))

            if self.is_cancelled():
                self.signals.cancelled.emit()
                return
            self.signals.result.emit(BlankResult(
                modality=self.modality,
                stream=self.stream,
                channels=self.channels,
                trials=[e.number for e in self.events],
                start_s=start_s,
                end_s=end_s,
                interpolation=st.interpolation,
                mode=st.mode,
                n_capped=n_capped,
                median_post_s=float(np.median(
                    end_s - np.array([e.offset for e in self.events])[:, None]
                )),
            ))
            self.signals.finished.emit()

        except Exception as e:
            self.signals.error.emit(str(e))


class ExportWorker(CancellableWorker):
    """
    Chunked int16 binary export (see core.export.export_binary).

    Emits result: dict with n_samples, n_clipped, cancelled.
    """

    def __init__(self, **kwargs):
        super().__init__()
        self.kwargs = kwargs

    @Slot()
    def run(self):
        from core.export import export_binary

        self.signals.started.emit()
        try:
            result = export_binary(
                progress=self.signals.progress.emit,
                cancelled=self.is_cancelled,
                **self.kwargs,
            )
            if result["cancelled"]:  # export_binary has deleted the partial file
                self.signals.cancelled.emit()
                return
            self.signals.result.emit(result)
            self.signals.finished.emit()
        except Exception as e:
            self.signals.error.emit(str(e))


class SpikeDetectWorker(CancellableWorker):
    """
    Freeze sigma per channel from one window, then detect threshold crossings over the
    whole recording (see core.spike_detect).

    Emits result: SpikeResult.
    """

    def __init__(self, reader, stream: str, channels, fs: float, num_channels: int,
                 n_samples: int, sigma_window, settings, blank_results=(), filters=None,
                 processing: str = ""):
        super().__init__()
        self.reader = reader
        self.stream = stream
        self.channels = list(channels)
        self.fs = fs
        self.num_channels = num_channels
        self.n_samples = n_samples
        self.sigma_window = sigma_window
        self.settings = settings
        self.blank_results = list(blank_results)
        self.filters = filters
        self.processing = processing

    @Slot()
    def run(self):
        from core.spike_detect import freeze_sigma, detect_spikes
        from models.spikes import SpikeResult

        self.signals.started.emit()
        try:
            fs = self.fs
            t0, t1 = self.sigma_window
            i0 = max(0, int(round(t0 * fs)))
            i1 = min(self.n_samples, max(i0 + 1, int(round(t1 * fs))))
            if i1 - i0 < 2:
                raise ValueError(
                    f"The view σ is frozen from ({t0:.2f}–{t1:.2f} s) holds no data; show a real time range first"
                )
            warnings: List[str] = []
            sigma = freeze_sigma(
                self.reader, self.stream, self.channels, i0, i1, fs, self.num_channels,
                self.blank_results, self.filters,
                progress=lambda p: self.signals.progress.emit(int(0.1 * p)),
                cancelled=self.is_cancelled, warnings=warnings,
            )
            if sigma is None or self.is_cancelled():
                self.signals.cancelled.emit()
                return
            times = detect_spikes(
                self.reader, self.stream, self.channels, 0, self.n_samples, fs, self.num_channels,
                self.settings.k * sigma, self.settings.polarity, self.settings.refractory_ms,
                self.blank_results, self.filters,
                progress=lambda p: self.signals.progress.emit(10 + int(0.9 * p)),
                cancelled=self.is_cancelled, warnings=warnings,
            )
            if times is None or self.is_cancelled():
                self.signals.cancelled.emit()
                return
            self.signals.result.emit(SpikeResult(
                stream=self.stream,
                fs=fs,
                channels=self.channels,
                times=times,
                sigma={c: float(s) for c, s in zip(self.channels, sigma)},
                k=self.settings.k,
                polarity=self.settings.polarity,
                refractory_ms=self.settings.refractory_ms,
                sigma_window=(i0 / fs, i1 / fs),
                processing=self.processing,
                duration=self.n_samples / fs,
                warnings=warnings,
            ))
            self.signals.finished.emit()
        except Exception as e:
            self.signals.error.emit(str(e))


class WorkerManager:
    """Manages worker threads and provides cancellation."""

    def __init__(self, max_threads: int = 4):
        self.thread_pool = QThreadPool.globalInstance()
        self.thread_pool.setMaxThreadCount(max_threads)
        self._active_workers: List[CancellableWorker] = []

    def submit(self, worker: CancellableWorker):
        """Submit a worker for execution."""
        self._active_workers.append(worker)

        # Remove from list when done. The closure must not hold the worker itself: it is
        # stored in a Qt connection on worker.signals, a cycle Python's GC can't see, which
        # kept every finished worker (and the buffers it referenced) alive.
        ref = weakref.ref(worker)

        def cleanup():
            w = ref()
            if w is not None and w in self._active_workers:
                self._active_workers.remove(w)

        worker.signals.finished.connect(cleanup)
        worker.signals.error.connect(cleanup)
        worker.signals.cancelled.connect(cleanup)

        self.thread_pool.start(worker)

    def cancel_all(self):
        """Cancel all active workers."""
        for worker in self._active_workers:
            worker.cancel()

    def wait_all(self, timeout_ms: int = 5000) -> bool:
        """Wait for all workers to complete."""
        return self.thread_pool.waitForDone(timeout_ms)
