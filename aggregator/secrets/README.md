# aggregator/secrets/

Credential files for credentialed scrape jobs (see `../scrape_configs.d/`).
Mounted read-only into the Prometheus container at `/etc/prometheus/secrets/`.

**Everything in this directory except this README is gitignored.** Files here
are provisioned by Ansible (or `make kube`) ONLY on the aggregator that owns a
cluster. On every other aggregator this directory stays empty and nothing
references it.

Expected files on a cluster-owning aggregator:

| File             | What it is                                              |
|------------------|---------------------------------------------------------|
| `kube-token`     | ServiceAccount bearer token (nodes/proxy + metrics RBAC)|
| `kube-ca.crt`    | The cluster's API server CA certificate                 |

Generate both with `make kube` on the cluster — it bootstraps the
ServiceAccount + RBAC, then prints the token, the CA, and the apiserver
endpoint to drop into `scrape_configs.d/kube-cadvisor.yml`.
