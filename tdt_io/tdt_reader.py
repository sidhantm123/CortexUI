"""TDT file reading with windowed loading support."""
import warnings
from pathlib import Path
from typing import Dict, List, Optional, Tuple
import numpy as np

from models.recording import StreamInfo, RecordingBlock

try:
    import tdt
    TDT_AVAILABLE = True
except ImportError:
    TDT_AVAILABLE = False


# Kept for backward compatibility; new code should use RecordingBlock
TDTBlock = RecordingBlock

# TSQ data formats (tdt.DFORM_*) -> sample type in the .tev
_DFORM_DTYPES = {0: "<f4", 1: "<i4", 2: "<i2", 3: "i1", 4: "<f8", 5: "<i8"}


def _sample_index(t: float, fs: float) -> int:
    """First sample at or after time t (see blackrock_reader._sample_index)."""
    return int(np.floor(t * fs + 1e-6))


class _TevStream:
    """
    A stream store whose samples are in the block's .tev file, located from the parsed TSQ
    index: every record holds the next `npts` samples of one channel, at a byte offset.
    """

    def __init__(self, tev_path: str, store):
        self.tev_path = tev_path
        self.fs = float(store.fs)
        self.dtype = np.dtype(_DFORM_DTYPES[int(store.dform)])
        self.record_bytes = (int(store.size) - 10) * 4  # size counts 4-byte words, header included
        self.npts = self.record_bytes // self.dtype.itemsize
        offsets = np.asarray(store.data, dtype=np.uint64)
        chan = np.asarray(store.chan, dtype=np.int64)
        if chan.size != offsets.size:  # tdt stores [1] for a one-channel store
            chan = np.ones(offsets.size, dtype=np.int64)
        order = np.argsort(np.asarray(store.ts), kind="stable")  # records in time order
        offsets, chan = offsets[order], chan[order]
        self.num_channels = int(chan.max())
        self.offsets = [offsets[chan == c + 1].astype(np.int64) for c in range(self.num_channels)]
        self.num_samples = min(len(o) for o in self.offsets) * self.npts

    def read(self, i0: int, i1: int, channels: List[int], dtype) -> np.ndarray:
        """Samples [i0, i1) of the given channels, (channels, samples)."""
        out = np.empty((len(channels), i1 - i0), dtype=dtype)
        if i1 <= i0:
            return out
        r0, r1 = i0 // self.npts, -(-i1 // self.npts)
        s0 = i0 - r0 * self.npts
        tev = np.memmap(self.tev_path, dtype=np.uint8, mode="r")
        within = np.arange(self.record_bytes)
        for row, c in enumerate(channels):
            starts = self.offsets[c][r0:r1]
            if starts.size and starts[-1] + self.record_bytes > tev.size:
                raise ValueError("the .tev file is shorter than its index (.tsq) says")
            samples = tev[(starts[:, None] + within).ravel()].view(self.dtype)
            out[row] = samples[s0: s0 + (i1 - i0)]
        del tev
        return out


