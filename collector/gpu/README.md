# GPU collector overlay — dcgm-exporter

Device-truth telemetry for a GPU-holding node. Staged by Weaver 2026-08-19,
**not brought up** — the live `up` is a Disco-present action.

## Why this exists alongside `collector/gpu-exporter/`

They answer different questions and both are correct.

| | `gpu-exporter` (Jun 2026) | `gpu/` dcgm overlay (this) |
|---|---|---|
| runs on | libvirt **host** (Balthazar) | the node **holding** the card (gato) |
| reads via | qemu-guest-agent → `nvidia-smi` | DCGM, in-guest, direct |
| answers | *who holds the 3080* | *what the silicon is doing* |
| fidelity | holder, VRAM, `utilization.gpu` | SM/DRAM/tensor activity, clocks, throttle reasons, XID/ECC |

Custody is real and worth keeping. But `utilization.gpu` only reports that a
kernel was resident — not that the chip did work. That field is retained in
`dcgm-counters.csv` deliberately, as the control series to plot against
`SM_ACTIVE`. The divergence between those two lines is the whole argument.

## Bring it up (on gato)

```bash
cd ~/bis-observer/collector
echo 'DCGM_EXPORTER_PORT=9400' >> .env
docker compose -f docker-compose.yml -f gpu/docker-compose.dcgm.yml up -d
```

## Verify, in this order

```bash
# 1. exporter is answering at all
curl -s localhost:9400/metrics | head -20

# 2. THE LOAD-BEARING CHECK — are the profiling fields actually present?
curl -s localhost:9400/metrics | grep -c DCGM_FI_PROF_

# 3. alloy picked up the new job
curl -s localhost:12345/api/v0/web/components | grep -o dcgm_exporter

# 4. it reached the aggregator (run against cerberus)
curl -s 'http://cerberus:9090/api/v1/query?query=DCGM_FI_DEV_FB_USED'
```

## RISK — profiling fields on GeForce

**Step 2 above is the one that can sink this.** DCGM's profiling metrics
(`DCGM_FI_PROF_*` — SM_ACTIVE, DRAM_ACTIVE, tensor pipe activity) are a
datacenter-GPU feature. Support on consumer GeForce silicon is inconsistent and
version-dependent; the fields may return `N/A`, or be absent from the scrape
entirely, even when dcgm-exporter itself comes up clean.

If `grep -c DCGM_FI_PROF_` returns 0 on the 3080, the fallback is a small NVML
sampling exporter (`nvidia-smi dmon`-equivalent) that derives an SM-activity
proxy from high-frequency sampling. Coarser than DCP, but it still beats
`utilization.gpu` and it still demonstrates the point. Decide that after the
first `up` — do not design around it in advance.

Also unverified: the pinned image tag. If `nvcr.io` pull fails, set
`DCGM_EXPORTER_IMAGE` in `.env` to a tag that exists.

## Scope

GPU nodes only. Never add to `docker-compose.armv7.yml` or a non-GPU node's
compose. The base `docker-compose.yml` stays fleet-identical — this overlay is
the node-specific extra, mirroring how the aggregator keeps credentialed jobs in
`scrape_configs.d/` instead of the shared `prometheus.yml`.
