#!/usr/bin/env python3
"""
gpu-exporter — Prometheus exporter for a PCI-passthrough GPU.

A passthrough GPU (vfio-pci on the host) cannot be read by the host directly —
only the guest holding the card can. This exporter composes two modules:

  vmctl.gpu_holder()  → which VM currently holds the card, or None
  gpu_probe.query(vm) → nvidia-smi read from INSIDE that VM via qemu-guest-agent

so the host can publish GPU VRAM/util history regardless of which VM holds the
card — and without an in-guest exporter. One read path, the same one vmctl's
swap busy-guard uses.

Metrics (text format, matches the docker-inventory exporter's house style):
  gpu_holder_present{node}                 1 if a GPU VM is running, else 0
  gpu_holder_info{vm,node}                 1  — labels name the current holder
  gpu_probe_available{vm,node}             1 if nvidia-smi was readable, else 0
  gpu_vram_used_bytes{vm,node}             VRAM in use (only when available)
  gpu_vram_total_bytes{vm,node}            total VRAM (only when available)
  gpu_vram_used_ratio{vm,node}            used/total, 0..1 (only when available)
  gpu_utilization_percent{vm,node}         GPU util %, 0..100 (only when available)
  gpu_probe_last_success_timestamp{node}   Unix ts of last successful read (0 if never)
  gpu_probe_scrape_timestamp{node}         Unix ts of this scrape

Design choices (autonomous session, noted for review):
- When NO GPU VM is running, we emit gpu_holder_present 0 and skip value metrics —
  there is genuinely nothing holding the card. Absent series >> fabricated zeros.
- When a VM holds the card but its agent is unreadable (agent absent / smi failed),
  we emit gpu_probe_available 0 and SKIP the vram/util gauges rather than reporting
  0 — "unknown" must never read as "idle" (same invariant gpu_probe.is_busy keeps).
  gpu_probe_last_success_timestamp lets a dashboard show staleness.

Alloy scrapes this on localhost:${GPU_EXPORTER_PORT:-9339} and remote_writes to the
aggregator alongside node-exporter, cadvisor and docker-inventory.

Environment variables:
  NODE_NAME          — human label for this node (matches other collector metrics)
  ENVIRONMENT        — environment label (default: homelab)
  GPU_EXPORTER_PORT  — port to listen on (default: 9339)
  POLL_INTERVAL      — seconds between GPU polls (default: 30)
  PROBE_TIMEOUT      — per-query guest-exec timeout, seconds (default: 20)

"""

import os
import time
import logging

from flask import Flask, Response

import vmctl
import gpu_probe

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)

NODE_NAME     = os.environ.get("NODE_NAME", "unknown")
ENVIRONMENT   = os.environ.get("ENVIRONMENT", "homelab")
PORT          = int(os.environ.get("GPU_EXPORTER_PORT", "9339"))
POLL_INTERVAL = int(os.environ.get("POLL_INTERVAL", "30"))
PROBE_TIMEOUT = int(os.environ.get("PROBE_TIMEOUT", "20"))

MIB = 1024 * 1024

app = Flask(__name__)

# In-memory cache — rebuilt every POLL_INTERVAL seconds
_cache: str = ""
_last_poll: float = 0
_last_success_ts: float = 0  # persists across scrapes; staleness signal


def _escape(value: str) -> str:
    """Escape label value for Prometheus text format."""
    return value.replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n")


