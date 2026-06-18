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

## Permissions (read this before you `chmod`)

| Path                | Mode | Why                                                       |
|---------------------|------|-----------------------------------------------------------|
| `secrets/` (dir)    | 755  | Must stay **traversable** — git checks it out, and the    |
|                     |      | prometheus container (non-root uid) traverses it to read. |
| `kube-token`, `kube-ca.crt` | 644 | The prometheus container runs as a non-root uid and must read these via the bind mount. |

**Do NOT `chmod 600` the directory.** That strips the `x` (search) bit, so
nothing — not even the owner, not git, not the container — can enter it. It
breaks `git reset --hard` on deploy *and* the container's reads. `make kube`
sets these modes for you.

The host is the trust boundary here and the token is least-privilege (read-only
`nodes/proxy` + `nodes/metrics`). To lock the files tighter, `chown` them to the
prometheus container's uid and use `640` — but never `600`-as-your-user, the
container won't be able to read them.
