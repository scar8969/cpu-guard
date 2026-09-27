"""ASGI middlewares: CPU limit, memory limit, gradual throttling, rate limiting.

Written as pure ASGI so they work with FastAPI, Starlette, Quart, Litestar —
any ASGI framework.
"""

from __future__ import annotations

import asyncio
import json
import time
from collections import defaultdict, deque
from typing import Any, Callable, Deque, Dict, List, Optional, Tuple

from cpu_guard.config import (
    CpuGuardOptions,
    MemoryGuardOptions,
    RateLimitMode,
    RateLimitOptions,
    ThrottlingMode,
    ThrottlingOptions,
)
from cpu_guard.events import (
    CpuLimitExceededEvent,
    MemoryLimitExceededEvent,
    RateLimitExceededEvent,
    ThrottlingEvent,
)
from cpu_guard.monitor import ResourceMonitor
from cpu_guard.stats import GuardStatsService

# --------------------------------------------------------------------------- #
# helpers
# --------------------------------------------------------------------------- #


def _path_of(scope: dict) -> str:
    return scope.get("path", "") or scope.get("root_path", "") or ""


def _method_of(scope: dict) -> str:
    return scope.get("method", "")


def _client_id_of(scope: dict) -> str:
    client = scope.get("client")
    if client:
        return client[0] or "unknown"
    return "unknown"


def _is_excluded(scope: dict, excluded_paths: List[str], exclusion_predicate: Optional[Callable]) -> bool:
    path = _path_of(scope)
    for excluded in excluded_paths:
        if path == excluded or path.startswith(excluded):
            return True
    if exclusion_predicate is not None:
        try:
            if exclusion_predicate(scope):
                return True
        except Exception:
            pass
    return False


async def _send_text_response(send: Callable, status: int, body: str, content_type: str = "text/plain", headers: Optional[List[Tuple[bytes, bytes]]] = None) -> None:
    hdrs = [(b"content-type", content_type.encode("latin-1")), (b"cache-control", b"no-store")]
    if headers:
        hdrs.extend(headers)
    await send({"type": "http.response.start", "status": status, "headers": hdrs})
    await send({"type": "http.response.body", "body": body.encode("utf-8")})


def _request_meta(scope: dict) -> dict:
    return {"request": scope, "request_path": _path_of(scope), "http_method": _method_of(scope)}


# --------------------------------------------------------------------------- #
# CPU limit middleware
# --------------------------------------------------------------------------- #


class CpuLimitMiddleware:
    """Rejects requests when process CPU usage exceeds the configured threshold."""

    def __init__(self, app: Callable, options: CpuGuardOptions, monitor: ResourceMonitor):
        self.app = app
        self.options = options
        self.monitor = monitor

    async def __call__(self, scope: dict, receive: Callable, send: Callable) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        if _is_excluded(scope, self.options.excluded_paths, self.options.exclusion_predicate):
            await self.app(scope, receive, send)
            return

        cpu_usage = self.monitor.current_cpu
        if cpu_usage > self.options.max_cpu_percentage:
            self.monitor.increment_throttled()
            if self.options.on_cpu_limit_exceeded:
                try:
                    self.options.on_cpu_limit_exceeded(
                        CpuLimitExceededEvent(
                            cpu_usage_percentage=cpu_usage,
                            threshold=self.options.max_cpu_percentage,
                            **_request_meta(scope),
                        )
                    )
                except Exception:
                    pass
            await self._respond(scope, send, cpu_usage)
            return

        await self.app(scope, receive, send)

    async def _respond(self, scope: dict, send: Callable, cpu_usage: float) -> None:
        if self.options.custom_response_handler is not None:
            try:
                response = self.options.custom_response_handler(scope, cpu_usage)
                if asyncio.iscoroutine(response):
                    response = await response
                if response is not None:
                    status, body, ctype = response
                    await _send_text_response(send, status, body, ctype)
                    return
            except Exception:
                pass
        await _send_text_response(send, self.options.response_status_code, self.options.response_message, self.options.response_content_type)


# --------------------------------------------------------------------------- #
# memory limit middleware
# --------------------------------------------------------------------------- #


