# bis-observer

A portable, self-hosted observability stack built on the Grafana LGTM ecosystem.
Collects metrics and logs from any Docker-capable node and aggregates them into
a single Grafana instance.

## Architecture

```
[ collector node ]          [ collector node ]          [ collector node ]
  node-exporter               node-exporter               node-exporter
  cadvisor                    cadvisor                    cadvisor
  alloy ─────────────────────── alloy ─────────────────── alloy
         remote_write / loki push          │
                    │                      │
                    ▼                      ▼
              [ aggregator node ]
                prometheus  ← receives all metrics
                loki        ← receives all logs
                grafana     ← visualization
                + collector (self-monitoring)
```

Collectors are stateless and identical across all nodes — bare metal, VM,
Raspberry Pi, whatever runs Docker. The aggregator is the only node that
needs to be reachable by all collectors.

## Stack

| Component    | Role                                              | Port (default) |
|--------------|---------------------------------------------------|----------------|
| node-exporter| Host metrics: CPU, RAM, disk, network             | 9100           |
| cAdvisor     | Container metrics                                 | 8080 (lo only) |
| Alloy        | Unified collector: scrapes + ships metrics + logs | 12345          |
| Prometheus   | Metrics store, remote write receiver              | 9090           |
| Loki         | Log store, push receiver                          | 3100           |
| Grafana      | Visualization                                     | 3000           |

Alloy replaces both Promtail and prometheus-agent in a single container.
Promtail reached end-of-life March 2026.

## Quick Start

### Any collector node

```bash
cd collector
cp .env.example .env
$EDITOR .env          # set AGGREGATOR_HOST to the aggregator's IP
make collector
```

### Aggregator node

```bash
cp aggregator/.env.example aggregator/.env
cp collector/.env.example collector/.env

$EDITOR aggregator/.env   # set ports if needed (defaults are fine)
$EDITOR collector/.env    # set AGGREGATOR_HOST to THIS node's LAN/WireGuard IP
                          # do not use localhost — host networking bypasses loopback

make aggregator           # starts aggregator + collector (self-monitoring)
```

### k3s / Kubernetes node

Run `make kube` once before `make collector`. It bootstraps kube-state-metrics
via Helm, exposes it on a NodePort, and writes `KSM_PORT` into `collector/.env`
automatically. After that, `make collector` picks it up on every deploy.

```bash
cp collector/.env.example collector/.env
$EDITOR collector/.env    # set AGGREGATOR_HOST, CLUSTER_NAME

make kube                 # one-time bootstrap (idempotent)
make collector            # deploy collector with KSM scraping enabled
```

`make kube` is safe to re-run — it skips steps that are already complete and
updates `KSM_PORT` in `.env` if the NodePort assignment ever changes.

### Grafana

Navigate to `http://<aggregator-ip>:3000`.
Default credentials: `admin` / `admin`
Grafana prompts for a new password on first login.

Prometheus and Loki are pre-provisioned as data sources.

## Make targets

```
make collector          deploy collector stack on this node
make aggregator         deploy aggregator + collector stacks (aggregator node)
make kube               bootstrap kube-state-metrics on this k3s/k8s node (idempotent)
make restart            restart all running obs stacks on this node
make restart-collector  restart collector stack only
make restart-aggregator restart aggregator stack only
make down               tear down all running obs stacks on this node
make status             show status of all obs stacks
make help               show available targets
```

## Configuration

### collector/.env

| Variable             | Required | Default         | Description                                        |
|----------------------|----------|-----------------|----------------------------------------------------|
| AGGREGATOR_HOST      | ✓        | —               | IP or hostname of the aggregator node              |
| NODE_NAME            |          | system hostname | Label applied to all metrics and logs              |
| NODE_EXPORTER_PORT   |          | 9100            | Override if port is taken on this host             |
| AGGREGATOR_PROM_PORT |          | 9090            | Must match aggregator's Prometheus port            |
| AGGREGATOR_LOKI_PORT |          | 3100            | Must match aggregator's Loki port                  |
| KSM_PORT             |          | —               | NodePort for kube-state-metrics (k8s nodes only)   |
| CLUSTER_NAME         |          | —               | Cluster label in Grafana (k8s nodes only)          |

### aggregator/.env

| Variable             | Required | Default | Description                              |
|----------------------|----------|---------|------------------------------------------|
| AGGREGATOR_PROM_PORT |          | 9090    | Prometheus port exposed on this host     |
| AGGREGATOR_LOKI_PORT |          | 3100    | Loki port exposed on this host           |
| GRAFANA_PORT         |          | 3000    | Grafana port exposed on this host        |
| PROM_RETENTION       |          | 90d     | Prometheus data retention period         |
| LOKI_RETENTION       |          | 744h    | Loki data retention period (31 days)     |


## Repo layout

```
bis-observer/
├── Makefile
├── README.md
├── collector/
│   ├── docker-compose.yml
│   ├── .env.example
│   └── alloy/
│       ├── config.metrics.alloy      # metrics pipeline (node-exporter, cadvisor, remote_write)
│       ├── config.logs.alloy         # logs pipeline (loki.source.docker, syslog, loki.write)
│       └── config.ksm.alloy.tpl      # kube-state-metrics template (k8s nodes only)
│           config.ksm.alloy          # generated by make kube / make collector — gitignored
└── aggregator/
    ├── docker-compose.yml
    ├── .env.example
    └── config/
        ├── prometheus.yml
        ├── loki.yml
        └── grafana/
            └── provisioning/
                └── datasources/
                    └── datasources.yml
```

## Notes

- `.env` files are gitignored — they contain node-specific addressing.
  Always copy from `.env.example` and fill in locally.
- The aggregator node runs both stacks. `make aggregator` handles this by
  calling `make collector` after the aggregator stack is up.
- `make kube` is idempotent — re-running it skips completed steps and
  refreshes `KSM_PORT` in `.env` if needed.
- Adding a new node to the fleet: copy the `collector/` directory,
  set `AGGREGATOR_HOST`, run `make collector`. That's it.
- On k8s nodes: run `make kube` once first, then `make collector`.
- Tested on: Raspbian Trixie, Arch Linux, Ubuntu. Any Linux with Docker works.
