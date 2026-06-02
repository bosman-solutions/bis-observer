"""
aggroboard.py — fleet snapshot writer for bis-theseus.

Runs on a heartbeat. Builds a snapshot of all known hosts from
Prometheus, compares to the previous snapshot in memory, and writes
a Grafana-compatible dashboard JSON only when something has changed.

No state on disk between runs — first heartbeat after restart always writes.
"""

import asyncio
import json
import logging
import time
from pathlib import Path
from typing import Optional

import httpx

from .telemetry import HostQuery

logger = logging.getLogger(__name__)


async def _get_known_instances(
    client: httpx.AsyncClient, prom_url: str
) -> list[str]:
    """
    Return all unique instance labels seen by Node Exporter in the last 5 minutes.
    These are the hosts we know about.
    """
    try:
        resp = await client.get(
            f"{prom_url}/api/v1/query",
            params={"query": 'up{job=~"node.*|node-exporter.*"}'},
            timeout=10.0,
        )
        resp.raise_for_status()
        results = resp.json().get("data", {}).get("result", [])
        return [r["metric"].get("instance", "") for r in results if r["metric"].get("instance")]
    except Exception as e:
        logger.warning(f"failed to fetch known instances: {e}")
        return []


async def build_snapshot(prom_url: str, loki_url: str) -> dict:
    """
    Query all known hosts and return a fleet snapshot dict.
    Shape: { "generated_at": <unix>, "hosts": { <instance>: { ...metrics } } }
    """
    async with httpx.AsyncClient() as client:
        instances = await _get_known_instances(client, prom_url)
        logger.debug(f"snapshot: {len(instances)} instances found")

        tasks = {
            instance: HostQuery(instance, prom_url, loki_url).summary(client)
            for instance in instances
        }
        results = await asyncio.gather(*tasks.values(), return_exceptions=True)

        hosts = {}
        for instance, result in zip(tasks.keys(), results):
            if isinstance(result, Exception):
                logger.warning(f"snapshot failed for {instance}: {result}")
            else:
                hosts[instance] = result

        return {
            "generated_at": int(time.time()),
            "hosts": hosts,
        }


def _build_dashboard_json(snapshot: dict) -> dict:
    """
    Build a Grafana dashboard JSON from the fleet snapshot.
    Uses a simple stat panel per host showing key metrics.
    This is the aggroboard — a NOC-style fleet status view.
    """
    hosts = snapshot.get("hosts", {})
    panels = []
    panel_id = 1
    x, y = 0, 0
    panel_w, panel_h = 6, 4

    for instance, metrics in sorted(hosts.items()):
        hostname = instance.split(":")[0]
        cpu = metrics.get("cpu_pct")
        mem = metrics.get("mem_pct")
        disk = metrics.get("disk_pct")
        load = metrics.get("load1")

        def fmt(v: Optional[float], unit: str = "%") -> str:
            return f"{v:.1f}{unit}" if v is not None else "N/A"

        panels.append({
            "id": panel_id,
            "type": "stat",
            "title": hostname,
            "gridPos": {"x": x, "y": y, "w": panel_w, "h": panel_h},
            "options": {
                "reduceOptions": {"calcs": ["lastNotNull"]},
                "orientation": "auto",
                "textMode": "auto",
                "colorMode": "background",
            },
            "targets": [],
            # Static snapshot values — not live PromQL, just current state.
            # Live PromQL panels are in per-host dashboards (future).
            "fieldConfig": {
                "defaults": {},
                "overrides": [],
            },
            # Embed snapshot values as display text via description
            "description": (
                f"CPU: {fmt(cpu)}  |  MEM: {fmt(mem)}  |  "
                f"DISK: {fmt(disk)}  |  LOAD: {fmt(load, '')}"
            ),
        })

        panel_id += 1
        x += panel_w
        if x >= 24:
            x = 0
            y += panel_h

    generated_at = snapshot.get("generated_at", int(time.time()))

    return {
        "__inputs": [],
        "__requires": [],
        "annotations": {"list": []},
        "description": f"bis-theseus aggroboard — fleet snapshot. Last updated: {generated_at}",
        "editable": False,
        "fiscalYearStartMonth": 0,
        "graphTooltip": 0,
        "id": None,
        "links": [],
        "panels": panels,
        "refresh": "1m",
        "schemaVersion": 38,
        "tags": ["bis-theseus", "aggroboard"],
        "templating": {"list": []},
        "time": {"from": "now-1h", "to": "now"},
        "timepicker": {},
        "timezone": "browser",
        "title": "bis-aggroboard",
        "uid": "bis-aggroboard",
        "version": generated_at,
    }


class Aggroboard:
    """
    Heartbeat writer. Maintains previous snapshot in memory.
    Only writes to disk when snapshot changes.
    """

    def __init__(self, prom_url: str, loki_url: str, dashboard_path: Path, interval: int):
        self.prom_url = prom_url
        self.loki_url = loki_url
        self.dashboard_path = dashboard_path
        self.interval = interval
        self._previous: dict = {}

    async def run(self):
        logger.info(
            f"aggroboard starting — interval {self.interval}s, "
            f"writing to {self.dashboard_path}"
        )
        while True:
            try:
                await self._tick()
            except Exception as e:
                logger.error(f"aggroboard tick failed: {e}")
            await asyncio.sleep(self.interval)

    async def _tick(self):
        snapshot = await build_snapshot(self.prom_url, self.loki_url)

        # Compare hosts dict only — generated_at always differs
        if snapshot.get("hosts") == self._previous.get("hosts"):
            logger.debug("aggroboard: no change, skipping write")
            return

        dashboard = _build_dashboard_json(snapshot)
        self.dashboard_path.write_text(json.dumps(dashboard, indent=2))
        host_count = len(snapshot.get("hosts", {}))
        logger.info(f"aggroboard: wrote dashboard ({host_count} hosts)")
        self._previous = snapshot
