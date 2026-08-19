"""
links.py — Grafana deeplink generator for bis-theseus.

Two families of link live here, and they are not interchangeable.

DETAIL links (detail_url and friends) point at the provisioned detail
dashboards — bis-node-detail, bis-container-detail — with the subject carried
in template variables. These are what the bis-starmap identity card links to.
They work for a Viewer, they carry no encoded query, and they survive a
datasource being renamed, because the dashboard picks its own datasource.

EXPLORE links (*_explore_url) encode a whole query set into the URL and drop
the reader into the query editor. They are the richer tool and the wrong
default: Explore requires the Editor role, and the viewers_can_edit escape
hatch was removed in Grafana 12. Anyone reading the map as an anonymous
Viewer cannot open one. Keep them for authenticated operators — the
aggroboard row links — and use the detail links for anything public.

Grafana Explore URL shape:
  /explore?orgId=1&left=<base64url(JSON)>

The left param encodes datasource, queries, and time range.
"""

import base64
import json
from urllib.parse import quote

# Dashboard UIDs — must match the "uid" field in the provisioned JSON under
# config/grafana/provisioning/dashboards/. Changing one here without changing
# it there yields a 404, so they are named constants rather than inline strings.
NODE_DASHBOARD_UID      = "bis-node-detail"
CONTAINER_DASHBOARD_UID = "bis-container-detail"

DEFAULT_FROM = "now-3h"
DEFAULT_TO   = "now"


def _detail_url(
    grafana_url: str,
    uid: str,
    slug: str,
    variables: dict[str, str],
    time_from: str = DEFAULT_FROM,
    time_to: str = DEFAULT_TO,
) -> str:
    """
    Build a dashboard deeplink: /d/<uid>/<slug>?var-x=y&from=…&to=…

    No datasource UID is embedded. The dashboard carries a datasource-type
    template variable that defaults to the org default, so a fork with a
    differently-provisioned Prometheus gets working links for free.
    """
    params = [f"var-{k}={quote(str(v), safe='')}" for k, v in variables.items()]
    params += [f"from={quote(time_from, safe='')}", f"to={quote(time_to, safe='')}", "orgId=1"]
    return f"{grafana_url}/d/{uid}/{slug}?" + "&".join(params)


def node_detail_url(
    grafana_url: str,
    instance: str,
    time_from: str = DEFAULT_FROM,
    time_to: str = DEFAULT_TO,
) -> str:
    """
    Detail dashboard for one hardware node — bare metal or VM alike.

    instance: the Prometheus instance label, which in this fleet equals the
    hostname ("cerberus", "melchior") because node-exporter, cadvisor and
    docker-inventory all label with the bare name rather than host:port.
    """
    return _detail_url(
        grafana_url, NODE_DASHBOARD_UID, "bis-node-detail",
        {"instance": instance.split(":")[0]}, time_from, time_to,
    )


def container_detail_url(
    grafana_url: str,
    instance: str,
    container_name: str,
    time_from: str = DEFAULT_FROM,
    time_to: str = DEFAULT_TO,
) -> str:
    """
    Detail dashboard for one container.

    Both variables are required: container names are unique per host, not per
    fleet, so the host has to travel with the name.
    """
    return _detail_url(
        grafana_url, CONTAINER_DASHBOARD_UID, "bis-container-detail",
        {"node": instance.split(":")[0], "container": container_name},
        time_from, time_to,
    )


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
