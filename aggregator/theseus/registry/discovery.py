"""Seed/refresh the registry from Prometheus. Idempotent; runs on an interval."""
import time

import httpx

from . import db


def _addr(instance):
    return instance.split(":")[0]


def prom_query(prom_url, q):
    r = httpx.get(f"{prom_url}/api/v1/query", params={"query": q}, timeout=10.0)
    r.raise_for_status()
    return [s["metric"] for s in r.json()["data"]["result"]]


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

    conn.commit()
    return {"nodes": len(nodes), "containers": len(containers), "at": now}
