"""
aggroboard.py — fleet snapshot writer for bis-theseus.

Runs on a heartbeat. Builds a snapshot of all known hosts from
Prometheus, compares to the previous snapshot in memory, and writes
a Grafana-compatible dashboard JSON only when something has changed.

No state on disk between runs — first heartbeat after restart always writes.

Dashboard layout per host:
  Row 1: CPU %, Memory %, Load ratio (load1/cores), Uptime — stat panels
  Row 2: Disk table — mountpoints filtered to real filesystems only
"""

import asyncio
import json
import logging
import time
from pathlib import Path

import httpx

from .telemetry import HostQuery

logger = logging.getLogger(__name__)

# Filesystem types to exclude from disk table
EXCLUDE_FSTYPES = "tmpfs|squashfs|overlay|devtmpfs|ramfs|efivarfs|fuse.lxcfs"

# Panel sizing
STAT_W = 6
STAT_H = 4
DISK_W = 24
DISK_H = 6
ROW_H  = 2


async def _get_known_instances(
    client: httpx.AsyncClient, prom_url: str
) -> list[str]:
    """
    Return all unique instance labels seen by Node Exporter recently.
    """
    try:
        resp = await client.get(
            f"{prom_url}/api/v1/query",
            params={"query": 'up{job=~"node.*|node-exporter.*"}'},
            timeout=10.0,
        )
        resp.raise_for_status()
        results = resp.json().get("data", {}).get("result", [])
        return [
            r["metric"].get("instance", "")
            for r in results
            if r["metric"].get("instance")
        ]
    except Exception as e:
        logger.warning(f"failed to fetch known instances: {e}")
        return []


async def build_snapshot(prom_url: str, loki_url: str) -> dict:
    """
    Query all known hosts and return a fleet snapshot dict.
    Shape: { "generated_at": <unix>, "hosts": [<instance>, ...] }
    We only need the instance list for the dashboard — PromQL does the rest live.
    """
    async with httpx.AsyncClient() as client:
        instances = await _get_known_instances(client, prom_url)
        logger.debug(f"snapshot: {len(instances)} instances found")
        return {
            "generated_at": int(time.time()),
            "hosts": sorted(instances),
        }


def _stat_panel(
    panel_id: int,
    title: str,
    promql: str,
    unit: str,
    x: int,
    y: int,
    thresholds: list[dict] | None = None,
    decimals: int = 1,
) -> dict:
    """Single stat panel with a PromQL target."""
    if thresholds is None:
        thresholds = [
            {"color": "green", "value": None},
            {"color": "yellow", "value": 70},
            {"color": "red", "value": 90},
        ]
    return {
        "id": panel_id,
        "type": "stat",
        "title": title,
        "gridPos": {"x": x, "y": y, "w": STAT_W, "h": STAT_H},
        "options": {
            "reduceOptions": {"calcs": ["lastNotNull"]},
            "orientation": "auto",
            "textMode": "auto",
            "colorMode": "background",
            "graphMode": "none",
        },
        "fieldConfig": {
            "defaults": {
                "unit": unit,
                "decimals": decimals,
                "thresholds": {
                    "mode": "absolute",
                    "steps": thresholds,
                },
                "color": {"mode": "thresholds"},
            },
            "overrides": [],
        },
        "targets": [
            {
                "datasource": {"type": "prometheus", "uid": "${datasource}"},
                "expr": promql,
                "instant": True,
                "legendFormat": "",
            }
        ],
        "datasource": {"type": "prometheus", "uid": "${datasource}"},
    }


def _disk_table_panel(panel_id: int, instance: str, x: int, y: int) -> dict:
    """
    Table panel showing all real mountpoints for a host.
    Columns: mountpoint, size, used, available, used%.
    Filtered to exclude snap/tmpfs/overlay noise.
    """
    label = f'instance="{instance}"'
    fsfilter = f'fstype!~"{EXCLUDE_FSTYPES}"'

    # PromQL for each column — Grafana table joins on matching labels
    size_expr    = f'node_filesystem_size_bytes{{{label},{fsfilter}}}'
    avail_expr   = f'node_filesystem_avail_bytes{{{label},{fsfilter}}}'
    used_pct_expr = (
        f'100 - ((node_filesystem_avail_bytes{{{label},{fsfilter}}} / '
        f'node_filesystem_size_bytes{{{label},{fsfilter}}}) * 100)'
    )

    return {
        "id": panel_id,
        "type": "table",
        "title": "Disk",
        "gridPos": {"x": x, "y": y, "w": DISK_W, "h": DISK_H},
        "options": {
            "sortBy": [{"displayName": "Mountpoint"}],
            "cellHeight": "sm",
            "footer": {"show": False},
        },
        "fieldConfig": {
            "defaults": {
                "custom": {
                    "align": "auto",
                    "displayMode": "auto",
                },
            },
            "overrides": [
                {
                    "matcher": {"id": "byName", "options": "Size"},
                    "properties": [{"id": "unit", "value": "bytes"}],
                },
                {
                    "matcher": {"id": "byName", "options": "Available"},
                    "properties": [{"id": "unit", "value": "bytes"}],
                },
                {
                    "matcher": {"id": "byName", "options": "Used %"},
                    "properties": [
                        {"id": "unit", "value": "percent"},
                        {"id": "decimals", "value": 1},
                        {
                            "id": "custom.displayMode",
                            "value": "color-background",
                        },
                        {
                            "id": "thresholds",
                            "value": {
                                "mode": "absolute",
                                "steps": [
                                    {"color": "green", "value": None},
                                    {"color": "yellow", "value": 70},
                                    {"color": "red", "value": 90},
                                ],
                            },
                        },
                        {"id": "color", "value": {"mode": "thresholds"}},
                    ],
                },
            ],
        },
        "transformations": [
            {
                "id": "merge",
                "options": {},
            },
            {
                "id": "organize",
                "options": {
                    "renameByName": {
                        "mountpoint": "Mountpoint",
                        "Value #A": "Size",
                        "Value #B": "Available",
                        "Value #C": "Used %",
                    },
                    "excludeByName": {
                        "Time": True,
                        "instance": True,
                        "job": True,
                        "fstype": True,
                        "device": True,
                    },
                },
            },
        ],
        "targets": [
            {
                "datasource": {"type": "prometheus", "uid": "${datasource}"},
                "expr": size_expr,
                "instant": True,
                "legendFormat": "",
                "refId": "A",
                "format": "table",
            },
            {
                "datasource": {"type": "prometheus", "uid": "${datasource}"},
                "expr": avail_expr,
                "instant": True,
                "legendFormat": "",
                "refId": "B",
                "format": "table",
            },
            {
                "datasource": {"type": "prometheus", "uid": "${datasource}"},
                "expr": used_pct_expr,
                "instant": True,
                "legendFormat": "",
                "refId": "C",
                "format": "table",
            },
        ],
        "datasource": {"type": "prometheus", "uid": "${datasource}"},
    }


