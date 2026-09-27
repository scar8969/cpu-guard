"""Demo FastAPI app guarded by cpu_guard.

Demo endpoints: normal, CPU-intensive, memory-intensive, slow + the guarded
dashboard.

Run:
    uvicorn demo_app:app --port 8000
or:
    python demo_app.py
"""

from __future__ import annotations

import time

from cpu_guard.config import (
    CpuGuardOptions,
    MemoryGuardOptions,
    RateLimitMode,
    RateLimitOptions,
    ThrottlingMode,
    ThrottlingOptions,
)
from cpu_guard.guard import CpuGuard

try:
    from fastapi import FastAPI
    from fastapi.responses import JSONResponse
except ImportError:  # pragma: no cover
    raise SystemExit("pip install fastapi uvicorn to run the demo")


def build_app() -> FastAPI:
    app = FastAPI(title="CpuGuard Demo", version="1.0.0")

    @app.get("/")
    def root():
        return {"message": "CpuGuard demo. Try /cpu-intensive, /memory-intensive, /slow"}

    @app.get("/weatherforecast")
    def weatherforecast():
        summaries = ["Freezing", "Bracing", "Chilly", "Cool", "Mild", "Warm", "Balmy", "Hot", "Sweltering", "Scorching"]
        import random

        return [
            {"date": time.strftime("%Y-%m-%d", time.gmtime(time.time() + i * 86400)), "temperatureC": random.randint(-20, 55), "summary": random.choice(summaries)}
            for i in range(1, 6)
        ]

    @app.get("/cpu-intensive")
    def cpu_intensive():
        result = 0.0
        for i in range(3_000_000):
            result += (i ** 0.5) * __import__("math").sin(i)
        return {"result": result, "message": "CPU-intensive operation completed"}

    @app.get("/memory-intensive")
    def memory_intensive():
        data = [bytearray(1024 * 1024) for _ in range(10)]
        return {"allocatedMB": len(data), "message": "Memory-intensive operation completed"}

    @app.get("/slow")
    def slow():
        time.sleep(2.0)
        return {"message": "Slow operation completed"}

    @app.get("/health")
    def health():
        return {"status": "ok"}

    # ---- cpu_guard wiring ---- #
    cpu_options = CpuGuardOptions(
        max_cpu_percentage=80.0,
        response_status_code=503,
        response_message="Server is under heavy CPU load. Please try again later.",
        excluded_paths=["/health", "/cpuguard"],
        on_cpu_limit_exceeded=lambda e: print(f"[CpuGuard] CPU limit exceeded! Usage: {e.cpu_usage_percentage:.1f}% (Threshold: {e.threshold}%)"),
    )
    memory_options = MemoryGuardOptions(
        max_memory_percentage=85.0,
        use_percentage=True,
        response_status_code=503,
        response_message="Server memory usage is too high. Please try again later.",
        excluded_paths=["/health", "/cpuguard"],
    )
    throttle_options = ThrottlingOptions(
        soft_limit_percentage=50.0,
        hard_limit_percentage=85.0,
        min_delay_ms=100.0,
        max_delay_ms=3000.0,
        mode=ThrottlingMode.LINEAR,
        excluded_paths=["/health", "/cpuguard"],
    )
    rate_limit_options = RateLimitOptions(
        requests_per_window=100,
        window_seconds=60.0,
        mode=RateLimitMode.SLIDING_WINDOW,
        combine_with_cpu_limit=True,
        cpu_threshold_for_stricter_limits=70.0,
        high_cpu_rate_limit_factor=0.5,
        include_rate_limit_headers=True,
        excluded_paths=["/health", "/cpuguard"],
    )

    guard = CpuGuard(
        app,
        cpu_options=cpu_options,
        memory_options=memory_options,
        throttle_options=throttle_options,
        rate_limit_options=rate_limit_options,
        dashboard_base_path="/cpuguard",
    )
    guard.start()

    # expose the guard so the CLI can reach it
    app.state.cpu_guard = guard

    @app.get("/cpuguard/stats")
    async def stats():
        return guard.stats.get_summary()

    @app.get("/cpuguard/stats/full")
    async def stats_full():
        return guard.stats.get_full_stats()

    @app.get("/cpuguard/metrics")
    async def metrics():
        from fastapi.responses import PlainTextResponse

        return PlainTextResponse(guard.stats.get_prometheus_metrics(), media_type="text/plain; version=0.0.4")

    @app.get("/cpuguard/dashboard")
    async def dashboard():
        from fastapi.responses import HTMLResponse

        return HTMLResponse(guard.dashboard._html)

    return guard


# uvicorn serves `app` (the guarded ASGI entrypoint), NOT the raw FastAPI app.
# The CpuGuard wrapper routes /cpuguard/* to the dashboard app and guards
# everything else.
app = build_app()

if __name__ == "__main__":
    import uvicorn

    print("=" * 60)
    print("CpuGuard Demo Application")
    print("=" * 60)
    print("  - GET /weatherforecast     - Normal API endpoint")
    print("  - GET /cpu-intensive       - CPU-intensive endpoint (for testing)")
    print("  - GET /memory-intensive    - Memory-intensive endpoint")
    print("  - GET /slow                - Slow endpoint (for testing throttling)")
    print("  - GET /cpuguard/stats      - JSON stats API")
    print("  - GET /cpuguard/stats/full - Full stats with history")
    print("  - GET /cpuguard/metrics    - Prometheus metrics")
    print("  - GET /cpuguard/dashboard  - HTML dashboard")
    print("  - GET /health              - Health check endpoint")
    print("=" * 60)
    uvicorn.run(app, host="127.0.0.1", port=8000)
