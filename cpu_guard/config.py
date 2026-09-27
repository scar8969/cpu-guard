"""Configuration options for cpu_guard."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Callable, List, Optional


class ThrottlingMode(Enum):
    """Delay calculation mode for gradual throttling."""

    LINEAR = "linear"
    EXPONENTIAL = "exponential"


class RateLimitMode(Enum):
    """Rate limiting algorithm."""

    FIXED_WINDOW = "fixed_window"
    SLIDING_WINDOW = "sliding_window"
    TOKEN_BUCKET = "token_bucket"


@dataclass
class CpuGuardOptions:
    """Options for CPU limiting middleware."""

    max_cpu_percentage: float = 80.0
    monitoring_interval: float = 1.0  # seconds
    response_status_code: int = 503
    response_message: str = "CPU usage limit exceeded. Try again later."
    response_content_type: str = "text/plain"
    excluded_paths: List[str] = field(default_factory=list)
    # callable(request) -> bool; return True to skip guarding
    exclusion_predicate: Optional[Callable] = None
    # callable(request, cpu_usage) -> response; overrides default response
    custom_response_handler: Optional[Callable] = None
    # callable(event) -> None; fired when CPU limit exceeded
    on_cpu_limit_exceeded: Optional[Callable] = None


@dataclass
class MemoryGuardOptions:
    """Options for memory limiting middleware."""

    max_memory_bytes: int = 500 * 1024 * 1024
    max_memory_percentage: float = 80.0
    use_percentage: bool = True
    response_status_code: int = 503
    response_message: str = "Memory usage limit exceeded. Try again later."
    response_content_type: str = "text/plain"
    excluded_paths: List[str] = field(default_factory=list)
    exclusion_predicate: Optional[Callable] = None
    custom_response_handler: Optional[Callable] = None
    on_memory_limit_exceeded: Optional[Callable] = None


@dataclass
class ThrottlingOptions:
    """Options for gradual throttling middleware."""

    soft_limit_percentage: float = 60.0
    hard_limit_percentage: float = 90.0
    min_delay_ms: float = 100.0
    max_delay_ms: float = 5000.0
    mode: ThrottlingMode = ThrottlingMode.LINEAR
    response_status_code: int = 503
    response_message: str = "Server is overloaded. Try again later."
    response_content_type: str = "text/plain"
    excluded_paths: List[str] = field(default_factory=list)
    exclusion_predicate: Optional[Callable] = None
    custom_response_handler: Optional[Callable] = None
    include_memory: bool = False
    memory_weight: float = 0.3
    on_throttling: Optional[Callable] = None


@dataclass
class RateLimitOptions:
    """Options for rate limiting middleware."""

    requests_per_window: int = 100
    window_seconds: float = 60.0
    mode: RateLimitMode = RateLimitMode.SLIDING_WINDOW
    combine_with_cpu_limit: bool = True
    cpu_threshold_for_stricter_limits: float = 70.0
    high_cpu_rate_limit_factor: float = 0.5
    include_rate_limit_headers: bool = True
    response_status_code: int = 429
    response_message: str = "Rate limit exceeded. Try again later."
    response_content_type: str = "text/plain"
    excluded_paths: List[str] = field(default_factory=list)
    exclusion_predicate: Optional[Callable] = None
    custom_response_handler: Optional[Callable] = None
    # token bucket extras
    tokens_per_second: Optional[float] = None
    bucket_size: Optional[int] = None
    # callable(request) -> client id; default uses remote addr
    client_identifier_factory: Optional[Callable] = None
    on_rate_limit_exceeded: Optional[Callable] = None
