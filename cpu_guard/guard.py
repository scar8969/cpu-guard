"""One-shot CpuGuard facade: build the whole guarded app in a single call.

Wires the monitor, stats service, all four middlewares (in the recommended
order), and the dashboard app.
"""

from __future__ import annotations

from typing import Callable, Optional

from cpu_guard.config import (
    CpuGuardOptions,
    MemoryGuardOptions,
    RateLimitOptions,
    ThrottlingOptions,
)
from cpu_guard.dashboard import DashboardApp
from cpu_guard.health import HealthCheck
from cpu_guard.middleware import (
    CpuLimitMiddleware,
    GradualThrottlingMiddleware,
    MemoryGuardMiddleware,
    RateLimitMiddleware,
)
from cpu_guard.monitor import ResourceMonitor
from cpu_guard.stats import GuardStatsService


class CpuGuard:
    """Composition root: wraps an ASGI app with all guards + dashboard."""

    def __init__(
        self,
        app: Callable,
        cpu_options: Optional[CpuGuardOptions] = None,
        memory_options: Optional[MemoryGuardOptions] = None,
        throttle_options: Optional[ThrottlingOptions] = None,
        rate_limit_options: Optional[RateLimitOptions] = None,
        sampling_interval: float = 1.0,
        dashboard_base_path: str = "/cpuguard",
        health: Optional[HealthCheck] = None,
        use_psutil: bool = True,
    ):
        cpu_options = cpu_options or CpuGuardOptions()
        memory_options = memory_options or MemoryGuardOptions()
        throttle_options = throttle_options or ThrottlingOptions()
        rate_limit_options = rate_limit_options or RateLimitOptions()

        self.monitor = ResourceMonitor(sampling_interval=sampling_interval, use_psutil=use_psutil)
        self.stats = GuardStatsService(self.monitor)
        self.health = health or HealthCheck(self.monitor)
        self.dashboard = DashboardApp(self.stats, self.health, dashboard_base_path)

        # middleware order matters:
        # 1. rate limit  2. gradual throttle  3. cpu guard  4. memory guard
        self.rate_limit = RateLimitMiddleware(app, rate_limit_options, self.monitor)
        self.throttle = GradualThrottlingMiddleware(self.rate_limit, throttle_options, self.monitor)
        self.cpu_guard = CpuLimitMiddleware(self.throttle, cpu_options, self.monitor)
        self.memory_guard = MemoryGuardMiddleware(self.cpu_guard, memory_options, self.monitor)

        self._dashboard_app = DashboardApp(self.stats, self.health, dashboard_base_path)
        self._base_path = dashboard_base_path.rstrip("/")

    # ------------------------------------------------------------------ #
    def start(self) -> "CpuGuard":
        self.monitor.start()
        return self

    def stop(self) -> None:
        self.monitor.stop()

    def __enter__(self) -> "CpuGuard":
        return self.start()

    def __exit__(self, *exc) -> None:
        self.stop()

    # ------------------------------------------------------------------ #
    async def __call__(self, scope: dict, receive: Callable, send: Callable) -> None:
        path = scope.get("path", "")
        if scope.get("type") == "http" and (path == self._base_path or path.startswith(self._base_path + "/")):
            await self._dashboard_app(scope, receive, send)
            return
        # count every request that reaches the app
        if scope.get("type") == "http":
            self.stats.increment_total_requests()
        await self.memory_guard(scope, receive, send)
