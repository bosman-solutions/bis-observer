# GPU collector overlay — dcgm-exporter

Device-truth GPU telemetry. Optional overlay, applied only on nodes that
physically hold a card.

## Why this exists alongside `collector/gpu-exporter/`

Different questions, both valid.

| | `gpu-exporter` | `gpu/` dcgm overlay (this) |
|---|---|---|
| runs on | the libvirt **host** | the node **holding** the card |
| reads via | qemu-guest-agent → `nvidia-smi` | DCGM, in-guest, direct |
| answers | *which guest holds the card* | *what the silicon is doing* |
| fidelity | holder, VRAM, `utilization.gpu` | clocks, throttle reasons, XID/ECC |

`utilization.gpu` only reports that a kernel was **resident**, not that the chip
did work. It's kept in `dcgm-counters.csv` on purpose, as the control series to
plot against real activity. The gap between those two lines is the point.

## Bring it up

```bash
cd <repo>/collector
echo 'DCGM_EXPORTER_PORT=9400' >> .env
docker compose -f docker-compose.yml -f gpu/docker-compose.dcgm.yml up -d
```

## Verify, in this order

```bash
# 1. exporter answering at all
curl -s localhost:9400/metrics | head -20

# 2. are the profiling fields present? (0 on consumer cards — see below)
curl -s localhost:9400/metrics | grep -c DCGM_FI_PROF_

# 3. alloy picked up the new job
curl -s localhost:12345/api/v0/web/components | grep -o dcgm_exporter

# 4. it reached the aggregator (AGGREGATOR_HOST in .env)
curl -s "http://$AGGREGATOR_HOST:9090/api/v1/query?query=DCGM_FI_DEV_FB_USED"
```

## Consumer cards have no Data Center Profiling

On GeForce hardware `grep -c DCGM_FI_PROF_` returns **0**:

```
Not collecting DCP metrics: This request is serviced by a module of DCGM
that is not currently loaded
```

This is product segmentation, not a permissions or config problem — the nvidia
runtime, `SYS_ADMIN` and a clean DCGM init are all present and it still
declines. `SM_ACTIVE`, `SM_OCCUPANCY`, `DRAM_ACTIVE` and the tensor-pipe fields
are simply unavailable, and the counter file no longer asks for them.

| unavailable | substitute | what it costs |
|---|---|---|
| `PROF_SM_ACTIVE` | `SM_CLOCK` + throttle bitmask | throttling inferred from clocks under load, not observed at the SM |
| `PROF_DRAM_ACTIVE` | `MEM_COPY_UTIL` | time the controller was busy, not fraction of peak bandwidth |
| `PROF_PIPE_TENSOR_ACTIVE` | *(none)* | no way to tell whether tensor cores are engaged |

So "residency is not work" still holds, with three independent signals. Any
claim about *what fraction of the silicon* was working does not.

## The violation counters are wrong on consumer cards

`POWER_VIOLATION` and `THERMAL_VIOLATION` are documented as cumulative
microseconds spent capped. On GeForce they advance 30–55× faster than wall
clock and move in jumps, while `nvidia-smi` reports an idle, unthrottled card.
They emit, so they pass a presence check — and they fail a meaning check, which
is the one that matters. **A field that is present but wrong is worse than one
that is absent:** absent draws an empty panel that invites a question, wrong
draws a confident number that ends one. Both are excluded.

Throttle comes from the bitmask instead, which matches `nvidia-smi` exactly.
Bit 0 is GPU-idle — idle downclocking is not throttling:

```promql
100 * avg_over_time((DCGM_FI_DEV_CLOCK_THROTTLE_REASONS > bool 1)[5m:])
```

Percent of samples under a real cap. Weaker than a true duration, but true.

## Fields in use

`GPU_UTIL`, `MEM_COPY_UTIL`, `ENC_UTIL`, `DEC_UTIL`, `FB_FREE/USED/TOTAL`,
`SM_CLOCK`, `MEM_CLOCK`, `POWER_USAGE`, `ENFORCED_POWER_LIMIT`, `GPU_TEMP`,
`TOTAL_ENERGY_CONSUMPTION`, `CLOCK_THROTTLE_REASONS`, `XID_ERRORS`,
`PCIE_REPLAY_COUNTER`.

Accepted but never populated on consumer hardware: the ECC fields, `MEMORY_TEMP`
(no memory-junction sensor), and `PCIE_TX/RX_THROUGHPUT`. Empty panels read as
"healthy" to anyone who didn't build the board, so nothing that stays empty is
included.

Override the image with `DCGM_EXPORTER_IMAGE` in `.env` if the pinned tag stops
resolving.

## Known trap — do not nest the Alloy config mount

Bind-mounting `gpu/config.gpu.alloy` onto `/etc/alloy/config.gpu.alloy` fails at
container init: `/etc/alloy` is already a read-only bind of `collector/alloy/`,
and runc must create the target file before mounting onto it, which it cannot do
inside a read-only mount. Creating the directory first does not help — the
read-only parent is the blocker. `make collector` therefore **copies** the file
into `collector/alloy/`, where it is gitignored. Deploy is a reset, so the copy
is idempotent, and a node that loses its GPU has it removed.

## Scope

GPU nodes only. Never add this to `docker-compose.armv7.yml` or a non-GPU
node's compose. The base `docker-compose.yml` stays fleet-identical; this
overlay is the node-specific extra — the same pattern the aggregator uses to
keep credentialed jobs in `scrape_configs.d/` rather than the shared
`prometheus.yml`.
