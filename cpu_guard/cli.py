"""cpu-guard command line tool.

Commands:
    cpu-guard demo            run the demo FastAPI app (needs fastapi+uvicorn)
    cpu-guard load <url>      hammer an endpoint to trigger throttling/rate limits
    cpu-guard check <url>     print a stats snapshot from a running /cpuguard/stats

The load command is the proof loop: fire N concurrent requests at an endpoint
and report how many were throttled/delayed/rate-limited vs served.
"""

from __future__ import annotations

import argparse
import concurrent.futures
import sys
import time
import urllib.request
from collections import Counter


def _fetch(url: str, timeout: float = 30.0) -> tuple[int, float]:
    start = time.perf_counter()
    try:
        with urllib.request.urlopen(url, timeout=timeout) as r:
            status = r.status
            r.read()
    except urllib.error.HTTPError as e:
        status = e.code
    except Exception as e:  # noqa: BLE001
        return -1, time.perf_counter() - start
    return status, time.perf_counter() - start


def cmd_demo(args: argparse.Namespace) -> int:
    import uvicorn

    print("Starting CpuGuard demo app on", f"http://{args.host}:{args.port}")
    uvicorn.run("demo_app:app", host=args.host, port=args.port, reload=False)
    return 0


def cmd_load(args: argparse.Namespace) -> int:
    url = args.url
    n = args.requests
    concurrency = args.concurrency
    results: Counter = Counter()
    latencies: list[float] = []

    print(f"Firing {n} requests at {url} (concurrency={concurrency})...")
    start = time.perf_counter()
    with concurrent.futures.ThreadPoolExecutor(max_workers=concurrency) as ex:
        futures = [ex.submit(_fetch, url) for _ in range(n)]
        for fut in concurrent.futures.as_completed(futures):
            status, latency = fut.result()
            results[status] += 1
            latencies.append(latency)
    elapsed = time.perf_counter() - start

    print("\n--- results ---")
    for status, count in sorted(results.items(), key=lambda kv: (-kv[1], kv[0])):
        print(f"  HTTP {status}: {count}")
    if latencies:
        latencies.sort()
        p50 = latencies[len(latencies) // 2]
        p95 = latencies[int(len(latencies) * 0.95)]
        print(f"\n  total time: {elapsed:.2f}s")
        print(f"  p50 latency: {p50 * 1000:.0f}ms | p95 latency: {p95 * 1000:.0f}ms")
        print(f"  throughput: {n / elapsed:.0f} req/s")
    return 0


def cmd_check(args: argparse.Namespace) -> int:
    url = args.url.rstrip("/") + "/cpuguard/stats"
    try:
        with urllib.request.urlopen(url, timeout=10) as r:
            import json

            data = json.load(r)
    except Exception as e:  # noqa: BLE001
        print(f"failed to fetch {url}: {e}")
        return 1

    print(f"CPU:        {data['currentCpuUsage']}% (peak {data['peakCpuUsage']}%, avg {data['averageCpuUsage']}%)")
    print(f"Memory:     {data['currentMemoryUsage']}% ({data['currentMemoryBytes'] / 1024 / 1024:.0f} MB / {data['totalMemoryBytes'] / 1024 / 1024:.0f} MB)")
    print(f"Requests:   total={data['totalRequests']} throttled={data['totalRequestsThrottled']} delayed={data['totalRequestsDelayed']} rate_limited={data['totalRequestsRateLimited']}")
    print(f"Uptime:     {data['uptimeSeconds']:.0f}s")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(prog="cpu-guard", description="CpuGuard - resource management middleware for Python web apps")
    sub = parser.add_subparsers(dest="command")

    demo = sub.add_parser("demo", help="run the demo FastAPI app")
    demo.add_argument("--host", default="127.0.0.1")
    demo.add_argument("--port", type=int, default=8000)

    load = sub.add_parser("load", help="hammer an endpoint to trigger throttling")
    load.add_argument("url")
    load.add_argument("-n", "--requests", type=int, default=200)
    load.add_argument("-c", "--concurrency", type=int, default=20)

    check = sub.add_parser("check", help="print stats from a running /cpuguard/stats")
    check.add_argument("url")

    args = parser.parse_args()
    if args.command == "demo":
        return cmd_demo(args)
    if args.command == "load":
        return cmd_load(args)
    if args.command == "check":
        return cmd_check(args)
    parser.print_help()
    return 0


if __name__ == "__main__":
    sys.exit(main())
