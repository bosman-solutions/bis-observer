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

*Logged: 2026-04-28*

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

*Logged: 2026-06-16*

## Planned

### Push/pull inventory reconciliation
Cross-check what collectors remote_write against what the aggregator pulls from
`/targets`, so nothing is double-counted or silently missed.

### k8s pod log shipping
Per-pod metrics now exist (cadvisor pull) but pod *logs* are not yet ingested —
the collector Alloy ships Docker logs, not k8s pod logs. theseus `PodQuery.log_tail`
is wired but returns empty until a k8s log path lands (Alloy `loki.source.kubernetes`
in-cluster, or apiserver-proxied kubelet logs). Decide the pattern before building.

*Logged: 2026-06-16*

### GPU VRAM / utilization metrics (vfio passthrough)
The GPU is PCI-passthrough'd to whichever VM holds it,
bound to `vfio-pci` on the host — so the host CANNOT read it; `nvidia-smi` only
works inside the holding guest. SCC's `gpu_probe.py` already reads used/total/util
from inside the holder via the qemu-guest-agent (`guest-exec nvidia-smi`). Plan: a
host-side Prometheus exporter on the libvirt host that calls `gpu_probe.query(<current GPU
holder>)` and exposes `gpu_vram_used_bytes`, `gpu_vram_total_bytes`,
`gpu_utilization_percent{vm="…"}`; Alloy scrapes it → remote_write. No in-guest
exporter needed (esp. Windows guests) — the host pulls through the agent.
Depends on qemu-guest-agent being present in each GPU guest. Same probe also powers vmctl's
swap busy-guard, so the data path is shared.

### Windows observability discovery
The Linux collector pattern (node-exporter + cadvisor + Alloy) doesn't fit a
Windows node. Do discovery on how to observe a Windows guest: likely `windows_exporter`
(CPU/RAM/disk/net/services, Prometheus-native) shipped via Alloy-for-Windows or
pulled into a `file_sd` target; plus Apollo (stream service) health/log shipping.
Decide whether it runs a slim Windows collector or is pulled by the
aggregator. Docker-on-Windows is NOT the path (Docker Desktop wants a desktop
session) — a Windows-specific collector + maybe a `Makefile.windows`, not the
Linux compose stack.

*Logged: 2026-06-22*

### Convention: env defaults live in compose, not in .env
`.env` is gitignored, so it cannot be updated by a deploy — which meant every
new setting had to be hand-added on every node or the stack came up missing it.
The fix is to stop putting settings in `.env` at all: give each one a default in
`docker-compose.yml` as `${KEY:-default}`, so a node that has never heard of a
new key still comes up correctly. `.env` then carries only what genuinely
differs per machine, plus secrets.

**When adding a feature:** default it in the compose file, document it in the
README table, leave `.env.example` alone. Only touch `.env.example` if the value
cannot have a safe default.

Current state after this pass: the collector has exactly one required key
(`AGGREGATOR_HOST`, enforced by `make collector`); the aggregator has none.

Not done yet, in order of value:
- **Secrets don't belong in `.env`.** Plaintext on disk, visible in
  `docker inspect`. `GRAFANA_TOKEN` is there now and an HA token is coming.
  Options: ansible-vault (the fleet already has a vault password file) or
  Docker secrets.
- **Regenerate rather than patch.** Ideally `.env` is rebuilt from a template on
  every deploy, the same hard-reset discipline the repo already uses, so nothing
  hand-edited survives. Blocked: that needs a per-node values source, and one
  only exists for the Ansible-managed nodes (`/etc/ansible/hosts` on the node
  running the playbooks). For the CI-deployed nodes the values exist only inside
  the `.env` files themselves. Build one fleet inventory both deployers can read.
- **The Ansible `deploy_collector.yml` copies `.env.example` over `.env` on every
  run**, wiping anything set by hand. Now redundant — `make` handles the file.
  Delete that task. Note the playbook is unversioned and lives in the deploy
  user's home directory, not in this repo.

*Logged: 2026-09-01*

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

*Logged: 2026-06-18*
