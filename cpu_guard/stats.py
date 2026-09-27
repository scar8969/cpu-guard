"""Guard statistics service: history ring buffers + request counters."""

from __future__ import annotations

import threading
import time
from collections import deque
from typing import Deque, Dict, List, Optional

from cpu_guard.monitor import ResourceMonitor, ResourceSnapshot


class GuardStatsService:
    """Collects request counters and a rolling history of CPU/memory samples."""

    def __init__(self, monitor: ResourceMonitor, max_history_size: int = 120):
        self._monitor = monitor
        self._max_history = max_history_size
        self._lock = threading.Lock()
        self._cpu_history: Deque[Dict[str, float]] = deque(maxlen=max_history_size)
        self._mem_history: Deque[Dict[str, float]] = deque(maxlen=max_history_size)
        self._total_requests = 0
        self._start_time = time.time()

    def record_data_point(self) -> None:
        now = time.time()
        with self._lock:
            self._cpu_history.append({"timestamp": now, "value": self._monitor.current_cpu})
            self._mem_history.append({"timestamp": now, "value": self._monitor.current_memory_percentage})

    def increment_total_requests(self) -> None:
        with self._lock:
            self._total_requests += 1

    @property
    def total_requests(self) -> int:
        with self._lock:
            return self._total_requests

    @property
    def uptime_seconds(self) -> float:
        return time.time() - self._start_time

    def get_summary(self) -> dict:
        snap: ResourceSnapshot = self._monitor.get_snapshot()
        return {
            "currentCpuUsage": round(snap.cpu_usage_percentage, 2),
            "currentMemoryUsage": round(snap.memory_usage_percentage, 2),
            "currentMemoryBytes": snap.memory_usage_bytes,
            "totalMemoryBytes": snap.total_memory_bytes,
            "averageCpuUsage": round(snap.average_cpu_usage, 2),
            "peakCpuUsage": round(snap.peak_cpu_usage, 2),
            "averageMemoryUsage": round(snap.average_memory_usage, 2),
            "peakMemoryUsage": round(snap.peak_memory_usage, 2),
            "totalRequestsThrottled": snap.requests_throttled,
            "totalRequestsDelayed": snap.requests_delayed,
            "totalRequestsRateLimited": snap.requests_rate_limited,
            "totalRequests": self.total_requests,
            "uptimeSeconds": round(self.uptime_seconds, 2),
            "lastUpdated": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        }

    def get_full_stats(self) -> dict:
        summary = self.get_summary()
        with self._lock:
            summary["cpuHistory"] = list(self._cpu_history)
            summary["memoryHistory"] = list(self._mem_history)
        return summary

    def get_prometheus_metrics(self) -> str:
        """Zero-dependency Prometheus text exposition."""
        snap: ResourceSnapshot = self._monitor.get_snapshot()
        lines = [
            "# HELP cpuguard_requests_throttled_total Requests throttled due to limits",
            "# TYPE cpuguard_requests_throttled_total counter",
            f"cpuguard_requests_throttled_total {snap.requests_throttled}",
            "# HELP cpuguard_requests_delayed_total Requests delayed by throttling",
            "# TYPE cpuguard_requests_delayed_total counter",
            f"cpuguard_requests_delayed_total {snap.requests_delayed}",
            "# HELP cpuguard_requests_ratelimited_total Requests rejected by rate limiting",
            "# TYPE cpuguard_requests_ratelimited_total counter",
            f"cpuguard_requests_ratelimited_total {snap.requests_rate_limited}",
            "# HELP cpuguard_requests_total Total requests seen",
            "# TYPE cpuguard_requests_total counter",
            f"cpuguard_requests_total {self.total_requests}",
            "# HELP cpuguard_cpu_usage_percent Current CPU usage percentage",
            "# TYPE cpuguard_cpu_usage_percent gauge",
            f"cpuguard_cpu_usage_percent {snap.cpu_usage_percentage:.2f}",
            "# HELP cpuguard_memory_usage_percent Current memory usage percentage",
            "# TYPE cpuguard_memory_usage_percent gauge",
            f"cpuguard_memory_usage_percent {snap.memory_usage_percentage:.2f}",
            "# HELP cpuguard_memory_usage_bytes Current RSS in bytes",
            "# TYPE cpuguard_memory_usage_bytes gauge",
            f"cpuguard_memory_usage_bytes {snap.memory_usage_bytes}",
            "# HELP cpuguard_uptime_seconds Process uptime",
            "# TYPE cpuguard_uptime_seconds gauge",
            f"cpuguard_uptime_seconds {self.uptime_seconds:.2f}",
        ]
        return "\n".join(lines) + "\n"