def _row_panel(panel_id: int, title: str, y: int) -> dict:
    return {
        "id": panel_id,
        "type": "row",
        "title": title,
        "collapsed": False,
        "gridPos": {"x": 0, "y": y, "w": 24, "h": ROW_H},
        "panels": [],
    }


def _build_dashboard_json(snapshot: dict, prom_url: str) -> dict:
    """
    Build a Grafana dashboard JSON from the fleet snapshot.
    Per host: row header, four stat panels, disk table.
    """
    hosts = snapshot.get("hosts", [])
    panels = []
    panel_id = 1
    y = 0

    for instance in hosts:
        hostname = instance.split(":")[0]
        label = f'instance="{instance}"'

        # ── Row header ──────────────────────────────────────────────────────
        panels.append(_row_panel(panel_id, hostname.upper(), y))
        panel_id += 1
        y += ROW_H

        # ── Stat panels ─────────────────────────────────────────────────────
        cpu_expr = (
            f'100 - (avg by(instance)(rate(node_cpu_seconds_total{{{label},mode="idle"}}[5m])) * 100)'
        )
        mem_expr = (
            f'100 - ((node_memory_MemAvailable_bytes{{{label}}} / '
            f'node_memory_MemTotal_bytes{{{label}}}) * 100)'
        )
        # Load ratio: load1 / logical CPU count — 1.0 = fully loaded
        load_expr = (
            f'node_load1{{{label}}} / '
            f'count without(cpu,mode)(node_cpu_seconds_total{{{label},mode="idle"}})'
        )
        uptime_expr = f'time() - node_boot_time_seconds{{{label}}}'

        panels.append(_stat_panel(
            panel_id, "CPU", cpu_expr, "percent", x=0, y=y,
        ))
        panel_id += 1

        panels.append(_stat_panel(
            panel_id, "Memory", mem_expr, "percent", x=STAT_W, y=y,
        ))
        panel_id += 1

        panels.append(_stat_panel(
            panel_id, "Load", load_expr, "short", x=STAT_W * 2, y=y,
            thresholds=[
                {"color": "green", "value": None},
                {"color": "yellow", "value": 0.7},
                {"color": "red", "value": 1.0},
            ],
            decimals=2,
        ))
        panel_id += 1

        panels.append(_stat_panel(
            panel_id, "Uptime", uptime_expr, "s", x=STAT_W * 3, y=y,
            thresholds=[{"color": "blue", "value": None}],
            decimals=0,
        ))
        panel_id += 1
        y += STAT_H

        # ── Disk table ───────────────────────────────────────────────────────
        panels.append(_disk_table_panel(panel_id, instance, x=0, y=y))
        panel_id += 1
        y += DISK_H

    generated_at = snapshot.get("generated_at", int(time.time()))

    return {
        "__inputs": [
            {
                "name": "datasource",
                "label": "Prometheus",
                "description": "",
                "type": "datasource",
                "pluginId": "prometheus",
                "pluginName": "Prometheus",
            }
        ],
        "__requires": [],
        "annotations": {"list": []},
        "description": f"bis-theseus aggroboard — auto-generated fleet overview. {generated_at}",
        "editable": False,
        "graphTooltip": 0,
        "id": None,
        "links": [],
        "panels": panels,
        "refresh": "1m",
        "schemaVersion": 38,
        "tags": ["bis-theseus", "aggroboard"],
        "templating": {
            "list": [
                {
                    "current": {},
                    "hide": 0,
                    "includeAll": False,
                    "name": "datasource",
                    "options": [],
                    "query": "prometheus",
                    "refresh": 1,
                    "type": "datasource",
                    "label": "Prometheus",
                }
            ]
        },
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

    def __init__(
        self,
        prom_url: str,
        loki_url: str,
        dashboard_path: Path,
        interval: int,
    ):
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

        if snapshot.get("hosts") == self._previous.get("hosts"):
            logger.debug("aggroboard: no change, skipping write")
            return

        dashboard = _build_dashboard_json(snapshot, self.prom_url)
        self.dashboard_path.write_text(json.dumps(dashboard, indent=2))
        host_count = len(snapshot.get("hosts", []))
        logger.info(f"aggroboard: wrote dashboard ({host_count} hosts)")
        self._previous = snapshot
