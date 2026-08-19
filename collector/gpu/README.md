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

# 4. it reached the aggregator this node ships to (AGGREGATOR_HOST in .env —
#    melchior for the home stack, cerberus for the edge stack)
curl -s 'http://melchior:9090/api/v1/query?query=DCGM_FI_DEV_FB_USED'
```

## SETTLED — profiling fields are NOT available on GeForce

Tested on the 3080, 2026-08-19. `grep -c DCGM_FI_PROF_` returns **0**, and the
exporter says why:

```
Not collecting DCP metrics: This request is serviced by a module of DCGM
that is not currently loaded
Skipping line 1 ('DCGM_FI_PROF_SM_ACTIVE'): metric not enabled
```

Data Center Profiling is a product-segmentation boundary, not a permission or
configuration problem — `SYS_ADMIN`, the nvidia runtime and a clean DCGM init
are all present and it still declines. There is nothing to fix. SM_ACTIVE,
SM_OCCUPANCY, DRAM_ACTIVE and the tensor-pipe fields are unavailable on this
class of card and the counter file no longer asks for them.

### What replaces them

| lost | replacement | what it costs us |
|---|---|---|
| `PROF_SM_ACTIVE` | `SM_CLOCK` + violation counters | no direct measure of warp occupancy; throttling is inferred from clocks falling under load rather than observed at the SM |
| `PROF_DRAM_ACTIVE` | `MEM_COPY_UTIL` | percent of time the controller was busy, not the fraction of peak bandwidth — separates memory-bound from compute-bound, but won't quantify headroom |
| `PROF_PIPE_TENSOR_ACTIVE` | *(nothing)* | cannot tell whether tensor cores are engaged, so no fp16-vs-ternary kernel comparison on this hardware |

The claim that survives is "occupancy is not work, and here are three
independent signals that prove it." The claim that does not survive is any
statement about *what fraction of the silicon* was doing the work.

`DCGM_FI_DEV_POWER_VIOLATION` / `THERMAL_VIOLATION` turned out to be a better
throttle signal than the bitmask anyway: they are cumulative microseconds spent
capped, so `rate()` gives the fraction of wall time a tenant was throttled — a
duration you can hold an SLO against instead of a flag you have to catch live.

### Verified emitting on the 3080

`GPU_UTIL`, `MEM_COPY_UTIL`, `ENC_UTIL`, `DEC_UTIL`, `FB_FREE/USED/TOTAL`,
`SM_CLOCK`, `MEM_CLOCK`, `POWER_USAGE`, `ENFORCED_POWER_LIMIT`, `GPU_TEMP`,
`MEMORY_TEMP`, `TOTAL_ENERGY_CONSUMPTION`, `POWER_VIOLATION`,
`THERMAL_VIOLATION`, `CLOCK_THROTTLE_REASONS`, `XID_ERRORS`,
`PCIE_REPLAY_COUNTER`.

Accepted but never populated here: the ECC fields (GeForce has no ECC) and
`PCIE_TX/RX_THROUGHPUT`. Empty panels read as "healthy" to everyone who didn't
build the board, so nothing that stays empty belongs on it.

Image tag `3.3.9-3.6.1-ubuntu22.04` is confirmed pulling. If it ever stops, set
`DCGM_EXPORTER_IMAGE` in `.env`.

## Known trap — do NOT nest the Alloy config mount

Bind-mounting `gpu/config.gpu.alloy` onto `/etc/alloy/config.gpu.alloy` fails at
container init: `/etc/alloy` is already a read-only bind of `collector/alloy/`,
and runc must create the target file before mounting onto it, which it cannot do
inside a read-only mount. Creating the directory does not help — the read-only
parent is the blocker. `make collector` therefore *copies* the file into
`collector/alloy/` on GPU nodes, where it is gitignored. Deploy is a reset, so
the copy is idempotent and a node that loses its GPU has it removed.

## Scope

GPU nodes only. Never add to `docker-compose.armv7.yml` or a non-GPU node's
compose. The base `docker-compose.yml` stays fleet-identical — this overlay is
the node-specific extra, mirroring how the aggregator keeps credentialed jobs in
`scrape_configs.d/` instead of the shared `prometheus.yml`.
