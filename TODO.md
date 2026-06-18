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

**Fixed 2026-06-18:** `make kube` now runs a `kubectl cluster-info` preflight;
on failure with a root-only `k3s.yaml` present it prints the `install` remediation
and exits cleanly instead of vomiting a raw helm error.

*Logged: 2026-06-16 — Weaver 🕸️*

## Planned

### Push/pull inventory reconciliation
Cross-check what collectors remote_write against what the aggregator pulls from
`/targets`, so nothing is double-counted or silently missed.

### k8s pod log shipping
Per-pod metrics now exist (cadvisor pull) but pod *logs* are not yet ingested —
the collector Alloy ships Docker logs, not k8s pod logs. theseus `PodQuery.log_tail`
is wired but returns empty until a k8s log path lands (Alloy `loki.source.kubernetes`
in-cluster, or apiserver-proxied kubelet logs). Decide the pattern before building.

*Logged: 2026-06-16 — Weaver 🕸️*

## Done

### Kubelet per-pod usage via apiserver proxy — 2026-06-18
Shipped as the `kube-cadvisor-pull` job in `scrape_configs.d/` (not the shared
`prometheus.yml` — the credential-safety concern was real: a missing `ca_file`
fails config load, so the credentialed job is isolated to the owning aggregator via
`scrape_config_files`, where a missing secret can never break a LAN aggregator).
`make kube` bootstraps the `obs-cadvisor-reader` SA + RBAC + token. theseus exposes
the data through `PodQuery` / `/api/pod/*`.

### aggrokube: de-hardcode the workload panel — 2026-06-18
Done. `aggrokube.py` discovers Deployments/StatefulSets/DaemonSets from KSM labels
and renders per-workload readiness; `arcade`/`puzzu` hardcoding removed.

*Logged: 2026-06-18 — Weaver 🕸️*
