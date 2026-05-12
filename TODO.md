# bis-observer — TODO

## Known Issues

### Prometheus loses Docker network alias on independent restart
**Symptom:** Grafana dashboard shows "No data" / "server misbehaving" errors.
Grafana can't resolve the `prometheus` hostname inside the container network.

**Root cause:** When Docker daemon restarts `obs-prometheus` independently
(host reboot, OOM kill, manual `docker restart`) rather than through
`docker compose`, the container comes back without its network aliases
(`prometheus`, `obs-prometheus`) registered on the `obs-aggregator` network.

**Workaround:** Run `make aggregator` to recreate the full stack via compose,
which correctly re-registers all network aliases.

**Proper fix:** Add a healthcheck to the prometheus service and update
grafana's `depends_on` to `condition: service_healthy`.

```yaml
# aggregator/docker-compose.yml — prometheus service
healthcheck:
  test: ["CMD", "wget", "--quiet", "--tries=1", "--spider", "http://localhost:9090/-/healthy"]
  interval: 15s
  timeout: 5s
  retries: 3

# aggregator/docker-compose.yml — grafana service
depends_on:
  prometheus:
    condition: service_healthy
  loki:
    condition: service_started
```

*Logged: 2026-04-28 — Weaver 🕸️*
