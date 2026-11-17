"""Worker threads for background processing."""
from typing import List, Optional, Tuple
import numpy as np

from PySide6.QtCore import QObject, QRunnable, Signal, Slot, QThreadPool

from models import FilterConfig
from dsp import apply_filter_chain, compute_fft, compute_psd, decimate_for_display


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


class DataLoadWorker(CancellableWorker):
    """
    Worker for loading stream data.

    Emits result as dict with:
        - 'data': np.ndarray (n_channels, n_samples)
        - 'time': np.ndarray
        - 'stream_name': str
        - 'channels': list of channel indices
    """

    def __init__(
        self,
        reader,
        stream_name: str,
        channels: List[int],
        start_time: float,
        end_time: float,
    ):
        super().__init__()
        self.reader = reader
        self.stream_name = stream_name
        self.channels = channels
        self.start_time = start_time
        self.end_time = end_time

    @Slot()
    def run(self):
        """Execute the data loading."""
        self.signals.started.emit()

        try:
            if self.is_cancelled():
                self.signals.cancelled.emit()
                return

            self.signals.progress.emit(10)

            data, time_axis = self.reader.get_stream_data(
                self.stream_name,
                channels=self.channels,
                start_time=self.start_time,
                end_time=self.end_time,
            )

            if self.is_cancelled():
                self.signals.cancelled.emit()
                return

            self.signals.progress.emit(100)

            result = {
                "data": data,
                "time": time_axis,
                "stream_name": self.stream_name,
                "channels": self.channels,
            }

            self.signals.result.emit(result)
            self.signals.finished.emit()

        except Exception as e:
            self.signals.error.emit(str(e))


class FilterWorker(CancellableWorker):
    """
    Worker for applying filter chains.

    Emits result as dict with:
        - 'data': filtered np.ndarray
        - 'time': np.ndarray (unchanged)
        - 'warnings': list of warning strings
        - 'vrms': dict mapping channel index to Vrms value
    """

    def __init__(
        self,
        data: np.ndarray,
        time_axis: np.ndarray,
        filters: List[FilterConfig],
        fs: float,
        global_bypass: bool = False,
        all_channel_data: Optional[np.ndarray] = None,
        channel_indices: Optional[List[int]] = None,
        max_plot_points: int = 8000,
    ):
        super().__init__()
        self.data = data
        self.time_axis = time_axis
        self.filters = filters
        self.fs = fs
        self.global_bypass = global_bypass
        self.all_channel_data = all_channel_data
        self.channel_indices = channel_indices
        self.max_plot_points = max_plot_points

    @Slot()
    def run(self):
        """Execute the filtering and decimation."""
        self.signals.started.emit()

        try:
            if self.is_cancelled():
                self.signals.cancelled.emit()
                return

            self.signals.progress.emit(10)

            filtered_data, warnings, vrms = apply_filter_chain(
                self.data,
                self.filters,
                self.fs,
                self.global_bypass,
                all_channel_data=self.all_channel_data,
                channel_indices=self.channel_indices,
            )

            if self.is_cancelled():
                self.signals.cancelled.emit()
                return

            self.signals.progress.emit(60)

            # Decimate for display
            if filtered_data.ndim == 1:
                n_samples = len(filtered_data)
            else:
                n_samples = filtered_data.shape[1]

            if n_samples > self.max_plot_points:
                plot_data, plot_time = decimate_for_display(
                    filtered_data, self.time_axis,
                    target_points=self.max_plot_points,
                )
            else:
                plot_data = filtered_data
                plot_time = self.time_axis

            if self.is_cancelled():
                self.signals.cancelled.emit()
                return

            self.signals.progress.emit(100)

            result = {
                "data": filtered_data,
                "plot_data": plot_data,
                "plot_time": plot_time,
                "time": self.time_axis,
                "warnings": warnings,
                "vrms": vrms,
            }

            self.signals.result.emit(result)
            self.signals.finished.emit()

        except Exception as e:
            self.signals.error.emit(str(e))


