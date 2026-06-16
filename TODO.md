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

### `make kube` unreachable cluster when run as non-root on k3s
**Symptom:** `make kube` fails with
`error loading config file "/etc/rancher/k3s/k3s.yaml": permission denied`,
then `Kubernetes cluster unreachable`.

**Root cause:** k3s writes its admin kubeconfig `0600 root:root`. helm/kubectl
run as the deploying user can't read it.

**Workaround:** give the user a personal copy (do not loosen the root file):
```bash
sudo install -o "$USER" -g "$USER" -m600 /etc/rancher/k3s/k3s.yaml ~/.kube/config
echo 'export KUBECONFIG=$HOME/.kube/config' >> ~/.bashrc
```
Ansible deploys with `become`, so it is unaffected.

**Proper fix candidate:** `make kube` detects an unreachable cluster + a
root-only `k3s.yaml` and prints this remediation instead of a raw helm error.

*Logged: 2026-06-16 — Weaver 🕸️*

## Planned

### Kubelet per-pod usage via apiserver proxy
KSM gives cluster *object state*; not per-pod CPU/memory. Add a second pull job
that scrapes each node's kubelet **through the kube-apiserver proxy**
(`/api/v1/nodes/<node>/proxy/metrics/cadvisor`), so every node is reachable from
one endpoint and workers run nothing extra. Carries a ServiceAccount token +
TLS — verify a credentialed job cannot fail a LAN aggregator's config load
before adding it to the shared `prometheus.yml`.

### Push/pull inventory reconciliation
Cross-check what collectors remote_write against what the aggregator pulls from
`/targets`, so nothing is double-counted or silently missed.

### aggrokube: de-hardcode the workload panel
`aggrokube.py` hardcodes `namespace="arcade", deployment="puzzu"` in a stat
panel. Make it discovery-driven so the dashboard is cluster-agnostic.

*Logged: 2026-06-16 — Weaver 🕸️*