class MemoryGuardMiddleware:
    """Rejects requests when process memory usage exceeds the configured threshold."""

    def __init__(self, app: Callable, options: MemoryGuardOptions, monitor: ResourceMonitor):
        self.app = app
        self.options = options
        self.monitor = monitor

    async def __call__(self, scope: dict, receive: Callable, send: Callable) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        if _is_excluded(scope, self.options.excluded_paths, self.options.exclusion_predicate):
            await self.app(scope, receive, send)
            return

        if self.options.use_percentage:
            value = self.monitor.current_memory_percentage
            threshold = self.options.max_memory_percentage
        else:
            value = float(self.monitor.current_memory_bytes)
            threshold = float(self.options.max_memory_bytes)

        if value > threshold:
            self.monitor.increment_throttled()
            if self.options.on_memory_limit_exceeded:
                try:
                    self.options.on_memory_limit_exceeded(
                        MemoryLimitExceededEvent(
                            memory_usage_bytes=self.monitor.current_memory_bytes,
                            memory_usage_percentage=self.monitor.current_memory_percentage,
                            threshold=threshold,
                            is_percentage_threshold=self.options.use_percentage,
                            **_request_meta(scope),
                        )
                    )
                except Exception:
                    pass
            await self._respond(scope, send, value)
            return

        await self.app(scope, receive, send)

    async def _respond(self, scope: dict, send: Callable, value: float) -> None:
        if self.options.custom_response_handler is not None:
            try:
                response = self.options.custom_response_handler(scope, value)
                if asyncio.iscoroutine(response):
                    response = await response
                if response is not None:
                    status, body, ctype = response
                    await _send_text_response(send, status, body, ctype)
                    return
            except Exception:
                pass
        await _send_text_response(send, self.options.response_status_code, self.options.response_message, self.options.response_content_type)


# --------------------------------------------------------------------------- #
# gradual throttling middleware
# --------------------------------------------------------------------------- #


class GradualThrottlingMiddleware:
    """Progressively delays requests as resource usage rises between soft/hard limits.

    Between soft and hard limit the request is delayed (linear or exponential curve);
    at or beyond the hard limit it is rejected outright.
    """

    def __init__(self, app: Callable, options: ThrottlingOptions, monitor: ResourceMonitor):
        self.app = app
        self.options = options
        self.monitor = monitor

    async def __call__(self, scope: dict, receive: Callable, send: Callable) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        if _is_excluded(scope, self.options.excluded_paths, self.options.exclusion_predicate):
            await self.app(scope, receive, send)
            return

        resource_usage = self._get_resource_usage()

        # hard limit: reject
        if resource_usage >= self.options.hard_limit_percentage:
            self.monitor.increment_throttled()
            self._raise_event(scope, resource_usage, 0.0, True)
            if self.options.custom_response_handler is not None:
                try:
                    response = self.options.custom_response_handler(scope, resource_usage)
                    if asyncio.iscoroutine(response):
                        response = await response
                    if response is not None:
                        status, body, ctype = response
                        await _send_text_response(send, status, body, ctype)
                        return
                except Exception:
                    pass
            await _send_text_response(send, self.options.response_status_code, self.options.response_message, self.options.response_content_type)
            return

        # soft limit: delay
        if resource_usage >= self.options.soft_limit_percentage:
            delay_ms = self._calculate_delay(resource_usage)
            if delay_ms > 0:
                self.monitor.increment_delayed()
                self._raise_event(scope, resource_usage, delay_ms, False)
                await asyncio.sleep(delay_ms / 1000.0)

        await self.app(scope, receive, send)

    def _get_resource_usage(self) -> float:
        cpu = self.monitor.current_cpu
        if not self.options.include_memory:
            return cpu
        mem = self.monitor.current_memory_percentage
        cpu_weight = 1.0 - self.options.memory_weight
        return (cpu * cpu_weight) + (mem * self.options.memory_weight)

    def _calculate_delay(self, resource_usage: float) -> float:
        span = self.options.hard_limit_percentage - self.options.soft_limit_percentage
        if span <= 0:
            return 0.0
        position = (resource_usage - self.options.soft_limit_percentage) / span
        position = max(0.0, min(1.0, position))
        min_d, max_d = self.options.min_delay_ms, self.options.max_delay_ms
        if self.options.mode == ThrottlingMode.EXPONENTIAL:
            return min_d + (position ** 2) * (max_d - min_d)
        return min_d + position * (max_d - min_d)

    def _raise_event(self, scope: dict, usage: float, delay_ms: float, rejected: bool) -> None:
        if self.options.on_throttling is None:
            return
        try:
            self.options.on_throttling(
                ThrottlingEvent(
                    resource_usage=usage,
                    delay_applied_ms=delay_ms,
                    was_rejected=rejected,
                    soft_limit=self.options.soft_limit_percentage,
                    hard_limit=self.options.hard_limit_percentage,
                    **_request_meta(scope),
                )
            )
        except Exception:
            pass


# --------------------------------------------------------------------------- #
# rate limit middleware
# --------------------------------------------------------------------------- #


class _ClientRateLimitInfo:
    __slots__ = ("window_start", "request_count", "timestamps", "tokens", "last_token_update", "last_access")

    def __init__(self, now: float):
        self.window_start = now
        self.request_count = 0
        self.timestamps: Deque[float] = deque()
        self.tokens = 0.0
        self.last_token_update = now
        self.last_access = now

