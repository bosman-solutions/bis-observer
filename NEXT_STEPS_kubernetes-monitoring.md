# kubernetes-monitoring branch — next steps before merge to main

## Status as of 2026-04-06
Branch is tested and working on Cerberus. kube_node_info flowing to Melchior.
All steps below must be verified before merging to main.

---

## Before merging

### 1. Verify metrics stability
Let the stack run for at least one full scrape cycle (15s) and confirm
metrics are still flowing to Melchior:

```bash
# on Balthazar
curl -s "http://melchior:9090/api/v1/query?query=kube_node_info" | python3 -m json.tool | grep cluster
```

Expected: `"cluster": "hybrid-cloud-demo"`

### 2. Verify Alloy loaded both configs cleanly
Check Alloy logs for any errors related to config loading:

```bash
# on Cerberus
docker logs obs-alloy --tail 50 | grep -i "error\|warn\|ksm\|kube"
```

No errors = good. If you see config parse errors, do not merge.

### 3. Verify make collector is idempotent
Run make collector a second time and confirm it doesn't break anything:

```bash
# on Cerberus
cd ~/bis-observer
make collector
docker ps --filter name=obs --format "table {{.Names}}\t{{.Status}}"
```

All three containers should still show healthy uptime (not restarted from zero).

### 4. Verify a node WITHOUT KSM_PORT is unaffected
On Balthazar, confirm the standard collector still works with no KSM_PORT set:

```bash
# on Balthazar
cd /opt/services/balthazar-host/bis-observer
make collector
```

Should print:
  KSM_PORT not set — skipping kube-state-metrics scrape config.

And Balthazar's Alloy should keep running normally.

---

## Merge sequence

Once all four checks pass:

```bash
# on Balthazar
cd /opt/services/balthazar-host/bis-observer
git checkout main
git merge kubernetes-monitoring
git push origin main
git branch -d kubernetes-monitoring
git push origin --delete kubernetes-monitoring
```

Then on Cerberus, switch back to main:

```bash
# on Cerberus
cd ~/bis-observer
git fetch origin
git checkout main
git pull origin main
```

Cerberus's local .env (with KSM_PORT and CLUSTER_NAME) stays in place —
it's gitignored, the merge doesn't touch it.

---

## Post-merge

### Grafana breathing panel (next session)
With kube_node_info flowing, build the panel on Melchior:

Key metrics:
  kube_node_info{cluster="hybrid-cloud-demo"}               — node existence
  kube_node_status_condition{condition="Ready",status="true"} — nodes ready
  kube_horizontalpodautoscaler_status_current_replicas       — HPA replicas

Panel type: Time series, last 30 minutes, 10s refresh.
Split by topology.kubernetes.io/zone label to show private vs GCP.

Embed in Flask UI as iframe once dashboard UID is known.

---

*Weaver 🕸️ — 2026-04-06*