class TDTReader:
    """
    Reader for TDT neural recording data with windowed loading.

    The block's index (.tsq) is parsed once; stream windows are then read straight from the
    .tev at exact sample indices. Stores kept in .sev files are read through tdt with the
    same parsed headers.
    """

    # Known TDT file extensions
    TDT_EXTENSIONS = {".tdx", ".tev", ".tsq", ".tnt", ".tbk", ".tin", ".sev"}

    def __init__(self):
        self._block_path: Optional[str] = None
        self._block_info: Optional[TDTBlock] = None
        self._headers = None  # parsed .tsq (tdt.read_block(headers=1))
        # Streams are named like tdt's data.streams fields (tdt.fix_var_name of the store name)
        self._tev: Dict[str, _TevStream] = {}  # streams read from the .tev
        self._sev: Dict[str, Tuple[str, float]] = {}  # streams in .sev files -> (store name, fs)

    @staticmethod
    def is_tdt_folder(folder_path: str) -> bool:
        """Check if folder contains TDT data files."""
        if not TDT_AVAILABLE:
            return False

        folder = Path(folder_path)
        if not folder.is_dir():
            return False

        return any(
            f.suffix.lower() in TDTReader.TDT_EXTENSIONS for f in folder.iterdir()
        )

    def load_block(self, folder_path: str, headers_only: bool = True) -> TDTBlock:
        """
        Load a TDT block's index; no sample data is read or kept in memory.

        Args:
            folder_path: Path to TDT block folder
            headers_only: Accepted for interface compatibility; data is never cached.

        Returns:
            TDTBlock with metadata

        Raises:
            ImportError: If tdt package not available
            FileNotFoundError: If folder doesn't exist
            ValueError: If not a valid TDT block
        """
        if not TDT_AVAILABLE:
            raise ImportError(
                "TDT package not installed. Install with: pip install tdt"
            )

        folder = Path(folder_path)
        if not folder.exists():
            raise FileNotFoundError(f"Folder not found: {folder_path}")

        try:
            with warnings.catch_warnings():
                warnings.simplefilter("ignore")  # missing .tnt/.tbk notes etc.
                headers = tdt.read_block(str(folder), headers=1)
                sev_names = set(tdt.read_sev(str(folder), just_names=True) or [])
        except Exception as e:
            raise ValueError(f"Failed to read TDT block: {e}")

        start = float(np.atleast_1d(headers.start_time)[0])
        stop = float(np.atleast_1d(headers.stop_time)[0])
        block_duration = stop - start if np.isfinite(stop) and stop > start else 0.0

        tev: Dict[str, _TevStream] = {}
        sev: Dict[str, Tuple[str, float]] = {}
        streams: Dict[str, StreamInfo] = {}
        for name, store in headers.stores.items():
            if getattr(store, "type_str", "") != "streams" or str(store.name) in sev_names:
                continue
            try:
                s = _TevStream(str(headers.tev_path), store)
            except (AttributeError, KeyError, TypeError, ValueError):
                continue
            if s.num_samples < 1:
                continue
            tev[name] = s
            streams[name] = StreamInfo(
                name=name, num_channels=s.num_channels, sample_rate=s.fs,
                duration=s.num_samples / s.fs, num_samples=s.num_samples, dtype=str(s.dtype),
            )

        # Stores in .sev files (a .tsq entry is optional for them): channel count from a
        # short read; the length is the block's (exact only to the block's stop time)
        for store_name in sorted(sev_names):
            name = tdt.fix_var_name(store_name)
            try:
                with warnings.catch_warnings():
                    warnings.simplefilter("ignore")
                    d = tdt.read_block(str(folder), headers=headers, store=store_name, t2=0.1)
                stream = d.streams[name]
            except Exception:
                continue
            fs = float(stream.fs)
            n_samples = int(round(block_duration * fs))
            if fs <= 0 or n_samples < 1:
                continue
            sev[name] = (store_name, fs)
            streams[name] = StreamInfo(
                name=name, num_channels=int(np.atleast_2d(stream.data).shape[0]), sample_rate=fs,
                duration=n_samples / fs, num_samples=n_samples, dtype=str(np.asarray(stream.data).dtype),
            )

        if not streams:
            raise ValueError(
                "No valid streams found in TDT block. "
                "Ensure the folder contains valid TDT recording data."
            )

        self.close()
        self._block_path = str(folder)
        self._headers = headers
        self._tev, self._sev = tev, sev
        self._block_info = TDTBlock(
            path=str(folder),
            name=folder.name,
            start_time=start,
            stop_time=stop if np.isfinite(stop) else start + block_duration,
            duration=block_duration or max(s.duration for s in streams.values()),
            streams=streams,
        )
        return self._block_info

    def get_stream_data(
        self,
        stream_name: str,
        channels: Optional[List[int]] = None,
        start_time: Optional[float] = None,
        end_time: Optional[float] = None,
        dtype=np.float64,
    ) -> Tuple[np.ndarray, np.ndarray]:
        """
        Get stream data for specified channels and time range.

        Args:
            stream_name: Name of the stream
            channels: List of channel indices (0-based). None = all channels.
            start_time: Start time in seconds. None = beginning.
            end_time: End time in seconds. None = end.
            dtype: float64 (default) or float32 (plot buffers).

        Returns:
            Tuple of (data, time_axis) where:
                data: ndarray of shape (n_channels, n_samples): samples
                      [floor(start * fs), floor(end * fs)), like the Blackrock reader
                time_axis: ndarray of time values in seconds
        """
        if self._block_path is None:
            raise ValueError("No block loaded. Call load_block first.")

        if stream_name not in self._block_info.streams:
            raise ValueError(f"Stream '{stream_name}' not found in block")

        info = self._block_info.streams[stream_name]
        fs = info.sample_rate
        i0 = min(max(0, _sample_index(start_time or 0.0, fs)), info.num_samples)
        i1 = info.num_samples if end_time is None else _sample_index(end_time, fs)
        i1 = min(max(i0, i1), info.num_samples)

        if channels is None:
            valid_channels = list(range(info.num_channels))
        else:
            valid_channels = [c for c in channels if 0 <= c < info.num_channels]
            if not valid_channels:
                raise ValueError(f"No valid channels in selection: {channels}")

        try:
            if stream_name in self._tev:
                data = self._tev[stream_name].read(i0, i1, valid_channels, dtype)
            else:
                data = self._read_sev(stream_name, i0, i1, valid_channels, dtype)
        except Exception as e:
            raise ValueError(f"Failed to read stream data: {e}")

        return data, (i0 + np.arange(data.shape[1])) / fs

    def _read_sev(self, name: str, i0: int, i1: int, channels: List[int], dtype) -> np.ndarray:
        """A .sev store through tdt, which maps t1 to ceil(t1 * fs): ask a quarter sample early."""
        if i1 <= i0:
            return np.empty((len(channels), 0), dtype=dtype)
        store_name, fs = self._sev[name]
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            d = tdt.read_block(self._block_path, headers=self._headers, store=store_name,
                               t1=max(0.0, (i0 - 0.25) / fs), t2=(i1 - 0.25) / fs)
        data = np.atleast_2d(d.streams[name].data)[:, : i1 - i0]
        return data[channels].astype(dtype)

    def get_block_info(self) -> Optional[TDTBlock]:
        """Return current block info."""
        return self._block_info

    def close(self):
        """Forget the block (nothing is held open)."""
        self._block_path = None
        self._block_info = None
        self._headers = None
        self._tev = {}
        self._sev = {}
