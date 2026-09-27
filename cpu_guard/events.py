"""Guard event dataclasses."""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any


@dataclass
class GuardEvent:
    """Base class for all guard events."""

    request: Any = None  # the WSGI/ASGI request object
    request_path: str = ""
    http_method: str = ""
    timestamp: float = field(default_factory=time.time)


@dataclass
class CpuLimitExceededEvent(GuardEvent):
    cpu_usage_percentage: float = 0.0
    threshold: float = 0.0


@dataclass
class MemoryLimitExceededEvent(GuardEvent):
    memory_usage_bytes: int = 0
    memory_usage_percentage: float = 0.0
    threshold: float = 0.0
    is_percentage_threshold: bool = True


@dataclass
class RateLimitExceededEvent(GuardEvent):
    client_identifier: str = ""
    request_count: int = 0
    rate_limit: int = 0
    reset_in_seconds: float = 0.0


@dataclass
class ThrottlingEvent(GuardEvent):
    resource_usage: float = 0.0
    delay_applied_ms: float = 0.0
    was_rejected: bool = False
    soft_limit: float = 0.0
    hard_limit: float = 0.0
