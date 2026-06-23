# gpu-exporter — host-side Prometheus exporter for the passthrough RTX 3080

The 3080 is PCI-passthrough'd to a VM (`vfio-pci` on the host), so the host can't
read its VRAM directly — only the guest holding the card can. This exporter
composes two SCC modules so the **host** can publish GPU history regardless of who
holds the card, with no in-guest exporter:

- `vmctl.gpu_holder()` → which VM holds the GPU now (`gato` | `tiny11` | `None`)
- `gpu_probe.query(vm)` → `nvidia-smi` read inside that VM via qemu-guest-agent

Same single read path the `vmctl swap` busy-guard uses.

## Metrics (`:9339/metrics`)

| metric | labels | meaning |
|---|---|---|
| `gpu_holder_present` | node | 1 if any GPU VM is running, else 0 |
| `gpu_holder_info` | vm,node | 1 — labels name the current holder |
| `gpu_probe_available` | vm,node | 1 if `nvidia-smi` was readable, else 0 |
| `gpu_vram_used_bytes` | vm,node | VRAM in use (only when available) |
| `gpu_vram_total_bytes` | vm,node | total VRAM (only when available) |
| `gpu_vram_used_ratio` | vm,node | used/total, 0..1 (only when available) |
| `gpu_utilization_percent` | vm,node | GPU util %, 0..100 (only when available) |
| `gpu_probe_last_success_timestamp` | node | Unix ts of last good read (staleness signal) |
| `gpu_probe_scrape_timestamp` | node | Unix ts of this scrape |

Invariant carried from `gpu_probe`: when the holder's agent is unreadable, we emit
`gpu_probe_available 0` and **skip** the vram/util gauges — "unknown" never reads
as "idle". `last_success_timestamp` lets a dashboard alert on staleness:
`time() - gpu_probe_last_success_timestamp > 300`.

## Live-validated 2026-06-22

Against the running `tiny11` (holds the card while Disco games):
`gpu_holder_info{vm="tiny11"} 1`, `gpu_vram_used_bytes 901775360` (860 MiB),
`gpu_utilization_percent 2`. Byte conversions exact. Unit suite:
`PYTHONPATH=. python3 test_gpu_exporter.py` → 20 assertions pass (in the scc repo).

## Build

`gpu_probe.py` and `vmctl.py` are vendored from `~/claude/scc` (source of truth)
into the build context, then copied into the image:

```bash
./sync_modules.sh        # copies gpu_probe.py + vmctl.py from ~/claude/scc
docker compose build gpu-exporter
```

## Deploy — DISCO-PRESENT (staged, not wired live)

Per the SCC containerization rule, an autonomous session stages but does not bring
live infra up. To wire it in, add this service to `collector/docker-compose.yml`
and the scrape block to `alloy/config.metrics.alloy`, then `docker compose up -d`.

Note: this container needs the **libvirt socket** (to drive `qemu:///system` and
issue guest-agent commands) — the same mount `vmctl_mcp` uses. If the host uses
`/run/libvirt`, mount that path instead.

### compose service

```yaml
  # GPU EXPORTER — passthrough 3080 telemetry, read from the holding VM via
  # qemu-guest-agent. Host-side; needs the libvirt socket (like vmctl_mcp).
  gpu-exporter:
    build: ./gpu-exporter
    container_name: obs-gpu-exporter
    restart: unless-stopped
    network_mode: host
    volumes:
      - /var/run/libvirt:/var/run/libvirt        # or /run/libvirt
    environment:
      - NODE_NAME=${NODE_NAME}
      - ENVIRONMENT=homelab
      - GPU_EXPORTER_PORT=${GPU_EXPORTER_PORT:-9339}
      - POLL_INTERVAL=30
```

Add `gpu-exporter` under `alloy:`'s `depends_on:`, and append to `.env.example`:

```
# GPU exporter — passthrough 3080 telemetry
GPU_EXPORTER_PORT=9339
```

### Alloy scrape (append to `alloy/config.metrics.alloy`)

```alloy
prometheus.scrape "gpu_exporter" {
  targets = [{
    __address__ = "localhost:" + coalesce(env("GPU_EXPORTER_PORT"), "9339"),
    instance    = env("NODE_NAME"),
  }]
  forward_to      = [prometheus.remote_write.aggregator.receiver]
  scrape_interval = "30s"
  job_name        = "gpu-exporter"
}
```

(Reuses the existing `prometheus.remote_write.aggregator` from the metrics config.)

## Caveat — only the GPU-host node

This belongs **only** on the libvirt host that owns the 3080 (Balthazar). It is
not part of the portable collector profile that ships to every node. Keep it out
of `docker-compose.armv7.yml` and any non-GPU node's compose.

Author: Weaver · 2026-06-22
