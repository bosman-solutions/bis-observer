"""
aggroboard.py — fleet snapshot writer for bis-theseus.

Heartbeat loop:
  1. Fetch current host list from Prometheus
  2. Compare against aggroboard_hosts.json sidecar
  3. If hosts changed — write new aggroboard.json + update sidecar
  4. If same — do nothing (dashboard stays stable, Grafana won't prompt)

Sidecar schema:
  {
    "version": 1,
    "updated_at": 1780433206,
    "hosts": {
      "melchior": {
        "instance": "melchior",
        "explore_url": "http://melchior:3000/explore?..."
      }
    }
  }

Hosts become a dict keyed by hostname. Links are generated once on discovery.
"""

import asyncio
import json
import logging
import time
from pathlib import Path

import httpx

from .links import host_explore_url

logger = logging.getLogger(__name__)

EXCLUDE_FSTYPES = "tmpfs|squashfs|overlay|devtmpfs|ramfs|efivarfs|fuse.lxcfs"

STAT_W = 6
STAT_H = 4
DISK_W = 24
DISK_H = 6
ROW_H  = 2


# ── Grafana datasource lookup ─────────────────────────────────────────────────

async def _get_prometheus_uid(grafana_url: str, token: str) -> str | None:
    if not token:
        return None
    try:
        async with httpx.AsyncClient() as client:
            resp = await client.get(
                f"{grafana_url}/api/datasources",
                headers={"Authorization": f"Bearer {token}"},
                timeout=5.0,
            )
            resp.raise_for_status()
            for ds in resp.json():
                if ds.get("type") == "prometheus":
                    uid = ds.get("uid")
                    logger.info(f"resolved Prometheus datasource UID: {uid}")
                    return uid
    except Exception as e:
        logger.warning(f"could not resolve Prometheus datasource UID: {e}")
    return None


# ── Sidecar helpers ───────────────────────────────────────────────────────────

def _read_sidecar(path: Path) -> dict:
    try:
        return json.loads(path.read_text())
    except Exception:
        return {"version": 0, "updated_at": 0, "hosts": {}}


def _write_sidecar(path: Path, version: int, hosts: dict) -> None:
    path.write_text(json.dumps({
        "version": version,
        "updated_at": int(time.time()),
        "hosts": hosts,
    }, indent=2))


# ── Prometheus instance discovery ─────────────────────────────────────────────

async def _get_known_instances(
    client: httpx.AsyncClient, prom_url: str
) -> list[str]:
    try:
        resp = await client.get(
            f"{prom_url}/api/v1/query",
            params={"query": 'up{job=~"node.*|node-exporter.*"}'},
            timeout=10.0,
        )
        resp.raise_for_status()
        results = resp.json().get("data", {}).get("result", [])
        return sorted([
            r["metric"].get("instance", "")
            for r in results
            if r["metric"].get("instance")
        ])
    except Exception as e:
        logger.warning(f"failed to fetch known instances: {e}")
        return []


# ── Panel builders ────────────────────────────────────────────────────────────

def _datasource_ref(uid: str | None) -> dict:
    if uid:
        return {"type": "prometheus", "uid": uid}
    return {"type": "prometheus", "uid": "${datasource}"}


def _stat_panel(
    panel_id: int,
    title: str,
    promql: str,
    unit: str,
    x: int,
    y: int,
    ds_ref: dict,
    thresholds: list[dict] | None = None,
    decimals: int = 1,
) -> dict:
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
                "thresholds": {"mode": "absolute", "steps": thresholds},
                "color": {"mode": "thresholds"},
            },
            "overrides": [],
        },
        "targets": [
            {
                "datasource": ds_ref,
                "expr": promql,
                "instant": True,
                "legendFormat": "",
            }
        ],
        "datasource": ds_ref,
    }


