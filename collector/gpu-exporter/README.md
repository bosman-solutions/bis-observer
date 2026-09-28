# gpu-exporter — host-side telemetry for a passthrough GPU

When a GPU is PCI-passthrough'd to a VM (`vfio-pci` on the host), the host can't
read it directly — only the guest holding the card can. This exporter lets the
**host** publish GPU history regardless of which guest holds it, with no
in-guest exporter required:

- `vmctl.gpu_holder()` → which VM currently holds the card, or `None`
- `gpu_probe.query(vm)` → `nvidia-smi` read inside that VM via qemu-guest-agent

Same read path the `vmctl swap` busy-guard uses.

## Metrics (`:9339/metrics`)

| metric | labels | meaning |
|---|---|---|
| `gpu_holder_present` | node | 1 if any GPU VM is running |
| `gpu_holder_info` | vm,node | 1 — labels name the current holder |
| `gpu_probe_available` | vm,node | 1 if `nvidia-smi` was readable |
| `gpu_vram_used_bytes` | vm,node | VRAM in use (only when available) |
| `gpu_vram_total_bytes` | vm,node | total VRAM (only when available) |
| `gpu_vram_used_ratio` | vm,node | used/total, 0..1 (only when available) |
| `gpu_utilization_percent` | vm,node | GPU util %, 0..100 (only when available) |
| `gpu_probe_last_success_timestamp` | node | Unix ts of last good read |
| `gpu_probe_scrape_timestamp` | node | Unix ts of this scrape |

Invariant carried from `gpu_probe`: when the holder's agent is unreadable the
exporter emits `gpu_probe_available 0` and **skips** the vram/util gauges —
"unknown" must never render as "idle". Alert on staleness with
`time() - gpu_probe_last_success_timestamp > 300`.

## Build

`gpu_probe.py` and `vmctl.py` are vendored into the build context from their
source repo, then copied into the image:

```bash
./sync_modules.sh
docker compose build gpu-exporter
```

## Wiring it in

Add the service to `collector/docker-compose.yml` and the scrape block to
`alloy/config.metrics.alloy`.

The container needs the **libvirt socket** to drive `qemu:///system` and issue
guest-agent commands. Mount `/run/libvirt` instead if that's where the host
keeps it.

```yaml
  gpu-exporter:
    build: ./gpu-exporter
    container_name: obs-gpu-exporter
    restart: unless-stopped
    network_mode: host
    volumes:
      - /var/run/libvirt:/var/run/libvirt
    environment:
      - NODE_NAME=${NODE_NAME}
      - ENVIRONMENT=${ENVIRONMENT:-homelab}
      - GPU_EXPORTER_PORT=${GPU_EXPORTER_PORT:-9339}
      - POLL_INTERVAL=${GPU_POLL_INTERVAL:-30}
```

Add `gpu-exporter` under `alloy:`'s `depends_on:`, then:

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

Reuses the existing `prometheus.remote_write.aggregator` from the metrics config.

## Scope

Only the libvirt host that owns the card. This is not part of the portable
collector profile that ships to every node — keep it out of
`docker-compose.armv7.yml` and any non-GPU node's compose.