class RateLimitMiddleware:
    """Per-client rate limiting with three algorithms + CPU-aware stricter limits."""

    def __init__(
        self,
        app: Callable,
        options: RateLimitOptions,
        monitor: Optional[ResourceMonitor] = None,
    ):
        self.app = app
        self.options = options
        self.monitor = monitor
        self._clients: Dict[str, _ClientRateLimitInfo] = {}
        self._lock = asyncio.Lock()

    async def __call__(self, scope: dict, receive: Callable, send: Callable) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        if _is_excluded(scope, self.options.excluded_paths, self.options.exclusion_predicate):
            await self.app(scope, receive, send)
            return

        client_id = self._get_client_id(scope)
        now = time.time()
        effective_limit = self._get_effective_limit()

        async with self._lock:
            info = self._clients.get(client_id)
            if info is None:
                # token bucket starts full so the first request is allowed
                info = _ClientRateLimitInfo(now)
                if self.options.mode == RateLimitMode.TOKEN_BUCKET:
                    info.tokens = float(self.options.bucket_size or effective_limit)
                self._clients[client_id] = info
            info.last_access = now
            allowed, request_count, reset_in = self._check(info, now, effective_limit)

        self._cleanup(now)

        # rate limit headers
        if self.options.include_rate_limit_headers:
            headers = [
                (b"x-ratelimit-limit", str(effective_limit).encode()),
                (b"x-ratelimit-remaining", str(max(0, effective_limit - request_count)).encode()),
                (b"x-ratelimit-reset", str(int(reset_in)).encode()),
            ]
        else:
            headers = None

        if not allowed:
            self.monitor.increment_rate_limited() if self.monitor else None
            if self.options.on_rate_limit_exceeded:
                try:
                    self.options.on_rate_limit_exceeded(
                        RateLimitExceededEvent(
                            client_identifier=client_id,
                            request_count=request_count,
                            rate_limit=effective_limit,
                            reset_in_seconds=reset_in,
                            **_request_meta(scope),
                        )
                    )
                except Exception:
                    pass
            if self.options.custom_response_handler is not None:
                try:
                    response = self.options.custom_response_handler(scope, effective_limit - request_count, reset_in)
                    if asyncio.iscoroutine(response):
                        response = await response
                    if response is not None:
                        status, body, ctype = response
                        await _send_text_response(send, status, body, ctype, headers)
                        return
                except Exception:
                    pass
            retry_headers = (headers or []) + [(b"retry-after", str(int(reset_in)).encode())]
            await _send_text_response(send, self.options.response_status_code, self.options.response_message, self.options.response_content_type, retry_headers)
            return

        await self.app(scope, receive, send)

    # ------------------------------------------------------------------ #
    def _get_client_id(self, scope: dict) -> str:
        if self.options.client_identifier_factory is not None:
            try:
                return str(self.options.client_identifier_factory(scope))
            except Exception:
                pass
        return _client_id_of(scope)

    def _get_effective_limit(self) -> int:
        base = self.options.requests_per_window
        if not self.options.combine_with_cpu_limit or self.monitor is None:
            return base
        if self.monitor.current_cpu >= self.options.cpu_threshold_for_stricter_limits:
            return max(1, int(base * self.options.high_cpu_rate_limit_factor))
        return base

    def _check(self, info: _ClientRateLimitInfo, now: float, limit: int) -> Tuple[bool, int, float]:
        mode = self.options.mode
        if mode == RateLimitMode.TOKEN_BUCKET:
            return self._check_token_bucket(info, now, limit)
        if mode == RateLimitMode.SLIDING_WINDOW:
            return self._check_sliding_window(info, now, limit)
        return self._check_fixed_window(info, now, limit)

    def _check_fixed_window(self, info: _ClientRateLimitInfo, now: float, limit: int) -> Tuple[bool, int, float]:
        if now >= info.window_start + self.options.window_seconds:
            info.window_start = now
            info.request_count = 0
        info.request_count += 1
        reset_in = (info.window_start + self.options.window_seconds) - now
        return (info.request_count <= limit, info.request_count, reset_in)

    def _check_sliding_window(self, info: _ClientRateLimitInfo, now: float, limit: int) -> Tuple[bool, int, float]:
        window_start = now - self.options.window_seconds
        ts = info.timestamps
        while ts and ts[0] < window_start:
            ts.popleft()
        count = len(ts)
        if count >= limit:
            oldest = ts[0]
            return (False, count, oldest + self.options.window_seconds - now)
        ts.append(now)
        return (True, count + 1, self.options.window_seconds)

    def _check_token_bucket(self, info: _ClientRateLimitInfo, now: float, limit: int) -> Tuple[bool, int, float]:
        tokens_per_sec = self.options.tokens_per_second or (limit / self.options.window_seconds)
        bucket_size = self.options.bucket_size or limit
        elapsed = now - info.last_token_update
        info.tokens = min(bucket_size, info.tokens + elapsed * tokens_per_sec)
        info.last_token_update = now
        if info.tokens >= 1.0:
            info.tokens -= 1.0
            return (True, bucket_size - int(info.tokens), 1.0 / tokens_per_sec)
        time_to_next = (1.0 - info.tokens) / tokens_per_sec
        return (False, bucket_size, time_to_next)

    def _cleanup(self, now: float) -> None:
        cutoff = now - self.options.window_seconds - 300  # 5 min grace
        stale = [cid for cid, info in self._clients.items() if info.last_access < cutoff]
        for cid in stale:
            self._clients.pop(cid, None)