def build_fs_table(
    panel_id: int,
    title: str,
    x: int,
    y: int,
    w: int,
    h: int,
    size_expr: str,
    avail_expr: str,
    used_pct_expr: str,
    ds_ref: dict,
    sort_by: str = "Mountpoint",
) -> dict:
    """
    Generic filesystem table panel.

    Three queries: size, available, used%. filterFieldsByName strips device/fstype
    noise before merge so all three join cleanly on mountpoint alone.
    Reusable for host disks, container volumes, or any mountpoint-keyed data.

    Columns: Mountpoint | Used % | Available | Size
    """
    return {
        "id": panel_id,
        "type": "table",
        "title": title,
        "gridPos": {"x": x, "y": y, "w": w, "h": h},
        "options": {
            "sortBy": [{"displayName": sort_by}],
            "cellHeight": "sm",
            "footer": {"show": False},
        },
        "fieldConfig": {
            "defaults": {"custom": {"align": "auto", "displayMode": "auto"}},
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
                        {"id": "custom.displayMode", "value": "color-background"},
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
            # Keep only mountpoint and values — strips device/fstype/instance
            # label differences that prevent merge from joining rows correctly.
            {
                "id": "filterFieldsByName",
                "options": {
                    "include": {"pattern": "^(mountpoint|Value #A|Value #B|Value #C|Time)$"},
                },
            },
            {
                "id": "merge",
                "options": {"reducers": []},
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
                    "indexByName": {
                        "mountpoint": 0,
                        "Value #C": 1,
                        "Value #B": 2,
                        "Value #A": 3,
                    },
                    "excludeByName": {
                        "Time": True,
                    },
                },
            },
        ],
        "targets": [
            {
                "datasource": ds_ref,
                "expr": size_expr,
                "instant": True,
                "legendFormat": "{{mountpoint}}",
                "refId": "A",
                "format": "table",
            },
            {
                "datasource": ds_ref,
                "expr": avail_expr,
                "instant": True,
                "legendFormat": "{{mountpoint}}",
                "refId": "B",
                "format": "table",
            },
            {
                "datasource": ds_ref,
                "expr": used_pct_expr,
                "instant": True,
                "legendFormat": "{{mountpoint}}",
                "refId": "C",
                "format": "table",
            },
        ],
        "datasource": ds_ref,
    }


def _disk_table_panel(
    panel_id: int, instance: str, x: int, y: int, ds_ref: dict
) -> dict:
    label    = f'instance="{instance}"'
    fsfilter = f'fstype!~"{EXCLUDE_FSTYPES}"'

    return build_fs_table(
        panel_id     = panel_id,
        title        = "Disk",
        x=x, y=y,
        w=DISK_W, h=DISK_H,
        size_expr    = f'node_filesystem_size_bytes{{{label},{fsfilter}}}',
        avail_expr   = f'node_filesystem_avail_bytes{{{label},{fsfilter}}}',
        used_pct_expr= (
            f'100 - ((node_filesystem_avail_bytes{{{label},{fsfilter}}} / '
            f'node_filesystem_size_bytes{{{label},{fsfilter}}}) * 100)'
        ),
        ds_ref       = ds_ref,
    )


def _row_panel(panel_id: int, title: str, y: int, link: str = "") -> dict:
    panel = {
        "id": panel_id,
        "type": "row",
        "title": title,
        "collapsed": False,
        "gridPos": {"x": 0, "y": y, "w": 24, "h": ROW_H},
        "panels": [],
    }
    if link:
        panel["links"] = [{"title": f"Explore {title.lower()} in Grafana", "url": link, "targetBlank": True}]
    return panel


def _link_panel(panel_id: int, hostname: str, explore_url: str, y: int) -> dict:
    """Slim full-width text panel with Grafana Explore deeplink."""
    return {
        "id": panel_id,
        "type": "text",
        "title": "",
        "gridPos": {"x": 0, "y": y, "w": 24, "h": 2},
        "options": {
            "mode": "markdown",
            "content": f"[📊 Explore {hostname} in Grafana →]({explore_url})",
        },
        "transparent": True,
    }


def _build_dashboard_json(hosts: dict, version: int, ds_ref: dict) -> dict:
    panels = []
    panel_id = 1
    y = 0

    for hostname, host_data in sorted(hosts.items()):
        instance = host_data.get("instance", hostname)
        label    = f'instance="{instance}"'

        explore_url = host_data.get("explore_url", "")
        panels.append(_row_panel(panel_id, hostname.upper(), y, link=explore_url))
        panel_id += 1
        y += ROW_H

        cpu_expr = (
            f'100 - (avg by(instance)(rate(node_cpu_seconds_total{{{label},mode="idle"}}[5m])) * 100)'
        )
        mem_expr = (
            f'100 - ((node_memory_MemAvailable_bytes{{{label}}} / '
            f'node_memory_MemTotal_bytes{{{label}}}) * 100)'
        )
        load_expr = (
            f'node_load1{{{label}}} / '
            f'count without(cpu,mode)(node_cpu_seconds_total{{{label},mode="idle"}})'
        )
        uptime_expr = f'time() - node_boot_time_seconds{{{label}}}'

        panels.append(_stat_panel(
            panel_id, "CPU", cpu_expr, "percent", x=0, y=y, ds_ref=ds_ref,
        ))
        panel_id += 1

        panels.append(_stat_panel(
            panel_id, "Memory", mem_expr, "percent", x=STAT_W, y=y, ds_ref=ds_ref,
        ))
        panel_id += 1

        panels.append(_stat_panel(
            panel_id, "Load", load_expr, "short", x=STAT_W * 2, y=y, ds_ref=ds_ref,
            thresholds=[
                {"color": "green", "value": None},
                {"color": "yellow", "value": 0.7},
                {"color": "red", "value": 1.0},
            ],
            decimals=2,
        ))
        panel_id += 1

        panels.append(_stat_panel(
            panel_id, "Uptime", uptime_expr, "s", x=STAT_W * 3, y=y, ds_ref=ds_ref,
            thresholds=[{"color": "blue", "value": None}],
            decimals=0,
        ))
        panel_id += 1
        y += STAT_H

        panels.append(_disk_table_panel(panel_id, instance, x=0, y=y, ds_ref=ds_ref))
        panel_id += 1
        y += DISK_H

    return {
        "__inputs": [],
        "__requires": [],
        "annotations": {"list": []},
        "description": "bis-theseus aggroboard — auto-generated fleet overview.",
        "editable": True,
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
        "version": version,
    }


