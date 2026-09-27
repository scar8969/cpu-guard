"""Unit tests for cpu_guard pure logic: throttling delay curve, rate limit
algorithms, health check, monitor sampling, and stats service.
"""

import time

import pytest

from cpu_guard.config import RateLimitMode, ThrottlingMode
from cpu_guard.health import HealthCheck
from cpu_guard.middleware import GradualThrottlingMiddleware, _ClientRateLimitInfo
from cpu_guard.monitor import ResourceMonitor
from cpu_guard.stats import GuardStatsService


# --------------------------------------------------------------------------- #
# throttling delay curve
# --------------------------------------------------------------------------- #


class _ThrottleHarness:
    def __init__(self, **kwargs):
        from cpu_guard.config import ThrottlingOptions

        self.options = ThrottlingOptions(**kwargs)
        self.monitor = ResourceMonitor(sampling_interval=0.01)
        self.mw = GradualThrottlingMiddleware(lambda scope, receive, send: None, self.options, self.monitor)


def test_linear_delay_at_soft_limit_is_min():
    h = _ThrottleHarness(soft_limit_percentage=50.0, hard_limit_percentage=90.0, min_delay_ms=100.0, max_delay_ms=5000.0)
    assert h.mw._calculate_delay(50.0) == 100.0


def test_linear_delay_at_hard_limit_is_max():
    h = _ThrottleHarness(soft_limit_percentage=50.0, hard_limit_percentage=90.0, min_delay_ms=100.0, max_delay_ms=5000.0)
    assert h.mw._calculate_delay(90.0) == 5000.0


def test_linear_delay_midpoint():
    h = _ThrottleHarness(soft_limit_percentage=50.0, hard_limit_percentage=90.0, min_delay_ms=100.0, max_delay_ms=5000.0)
    # position = (70-50)/40 = 0.5 -> 100 + 0.5*4900 = 2550
    assert h.mw._calculate_delay(70.0) == 2550.0


def test_exponential_delay_is_quadratic():
    h = _ThrottleHarness(
        soft_limit_percentage=50.0, hard_limit_percentage=90.0, min_delay_ms=100.0, max_delay_ms=5000.0, mode=ThrottlingMode.EXPONENTIAL
    )
    # position 0.5 -> 100 + 0.25*4900 = 1325
    assert h.mw._calculate_delay(70.0) == 1325.0


def test_delay_clamps_out_of_range():
    h = _ThrottleHarness(soft_limit_percentage=50.0, hard_limit_percentage=90.0, min_delay_ms=100.0, max_delay_ms=5000.0)
    assert h.mw._calculate_delay(10.0) == 100.0  # below soft -> clamped to min
    assert h.mw._calculate_delay(150.0) == 5000.0  # above hard -> clamped to max


def test_combined_resource_usage_weights():
    from cpu_guard.config import ThrottlingOptions

    opts = ThrottlingOptions(include_memory=True, memory_weight=0.3)
    monitor = ResourceMonitor()
    monitor._current_cpu = 40.0
    monitor._current_mem_pct = 80.0
    mw = GradualThrottlingMiddleware(lambda *a: None, opts, monitor)
    # 40*0.7 + 80*0.3 = 28 + 24 = 52
    assert mw._get_resource_usage() == pytest.approx(52.0)


# --------------------------------------------------------------------------- #
# rate limiting algorithms
# --------------------------------------------------------------------------- #


def _make_rl_mw(**kwargs):
    from cpu_guard.config import RateLimitOptions

    opts = RateLimitOptions(requests_per_window=10, window_seconds=60.0, **kwargs)
    monitor = ResourceMonitor()
    mw = type(
        "RL",
        (object,),
        {
            "options": opts,
            "monitor": monitor,
            "_check": None,
        },
    )()
    # bind real methods
    import cpu_guard.middleware as m

    mw._check_fixed_window = m.RateLimitMiddleware._check_fixed_window.__get__(mw)
    mw._check_sliding_window = m.RateLimitMiddleware._check_sliding_window.__get__(mw)
    mw._check_token_bucket = m.RateLimitMiddleware._check_token_bucket.__get__(mw)
    return mw


def test_fixed_window_allows_then_blocks():
    mw = _make_rl_mw(mode=RateLimitMode.FIXED_WINDOW)
    info = _ClientRateLimitInfo(time.time())
    for _ in range(10):
        allowed, count, _ = mw._check_fixed_window(info, time.time(), 10)
        assert allowed
    allowed, count, _ = mw._check_fixed_window(info, time.time(), 10)
    assert not allowed
    assert count == 11


def test_fixed_window_resets_after_window():
    mw = _make_rl_mw(mode=RateLimitMode.FIXED_WINDOW)
    info = _ClientRateLimitInfo(time.time())
    for _ in range(10):
        mw._check_fixed_window(info, time.time(), 10)
    future = time.time() + 61.0
    allowed, count, _ = mw._check_fixed_window(info, future, 10)
    assert allowed
    assert count == 1


