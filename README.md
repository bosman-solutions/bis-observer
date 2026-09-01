# bis-observer

![Prometheus](https://img.shields.io/badge/Prometheus-E6522C?logo=prometheus&logoColor=white)
![Loki](https://img.shields.io/badge/Loki-F5A800?logo=grafana&logoColor=black)
![Grafana](https://img.shields.io/badge/Grafana-F46800?logo=grafana&logoColor=white)
![Alloy](https://img.shields.io/badge/Alloy-F46800?logo=grafana&logoColor=white)
![Docker](https://img.shields.io/badge/Docker-2496ED?logo=docker&logoColor=white)
![Kubernetes](https://img.shields.io/badge/Kubernetes-326CE5?logo=kubernetes&logoColor=white)
![Ansible](https://img.shields.io/badge/Ansible-EE0000?logo=ansible&logoColor=white)
![Linux](https://img.shields.io/badge/Linux-FCC624?logo=linux&logoColor=black)
![Self-hosted](https://img.shields.io/badge/self--hosted-000000)

A portable, self-hosted observability stack built on the Grafana LGTM ecosystem.
Collects metrics and logs from any Docker-capable node and aggregates them into
a single Grafana instance.

## Architecture

```
[ collector ]        [ collector ]        [ collector ]
 node-exporter        node-exporter        node-exporter
 cadvisor             cadvisor             cadvisor
 alloy                alloy                alloy
      └────────── remote_write / loki push ──────────┘
                          │
                          ▼
                  [ aggregator ]
                    prometheus   ← all metrics
                    loki         ← all logs
                    grafana      ← visualization
                    theseus      ← dashboard generation
                    + collector  ← self-monitoring
```

Collectors are stateless and identical on every node — bare metal, VM, SBC,
anything running Docker. The aggregator is the only node that has to be
reachable by all of them.

Two ingestion paths feed the aggregator:

- **Push** — full collectors remote_write metrics and push logs, as drawn.
- **Pull** — nodes that can't run Alloy (armv7, IoT) and Kubernetes clusters are
  scraped by Prometheus from `file_sd` lists in `aggregator/targets/`.

Target files and per-node addressing are provisioned by Ansible from the fleet
inventory.

## Stack

| Component          | Role                                        | Default port    |
|--------------------|---------------------------------------------|-----------------|
| node-exporter      | Host metrics: CPU, RAM, disk, network       | 9100            |
| cAdvisor           | Container metrics                           | 8080 (lo only)  |
| Alloy              | Scrapes and ships metrics + logs            | 12345           |
| docker-inventory   | Container inventory, incl. dead containers  | 9338            |
| Prometheus         | Metrics store, remote-write receiver        | 9090            |
| Loki               | Log store, push receiver                    | 3100            |
| Grafana            | Visualization                               | 3000            |
| theseus            | Generates dashboards from live inventory    | 8182            |
| kube-state-metrics | Cluster object state (k8s only)             | 8080 → NodePort |
| kubelet cAdvisor   | Per-pod CPU/mem/net (k8s only, via apiserver proxy) | 6443    |

Alloy replaces both Promtail and prometheus-agent in one container; Promtail
reached end-of-life in March 2026.

## Quick start

**Collector node**

```bash
cp collector/.env.example collector/.env
$EDITOR collector/.env        # set AGGREGATOR_HOST
make collector
```

**Aggregator node** — runs both stacks; `make aggregator` calls `make collector`.

```bash
cp collector/.env.example collector/.env
$EDITOR collector/.env        # AGGREGATOR_HOST = this node's own LAN/VPN IP,
                              # not localhost — host networking bypasses loopback
make aggregator
```

The aggregator's own `.env` is optional; every key there has a default and the
file is seeded automatically if absent.

Then open `http://<aggregator>:3000`. Login is `admin` / `admin`; Grafana
prompts for a new password on first login. Prometheus and Loki are
pre-provisioned as data sources.

## Kubernetes

A cluster is ingested by the aggregator as a pull target, never by a collector.
Run once on the cluster:

```bash
make kube        # idempotent
```

That installs kube-state-metrics via Helm, exposes it on a NodePort, creates a
least-privilege ServiceAccount (`nodes/proxy`, `nodes/metrics`, `pods/log`),
writes its token and the cluster CA to `aggregator/secrets/`, and prints the
apiserver endpoint. Then, on the aggregator that owns the cluster:

| What | Where | Shape |
|------|-------|-------|
| Cluster object state | `aggregator/targets/kube-state-metrics.yml` | `targets/kube-state-metrics.yml.example` |
| Per-pod CPU/mem/net | `aggregator/scrape_configs.d/kube-cadvisor.yml` | `scrape_configs.d/kube-cadvisor.yml.example` |
| Pod logs | `K8S_API_URL` in `aggregator/.env` | apiserver endpoint from `make kube` |

Both files are Ansible-provisioned. An aggregator with neither scrapes no
cluster, which is correct for one that doesn't own a cluster.

Credentialed jobs live in `scrape_configs.d/`, not `prometheus.yml`, because a
missing `ca_file` fails Prometheus config load outright — one in the
fleet-shared config would brick every aggregator lacking the secret. A glob
matching zero files is a no-op, so the isolation is load-bearing.

Pod logs have no shipper and no DaemonSet, deliberately: armv7 workers can't run
Alloy. theseus tails them from the apiserver at
`GET /api/pod/<namespace>/<pod>/logs`. Live tail only, no history.

## Make targets

```
make collector           deploy collector stack on this node
make aggregator          deploy aggregator + collector stacks
make kube                bootstrap kube-state-metrics on this cluster
make check-env           warn about required keys missing from .env
make restart             restart all running stacks on this node
make restart-collector   restart collector stack only
make restart-aggregator  restart aggregator stack only
make down                tear down running stacks on this node
make status              show stack status
make help                list targets
```

## Configuration

Defaults live in `docker-compose.yml` as `${KEY:-default}`, **not** in `.env`.
A node that has never heard of a new key still comes up correctly. Put a key in
`.env` only when the value differs per machine, or when it is a secret.

Adding a feature? Default it in the compose file, add a row here, and leave
`.env.example` alone.

### collector/.env

| Variable             | Required | Default         | Description                             |
|----------------------|----------|-----------------|-----------------------------------------|
| AGGREGATOR_HOST      | ✓        | —               | Address of the aggregator node          |
| NODE_NAME            |          | system hostname | Label applied to all metrics and logs   |
| NODE_EXPORTER_PORT   |          | 9100            | Override on port conflict               |
| INVENTORY_PORT       |          | 9338            | docker-inventory exporter               |
| AGGREGATOR_PROM_PORT |          | 9090            | Must match the aggregator               |
| AGGREGATOR_LOKI_PORT |          | 3100            | Must match the aggregator               |

`AGGREGATOR_HOST` is the only required key anywhere; `make collector` refuses to
deploy without it. A collector pointed at nothing looks healthy and reports
nothing, so it fails loudly instead.

### aggregator/.env

Nothing is required.

| Variable             | Default                 | Description                                      |
|----------------------|-------------------------|--------------------------------------------------|
| AGGREGATOR_PROM_PORT | 9090                    | Prometheus port on this host                     |
| AGGREGATOR_LOKI_PORT | 3100                    | Loki port on this host                           |
| GRAFANA_PORT         | 3000                    | Grafana port on this host                        |
| THESEUS_PORT         | 8182                    | theseus port on this host                        |
| PROM_RETENTION       | 90d                     | Prometheus retention                             |
| LOKI_RETENTION       | 744h                    | Loki retention (31 days)                         |
| GRAFANA_ANON         | false                   | Anonymous Viewer-only read; on for a public map  |
| AGGROBOARD_INTERVAL  | 60                      | theseus heartbeat, seconds                       |
| GRAFANA_URL          | http://obs-grafana:3000 | In-network Grafana address theseus calls         |
| GRAFANA_EXTERNAL_URL | *(GRAFANA_URL)*         | Browser-facing Grafana address for deeplinks     |
| GRAFANA_TOKEN        | *(none)*                | Grafana service account token (Viewer). Secret.  |
| K8S_API_URL          | *(none)*                | Control-plane API; enables pod log tailing       |
| K8S_CONTROL_PLANE    | *(none)*                | Comma-separated control-plane node names         |

Set `GRAFANA_EXTERNAL_URL` on any aggregator whose dashboard links are opened
from a browser — the default only resolves inside the Docker network, so links
handed outside it will be dead.

## Layout

```
collector/     stateless, identical on every node
  gpu/         dcgm overlay, GPU-holding nodes only
  gpu-exporter/  host-side VRAM exporter for passthrough GPUs
  alloy/       scrape + remote_write, docker/syslog → loki
aggregator/    prometheus, loki, grafana, theseus
  targets/          file_sd pull targets (Ansible-provisioned)
  scrape_configs.d/ credentialed jobs, owning aggregator only
  secrets/          SA token + cluster CA (gitignored)
scripts/       envset.sh (idempotent upsert), checkenv.sh (drift check)
```

## Notes

- `.env` files are gitignored — they carry node-specific addressing. Copy from
  `.env.example`, or let Ansible place them.
- Deploys are a **reset, not a pull**: each node force-checks-out the pushed
  commit and discards local edits. Author in a clone and push; never hand-edit a
  deploy path.
- Adding a node: copy the repo, set `AGGREGATOR_HOST`, `make collector`.
- Adding a cluster: `make kube` on it, then place its target files on the
  aggregator that owns it.
- Tested on Debian/Raspbian, Arch, and Ubuntu. Any Linux with Docker works.
