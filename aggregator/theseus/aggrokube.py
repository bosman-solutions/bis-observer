"""
aggrokube.py — kubernetes cluster dashboard writer for bis-theseus.

Mirrors the aggroboard heartbeat pattern but sources from kube-state-metrics
via Prometheus. Writes aggrokube.json into the shared Grafana provisioning
directory alongside aggroboard.json.

Layout:
  ROW: CLUSTER OVERVIEW  — nodes ready, pods running, deployments ok, puzzu replicas
  ROW: <node>            — per node: ready status, pod count, allocatable RAM/CPU
  ROW: <namespace>       — per namespace (non-system): deployment replicas, pod phase table

Sidecar schema (aggrokube_state.json):
  {
    "version": 1,
    "updated_at": 1781446000,
    "nodes": ["cerberus", "fido", "princess", "rex"],
    "namespaces": ["arcade", "monitoring"]
  }
"""

import asyncio
import json
import logging
import time
from pathlib import Path

import httpx

logger = logging.getLogger(__name__)

# Panel geometry — matches aggroboard conventions
STAT_W  = 6
STAT_H  = 4
ROW_H   = 2
TABLE_W = 24
TABLE_H = 6

# Namespaces we don't care about in the user-facing dashboard
SYSTEM_NAMESPACES = {"kube-system", "kube-public", "kube-node-lease"}


# ── Prometheus query helper ───────────────────────────────────────────────────

async def _query(client: httpx.AsyncClient, prom_url: str, promql: str) -> list[dict]:
    try:
        resp = await client.get(
            f"{prom_url}/api/v1/query",
            params={"query": promql},
            timeout=10.0,
        )
        resp.raise_for_status()
        return resp.json().get("data", {}).get("result", [])
    except Exception as e:
        logger.warning(f"kube query failed ({promql[:60]}): {e}")
        return []


# ── Cluster discovery ─────────────────────────────────────────────────────────

async def _discover_cluster(
    client: httpx.AsyncClient, prom_url: str
) -> tuple[list[str], list[str]]:
    """Return (node_names, namespace_names) from live KSM data."""
    node_results = await _query(client, prom_url, "kube_node_info")
    nodes = sorted({r["metric"].get("node", "") for r in node_results if r["metric"].get("node")})

    ns_results = await _query(client, prom_url, "kube_namespace_labels")
    # fall back to pod_info namespaces if namespace_labels isn't available
    if not ns_results:
        ns_results = await _query(client, prom_url, "kube_pod_info")
        namespaces = sorted({
            r["metric"].get("namespace", "")
            for r in ns_results
            if r["metric"].get("namespace") and r["metric"].get("namespace") not in SYSTEM_NAMESPACES
        })
    else:
        namespaces = sorted({
            r["metric"].get("namespace", "")
            for r in ns_results
            if r["metric"].get("namespace") and r["metric"].get("namespace") not in SYSTEM_NAMESPACES
        })

    return nodes, namespaces


# ── Sidecar helpers ───────────────────────────────────────────────────────────

def _read_sidecar(path: Path) -> dict:
    try:
        return json.loads(path.read_text())
    except Exception:
        return {"version": 0, "updated_at": 0, "nodes": [], "namespaces": []}


def _write_sidecar(path: Path, version: int, nodes: list[str], namespaces: list[str]) -> None:
    path.write_text(json.dumps({
        "version": version,
        "updated_at": int(time.time()),
        "nodes": nodes,
        "namespaces": namespaces,
    }, indent=2))


# ── Panel builders ────────────────────────────────────────────────────────────

def _row(panel_id: int, title: str, y: int) -> dict:
    return {
        "id": panel_id,
        "type": "row",
        "title": title,
        "collapsed": False,
        "gridPos": {"x": 0, "y": y, "w": 24, "h": ROW_H},
        "panels": [],
    }


def _stat(
    panel_id: int,
    title: str,
    expr: str,
    unit: str,
    x: int,
    y: int,
    ds_ref: dict,
    thresholds: list[dict] | None = None,
    decimals: int = 0,
    instant: bool = True,
) -> dict:
    if thresholds is None:
        thresholds = [
            {"color": "green", "value": None},
            {"color": "yellow", "value": 0.7},
            {"color": "red",    "value": 0.9},
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
        "targets": [{"datasource": ds_ref, "expr": expr, "instant": instant, "legendFormat": ""}],
        "datasource": ds_ref,
    }

