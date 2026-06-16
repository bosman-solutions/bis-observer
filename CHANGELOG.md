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
- `targets/kube-state-metrics.yml.example` — KSM pull-target shape.
- `scripts/envset.sh` — idempotent `KEY=VALUE` upsert for `.env` files.
- `scripts/checkenv.sh` + `make check-env` — advisory drift check against `.env.example`.

### Changed
- `.env` tooling now upserts tool-owned keys (`NODE_NAME`, `KSM_PORT`) in place
  instead of appending, eliminating `.env` drift. Reconciled `collector/.env.example`.
- `make kube` installs KSM + a NodePort and reports the endpoint for the aggregator
  target file; it no longer writes any collector `.env`.
- Standardized Kubernetes config comments to neutral, factual house style.
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
