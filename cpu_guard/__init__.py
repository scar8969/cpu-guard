"""cpu_guard - resource management middleware for Python web apps.

Protect your application from overload with CPU limiting, memory limiting,
gradual throttling, rate limiting, health checks, Prometheus metrics, and a
real-time dashboard.
"""

from cpu_guard.config import (
    CpuGuardOptions,
    MemoryGuardOptions,
    ThrottlingOptions,
    RateLimitOptions,
    ThrottlingMode,
    RateLimitMode,
)
from cpu_guard.monitor import ResourceMonitor, ResourceSnapshot
from cpu_guard.stats import GuardStatsService
from cpu_guard.middleware import (
    CpuLimitMiddleware,
    MemoryGuardMiddleware,
    GradualThrottlingMiddleware,
    RateLimitMiddleware,
)
from cpu_guard.dashboard import DashboardApp
from cpu_guard.health import HealthCheck
from cpu_guard.events import (
    GuardEvent,
    CpuLimitExceededEvent,
    MemoryLimitExceededEvent,
    RateLimitExceededEvent,
    ThrottlingEvent,
)
from cpu_guard.guard import CpuGuard

__version__ = "1.0.0"
__all__ = [
    "CpuGuardOptions",
    "MemoryGuardOptions",
    "ThrottlingOptions",
    "RateLimitOptions",
    "ThrottlingMode",
    "RateLimitMode",
    "ResourceMonitor",
    "ResourceSnapshot",
    "GuardStatsService",
    "CpuLimitMiddleware",
    "MemoryGuardMiddleware",
    "GradualThrottlingMiddleware",
    "RateLimitMiddleware",
    "DashboardApp",
    "HealthCheck",
    "GuardEvent",
    "CpuLimitExceededEvent",
    "MemoryLimitExceededEvent",
    "RateLimitExceededEvent",
    "ThrottlingEvent",
    "CpuGuard",
]