def _pod_phase_table(panel_id: int, namespace: str, x: int, y: int, ds_ref: dict) -> dict:
    """Table showing pod name, phase, and ready state for a namespace."""
    return {
        "id": panel_id,
        "type": "table",
        "title": f"{namespace} — pods",
        "gridPos": {"x": x, "y": y, "w": TABLE_W, "h": TABLE_H},
        "options": {"cellHeight": "sm", "footer": {"show": False}},
        "fieldConfig": {
            "defaults": {"custom": {"align": "auto", "displayMode": "auto"}},
            "overrides": [
                {
                    "matcher": {"id": "byName", "options": "Ready"},
                    "properties": [
                        {"id": "custom.displayMode", "value": "color-background"},
                        {
                            "id": "thresholds",
                            "value": {
                                "mode": "absolute",
                                "steps": [
                                    {"color": "red",   "value": None},
                                    {"color": "green", "value": 1},
                                ],
                            },
                        },
                        {"id": "color", "value": {"mode": "thresholds"}},
                        {"id": "mappings", "value": [
                            {"type": "value", "options": {"1": {"text": "yes"}, "0": {"text": "no"}}},
                        ]},
                    ],
                },
            ],
        },
        "transformations": [
            {"id": "filterFieldsByName", "options": {"include": {"pattern": "^(pod|phase|Value|Time)$"}}},
            {"id": "merge", "options": {"reducers": []}},
            {"id": "organize", "options": {
                "renameByName": {"pod": "Pod", "phase": "Phase", "Value": "Ready"},
                "indexByName": {"pod": 0, "phase": 1, "Value": 2},
                "excludeByName": {"Time": True},
            }},
        ],
        "targets": [
            {
                "datasource": ds_ref,
                "expr": f'kube_pod_status_ready{{namespace="{namespace}",condition="true"}}',
                "instant": True,
                "legendFormat": "{{{{pod}}}}",
                "refId": "A",
                "format": "table",
            }
        ],
        "datasource": ds_ref,
    }


# ── Dashboard builder ─────────────────────────────────────────────────────────

def _build_dashboard(
    nodes: list[str],
    namespaces: list[str],
    version: int,
    ds_ref: dict,
) -> dict:
    panels = []
    pid = 1
    y = 0

    # ── CLUSTER OVERVIEW row ──────────────────────────────────────────────────
    panels.append(_row(pid, "CLUSTER OVERVIEW", y)); pid += 1; y += ROW_H

    panels.append(_stat(pid, "Nodes Ready", 'count(kube_node_status_condition{condition="Ready",status="true"})',
        "short", x=0, y=y, ds_ref=ds_ref,
        thresholds=[{"color": "green", "value": None}])); pid += 1

    panels.append(_stat(pid, "Pods Running",
        'count(kube_pod_status_phase{phase="Running"})',
        "short", x=STAT_W, y=y, ds_ref=ds_ref,
        thresholds=[{"color": "green", "value": None}])); pid += 1

    panels.append(_stat(pid, "Deployments Available",
        'sum(kube_deployment_status_replicas_available) / sum(kube_deployment_status_replicas)',
        "percentunit", x=STAT_W*2, y=y, ds_ref=ds_ref, decimals=0,
        thresholds=[
            {"color": "red",    "value": None},
            {"color": "yellow", "value": 0.5},
            {"color": "green",  "value": 1.0},
        ])); pid += 1

    panels.append(_stat(pid, "puzzu replicas",
        'kube_deployment_status_replicas_ready{namespace="arcade",deployment="puzzu"}'
        ' / kube_deployment_status_replicas{namespace="arcade",deployment="puzzu"}',
        "percentunit", x=STAT_W*3, y=y, ds_ref=ds_ref, decimals=0,
        thresholds=[
            {"color": "red",    "value": None},
            {"color": "yellow", "value": 0.5},
            {"color": "green",  "value": 1.0},
        ])); pid += 1

    y += STAT_H

    # ── Per-node rows ─────────────────────────────────────────────────────────
    for node in nodes:
        role = "control-plane" if node == "cerberus" else "worker"
        panels.append(_row(pid, f"{node.upper()}  [{role}]", y)); pid += 1; y += ROW_H

        panels.append(_stat(pid, "Node Ready",
            f'kube_node_status_condition{{node="{node}",condition="Ready",status="true"}}',
            "short", x=0, y=y, ds_ref=ds_ref,
            thresholds=[
                {"color": "red",   "value": None},
                {"color": "green", "value": 1},
            ],
            decimals=0)); pid += 1

        panels.append(_stat(pid, "Pods Scheduled",
            f'count(kube_pod_info{{node="{node}"}})',
            "short", x=STAT_W, y=y, ds_ref=ds_ref,
            thresholds=[{"color": "blue", "value": None}])); pid += 1

        panels.append(_stat(pid, "Allocatable RAM",
            f'kube_node_status_allocatable{{node="{node}",resource="memory",unit="byte"}}',
            "bytes", x=STAT_W*2, y=y, ds_ref=ds_ref,
            thresholds=[{"color": "purple", "value": None}])); pid += 1

        panels.append(_stat(pid, "Allocatable CPU",
            f'kube_node_status_allocatable{{node="{node}",resource="cpu",unit="core"}}',
            "short", x=STAT_W*3, y=y, ds_ref=ds_ref,
            thresholds=[{"color": "purple", "value": None}],
            decimals=0)); pid += 1

        y += STAT_H

    # ── Per-namespace rows ────────────────────────────────────────────────────
    for ns in namespaces:
        panels.append(_row(pid, f"NS: {ns.upper()}", y)); pid += 1; y += ROW_H

        panels.append(_stat(pid, "Replicas Ready",
            f'sum(kube_deployment_status_replicas_ready{{namespace="{ns}"}})'
            f' / sum(kube_deployment_status_replicas{{namespace="{ns}"}})',
            "percentunit", x=0, y=y, ds_ref=ds_ref, decimals=0,
            thresholds=[
                {"color": "red",    "value": None},
                {"color": "yellow", "value": 0.5},
                {"color": "green",  "value": 1.0},
            ])); pid += 1

        panels.append(_stat(pid, "Pods Running",
            f'count(kube_pod_status_phase{{namespace="{ns}",phase="Running"}})',
            "short", x=STAT_W, y=y, ds_ref=ds_ref,
            thresholds=[{"color": "green", "value": None}])); pid += 1

        y += STAT_H

        panels.append(_pod_phase_table(pid, ns, x=0, y=y, ds_ref=ds_ref)); pid += 1
        y += TABLE_H

    return {
        "__inputs": [], "__requires": [],
        "annotations": {"list": []},
        "description": "bis-theseus aggrokube — auto-generated kubernetes cluster overview.",
        "editable": True,
        "graphTooltip": 0,
        "id": None,
        "links": [],
        "panels": panels,
        "refresh": "1m",
        "schemaVersion": 38,
        "tags": ["bis-theseus", "aggrokube"],
        "templating": {"list": []},
        "time": {"from": "now-1h", "to": "now"},
        "timepicker": {},
        "timezone": "browser",
        "title": "bis-aggrokube",
        "uid": "bis-aggrokube",
        "version": version,
    }


