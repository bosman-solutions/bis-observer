"""Seed/refresh the registry from Prometheus. Idempotent; runs on an interval."""
import time

import httpx

from . import db


def _addr(instance):
    return instance.split(":")[0]


def prom_query(prom_url, q, values=False):
    r = httpx.get(f"{prom_url}/api/v1/query", params={"query": q}, timeout=10.0)
    r.raise_for_status()
    res = r.json()["data"]["result"]
    if values:
        return [(s["metric"], float(s["value"][1])) for s in res]
    return [s["metric"] for s in res]


def discover(conn, prom_url, query=prom_query):
    """
    Nodes from node_uname_info, containers from cAdvisor. This fleet labels
    instance with the bare hostname, so the host part of instance is the host.
    """
    now = int(time.time())

    nodes = query(prom_url, "node_uname_info")
    for m in nodes:
        host = _addr(m["instance"])
        db.upsert(conn, f"node:{host}", "node",
                  {"host": host, "instance": m["instance"],
                   "nodename": m.get("nodename", host)}, now)

    containers = query(prom_url, 'count by (instance, name) (container_last_seen{name!=""})')
    for m in containers:
        host = _addr(m["instance"])
        db.upsert(conn, f"container:{host}/{m['name']}", "container",
                  {"host": host, "instance": m["instance"], "name": m["name"]}, now)

    rated = _log_rates(conn, prom_url, query, now,
                       [f"node:{_addr(m['instance'])}" for m in nodes],
                       [f"container:{_addr(m['instance'])}/{m['name']}" for m in containers])

    conn.commit()
    return {"nodes": len(nodes), "containers": len(containers), "log_rates": rated, "at": now}


def _log_rates(conn, prom_url, query, now, node_ids, container_ids):
    """
    Error/warn lines per minute per entity, from the Loki recording rule
    bis:log_lines:rate5m (remote-written into Prometheus). Written into each
    entity's snapshot.log. When the rule has produced nothing yet, snapshots
    are left alone — unknown stays unknown instead of reading as a clean 0.
    """
    rows = query(prom_url, 'sum by (node, container_name, level) (bis:log_lines:rate5m{level=~"error|warn"})', values=True)
    if not rows:
        return 0
    per = {}
    for m, v in rows:
        lvl = "error_per_min" if m.get("level") == "error" else "warn_per_min"
        for eid in (f"node:{m.get('node')}", f"container:{m.get('node')}/{m.get('container_name')}"):
            d = per.setdefault(eid, {"error_per_min": 0.0, "warn_per_min": 0.0})
            d[lvl] += v * 60
    for eid in node_ids + container_ids:
        d = per.get(eid, {"error_per_min": 0.0, "warn_per_min": 0.0})
        snap = (db.get(conn, eid) or {}).get("snapshot", {})
        snap["log"] = {k: round(x, 2) for k, x in d.items()} | {"at": now, "window": "5m"}
        db.set_snapshot(conn, eid, snap)
    return len(node_ids) + len(container_ids)
