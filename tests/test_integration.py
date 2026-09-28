"""Integration tests: prove the guards actually fire over real ASGI requests.

These use an in-memory ASGI transport (httpx ASGITransport) so no server
process is needed — deterministic, fast, and they exercise the full
middleware chain end to end.
"""

import time

import pytest

from cpu_guard.config import (
    CpuGuardOptions,
    MemoryGuardOptions,
    RateLimitMode,
    RateLimitOptions,
    ThrottlingMode,
    ThrottlingOptions,
)
from cpu_guard.guard import CpuGuard
from cpu_guard.middleware import RateLimitMiddleware
from cpu_guard.monitor import ResourceMonitor


def _make_app():
    """A tiny ASGI app that returns 200 'hello'."""
    async def app(scope, receive, send):
        await send({"type": "http.response.start", "status": 200, "headers": [(b"content-type", b"text/plain")]})
        await send({"type": "http.response.body", "body": b"hello"})

    return app


def _run(guard, path="/", client=("127.0.0.1", 1234), headers=None):
    """Run a single request through the guard chain, return (status, headers)."""
    import asyncio

    import httpx

    async def _do():
        transport = httpx.ASGITransport(app=guard)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as ac:
            resp = await ac.get(path, headers=headers or {})
            return resp.status_code, dict(resp.headers)

    return asyncio.run(_do())


# --------------------------------------------------------------------------- #
# CPU guard
# --------------------------------------------------------------------------- #


def test_cpu_guard_blocks_above_threshold():
    opts = CpuGuardOptions(max_cpu_percentage=50.0)
    monitor = ResourceMonitor()
    monitor._current_cpu = 90.0  # force over threshold
    from cpu_guard.middleware import CpuLimitMiddleware

    mw = CpuLimitMiddleware(_make_app(), opts, monitor)
    status, _ = _run(mw)
    assert status == 503


def test_cpu_guard_passes_below_threshold():
    opts = CpuGuardOptions(max_cpu_percentage=50.0)
    monitor = ResourceMonitor()
    monitor._current_cpu = 10.0
    from cpu_guard.middleware import CpuLimitMiddleware

    mw = CpuLimitMiddleware(_make_app(), opts, monitor)
    status, _ = _run(mw)
    assert status == 200


def test_cpu_guard_excluded_paths():
    opts = CpuGuardOptions(max_cpu_percentage=50.0, excluded_paths=["/health"])
    monitor = ResourceMonitor()
    monitor._current_cpu = 90.0
    from cpu_guard.middleware import CpuLimitMiddleware

    mw = CpuLimitMiddleware(_make_app(), opts, monitor)
    status, _ = _run(mw, path="/health")
    assert status == 200


def test_cpu_guard_custom_response_handler():
    opts = CpuGuardOptions(
        max_cpu_percentage=50.0,
        custom_response_handler=lambda scope, cpu: (418, '{"error":"overloaded","cpu":%s}' % cpu, "application/json"),
    )
    monitor = ResourceMonitor()
    monitor._current_cpu = 90.0
    from cpu_guard.middleware import CpuLimitMiddleware

    mw = CpuLimitMiddleware(_make_app(), opts, monitor)
    status, headers = _run(mw)
    assert status == 418
    assert headers.get("content-type") == "application/json"


def test_cpu_guard_event_callback_fires():
    fired = []

    def handler(e):
        fired.append(e)

    opts = CpuGuardOptions(max_cpu_percentage=50.0, on_cpu_limit_exceeded=handler)
    monitor = ResourceMonitor()
    monitor._current_cpu = 90.0
    from cpu_guard.middleware import CpuLimitMiddleware

    mw = CpuLimitMiddleware(_make_app(), opts, monitor)
    _run(mw)
    assert len(fired) == 1
    assert fired[0].cpu_usage_percentage == 90.0
    assert fired[0].threshold == 50.0


# --------------------------------------------------------------------------- #
# memory guard
# --------------------------------------------------------------------------- #


def test_memory_guard_blocks_above_threshold():
    opts = MemoryGuardOptions(max_memory_percentage=50.0, use_percentage=True)
    monitor = ResourceMonitor()
    monitor._current_mem_pct = 90.0
    from cpu_guard.middleware import MemoryGuardMiddleware

    mw = MemoryGuardMiddleware(_make_app(), opts, monitor)
    status, _ = _run(mw)
    assert status == 503


def test_memory_guard_byte_mode():
    opts = MemoryGuardOptions(max_memory_bytes=1000, use_percentage=False)
    monitor = ResourceMonitor()
    monitor._current_mem_bytes = 5000
    from cpu_guard.middleware import MemoryGuardMiddleware

    mw = MemoryGuardMiddleware(_make_app(), opts, monitor)
    status, _ = _run(mw)
    assert status == 503


# --------------------------------------------------------------------------- #
# gradual throttling
# --------------------------------------------------------------------------- #


