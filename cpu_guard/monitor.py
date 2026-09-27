"""Background resource monitor: samples CPU and memory usage on an interval.

Uses psutil when available (accurate process CPU % + total system memory);
falls back to stdlib-only process-time sampling otherwise.
"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass

try:
    import psutil  # type: ignore

    _HAS_PSUTIL = True
except ImportError:  # pragma: no cover
    psutil = None
    _HAS_PSUTIL = False


@dataclass
class ResourceSnapshot:
    """A point-in-time snapshot of resource usage."""

    cpu_usage_percentage: float
    memory_usage_percentage: float
    memory_usage_bytes: int
    total_memory_bytes: int
    average_cpu_usage: float
    peak_cpu_usage: float
    average_memory_usage: float
    peak_memory_usage: float
    requests_throttled: int
    requests_delayed: int
    requests_rate_limited: int
    timestamp: float


class ResourceMonitor:
    """Samples CPU/memory usage in a background thread.

    Thread-safe: all state is mutated under a lock; snapshots are cheap.
    """

    def __init__(self, sampling_interval: float = 1.0, use_psutil: bool = True):
        self.sampling_interval = sampling_interval
        self._use_psutil = use_psutil and _HAS_PSUTIL
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._psutil_proc = psutil.Process() if self._use_psutil else None  # type: ignore

        self._current_cpu = 0.0
        self._current_mem_pct = 0.0
        self._current_mem_bytes = 0
        self._total_mem_bytes = self._detect_total_memory()

        self._avg_cpu = 0.0
        self._peak_cpu = 0.0
        self._avg_mem = 0.0
        self._peak_mem = 0.0
        self._sample_count = 0

        self._throttled = 0
        self._delayed = 0
        self._rate_limited = 0

        self._last_sample_time = time.monotonic()
        self._last_cpu_time = self._process_cpu_time()

    # ------------------------------------------------------------------ #
    # lifecycle
    # ------------------------------------------------------------------ #
    def start(self) -> "ResourceMonitor":
        if self._thread is None or not self._thread.is_alive():
            self._stop.clear()
            self._thread = threading.Thread(target=self._run, name="cpu-guard-monitor", daemon=True)
            self._thread.start()
        return self

    def stop(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=2.0)
            self._thread = None

    def __enter__(self) -> "ResourceMonitor":
        return self.start()

    def __exit__(self, *exc) -> None:
        self.stop()

    # ------------------------------------------------------------------ #
    # sampling loop
    # ------------------------------------------------------------------ #
    def _run(self) -> None:
        while not self._stop.wait(self.sampling_interval):
            self._sample()

    def _sample(self) -> None:
        try:
            now = time.monotonic()
            cpu = self._sample_cpu(now)
            mem_bytes, mem_pct = self._sample_memory()

            with self._lock:
                self._current_cpu = cpu
                self._current_mem_bytes = mem_bytes
                self._current_mem_pct = mem_pct
                self._sample_count += 1
                n = self._sample_count
                self._avg_cpu = ((self._avg_cpu * (n - 1)) + cpu) / n
                self._avg_mem = ((self._avg_mem * (n - 1)) + mem_pct) / n
                if cpu > self._peak_cpu:
                    self._peak_cpu = cpu
                if mem_pct > self._peak_mem:
                    self._peak_mem = mem_pct
        except Exception:
            # sampling must never take the app down
            pass

    def _sample_cpu(self, now: float) -> float:
        if self._use_psutil and self._psutil_proc is not None:
            # cpu_percent needs a persistent Process handle to have a baseline;
            # first call returns 0.0, subsequent calls return real usage.
            return min(100.0, max(0.0, self._psutil_proc.cpu_percent(interval=None)))  # type: ignore
        # stdlib fallback: delta of process CPU time over wall time
        cpu_time = self._process_cpu_time()
        elapsed = now - self._last_sample_time
        usage = 0.0
        if elapsed > 0:
            usage = ((cpu_time - self._last_cpu_time) / elapsed) * 100.0
        self._last_sample_time = now
        self._last_cpu_time = cpu_time
        return min(100.0, max(0.0, usage))

    def _sample_memory(self) -> tuple[int, float]:
        if self._use_psutil and self._psutil_proc is not None:
            mem_bytes = self._psutil_proc.memory_info().rss  # type: ignore
        else:
            mem_bytes = self._process_memory_bytes()
        total = self._total_mem_bytes or 1
        pct = (mem_bytes / total) * 100.0
        return mem_bytes, min(100.0, max(0.0, pct))

    # ------------------------------------------------------------------ #
    # counters (called by middleware)
    # ------------------------------------------------------------------ #
    def increment_throttled(self) -> None:
        with self._lock:
            self._throttled += 1

    def increment_delayed(self) -> None:
        with self._lock:
            self._delayed += 1

    def increment_rate_limited(self) -> None:
        with self._lock:
            self._rate_limited += 1

    # ------------------------------------------------------------------ #
    # reads
    # ------------------------------------------------------------------ #
    @property
    def current_cpu(self) -> float:
        with self._lock:
            return self._current_cpu

    @property
    def current_memory_bytes(self) -> int:
        with self._lock:
            return self._current_mem_bytes

    @property
    def current_memory_percentage(self) -> float:
        with self._lock:
            return self._current_mem_pct

    @property
    def total_memory_bytes(self) -> int:
        return self._total_mem_bytes

    def get_snapshot(self) -> ResourceSnapshot:
        with self._lock:
            return ResourceSnapshot(
                cpu_usage_percentage=self._current_cpu,
                memory_usage_percentage=self._current_mem_pct,
                memory_usage_bytes=self._current_mem_bytes,
                total_memory_bytes=self._total_mem_bytes,
                average_cpu_usage=self._avg_cpu,
                peak_cpu_usage=self._peak_cpu,
                average_memory_usage=self._avg_mem,
                peak_memory_usage=self._peak_mem,
                requests_throttled=self._throttled,
                requests_delayed=self._delayed,
                requests_rate_limited=self._rate_limited,
                timestamp=time.time(),
            )

    # ------------------------------------------------------------------ #
    # platform helpers
    # ------------------------------------------------------------------ #
    @staticmethod
    def _process_cpu_time() -> float:
        try:
            import os

            if os.name == "nt":  # pragma: no cover
                import ctypes

                class FILETIME(ctypes.Structure):
                    _fields_ = [("dwLowDateTime", ctypes.c_uint32), ("dwHighDateTime", ctypes.c_uint32)]

                kernel32 = ctypes.windll.kernel32
                creation = FILETIME()
                exit_t = FILETIME()
                kernel = FILETIME()
                user = FILETIME()
                kernel32.GetProcessTimes(
                    kernel32.GetCurrentProcess(), ctypes.byref(creation), ctypes.byref(exit_t),
                    ctypes.byref(kernel), ctypes.byref(user),
                )
                def _to_sec(ft: FILETIME) -> float:
                    return ((ft.dwHighDateTime << 32) | ft.dwLowDateTime) / 10_000_000.0

                return _to_sec(kernel) + _to_sec(user)
            import resource  # type: ignore

            return resource.getrusage(resource.RUSAGE_SELF).ru_utime + resource.getrusage(resource.RUSAGE_SELF).ru_stime
        except Exception:
            return 0.0

    @staticmethod
    def _process_memory_bytes() -> int:
        try:
            import os

            if os.name == "nt":  # pragma: no cover
                import ctypes

                class PROCESS_MEMORY_COUNTERS(ctypes.Structure):
                    _fields_ = [
                        ("cb", ctypes.c_uint32),
                        ("PageFaultCount", ctypes.c_uint32),
                        ("PeakWorkingSetSize", ctypes.c_size_t),
                        ("WorkingSetSize", ctypes.c_size_t),
                        ("QuotaPeakPagedPoolUsage", ctypes.c_size_t),
                        ("QuotaPagedPoolUsage", ctypes.c_size_t),
                        ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t),
                        ("QuotaNonPagedUsage", ctypes.c_size_t),
                        ("PagefileUsage", ctypes.c_size_t),
                        ("PeakPagefileUsage", ctypes.c_size_t),
                    ]

                ctypes.windll.psapi.GetProcessMemoryInfo(
                    ctypes.windll.kernel32.GetCurrentProcess(),
                    ctypes.byref(PROCESS_MEMORY_COUNTERS()),
                    ctypes.sizeof(PROCESS_MEMORY_COUNTERS),
                )
                return int(PROCESS_MEMORY_COUNTERS().WorkingSetSize)
            with open("/proc/self/statm") as f:  # pragma: no cover
                parts = f.read().split()
                return int(parts[1]) * os.sysconf("SC_PAGE_SIZE")
        except Exception:
            return 0

    @staticmethod
    def _detect_total_memory() -> int:
        try:
            if _HAS_PSUTIL:
                return int(psutil.virtual_memory().total)  # type: ignore
            import os

            if os.name == "nt":  # pragma: no cover
                import ctypes

                class MEMORYSTATUSEX(ctypes.Structure):
                    _fields_ = [
                        ("dwLength", ctypes.c_uint32),
                        ("dwMemoryLoad", ctypes.c_uint32),
                        ("ullTotalPhys", ctypes.c_uint64),
                        ("ullAvailPhys", ctypes.c_uint64),
                        ("ullTotalPageFile", ctypes.c_uint64),
                        ("ullAvailPageFile", ctypes.c_uint64),
                        ("ullTotalVirtual", ctypes.c_uint64),
                        ("ullAvailVirtual", ctypes.c_uint64),
                        ("ullAvailExtendedVirtual", ctypes.c_uint64),
                    ]

                m = MEMORYSTATUSEX()
                m.dwLength = ctypes.sizeof(MEMORYSTATUSEX)
                ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(m))
                return int(m.ullTotalPhys)
            with open("/proc/meminfo") as f:  # pragma: no cover
                for line in f:
                    if line.startswith("MemTotal:"):
                        return int(line.split()[1]) * 1024
        except Exception:
            pass
        return 8 * 1024 * 1024 * 1024  # 8GB fallback