def build_metrics(now=None) -> str:
    """Read the current GPU holder + its telemetry; return Prometheus text.

    Never raises — vmctl/gpu_probe failures degrade to availability=0 series so the
    exporter itself never falls over (an exporter that crashes is worse than one
    reporting 'unknown')."""
    global _last_success_ts
    now = now if now is not None else time.time()
    node = _escape(NODE_NAME)
    env  = _escape(ENVIRONMENT)

    lines = [
        "# HELP gpu_holder_present 1 if a GPU-passthrough VM is currently running.",
        "# TYPE gpu_holder_present gauge",
        "# HELP gpu_holder_info Labels identify the VM currently holding the GPU.",
        "# TYPE gpu_holder_info gauge",
        "# HELP gpu_probe_available 1 if nvidia-smi was readable inside the holder.",
        "# TYPE gpu_probe_available gauge",
        "# HELP gpu_vram_used_bytes GPU VRAM in use, bytes.",
        "# TYPE gpu_vram_used_bytes gauge",
        "# HELP gpu_vram_total_bytes Total GPU VRAM, bytes.",
        "# TYPE gpu_vram_total_bytes gauge",
        "# HELP gpu_vram_used_ratio GPU VRAM used / total, 0..1.",
        "# TYPE gpu_vram_used_ratio gauge",
        "# HELP gpu_utilization_percent GPU utilization, 0..100.",
        "# TYPE gpu_utilization_percent gauge",
        "# HELP gpu_probe_last_success_timestamp Unix time of last successful GPU read.",
        "# TYPE gpu_probe_last_success_timestamp gauge",
        "# HELP gpu_probe_scrape_timestamp Unix time this exporter built these metrics.",
        "# TYPE gpu_probe_scrape_timestamp gauge",
    ]

    # 1) Who holds the card? gpu_probe/vmctl shell out to virsh; never let that
    #    bring the exporter down.
    try:
        holder = vmctl.gpu_holder()
    except Exception as e:  # noqa: BLE001
        logger.error(f"gpu_holder() failed: {e}")
        holder = None

    if not holder:
        lines.append(f'gpu_holder_present{{node="{node}",environment="{env}"}} 0')
        lines.append(f'gpu_probe_last_success_timestamp{{node="{node}"}} {_last_success_ts}')
        lines.append(f'gpu_probe_scrape_timestamp{{node="{node}"}} {now}')
        logger.info("no GPU VM running — nothing holds the card")
        return "\n".join(lines) + "\n"

    vm = _escape(holder)
    base = f'vm="{vm}",node="{node}",environment="{env}"'
    lines.append(f'gpu_holder_present{{node="{node}",environment="{env}"}} 1')
    lines.append(f"gpu_holder_info{{{base}}} 1")

    # 2) Read telemetry from inside the holder. query() never raises.
    m = gpu_probe.query(holder, overall_timeout=PROBE_TIMEOUT)
    if not m.get("available"):
        lines.append(f"gpu_probe_available{{{base}}} 0")
        logger.info(f"holder={holder} agent unreadable: {m.get('error')}")
    else:
        used_bytes  = m["mem_used_mb"] * MIB
        total_bytes = m["mem_total_mb"] * MIB
        ratio = round(m["mem_used_mb"] / m["mem_total_mb"], 4) if m["mem_total_mb"] else 0.0
        _last_success_ts = now
        lines.append(f"gpu_probe_available{{{base}}} 1")
        lines.append(f"gpu_vram_used_bytes{{{base}}} {used_bytes}")
        lines.append(f"gpu_vram_total_bytes{{{base}}} {total_bytes}")
        lines.append(f"gpu_vram_used_ratio{{{base}}} {ratio}")
        lines.append(f"gpu_utilization_percent{{{base}}} {m['util_pct']}")
        logger.info(f"holder={holder} vram={m['mem_used_mb']}/{m['mem_total_mb']}MiB "
                    f"util={m['util_pct']}%")

    lines.append(f'gpu_probe_last_success_timestamp{{node="{node}"}} {_last_success_ts}')
    lines.append(f'gpu_probe_scrape_timestamp{{node="{node}"}} {now}')
    return "\n".join(lines) + "\n"


def maybe_refresh():
    global _cache, _last_poll
    now = time.time()
    if now - _last_poll > POLL_INTERVAL or not _cache:
        _cache = build_metrics(now)
        _last_poll = now


@app.route("/metrics")
def metrics():
    maybe_refresh()
    return Response(_cache, mimetype="text/plain; version=0.0.4; charset=utf-8")


@app.route("/health")
def health():
    return {"status": "ok", "node": NODE_NAME}


if __name__ == "__main__":
    logger.info(f"gpu-exporter starting — node={NODE_NAME}, port={PORT}, "
                f"interval={POLL_INTERVAL}s")
    maybe_refresh()
    app.run(host="0.0.0.0", port=PORT)
