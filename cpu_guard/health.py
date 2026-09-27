"""Health check: CPU/memory thresholds -> healthy / degraded / unhealthy."""

from __future__ import annotations

from dataclasses import dataclass

from cpu_guard.monitor import ResourceMonitor


@dataclass
class HealthStatus:
    status: str  # "healthy" | "degraded" | "unhealthy"
    checks: dict


class HealthCheck:
    """Aggregates CPU and memory health based on thresholds."""

    def __init__(
        self,
        monitor: ResourceMonitor,
        cpu_degraded: float = 70.0,
        cpu_unhealthy: float = 90.0,
        mem_degraded: float = 70.0,
        mem_unhealthy: float = 90.0,
    ):
        self._monitor = monitor
        self.cpu_degraded = cpu_degraded
        self.cpu_unhealthy = cpu_unhealthy
        self.mem_degraded = mem_degraded
        self.mem_unhealthy = mem_unhealthy

    def check(self) -> HealthStatus:
        cpu = self._monitor.current_cpu
        mem = self._monitor.current_memory_percentage

        cpu_status = "healthy"
        if cpu >= self.cpu_unhealthy:
            cpu_status = "unhealthy"
        elif cpu >= self.cpu_degraded:
            cpu_status = "degraded"

        mem_status = "healthy"
        if mem >= self.mem_unhealthy:
            mem_status = "unhealthy"
        elif mem >= self.mem_degraded:
            mem_status = "degraded"

        overall = "healthy"
        if "unhealthy" in (cpu_status, mem_status):
            overall = "unhealthy"
        elif "degraded" in (cpu_status, mem_status):
            overall = "degraded"

        return HealthStatus(
            status=overall,
            checks={
                "cpu": {"status": cpu_status, "usage_percent": round(cpu, 2), "degraded_threshold": self.cpu_degraded, "unhealthy_threshold": self.cpu_unhealthy},
                "memory": {"status": mem_status, "usage_percent": round(mem, 2), "degraded_threshold": self.mem_degraded, "unhealthy_threshold": self.mem_unhealthy},
            },
        )

    def as_json(self) -> dict:
        hs = self.check()
        return {"status": hs.status, "checks": hs.checks}
