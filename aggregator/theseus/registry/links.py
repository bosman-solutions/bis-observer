"""
Lazy, fingerprinted link materialization for registry entities.

Builders are the existing ones in theseus/links.py — the registry stores their
output, it does not re-implement URL shapes. Each stored link carries a
fingerprint of (builder source, builder args). A link is built once and rebuilt
only when an input changes: new labels, a different GRAFANA_EXTERNAL_URL, the
Prometheus datasource UID resolving, or an edit to the builder function itself.

detail  — provisioned detail dashboard. Viewer-safe; the public face.
explore — Explore deeplink. Needs Editor since Grafana 12; operators only.
"""
import hashlib
import inspect
import json

from .. import links as gl

# kind -> link name -> (builder, public, args(labels, ext_url, prom_uid))
BUILDERS = {
    "node": {
        "detail":  (gl.node_detail_url, True,
                    lambda l, ext, uid: (ext, l["instance"])),
        "explore": (gl.host_explore_url, False,
                    lambda l, ext, uid: (ext, uid, l["instance"])),
    },
    "container": {
        "detail":  (gl.container_detail_url, True,
                    lambda l, ext, uid: (ext, l["instance"], l["name"])),
        "explore": (gl.container_explore_url, False,
                    lambda l, ext, uid: (ext, uid, l["instance"], l["name"])),
    },
}

_SRC_HASH = {}


def _src(fn):
    if fn not in _SRC_HASH:
        _SRC_HASH[fn] = hashlib.sha256(inspect.getsource(fn).encode()).hexdigest()[:12]
    return _SRC_HASH[fn]


def _fp(fn, args):
    blob = json.dumps([_src(fn), list(args)], sort_keys=True, default=str)
    return hashlib.sha256(blob.encode()).hexdigest()[:16]


def ensure_links(entity, ext_url, prom_uid):
    """Return (links, changed). Builds only what's missing or stale."""
    links = dict(entity["links"])
    changed = False
    for name, (fn, public, argf) in BUILDERS.get(entity["kind"], {}).items():
        try:
            args = argf(entity["labels"], ext_url, prom_uid)
        except KeyError as e:
            err = {"error": f"missing label {e}", "public": public}
            if links.get(name) != err:
                links[name], changed = err, True
            continue
        fp = _fp(fn, args)
        if links.get(name, {}).get("fp") == fp:
            continue
        links[name] = {"fp": fp, "url": fn(*args), "public": public}
        changed = True
    return links, changed