# ── Aggroboard class ──────────────────────────────────────────────────────────

class Aggroboard:
    def __init__(
        self,
        prom_url: str,
        loki_url: str,
        grafana_url: str,
        grafana_ext_url: str,
        grafana_token: str,
        dashboard_path: Path,
        interval: int,
    ):
        self.prom_url        = prom_url
        self.loki_url        = loki_url
        self.grafana_url     = grafana_url
        self.grafana_ext_url = grafana_ext_url
        self.grafana_token   = grafana_token
        self.dashboard_path  = dashboard_path
        self.sidecar_path    = dashboard_path.parent / "aggroboard_hosts.json"
        self.interval        = interval
        self._ds_uid: str | None = None
        self._ds_ref: dict   = {"type": "prometheus", "uid": "${datasource}"}

    async def _resolve_datasource(self):
        uid = await _get_prometheus_uid(self.grafana_url, self.grafana_token)
        if uid:
            self._ds_uid = uid
            self._ds_ref = {"type": "prometheus", "uid": uid}
            logger.info(f"datasource locked to uid: {uid}")
        else:
            logger.warning("no token or lookup failed — using ${datasource} fallback")

    async def run(self):
        logger.info(
            f"aggroboard starting — interval {self.interval}s, "
            f"writing to {self.dashboard_path}"
        )
        await self._resolve_datasource()
        while True:
            try:
                await self._tick()
            except Exception as e:
                logger.error(f"aggroboard tick failed: {e}")
            await asyncio.sleep(self.interval)

    async def _tick(self):
        async with httpx.AsyncClient() as client:
            current_instances = await _get_known_instances(client, self.prom_url)

        sidecar    = _read_sidecar(self.sidecar_path)
        prev_hosts = sidecar.get("hosts", {})
        prev_set   = set(prev_hosts.keys())
        curr_set   = set(current_instances)

        if curr_set == prev_set:
            logger.debug("aggroboard: hosts unchanged, skipping write")
            return

        added   = curr_set - prev_set
        removed = prev_set - curr_set

        # Build new hosts dict — keep existing entries, add new, drop removed
        new_hosts = {k: v for k, v in prev_hosts.items() if k not in removed}

        for instance in added:
            hostname = instance.split(":")[0]
            explore_url = host_explore_url(
                grafana_url    = self.grafana_ext_url,
                datasource_uid = self._ds_uid or "prometheus",
                instance       = instance,
            )
            new_hosts[hostname] = {
                "instance": instance,
                "explore_url": explore_url,
            }
            logger.info(f"aggroboard: new host discovered — {hostname} ({instance})")

        version   = sidecar.get("version", 0) + 1
        dashboard = _build_dashboard_json(new_hosts, version, self._ds_ref)

        self.dashboard_path.write_text(json.dumps(dashboard, indent=2))
        _write_sidecar(self.sidecar_path, version, new_hosts)

        logger.info(
            f"aggroboard: wrote dashboard v{version} "
            f"({len(new_hosts)} hosts"
            + (f", +{sorted(added)}" if added else "")
            + (f", -{sorted(removed)}" if removed else "")
            + ")"
        )

    def get_host(self, hostname: str) -> dict | None:
        """Return sidecar entry for a host. Used by API routes."""
        sidecar = _read_sidecar(self.sidecar_path)
        return sidecar.get("hosts", {}).get(hostname)

    def get_all_hosts(self) -> dict:
        """Return all host entries from sidecar."""
        return _read_sidecar(self.sidecar_path).get("hosts", {})