# ── Aggrokube class ───────────────────────────────────────────────────────────

class Aggrokube:
    def __init__(
        self,
        prom_url: str,
        grafana_url: str,
        grafana_ext_url: str,
        grafana_token: str,
        dashboard_path: Path,
        interval: int,
    ):
        self.prom_url        = prom_url
        self.grafana_url     = grafana_url
        self.grafana_ext_url = grafana_ext_url
        self.grafana_token   = grafana_token
        self.dashboard_path  = dashboard_path
        self.sidecar_path    = dashboard_path.parent / "aggrokube_state.json"
        self.interval        = interval
        self._ds_ref: dict   = {"type": "prometheus", "uid": "${datasource}"}

    async def _resolve_datasource(self) -> None:
        if not self.grafana_token:
            return
        try:
            async with httpx.AsyncClient() as client:
                resp = await client.get(
                    f"{self.grafana_url}/api/datasources",
                    headers={"Authorization": f"Bearer {self.grafana_token}"},
                    timeout=5.0,
                )
                resp.raise_for_status()
                for ds in resp.json():
                    if ds.get("type") == "prometheus":
                        uid = ds.get("uid")
                        self._ds_ref = {"type": "prometheus", "uid": uid}
                        logger.info(f"aggrokube: resolved datasource uid {uid}")
                        return
        except Exception as e:
            logger.warning(f"aggrokube: datasource lookup failed: {e}")

    async def run(self) -> None:
        logger.info(
            f"aggrokube starting — interval {self.interval}s, "
            f"writing to {self.dashboard_path}"
        )
        await self._resolve_datasource()
        while True:
            try:
                await self._tick()
            except Exception as e:
                logger.error(f"aggrokube tick failed: {e}")
            await asyncio.sleep(self.interval)

    async def _tick(self) -> None:
        async with httpx.AsyncClient() as client:
            nodes, namespaces = await _discover_cluster(client, self.prom_url)

        if not nodes:
            logger.debug("aggrokube: no kube nodes found in Prometheus — skipping")
            return

        sidecar = _read_sidecar(self.sidecar_path)
        if sidecar["nodes"] == nodes and sidecar["namespaces"] == namespaces:
            logger.debug("aggrokube: cluster topology unchanged, skipping write")
            return

        version   = sidecar.get("version", 0) + 1
        dashboard = _build_dashboard(nodes, namespaces, version, self._ds_ref)

        self.dashboard_path.write_text(json.dumps(dashboard, indent=2))
        _write_sidecar(self.sidecar_path, version, nodes, namespaces)

        logger.info(
            f"aggrokube: wrote dashboard v{version} "
            f"({len(nodes)} nodes, {len(namespaces)} namespaces: {namespaces})"
        )
