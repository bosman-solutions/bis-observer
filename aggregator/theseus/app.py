"""
app.py — bis-theseus Flask application.

Routes:
  GET /health                           — liveness probe
  GET /api/host/<instance>              — full host metric summary
  GET /api/host/<instance>/cpu          — cpu usage pct
  GET /api/host/<instance>/memory       — memory usage pct
  GET /api/host/<instance>/disk         — disk usage pct
  GET /api/service/<instance>/<project> — service summary
  GET /api/container/<instance>/<name>  — container summary
  GET /api/container/<instance>/<name>/logs — log tail

Aggroboard runs as a background asyncio task on startup.
"""

import asyncio
import logging
import os
from pathlib import Path

import httpx
from flask import Flask, jsonify

from .telemetry import ContainerQuery, HostQuery, ServiceQuery
from .aggroboard import Aggroboard

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)-8s %(name)s — %(message)s",
    datefmt="%Y-%m-%dT%H:%M:%S",
)
logger = logging.getLogger(__name__)

# ── Config ────────────────────────────────────────────────────────────────────
PROM_URL            = os.getenv("THESEUS_PROM_URL", "http://obs-prometheus:9090")
LOKI_URL            = os.getenv("THESEUS_LOKI_URL", "http://obs-loki:3100")
GRAFANA_URL         = os.getenv("GRAFANA_URL", "http://obs-grafana:3000")
GRAFANA_TOKEN       = os.getenv("GRAFANA_TOKEN", "")
AGGROBOARD_INTERVAL = int(os.getenv("AGGROBOARD_INTERVAL", "60"))
DASHBOARD_PATH      = Path(os.getenv("DASHBOARD_PATH", "/dashboards/aggroboard.json"))

app = Flask(__name__)

# ── Background task ───────────────────────────────────────────────────────────
_loop  = asyncio.new_event_loop()
_board = Aggroboard(
    prom_url      = PROM_URL,
    loki_url      = LOKI_URL,
    grafana_url   = GRAFANA_URL,
    grafana_token = GRAFANA_TOKEN,
    dashboard_path= DASHBOARD_PATH,
    interval      = AGGROBOARD_INTERVAL,
)


def _start_background():
    import threading
    def runner():
        _loop.run_until_complete(_board.run())
    t = threading.Thread(target=runner, daemon=True)
    t.start()
    logger.info("aggroboard background task started")


_start_background()


# ── Helpers ───────────────────────────────────────────────────────────────────
def _run(coro):
    return asyncio.run_coroutine_threadsafe(coro, _loop).result(timeout=15)


def _client():
    return httpx.AsyncClient(timeout=10.0)


# ── Routes ────────────────────────────────────────────────────────────────────
@app.get("/health")
def health():
    return jsonify({
        "status": "ok",
        "prom": PROM_URL,
        "loki": LOKI_URL,
        "grafana": GRAFANA_URL,
        "token_set": bool(GRAFANA_TOKEN),
    })


@app.get("/api/host/<instance>")
def host_summary(instance: str):
    async def _():
        async with _client() as c:
            return await HostQuery(instance, PROM_URL, LOKI_URL).summary(c)
    return jsonify(_run(_()))


@app.get("/api/host/<instance>/cpu")
def host_cpu(instance: str):
    async def _():
        async with _client() as c:
            return await HostQuery(instance, PROM_URL, LOKI_URL).cpu_usage_pct(c)
    return jsonify({"instance": instance, "cpu_pct": _run(_())})


@app.get("/api/host/<instance>/memory")
def host_memory(instance: str):
    async def _():
        async with _client() as c:
            return await HostQuery(instance, PROM_URL, LOKI_URL).memory_used_pct(c)
    return jsonify({"instance": instance, "mem_pct": _run(_())})


@app.get("/api/host/<instance>/disk")
def host_disk(instance: str):
    async def _():
        async with _client() as c:
            return await HostQuery(instance, PROM_URL, LOKI_URL).disk_used_pct(c)
    return jsonify({"instance": instance, "disk_pct": _run(_())})


@app.get("/api/service/<instance>/<project>")
def service_summary(instance: str, project: str):
    async def _():
        async with _client() as c:
            return await ServiceQuery(instance, project, PROM_URL, LOKI_URL).summary(c)
    return jsonify(_run(_()))


@app.get("/api/container/<instance>/<container_name>")
def container_summary(instance: str, container_name: str):
    async def _():
        async with _client() as c:
            return await ContainerQuery(instance, container_name, PROM_URL, LOKI_URL).summary(c)
    return jsonify(_run(_()))


@app.get("/api/container/<instance>/<container_name>/logs")
def container_logs(instance: str, container_name: str):
    async def _():
        async with _client() as c:
            return await ContainerQuery(instance, container_name, PROM_URL, LOKI_URL).log_tail(c)
    return jsonify({
        "instance": instance,
        "container": container_name,
        "lines": _run(_()),
    })
