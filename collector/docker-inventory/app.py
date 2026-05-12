#!/usr/bin/env python3
"""
docker-inventory — Prometheus exporter for Docker container state.

Exposes metrics for ALL containers Docker knows about — running, stopped,
exited, paused, dead. Fills the gap left by cAdvisor which only reports
running containers.

Metrics:
  docker_container_info{name, state, compose_project, compose_service,
                         image, node, environment} 1
  docker_container_started_at{...}   Unix timestamp of container start
  docker_container_finished_at{...}  Unix timestamp of container exit (0 if running)

Alloy scrapes this on localhost:${INVENTORY_PORT:-9338} and remote_writes
to the aggregator Prometheus alongside node-exporter and cAdvisor data.

Environment variables:
  NODE_NAME       — human label for this node (matches other collector metrics)
  ENVIRONMENT     — environment label (default: homelab)
  INVENTORY_PORT  — port to listen on (default: 9338)
  POLL_INTERVAL   — seconds between Docker polls (default: 30)
"""

import os
import time
import logging
from datetime import datetime, timezone

import docker
from flask import Flask, Response

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)

NODE_NAME     = os.environ.get("NODE_NAME", "unknown")
ENVIRONMENT   = os.environ.get("ENVIRONMENT", "homelab")
PORT          = int(os.environ.get("INVENTORY_PORT", "9338"))
POLL_INTERVAL = int(os.environ.get("POLL_INTERVAL", "30"))

app = Flask(__name__)
client = docker.from_env()

# In-memory cache — rebuilt every POLL_INTERVAL seconds
_cache: str = ""
_last_poll: float = 0


def _escape(value: str) -> str:
    """Escape label value for Prometheus text format."""
    return value.replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n")


def _parse_timestamp(iso_str: str) -> float:
    """Parse Docker ISO timestamp to Unix float. Returns 0 on failure."""
    if not iso_str or iso_str.startswith("0001"):
        return 0.0
    try:
        # Docker timestamps: 2026-05-11T14:32:42.123456789Z
        # Trim nanoseconds to microseconds for Python compat
        ts = iso_str[:26].rstrip("Z") + "+00:00"
        return datetime.fromisoformat(ts).timestamp()
    except Exception:
        return 0.0


def build_metrics() -> str:
    """Query Docker socket and build Prometheus text format output."""
    lines = [
        "# HELP docker_container_info Container state information from Docker socket.",
        "# TYPE docker_container_info gauge",
        "# HELP docker_container_started_at Unix timestamp when container last started.",
        "# TYPE docker_container_started_at gauge",
        "# HELP docker_container_finished_at Unix timestamp when container last exited (0 if running).",
        "# TYPE docker_container_finished_at gauge",
    ]

    try:
        containers = client.containers.list(all=True)
    except Exception as e:
        logger.error(f"Docker socket error: {e}")
        lines.append(f"# ERROR: {e}")
        return "\n".join(lines) + "\n"

    for container in containers:
        attrs   = container.attrs or {}
        config  = attrs.get("Config", {})
        state   = attrs.get("State", {})
        labels  = config.get("Labels") or {}

        name    = container.name.lstrip("/")
        status  = state.get("Status", "unknown")
        image   = config.get("Image", "")
        project = labels.get("com.docker.compose.project", "standalone")
        service = labels.get("com.docker.compose.service", name)

        started_at  = _parse_timestamp(state.get("StartedAt", ""))
        finished_at = _parse_timestamp(state.get("FinishedAt", ""))

        base = (
            f'name="{_escape(name)}",'
            f'state="{_escape(status)}",'
            f'compose_project="{_escape(project)}",'
            f'compose_service="{_escape(service)}",'
            f'image="{_escape(image)}",'
            f'node="{_escape(NODE_NAME)}",'
            f'environment="{_escape(ENVIRONMENT)}"'
        )

        lines.append(f"docker_container_info{{{base}}} 1")
        lines.append(f"docker_container_started_at{{{base}}} {started_at}")
        lines.append(f"docker_container_finished_at{{{base}}} {finished_at}")

    logger.info(f"Polled {len(containers)} containers")
    return "\n".join(lines) + "\n"


def maybe_refresh():
    global _cache, _last_poll
    now = time.time()
    if now - _last_poll > POLL_INTERVAL:
        _cache = build_metrics()
        _last_poll = now


@app.route("/metrics")
def metrics():
    maybe_refresh()
    return Response(_cache, mimetype="text/plain; version=0.0.4; charset=utf-8")


@app.route("/health")
def health():
    return {"status": "ok", "node": NODE_NAME}


if __name__ == "__main__":
    logger.info(f"docker-inventory starting — node={NODE_NAME}, port={PORT}, interval={POLL_INTERVAL}s")
    maybe_refresh()
    app.run(host="0.0.0.0", port=PORT)
