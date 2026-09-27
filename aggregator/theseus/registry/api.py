"""
Registry routes.

  GET  /entities[?kind=node|container]  — all entities, links materialized
  GET  /entities/<id>                   — one entity (id like node:cerberus)
  POST /entities/discover               — run discovery now
"""
import time

from flask import Blueprint, abort, current_app, g, jsonify, request

from . import db
from .discovery import discover
from .links import ensure_links

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
    e["stale"] = time.time() - e["last_seen"] > cfg["stale_after"]
    return e


@bp.get("")
def list_entities():
    conn = _conn()
    out = [_materialize(conn, e) for e in db.list_(conn, request.args.get("kind"))]
    conn.commit()
    return jsonify(out)


@bp.get("/<path:eid>")
def get_entity(eid):
    conn = _conn()
    e = db.get(conn, eid) or abort(404)
    e = _materialize(conn, e)
    conn.commit()
    return jsonify(e)


@bp.post("/discover")
def run_discovery():
    return jsonify(discover(_conn(), _cfg()["prom_url"]))