def test_sliding_window_evicts_old_timestamps():
    mw = _make_rl_mw(mode=RateLimitMode.SLIDING_WINDOW)
    info = _ClientRateLimitInfo(time.time())
    now = time.time()
    for i in range(10):
        mw._check_sliding_window(info, now + i * 0.001, 10)
    # all 10 within window -> next is blocked
    allowed, count, _ = mw._check_sliding_window(info, now + 0.02, 10)
    assert not allowed
    # advance past window -> old timestamps evicted
    allowed, count, _ = mw._check_sliding_window(info, now + 61.0, 10)
    assert allowed


def test_token_bucket_refills():
    mw = _make_rl_mw(mode=RateLimitMode.TOKEN_BUCKET, tokens_per_second=10.0, bucket_size=10)
    info = _ClientRateLimitInfo(time.time())
    info.tokens = 10.0  # middleware seeds the bucket full on first request
    now = time.time()
    for _ in range(10):
        allowed, _, _ = mw._check_token_bucket(info, now, 10)
        assert allowed
    allowed, _, _ = mw._check_token_bucket(info, now, 10)
    assert not allowed
    # wait 1s -> 10 tokens refilled
    allowed, _, _ = mw._check_token_bucket(info, now + 1.0, 10)
    assert allowed


def test_effective_limit_drops_when_cpu_high():
    from cpu_guard.config import RateLimitOptions
    from cpu_guard.middleware import RateLimitMiddleware

    opts = RateLimitOptions(requests_per_window=100, combine_with_cpu_limit=True, cpu_threshold_for_stricter_limits=70.0, high_cpu_rate_limit_factor=0.5)
    monitor = ResourceMonitor()
    monitor._current_cpu = 90.0
    mw = RateLimitMiddleware(lambda *a: None, opts, monitor)
    assert mw._get_effective_limit() == 50

    monitor._current_cpu = 10.0
    assert mw._get_effective_limit() == 100


# --------------------------------------------------------------------------- #
# health check
# --------------------------------------------------------------------------- #


def test_health_check_statuses():
    monitor = ResourceMonitor()
    hc = HealthCheck(monitor, cpu_degraded=70.0, cpu_unhealthy=90.0)

    monitor._current_cpu = 10.0
    monitor._current_mem_pct = 10.0
    assert hc.check().status == "healthy"

    monitor._current_cpu = 80.0
    assert hc.check().status == "degraded"

    monitor._current_cpu = 95.0
    assert hc.check().status == "unhealthy"


# --------------------------------------------------------------------------- #
# monitor sampling
# --------------------------------------------------------------------------- #


def test_monitor_snapshot_roundtrip():
    monitor = ResourceMonitor(sampling_interval=0.01)
    monitor._current_cpu = 42.0
    monitor._current_mem_pct = 33.0
    monitor._current_mem_bytes = 1234
    monitor._throttled = 5
    monitor._delayed = 2
    monitor._rate_limited = 1
    snap = monitor.get_snapshot()
    assert snap.cpu_usage_percentage == 42.0
    assert snap.memory_usage_percentage == 33.0
    assert snap.memory_usage_bytes == 1234
    assert snap.requests_throttled == 5
    assert snap.requests_delayed == 2
    assert snap.requests_rate_limited == 1
    assert snap.total_memory_bytes > 0


def test_monitor_start_stop():
    monitor = ResourceMonitor(sampling_interval=0.01)
    monitor.start()
    time.sleep(0.05)
    monitor.stop()
    assert monitor._thread is None or not monitor._thread.is_alive()


# --------------------------------------------------------------------------- #
# stats service
# --------------------------------------------------------------------------- #


def test_stats_summary_shape():
    monitor = ResourceMonitor()
    stats = GuardStatsService(monitor)
    stats.increment_total_requests()
    stats.record_data_point()
    summary = stats.get_summary()
    assert summary["totalRequests"] == 1
    assert "currentCpuUsage" in summary
    assert "uptimeSeconds" in summary
    assert "lastUpdated" in summary


def test_stats_full_has_history():
    monitor = ResourceMonitor()
    stats = GuardStatsService(monitor)
    for _ in range(3):
        stats.record_data_point()
    full = stats.get_full_stats()
    assert len(full["cpuHistory"]) == 3
    assert len(full["memoryHistory"]) == 3


def test_prometheus_metrics_text():
    monitor = ResourceMonitor()
    stats = GuardStatsService(monitor)
    text = stats.get_prometheus_metrics()
    assert "# HELP cpuguard_requests_throttled_total" in text
    assert "cpuguard_cpu_usage_percent" in text
    assert text.endswith("\n")
