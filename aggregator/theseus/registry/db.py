"""SQLite store for the entity registry. Identity + derived links only; metrics stay in Prometheus."""
import json
import sqlite3
import time

SCHEMA = """
CREATE TABLE IF NOT EXISTS entity (
  id         TEXT PRIMARY KEY,            -- node:cerberus, container:cerberus/obs-grafana
  kind       TEXT NOT NULL,
  labels     TEXT NOT NULL DEFAULT '{}',  -- observed identity labels (JSON)
  links      TEXT NOT NULL DEFAULT '{}',  -- materialized links + fingerprints (JSON)
  snapshot   TEXT NOT NULL DEFAULT '{}',  -- cached rollups (JSON), filled later
  first_seen INTEGER NOT NULL,
  last_seen  INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS entity_kind ON entity(kind);
"""


def connect(path):
    conn = sqlite3.connect(path, timeout=10)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.executescript(SCHEMA)
    return conn


def to_entity(row):
    return {
        "id": row["id"],
        "kind": row["kind"],
        "labels": json.loads(row["labels"]),
        "links": json.loads(row["links"]),
        "snapshot": json.loads(row["snapshot"]),
        "first_seen": row["first_seen"],
        "last_seen": row["last_seen"],
    }


def upsert(conn, eid, kind, labels, now=None):
    now = now or int(time.time())
    conn.execute(
        """INSERT INTO entity (id, kind, labels, first_seen, last_seen)
           VALUES (?, ?, ?, ?, ?)
           ON CONFLICT(id) DO UPDATE SET labels = excluded.labels,
                                         last_seen = excluded.last_seen""",
        (eid, kind, json.dumps(labels, sort_keys=True), now, now),
    )


def get(conn, eid):
    row = conn.execute("SELECT * FROM entity WHERE id = ?", (eid,)).fetchone()
    return to_entity(row) if row else None


def list_(conn, kind=None):
    rows = conn.execute(
        "SELECT * FROM entity WHERE (? IS NULL OR kind = ?) ORDER BY id", (kind, kind)
    ).fetchall()
    return [to_entity(r) for r in rows]


def set_links(conn, eid, links):
    conn.execute(
        "UPDATE entity SET links = ? WHERE id = ?",
        (json.dumps(links, sort_keys=True), eid),
    )


def set_snapshot(conn, eid, snapshot):
    conn.execute(
        "UPDATE entity SET snapshot = ? WHERE id = ?",
        (json.dumps(snapshot, sort_keys=True), eid),
    )
