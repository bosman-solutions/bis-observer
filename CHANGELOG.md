# Changelog

Notable changes to bis-observer. Format loosely follows Keep a Changelog;
dates are ISO-8601.

## [Unreleased]

### Added
- Kubernetes cluster monitoring via **aggregator pull**. A `kube-state-metrics-pull`
  job in `prometheus.yml` scrapes KSM through an Ansible-provisioned target file
  (`targets/kube-state-metrics.yml`). A cluster is ingested centrally on the
  aggregator that owns it — never pushed by a collector, never crossing to the LAN.
  An absent target file means zero targets, so the job is a no-op on aggregators
  that do not own a cluster.
- **Per-pod CPU/memory/network** via a `kube-cadvisor-pull` job that scrapes each
  node's kubelet cAdvisor **through the kube-apiserver proxy**
  (`/api/v1/nodes/<node>/proxy/metrics/cadvisor`) — one reachable endpoint, workers
  run nothing extra. Because the job carries a ServiceAccount token + cluster CA, it
  lives in `scrape_configs.d/` (loaded via Prometheus `scrape_config_files`), NOT in
  the fleet-shared `prometheus.yml`: a missing `ca_file` fails config load, so a
  credentialed job in the shared file would brick every cluster-less aggregator.
  A glob matching zero files is a no-op. See
  `scrape_configs.d/kube-cadvisor.yml.example`.
- `make kube` now also bootstraps a least-privilege `obs-cadvisor-reader`
  ServiceAccount + ClusterRole (`nodes/proxy`, `nodes/metrics`) and a long-lived
  token, writing the token + cluster CA to `aggregator/secrets/` (gitignored) and
  printing the apiserver endpoint for the aggregator's scrape config.
- theseus `PodQuery` + routes `GET /api/pod/<ns>/<pod>` (+ `/range`, `/logs`) —
  per-pod cpu%, working-set memory, net IO, and restart count, sourced from the new
  cAdvisor series. Consumed by bis-cadastre's pod scope panel.
- aggrokube cluster CPU/mem overview stats and per-namespace "top pods by CPU"
  panels, now that per-pod usage exists.
- `targets/kube-state-metrics.yml.example` — KSM pull-target shape.
- `scrape_configs.d/kube-cadvisor.yml.example` + `secrets/README.md` — credentialed
  scrape job shape and the secrets directory contract.
- `scripts/envset.sh` — idempotent `KEY=VALUE` upsert for `.env` files.
- `scripts/checkenv.sh` + `make check-env` — advisory drift check against `.env.example`.

### Changed
- `.env` tooling now upserts tool-owned keys (`NODE_NAME`, `KSM_PORT`) in place
  instead of appending, eliminating `.env` drift. Reconciled `collector/.env.example`.
- `make kube` installs KSM + a NodePort and reports the endpoint for the aggregator
  target file; it no longer writes any collector `.env`.
- Standardized Kubernetes config comments to neutral, factual house style.
- `aggrokube.py` workload panels are now **discovery-driven**: Deployments,
  StatefulSets, and DaemonSets are discovered per namespace from KSM labels and
  rendered as per-workload readiness stats. The hardcoded `arcade`/`puzzu` overview
  stat is gone, replaced by a cluster-agnostic "Workloads Ready" rollup. The
  topology sidecar now tracks workloads so the dashboard re-renders when they change.
- `.gitignore` now globs all generated theseus dashboard JSON, including the
  `aggrokube_state.json` / `aggroboard_state.json` sidecars.

### Removed
- Collector-side KSM scrape path (`config.ksm.alloy.tpl`, `KSM_PORT`/`CLUSTER_NAME`
  on the collector). A cluster is a logical entity, not a host — this removes the
  double-ingestion risk between collector push and aggregator pull.

### Fixed
- theseus background runner crashed on startup (`RuntimeError: no current event
  loop in thread`) after the `asyncio.gather` change — this had frozen **both**
  aggroboard and aggrokube generation. The event loop is now bound to the worker
  thread before `run_until_complete`.