class SpectralWorker(CancellableWorker):
    """
    Worker for spectral analysis (FFT/PSD).

    Emits result as dict with:
        - 'freqs': frequency array
        - 'magnitude': magnitude array (for FFT) or PSD values
        - 'method': 'fft' or 'psd'
        - 'channels': list of channel indices
    """

    def __init__(
        self,
        data: np.ndarray,
        fs: float,
        method: str = "psd",  # 'fft' or 'psd'
        channels: Optional[List[int]] = None,
        nperseg: int = 1024,
        overlap: float = 0.5,
    ):
        super().__init__()
        self.data = data
        self.fs = fs
        self.method = method
        self.channels = channels
        self.nperseg = nperseg
        self.overlap = overlap

    @Slot()
    def run(self):
        """Execute the spectral analysis."""
        self.signals.started.emit()

        try:
            if self.is_cancelled():
                self.signals.cancelled.emit()
                return

            self.signals.progress.emit(10)

            # Select channels
            if self.data.ndim == 1:
                data_to_analyze = self.data
                actual_channels = [0]
            else:
                if self.channels is not None:
                    valid_channels = [
                        c for c in self.channels
                        if 0 <= c < self.data.shape[0]
                    ]
                    if valid_channels:
                        data_to_analyze = self.data[valid_channels, :]
                        actual_channels = valid_channels
                    else:
                        data_to_analyze = self.data
                        actual_channels = list(range(self.data.shape[0]))
                else:
                    data_to_analyze = self.data
                    actual_channels = list(range(self.data.shape[0]))

            if self.is_cancelled():
                self.signals.cancelled.emit()
                return

            self.signals.progress.emit(50)

            if self.method == "fft":
                freqs, magnitude = compute_fft(data_to_analyze, self.fs)
            else:
                freqs, magnitude = compute_psd(
                    data_to_analyze,
                    self.fs,
                    nperseg=self.nperseg,
                    overlap=self.overlap,
                )

            if self.is_cancelled():
                self.signals.cancelled.emit()
                return

            self.signals.progress.emit(100)

            result = {
                "freqs": freqs,
                "magnitude": magnitude,
                "method": self.method,
                "channels": actual_channels,
            }

            self.signals.result.emit(result)
            self.signals.finished.emit()

        except Exception as e:
            self.signals.error.emit(str(e))


class DecimationWorker(CancellableWorker):
    """
    Worker for decimating data for display.

    Emits result as dict with:
        - 'data': decimated np.ndarray
        - 'time': decimated time axis
    """

    def __init__(
        self,
        data: np.ndarray,
        time_axis: np.ndarray,
        target_points: int = 4000,
    ):
        super().__init__()
        self.data = data
        self.time_axis = time_axis
        self.target_points = target_points

    @Slot()
    def run(self):
        """Execute the decimation."""
        self.signals.started.emit()

        try:
            if self.is_cancelled():
                self.signals.cancelled.emit()
                return

            decimated_data, decimated_time = decimate_for_display(
                self.data,
                self.time_axis,
                target_points=self.target_points,
            )

            result = {
                "data": decimated_data,
                "time": decimated_time,
            }

            self.signals.result.emit(result)
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
                chunks = []
                t = 0.0
                while t < duration:
                    if self.is_cancelled():
                        self.signals.cancelled.emit()
                        return
                    t_end = min(duration, t + self.CHUNK_SECONDS)
                    data, _ = self.reader.get_stream_data(
                        stream, channels=channels, start_time=t, end_time=t_end
                    )
                    chunks.append(np.atleast_2d(data).astype(np.float32))
                    done += t_end - t
                    t = t_end
                    self.signals.progress.emit(int(90 * done / total))
                lines = np.concatenate(chunks, axis=1)
                del chunks

                for row, a in enumerate(items):
                    label = self.labels.get((stream, a.channel), f"Ch {a.channel + 1}")
                    onsets, offsets, thr = detect_line_events(lines[row], fs, a.settings)
                    summary.append((label, a.modality, thr, len(onsets)))
                    events += [
                        StimEvent(a.modality, 0, float(on), float(off), stream, a.channel, label)
                        for on, off in zip(onsets, offsets)
                    ]
                del lines

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
            if result["cancelled"]:
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
            sigma = freeze_sigma(
                self.reader, self.stream, self.channels, i0, i1, fs, self.num_channels,
                self.blank_results, self.filters,
            )
            self.signals.progress.emit(3)
            times = detect_spikes(
                self.reader, self.stream, self.channels, 0, self.n_samples, fs, self.num_channels,
                self.settings.k * sigma, self.settings.polarity, self.settings.refractory_ms,
                self.blank_results, self.filters,
                progress=lambda p: self.signals.progress.emit(3 + int(0.97 * p)),
                cancelled=self.is_cancelled,
            )
            if times is None:
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

        # Remove from list when done
        def cleanup():
            if worker in self._active_workers:
                self._active_workers.remove(worker)

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
