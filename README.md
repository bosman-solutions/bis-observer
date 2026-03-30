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
# Configure both stacks
cp aggregator/.env.example aggregator/.env
cp collector/.env.example collector/.env

$EDITOR aggregator/.env   # set ports if needed (defaults are fine)
$EDITOR collector/.env    # set AGGREGATOR_HOST to THIS node's LAN/WireGuard IP
                          # do not use localhost — host networking bypasses loopback

make aggregator           # starts aggregator + collector (self-monitoring)
```

### Grafana

Navigate to `http://<aggregator-ip>:3000`.
Default credentials: `admin` / `admin`
Grafana will prompt you to set a new password on first login.

Prometheus and Loki are pre-provisioned as data sources — no manual setup needed.

## Make targets

```
make collector    deploy collector stack on this node
make aggregator   deploy aggregator + collector stacks on this node
make down         tear down all running obs stacks on this node
make status       show status of all obs stacks
make help         show available targets
```

## Configuration

### collector/.env

| Variable             | Required | Default       | Description                              |
|----------------------|----------|---------------|------------------------------------------|
| AGGREGATOR_HOST      | ✓        | —             | IP or hostname of the aggregator node    |
| NODE_NAME            |          | system hostname| Label applied to all metrics and logs   |
| NODE_EXPORTER_PORT   |          | 9100          | Override if port is taken on this host   |
| AGGREGATOR_PROM_PORT |          | 9090          | Must match aggregator's Prometheus port  |
| AGGREGATOR_LOKI_PORT |          | 3100          | Must match aggregator's Loki port        |

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
│       └── config.alloy
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
- The aggregator node should run both stacks. `make aggregator` handles this.
- Adding a new node to the fleet: copy the `collector/` directory,
  set `AGGREGATOR_HOST`, run `make collector`. That's it.
- Tested on: Raspbian Trixie, Arch Linux, Ubuntu. Any Linux with Docker works.
