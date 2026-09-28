"""
Registry routes.

  GET  /entities[?kind=][&state=up|offline|ended]  — entities, links materialized
  GET  /entities/<id>                   — one entity (id like node:cerberus)
  PUT  /entities/<id>  {"lifecycle": "persistent"|"ephemeral"|"retired"|null}
                                        — pin a class, clear the pin (null), or retire (delete)
  POST /entities/discover               — run discovery now

Every entity carries class (persistent|ephemeral) and state (up|offline|ended);
see lifecycle.py.
"""
import time

from flask import Blueprint, abort, current_app, g, jsonify, request

from . import db
from .discovery import discover
from .links import ensure_links
from . import lifecycle

bp = Blueprint("registry", __name__, url_prefix="/entities")


def _cfg():
    return current_app.config["REGISTRY"]


def _conn():
    if "registry_db" not in g:
        g.registry_db = db.connect(_cfg()["db_path"])
    return g.registry_db


def close_conn(_exc=None):
    conn = g.pop("registry_db", None)
    if conn is not None:
        conn.close()


def _materialize(conn, e):
    cfg = _cfg()
    links, changed = ensure_links(e, cfg["ext_url"], cfg["prom_uid"]())
    if changed:
        db.set_links(conn, e["id"], links)
    e["links"] = links
    now = time.time()
    e["stale"] = now - e["last_seen"] > cfg["stale_after"]
    return lifecycle.annotate(e, now)


@bp.get("")
def list_entities():
    conn = _conn()
    out = [_materialize(conn, e) for e in db.list_(conn, request.args.get("kind"))]
    conn.commit()
    want = request.args.get("state")
    if want:
        out = [e for e in out if e["state"] == want]
    return jsonify(out)


@bp.get("/<path:eid>")
def get_entity(eid):
    conn = _conn()
    e = db.get(conn, eid) or abort(404)
    e = _materialize(conn, e)
    conn.commit()
    return jsonify(e)


@bp.put("/<path:eid>")
def set_lifecycle(eid):
    conn = _conn()
    if db.get(conn, eid) is None:
        abort(404)
    want = (request.get_json(silent=True) or {}).get("lifecycle", "missing")
    if want == "retired":
        db.delete(conn, eid)
        conn.commit()
        return jsonify({"id": eid, "retired": True})
    if want not in ("persistent", "ephemeral", None):
        abort(400, "lifecycle must be persistent, ephemeral, retired, or null")
    db.set_lifecycle(conn, eid, want)
    conn.commit()
    return jsonify(_materialize(conn, db.get(conn, eid)))


@bp.post("/discover")
def run_discovery():
    return jsonify(discover(_conn(), _cfg()["prom_url"]))
