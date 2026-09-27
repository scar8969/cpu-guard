"""Real-time dashboard + stats/metrics/health endpoints as an ASGI app."""

from __future__ import annotations

import json
from typing import Callable, Optional

from cpu_guard.health import HealthCheck
from cpu_guard.stats import GuardStatsService

DASHBOARD_HTML = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>CpuGuard Dashboard</title>
<script src="https://cdn.jsdelivr.net/npm/chart.js"></script>
<style>
* { margin: 0; padding: 0; box-sizing: border-box; }
body { font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif; background: #1a1a2e; color: #eee; padding: 20px; }
h1 { color: #00d4ff; margin-bottom: 20px; }
.dashboard { display: grid; grid-template-columns: repeat(auto-fit, minmax(300px, 1fr)); gap: 20px; }
.card { background: #16213e; border-radius: 12px; padding: 20px; box-shadow: 0 4px 6px rgba(0,0,0,0.3); }
.card h2 { color: #00d4ff; font-size: 14px; text-transform: uppercase; margin-bottom: 15px; }
.metric { font-size: 36px; font-weight: bold; }
.metric.cpu { color: #ff6b6b; }
.metric.memory { color: #4ecdc4; }
.metric.requests { color: #ffe66d; }
.sub-metric { font-size: 14px; color: #888; margin-top: 5px; }
.chart-container { height: 200px; margin-top: 15px; }
.stats-grid { display: grid; grid-template-columns: 1fr 1fr; gap: 10px; }
.stat-item { background: #0f3460; padding: 15px; border-radius: 8px; }
.stat-label { font-size: 12px; color: #888; }
.stat-value { font-size: 24px; font-weight: bold; color: #00d4ff; }
.status { display: inline-block; padding: 4px 12px; border-radius: 20px; font-size: 12px; }
.status.healthy { background: #2ecc71; color: #fff; }
.status.degraded { background: #f39c12; color: #fff; }
.status.unhealthy { background: #e74c3c; color: #fff; }
.refresh-info { text-align: center; color: #666; font-size: 12px; margin-top: 20px; }
</style>
</head>
<body>
<h1>CpuGuard Dashboard</h1>
<div class="dashboard">
  <div class="card">
    <h2>CPU Usage</h2>
    <div class="metric cpu" id="cpu-value">--%</div>
    <div class="sub-metric">Peak: <span id="cpu-peak">--</span>% | Avg: <span id="cpu-avg">--</span>%</div>
    <div class="chart-container"><canvas id="cpu-chart"></canvas></div>
  </div>
  <div class="card">
    <h2>Memory Usage</h2>
    <div class="metric memory" id="memory-value">--%</div>
    <div class="sub-metric">Used: <span id="memory-used">--</span> MB / <span id="memory-total">--</span> MB</div>
    <div class="chart-container"><canvas id="memory-chart"></canvas></div>
  </div>
  <div class="card">
    <h2>Request Statistics</h2>
    <div class="stats-grid">
      <div class="stat-item"><div class="stat-label">Total Requests</div><div class="stat-value" id="total-requests">--</div></div>
      <div class="stat-item"><div class="stat-label">Throttled</div><div class="stat-value" id="throttled">--</div></div>
      <div class="stat-item"><div class="stat-label">Delayed</div><div class="stat-value" id="delayed">--</div></div>
      <div class="stat-item"><div class="stat-label">Rate Limited</div><div class="stat-value" id="rate-limited">--</div></div>
    </div>
  </div>
  <div class="card">
    <h2>System Status</h2>
    <div class="stats-grid">
      <div class="stat-item"><div class="stat-label">Uptime</div><div class="stat-value" id="uptime">--</div></div>
      <div class="stat-item"><div class="stat-label">Status</div><div class="stat-value"><span class="status healthy" id="status">Healthy</span></div></div>
    </div>
  </div>
</div>
<div class="refresh-info">Auto-refreshing every 2 seconds | Last update: <span id="last-update">--</span></div>
<script>
const statsEndpoint = '{{STATS_ENDPOINT}}';
const cpuData = { labels: [], data: [] };
const memoryData = { labels: [], data: [] };
const maxDataPoints = 30;
const cpuChart = new Chart(document.getElementById('cpu-chart'), {
  type: 'line',
  data: { labels: cpuData.labels, datasets: [{ label: 'CPU %', data: cpuData.data, borderColor: '#ff6b6b', tension: 0.3, fill: false }] },
  options: { responsive: true, maintainAspectRatio: false, scales: { y: { min: 0, max: 100 } }, plugins: { legend: { display: false } } }
});
const memoryChart = new Chart(document.getElementById('memory-chart'), {
  type: 'line',
  data: { labels: memoryData.labels, datasets: [{ label: 'Memory %', data: memoryData.data, borderColor: '#4ecdc4', tension: 0.3, fill: false }] },
  options: { responsive: true, maintainAspectRatio: false, scales: { y: { min: 0, max: 100 } }, plugins: { legend: { display: false } } }
});
function formatUptime(seconds) {
  const h = Math.floor(seconds / 3600);
  const m = Math.floor((seconds % 3600) / 60);
  const s = Math.floor(seconds % 60);
  return h + 'h ' + m + 'm ' + s + 's';
}
function getStatus(cpu, memory) {
  if (cpu > 90 || memory > 90) return { class: 'unhealthy', text: 'Unhealthy' };
  if (cpu > 70 || memory > 70) return { class: 'degraded', text: 'Degraded' };
  return { class: 'healthy', text: 'Healthy' };
}
async function updateDashboard() {
  try {
    const response = await fetch(statsEndpoint);
    const data = await response.json();
    const now = new Date().toLocaleTimeString();
    document.getElementById('cpu-value').textContent = data.currentCpuUsage.toFixed(1) + '%';
    document.getElementById('cpu-peak').textContent = data.peakCpuUsage.toFixed(1);
    document.getElementById('cpu-avg').textContent = data.averageCpuUsage.toFixed(1);
    document.getElementById('memory-value').textContent = data.currentMemoryUsage.toFixed(1) + '%';
    document.getElementById('memory-used').textContent = (data.currentMemoryBytes / 1024 / 1024).toFixed(0);
    document.getElementById('memory-total').textContent = (data.totalMemoryBytes / 1024 / 1024).toFixed(0);
    document.getElementById('total-requests').textContent = data.totalRequests;
    document.getElementById('throttled').textContent = data.totalRequestsThrottled;
    document.getElementById('delayed').textContent = data.totalRequestsDelayed;
    document.getElementById('rate-limited').textContent = data.totalRequestsRateLimited;
    document.getElementById('uptime').textContent = formatUptime(data.uptimeSeconds);
    const status = getStatus(data.currentCpuUsage, data.currentMemoryUsage);
    const statusEl = document.getElementById('status');
    statusEl.textContent = status.text;
    statusEl.className = 'status ' + status.class;
    document.getElementById('last-update').textContent = now;
    cpuData.labels.push(now);
    cpuData.data.push(data.currentCpuUsage);
    memoryData.labels.push(now);
    memoryData.data.push(data.currentMemoryUsage);
    if (cpuData.labels.length > maxDataPoints) {
      cpuData.labels.shift(); cpuData.data.shift(); memoryData.labels.shift(); memoryData.data.shift();
    }
    cpuChart.update();
    memoryChart.update();
  } catch (e) { console.error('Failed to fetch stats:', e); }
}
updateDashboard();
setInterval(updateDashboard, 2000);
</script>
</body>
</html>
"""


class DashboardApp:
    """ASGI app serving /stats, /stats/full, /metrics, /health and /dashboard."""

    def __init__(
        self,
        stats: GuardStatsService,
        health: Optional[HealthCheck] = None,
        base_path: str = "/cpuguard",
    ):
        self.stats = stats
        self.health = health
        self.base_path = base_path.rstrip("/")
        self._html = DASHBOARD_HTML.replace("{{STATS_ENDPOINT}}", self.base_path + "/stats")

    async def __call__(self, scope: dict, receive: Callable, send: Callable) -> None:
        if scope["type"] != "http":
            return
        path = scope.get("path", "")
        method = scope.get("method", "GET")
        if method != "GET":
            return

        if path == self.base_path + "/stats":
            body = json.dumps(self.stats.get_summary()).encode("utf-8")
            await self._json(send, body)
        elif path == self.base_path + "/stats/full":
            body = json.dumps(self.stats.get_full_stats()).encode("utf-8")
            await self._json(send, body)
        elif path == self.base_path + "/metrics":
            body = self.stats.get_prometheus_metrics().encode("utf-8")
            await self._text(send, body, "text/plain; version=0.0.4; charset=utf-8")
        elif path == self.base_path + "/health":
            if self.health is not None:
                body = json.dumps(self.health.as_json()).encode("utf-8")
                await self._json(send, body, status=200 if self.health.check().status == "healthy" else 503)
            else:
                body = b'{"status":"healthy"}'
                await self._json(send, body)
        elif path == self.base_path + "/dashboard":
            body = self._html.encode("utf-8")
            await self._text(send, body, "text/html; charset=utf-8")
        else:
            await self._json(send, b'{"error":"not found"}', status=404)

    async def _json(self, send: Callable, body: bytes, status: int = 200) -> None:
        await send({"type": "http.response.start", "status": status, "headers": [(b"content-type", b"application/json"), (b"cache-control", b"no-store")]})
        await send({"type": "http.response.body", "body": body})

    async def _text(self, send: Callable, body: bytes, content_type: str, status: int = 200) -> None:
        await send({"type": "http.response.start", "status": status, "headers": [(b"content-type", content_type.encode("latin-1")), (b"cache-control", b"no-store")]})
        await send({"type": "http.response.body", "body": body})
