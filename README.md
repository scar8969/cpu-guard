# cpu-guard

**Resource management middleware for Python web apps.** Protect your application from overload with CPU limiting, memory limiting, gradual throttling, rate limiting, health checks, Prometheus metrics, and a real-time dashboard.

![Dashboard](https://raw.githubusercontent.com/scar8969/cpu-guard/main/dashboard.png)

## Why this exists

Web apps die under load in predictable ways: CPU spikes, memory leaks, request floods. Most apps bolt on a rate limiter and call it a day — but that leaves CPU and memory pressure unhandled, and hard cutoffs feel brutal to users. This library gives you a complete, layered defense:

- **CPU guard** — reject requests when the process is pegged
- **Memory guard** — reject when RSS blows past a threshold (percentage or bytes)
- **Gradual throttling** — instead of hard cutoffs, *delay* requests on a linear or exponential curve as resources rise, so the app degrades gracefully
- **Rate limiting** — per-client, three algorithms, and it tightens automatically when CPU is high
- **Health checks, Prometheus metrics, and a live dashboard** — so you can see and alert on what's happening

It's pure ASGI, so it drops into FastAPI, Starlette, Quart, or Litestar with one line.

## Features

| Feature | Status |
|---|---|
| CPU limiting (process CPU % threshold) | ✅ |
| Memory limiting (percentage or absolute bytes) | ✅ |
| Gradual throttling (linear / exponential delay curve) | ✅ |
| Rate limiting (fixed window / sliding window / token bucket) | ✅ |
| CPU-aware rate limiting (stricter limits under load) | ✅ |
| Health checks (healthy / degraded / unhealthy) | ✅ |
| Prometheus metrics (`/cpuguard/metrics`) | ✅ |
| Real-time dashboard (`/cpuguard/dashboard`) | ✅ |
| Custom response handlers | ✅ |
| Event callbacks (CPU/memory/rate-limit/throttle) | ✅ |
| Path exclusions | ✅ |
| CLI (`cpu-guard demo / load / check`) | ✅ |

## Install

```bash
pip install cpu-guard
# or from source:
git clone https://github.com/scar8969/cpu-guard && cd cpu-guard
pip install -e ".[demo,test]"
```

## Quick start

```python
from cpu_guard.config import CpuGuardOptions, MemoryGuardOptions, ThrottlingOptions, RateLimitOptions
from cpu_guard.guard import CpuGuard
from fastapi import FastAPI

app = FastAPI()

@app.get("/")
def root():
    return {"message": "guarded"}

guard = CpuGuard(
    app,
    cpu_options=CpuGuardOptions(max_cpu_percentage=80.0, excluded_paths=["/health"]),
    memory_options=MemoryGuardOptions(max_memory_percentage=85.0),
    throttle_options=ThrottlingOptions(soft_limit_percentage=60.0, hard_limit_percentage=90.0),
    rate_limit_options=RateLimitOptions(requests_per_window=100, window_seconds=60.0),
)
guard.start()

# serve `guard`, not `app`:
# uvicorn main:guard --port 8000
```

Visit `/cpuguard/dashboard` for the real-time dashboard, `/cpuguard/stats` for JSON, `/cpuguard/metrics` for Prometheus.

## Configuration

### CPU guard

```python
cpu_options = CpuGuardOptions(
    max_cpu_percentage=80.0,          # reject above 80% CPU
    response_status_code=503,
    response_message="Server is under heavy load.",
    excluded_paths=["/health", "/cpuguard"],
    on_cpu_limit_exceeded=lambda e: print(f"CPU limit exceeded: {e.cpu_usage_percentage}%"),
    custom_response_handler=lambda scope, cpu: (503, '{"error":"CPU overload","usage":%s}' % cpu, "application/json"),
)
```

### Memory guard

```python
memory_options = MemoryGuardOptions(
    max_memory_percentage=85.0,       # or max_memory_bytes=500*1024*1024, use_percentage=False
    response_status_code=503,
)
```

### Gradual throttling

Instead of hard cutoffs, progressively delay requests as resources rise:

```python
throttle_options = ThrottlingOptions(
    soft_limit_percentage=60.0,       # start delaying
    hard_limit_percentage=90.0,       # reject
    min_delay_ms=100.0,
    max_delay_ms=5000.0,
    mode=ThrottlingMode.LINEAR,       # or EXPONENTIAL
    include_memory=True, memory_weight=0.3,
)
```

### Rate limiting

```python
rate_limit_options = RateLimitOptions(
    requests_per_window=100,
    window_seconds=60.0,
    mode=RateLimitMode.SLIDING_WINDOW,   # FIXED_WINDOW / TOKEN_BUCKET
    combine_with_cpu_limit=True,         # stricter limits when CPU is high
    cpu_threshold_for_stricter_limits=70.0,
    high_cpu_rate_limit_factor=0.5,
    include_rate_limit_headers=True,     # X-RateLimit-* headers
)
```

### Health checks

```python
from cpu_guard.health import HealthCheck
health = HealthCheck(guard.monitor, cpu_degraded=70.0, cpu_unhealthy=90.0)
# GET /cpuguard/health -> {"status":"healthy","checks":{...}}
```

## Endpoints

| Endpoint | Description |
|---|---|
| `GET /cpuguard/stats` | JSON stats summary |
| `GET /cpuguard/stats/full` | Full stats with history |
| `GET /cpuguard/metrics` | Prometheus text metrics |
| `GET /cpuguard/health` | Health check JSON |
| `GET /cpuguard/dashboard` | Real-time HTML dashboard |

## CLI

```bash
cpu-guard demo                 # run the demo FastAPI app on :8000
cpu-guard load http://:8000/   # hammer an endpoint, report statuses + latency
cpu-guard check http://:8000/  # print a stats snapshot
```

## Middleware order

Order matters:

```
rate limit → gradual throttle → CPU guard → memory guard
```

## Prometheus metrics

```
cpuguard_requests_throttled_total   counter
cpuguard_requests_delayed_total     counter
cpuguard_requests_ratelimited_total counter
cpuguard_requests_total             counter
cpuguard_cpu_usage_percent          gauge
cpuguard_memory_usage_percent       gauge
cpuguard_memory_usage_bytes         gauge
cpuguard_uptime_seconds             gauge
```

## Verification

```bash
python -m pytest tests/        # 31 tests
```

Live proof (from the demo server):

```
$ cpu-guard load http://127.0.0.1:8000/ -n 150 -c 20
HTTP 200: 100
HTTP 429: 50        # rate limit fired

$ cpu-guard check http://127.0.0.1:8000/
total=150 throttled=0 delayed=0 rate_limited=50
```

CPU throttling (5% threshold, 8 requests → 8×503, `throttled=8`).

## Project layout

```
cpu_guard/
  __init__.py      # public API
  config.py        # options dataclasses (CpuGuardOptions, ...)
  monitor.py       # background CPU/memory sampler
  middleware.py    # 4 ASGI middlewares
  stats.py         # counters + history + Prometheus text
  dashboard.py     # dashboard HTML + endpoints
  health.py        # health check
  guard.py         # CpuGuard facade (composition root)
  cli.py           # cpu-guard CLI
demo_app.py        # FastAPI demo
tests/             # 31 unit + integration tests
```

## License

MIT
