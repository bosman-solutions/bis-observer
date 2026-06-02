"""
links.py — Grafana deeplink generator for bis-theseus.

Builds Grafana Explore URLs for hosts, services, and containers.
Links are generated once on discovery and stored in the sidecar.

Grafana Explore URL shape:
  /explore?orgId=1&left=<base64url(JSON)>

The left param encodes datasource, queries, and time range.
"""

import base64
import json


def _encode_explore(datasource_uid: str, queries: list[dict], time_range: dict | None = None) -> str:
    """Encode an Explore state blob to base64url."""
    if time_range is None:
        time_range = {"from": "now-1h", "to": "now"}

    state = {
        "datasource": datasource_uid,
        "queries": queries,
        "range": time_range,
    }
    raw = json.dumps(state, separators=(",", ":"))
    encoded = base64.urlsafe_b64encode(raw.encode()).decode().rstrip("=")
    return encoded


def host_explore_url(
    grafana_url: str,
    datasource_uid: str,
    instance: str,
) -> str:
    """
    Grafana Explore deeplink for a host — cpu, memory, load, disk used%.
    instance: Prometheus instance label, e.g. "melchior" or "melchior:9100"
    """
    label = f'instance="{instance}"'
    fsfilter = 'fstype!~"tmpfs|squashfs|overlay|devtmpfs|ramfs|efivarfs|fuse.lxcfs"'

    queries = [
        {
            "refId": "CPU",
            "expr": f'100 - (avg by(instance)(rate(node_cpu_seconds_total{{{label},mode="idle"}}[5m])) * 100)',
            "legendFormat": "CPU %",
            "instant": False,
        },
        {
            "refId": "MEM",
            "expr": (
                f'100 - ((node_memory_MemAvailable_bytes{{{label}}} / '
                f'node_memory_MemTotal_bytes{{{label}}}) * 100)'
            ),
            "legendFormat": "Memory %",
            "instant": False,
        },
        {
            "refId": "LOAD",
            "expr": (
                f'node_load1{{{label}}} / '
                f'count without(cpu,mode)(node_cpu_seconds_total{{{label},mode="idle"}})'
            ),
            "legendFormat": "Load ratio",
            "instant": False,
        },
        {
            "refId": "DISK",
            "expr": (
                f'100 - ((node_filesystem_avail_bytes{{{label},{fsfilter}}} / '
                f'node_filesystem_size_bytes{{{label},{fsfilter}}}) * 100)'
            ),
            "legendFormat": "Disk % {{mountpoint}}",
            "instant": False,
        },
    ]

    encoded = _encode_explore(datasource_uid, queries)
    return f"{grafana_url}/explore?orgId=1&left={encoded}"


def service_explore_url(
    grafana_url: str,
    datasource_uid: str,
    instance: str,
    project: str,
) -> str:
    """
    Grafana Explore deeplink for a Docker Compose stack.
    Shows container count, running count, aggregate cpu and memory.
    """
    hostname = instance.split(":")[0]
    base = f'compose_project="{project}",node="{hostname}"'

    queries = [
        {
            "refId": "CONTAINERS",
            "expr": f'count(docker_container_info{{{base}}})',
            "legendFormat": "Total containers",
            "instant": False,
        },
        {
            "refId": "RUNNING",
            "expr": f'count(docker_container_info{{{base},state="running"}})',
            "legendFormat": "Running",
            "instant": False,
        },
    ]

    encoded = _encode_explore(datasource_uid, queries)
    return f"{grafana_url}/explore?orgId=1&left={encoded}"


def container_explore_url(
    grafana_url: str,
    datasource_uid: str,
    instance: str,
    container_name: str,
) -> str:
    """
    Grafana Explore deeplink for a single container.
    Shows cpu and memory usage timeseries.
    """
    hostname = instance.split(":")[0]
    clabel = f'name="{container_name}",instance=~"{hostname}.*"'

    queries = [
        {
            "refId": "CPU",
            "expr": f'rate(container_cpu_usage_seconds_total{{{clabel}}}[5m]) * 100',
            "legendFormat": "CPU %",
            "instant": False,
        },
        {
            "refId": "MEM",
            "expr": f'container_memory_usage_bytes{{{clabel}}}',
            "legendFormat": "Memory bytes",
            "instant": False,
        },
    ]

    encoded = _encode_explore(datasource_uid, queries)
    return f"{grafana_url}/explore?orgId=1&left={encoded}"