def test_throttle_delays_between_soft_and_hard():
    opts = ThrottlingOptions(soft_limit_percentage=50.0, hard_limit_percentage=90.0, min_delay_ms=50.0, max_delay_ms=200.0)
    monitor = ResourceMonitor()
    monitor._current_cpu = 70.0  # position 0.5 -> delay 125ms
    from cpu_guard.middleware import GradualThrottlingMiddleware

    mw = GradualThrottlingMiddleware(_make_app(), opts, monitor)
    start = time.perf_counter()
    status, _ = _run(mw)
    elapsed_ms = (time.perf_counter() - start) * 1000
    assert status == 200
    assert elapsed_ms >= 100  # at least the min delay


def test_throttle_rejects_at_hard_limit():
    opts = ThrottlingOptions(soft_limit_percentage=50.0, hard_limit_percentage=90.0)
    monitor = ResourceMonitor()
    monitor._current_cpu = 95.0
    from cpu_guard.middleware import GradualThrottlingMiddleware

    mw = GradualThrottlingMiddleware(_make_app(), opts, monitor)
    status, _ = _run(mw)
    assert status == 503


def test_throttle_no_delay_below_soft():
    opts = ThrottlingOptions(soft_limit_percentage=50.0, hard_limit_percentage=90.0, min_delay_ms=100.0)
    monitor = ResourceMonitor()
    monitor._current_cpu = 10.0
    from cpu_guard.middleware import GradualThrottlingMiddleware

    mw = GradualThrottlingMiddleware(_make_app(), opts, monitor)
    start = time.perf_counter()
    status, _ = _run(mw)
    assert status == 200
    assert (time.perf_counter() - start) * 1000 < 50


# --------------------------------------------------------------------------- #
# rate limiting
# --------------------------------------------------------------------------- #


def test_rate_limit_blocks_after_window_exhausted():
    opts = RateLimitOptions(requests_per_window=3, window_seconds=60.0, mode=RateLimitMode.FIXED_WINDOW, include_rate_limit_headers=True)
    monitor = ResourceMonitor()
    mw = RateLimitMiddleware(_make_app(), opts, monitor)

    for _ in range(3):
        status, _ = _run(mw)
        assert status == 200

    status, headers = _run(mw)
    assert status == 429
    assert headers.get("x-ratelimit-limit") == "3"
    assert headers.get("x-ratelimit-remaining") == "0"
    assert headers.get("retry-after") is not None


def test_rate_limit_per_client():
    # httpx ASGITransport always reports the same client IP, so we key clients
    # by an X-Client-ID header to prove per-client isolation.
    opts = RateLimitOptions(
        requests_per_window=2,
        window_seconds=60.0,
        mode=RateLimitMode.FIXED_WINDOW,
        client_identifier_factory=lambda scope: dict(scope.get("headers", [])).get(b"x-client-id", b"unknown").decode(),
    )
    monitor = ResourceMonitor()
    mw = RateLimitMiddleware(_make_app(), opts, monitor)

    assert _run(mw, headers={"X-Client-ID": "alice"})[0] == 200
    assert _run(mw, headers={"X-Client-ID": "alice"})[0] == 200
    assert _run(mw, headers={"X-Client-ID": "alice"})[0] == 429
    # different client unaffected
    assert _run(mw, headers={"X-Client-ID": "bob"})[0] == 200


# --------------------------------------------------------------------------- #
# full guard chain + dashboard
# --------------------------------------------------------------------------- #


def test_full_guard_chain_serves_and_counts():
    guard = CpuGuard(
        _make_app(),
        cpu_options=CpuGuardOptions(max_cpu_percentage=95.0),
        memory_options=MemoryGuardOptions(max_memory_percentage=95.0),
        throttle_options=ThrottlingOptions(soft_limit_percentage=95.0, hard_limit_percentage=99.0),
        rate_limit_options=RateLimitOptions(requests_per_window=1000, window_seconds=60.0),
    )
    guard.start()
    try:
        status, _ = _run(guard)
        assert status == 200
        assert guard.stats.total_requests == 1

        # dashboard endpoints work
        status, _ = _run(guard, path="/cpuguard/stats")
        assert status == 200
        status, _ = _run(guard, path="/cpuguard/dashboard")
        assert status == 200
        status, _ = _run(guard, path="/cpuguard/metrics")
        assert status == 200
        status, _ = _run(guard, path="/cpuguard/health")
        assert status == 200
    finally:
        guard.stop()


def test_full_guard_chain_rate_limits():
    guard = CpuGuard(
        _make_app(),
        cpu_options=CpuGuardOptions(max_cpu_percentage=95.0),
        rate_limit_options=RateLimitOptions(requests_per_window=2, window_seconds=60.0, mode=RateLimitMode.FIXED_WINDOW),
    )
    guard.start()
    try:
        assert _run(guard)[0] == 200
        assert _run(guard)[0] == 200
        assert _run(guard)[0] == 429
        assert guard.stats.get_summary()["totalRequestsRateLimited"] == 1
    finally:
        guard.stop()
