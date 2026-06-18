# =============================================================================
# observability-demo
# =============================================================================
# make collector    — deploy collector stack (any node)
# make aggregator   — deploy aggregator + collector stacks (aggregator node)
# make kube         — bootstrap kube-state-metrics on a k3s/k8s node (idempotent)
# make restart      — restart all running obs stacks on this node
# make restart-collector  — restart collector stack only
# make restart-aggregator — restart aggregator stack only
# make down         — tear down whichever stacks are running on this node
# make status       — show running stack status
# make help         — show this message
# =============================================================================

COLLECTOR_DIR  := ./collector
AGGREGATOR_DIR := ./aggregator
HOSTNAME       := $(shell hostname)
ARCH           := $(shell uname -m)
ENVSET         := sh ./scripts/envset.sh      # idempotent KEY=VALUE upsert
CHECKENV       := sh ./scripts/checkenv.sh     # required-key drift check

.PHONY: help collector aggregator theseus kube check-env restart restart-collector restart-aggregator down status

help:
	@echo ""
	@echo "  observability-demo"
	@echo ""
	@echo "  make collector    deploy collector stack (any node in the fleet)"
	@echo "  make aggregator   deploy aggregator + collector stacks (aggregator node)"
	@echo "  make theseus      build and deploy obs-theseus intelligence sidecar"
	@echo "  make kube         bootstrap kube-state-metrics on this k3s/k8s node (idempotent)"
	@echo "  make restart      restart all running obs stacks on this node"
	@echo "  make restart-collector  restart collector stack only"
	@echo "  make restart-aggregator restart aggregator stack only"
	@echo "  make down         tear down all running obs stacks on this node"
	@echo "  make status       show status of all obs stacks"
	@echo "  make check-env    warn about required keys missing from .env files"
	@echo ""
	@echo "  Notes:"
	@echo "    - Copy .env.example to .env in each stack directory before deploying"
	@echo "    - Aggregator node: set AGGREGATOR_HOST to this node's LAN/WireGuard IP"
	@echo "    - Collector nodes: set AGGREGATOR_HOST to the aggregator's IP"
	@echo "    - NODE_NAME defaults to system hostname ($(HOSTNAME)) if not set in .env"
	@echo "    - On k3s/k8s nodes: run make kube once before make collector"
	@echo ""

# Stamp NODE_NAME into collector .env if not already set.
# Upserts in place via envset.sh — never appends blindly (no drift).
_set_node_name:
	@if [ -f $(COLLECTOR_DIR)/.env ] && ! grep -qE '^NODE_NAME=[^[:space:]]' $(COLLECTOR_DIR)/.env 2>/dev/null; then \
		echo "  NODE_NAME not set — using hostname: $(HOSTNAME)"; \
		$(ENVSET) $(COLLECTOR_DIR)/.env NODE_NAME $(HOSTNAME); \
	fi

collector: _set_node_name
	@echo "→ Deploying collector stack..."
	@if [ ! -f $(COLLECTOR_DIR)/.env ]; then \
		echo "  ERROR: $(COLLECTOR_DIR)/.env not found."; \
		echo "         cp $(COLLECTOR_DIR)/.env.example $(COLLECTOR_DIR)/.env and set AGGREGATOR_HOST"; \
		exit 1; \
	fi
	@if [ "$(ARCH)" = "armv7l" ]; then \
		echo "  armv7 detected — deploying slim collector (no Alloy, no cAdvisor)..."; \
		docker compose -f $(COLLECTOR_DIR)/docker-compose.armv7.yml --env-file $(COLLECTOR_DIR)/.env up -d; \
	else \
		docker compose -f $(COLLECTOR_DIR)/docker-compose.yml --env-file $(COLLECTOR_DIR)/.env up -d; \
		echo "  restarting alloy to load config changes (bind mounts don't trigger recreate)..."; \
		docker compose -f $(COLLECTOR_DIR)/docker-compose.yml --env-file $(COLLECTOR_DIR)/.env restart alloy; \
	fi
	@echo "✓ Collector stack running."

aggregator: _set_node_name
	@echo "→ Deploying aggregator stack..."
	@if [ ! -f $(AGGREGATOR_DIR)/.env ]; then \
		echo "  ERROR: $(AGGREGATOR_DIR)/.env not found."; \
		echo "         cp $(AGGREGATOR_DIR)/.env.example $(AGGREGATOR_DIR)/.env"; \
		exit 1; \
	fi
	docker compose -f $(AGGREGATOR_DIR)/docker-compose.yml --env-file $(AGGREGATOR_DIR)/.env up -d --build
	@echo "✓ Aggregator stack running."
	@$(MAKE) collector

theseus:
	@echo "→ Building and deploying obs-theseus..."
	docker compose -f $(AGGREGATOR_DIR)/docker-compose.yml --env-file $(AGGREGATOR_DIR)/.env up -d --build theseus
	@echo "✓ obs-theseus running."

# Bootstrap kube-state-metrics on a k3s/k8s node.
# Idempotent — safe to run multiple times.
#
# KSM is ingested by the EDGE AGGREGATOR as a pull target, not by a
# collector. This target installs KSM + exposes a NodePort, then prints
# the address to drop into the aggregator's Ansible-managed target file
# (aggregator/targets/kube-state-metrics.yml). It does not touch any .env.
kube:
	@echo "→ Preflight: Kubernetes reachability..."
	@if ! kubectl cluster-info >/dev/null 2>&1; then \
		echo ""; \
		if [ -f /etc/rancher/k3s/k3s.yaml ] && [ ! -r /etc/rancher/k3s/k3s.yaml ]; then \
			echo "  ✗ k3s kubeconfig is root-only (0600 root:root); $$USER cannot read it."; \
			echo "    Stage a personal copy (does NOT loosen the root file), then re-run make kube:"; \
			echo ""; \
			echo "      mkdir -p ~/.kube"; \
			echo "      sudo install -o $$USER -g $$USER -m600 /etc/rancher/k3s/k3s.yaml ~/.kube/config"; \
			echo '      echo '"'"'export KUBECONFIG=$$HOME/.kube/config'"'"' >> ~/.bashrc'; \
			echo '      export KUBECONFIG=$$HOME/.kube/config'; \
		else \
			echo "  ✗ Kubernetes cluster unreachable — is the cluster up and KUBECONFIG set?"; \
		fi; \
		echo ""; \
		exit 1; \
	fi
	@echo "  ✓ cluster reachable."
	@echo "→ Bootstrapping kube-state-metrics..."
	@if ! helm repo list 2>/dev/null | grep -q prometheus-community; then \
		echo "  Adding prometheus-community helm repo..."; \
		helm repo add prometheus-community https://prometheus-community.github.io/helm-charts; \
	else \
		echo "  prometheus-community repo already present."; \
	fi
	helm repo update
	@if ! helm list -n monitoring 2>/dev/null | grep -q kube-state-metrics; then \
		echo "  Installing kube-state-metrics..."; \
		helm install kube-state-metrics prometheus-community/kube-state-metrics \
			--namespace monitoring --create-namespace; \
	else \
		echo "  kube-state-metrics already installed — upgrading..."; \
		helm upgrade kube-state-metrics prometheus-community/kube-state-metrics \
			--namespace monitoring; \
	fi
	@echo "  Waiting for KSM pod to be ready..."
	kubectl rollout status deployment/kube-state-metrics -n monitoring --timeout=60s
	@if ! kubectl get svc ksm-nodeport -n monitoring 2>/dev/null | grep -q ksm-nodeport; then \
		echo "  Exposing KSM via NodePort..."; \
		kubectl expose deployment kube-state-metrics \
			--type=NodePort --port=8080 --name=ksm-nodeport -n monitoring; \
	else \
		echo "  ksm-nodeport service already exists."; \
	fi
	$(eval KSM_PORT := $(shell kubectl get svc ksm-nodeport -n monitoring -o jsonpath='{.spec.ports[0].nodePort}'))
	@echo "✓ kube-state-metrics ready — NodePort $(KSM_PORT)"
	@echo "  Aggregator target: aggregator/targets/kube-state-metrics.yml"
	@echo "  Shape: aggregator/targets/kube-state-metrics.yml.example"
	@echo ""
	@echo "→ Bootstrapping cAdvisor reader (per-pod CPU/mem via apiserver proxy)..."
	@echo "  Applying ServiceAccount + RBAC (nodes/proxy, nodes/metrics)..."
	@printf '%s\n' \
		'apiVersion: v1' \
		'kind: ServiceAccount' \
		'metadata: { name: obs-cadvisor-reader, namespace: monitoring }' \
		'---' \
		'apiVersion: rbac.authorization.k8s.io/v1' \
		'kind: ClusterRole' \
		'metadata: { name: obs-cadvisor-reader }' \
		'rules:' \
		'- apiGroups: [""]' \
		'  resources: ["nodes/proxy", "nodes/metrics", "nodes/stats"]' \
		'  verbs: ["get", "list"]' \
		'---' \
		'apiVersion: rbac.authorization.k8s.io/v1' \
		'kind: ClusterRoleBinding' \
		'metadata: { name: obs-cadvisor-reader }' \
		'roleRef: { apiGroup: rbac.authorization.k8s.io, kind: ClusterRole, name: obs-cadvisor-reader }' \
		'subjects:' \
		'- kind: ServiceAccount' \
		'  name: obs-cadvisor-reader' \
		'  namespace: monitoring' \
		'---' \
		'apiVersion: v1' \
		'kind: Secret' \
		'metadata:' \
		'  name: obs-cadvisor-reader-token' \
		'  namespace: monitoring' \
		'  annotations: { kubernetes.io/service-account.name: obs-cadvisor-reader }' \
		'type: kubernetes.io/service-account-token' \
		| kubectl apply -f -
	@echo "  Waiting for the token controller to populate the secret..."
	@for i in 1 2 3 4 5 6 7 8 9 10; do \
		kubectl get secret obs-cadvisor-reader-token -n monitoring -o jsonpath='{.data.token}' 2>/dev/null | grep -q . && break; \
		sleep 1; \
	done
	@mkdir -p $(AGGREGATOR_DIR)/secrets
	@kubectl get secret obs-cadvisor-reader-token -n monitoring -o jsonpath='{.data.token}'  | base64 -d > $(AGGREGATOR_DIR)/secrets/kube-token
	@kubectl get secret obs-cadvisor-reader-token -n monitoring -o jsonpath='{.data.ca\.crt}' | base64 -d > $(AGGREGATOR_DIR)/secrets/kube-ca.crt
	@# Perms matter here: the dir must stay traversable (755) and the files
	@# readable (644) by the prometheus container's non-root uid. Do NOT 600 the
	@# directory — that strips the x bit and breaks both git checkout and the
	@# container's bind-mount reads. The host is the trust boundary; the token is
	@# least-privilege (read-only nodes/proxy + metrics).
	@chmod 755 $(AGGREGATOR_DIR)/secrets
	@chmod 644 $(AGGREGATOR_DIR)/secrets/kube-token $(AGGREGATOR_DIR)/secrets/kube-ca.crt
	$(eval APISERVER_IP := $(shell kubectl get nodes -l node-role.kubernetes.io/control-plane -o jsonpath='{.items[0].status.addresses[?(@.type=="InternalIP")].address}' 2>/dev/null))
	@echo "✓ cAdvisor reader ready."
	@echo ""
	@echo "  ───────────────────────────────────────────────────────────────"
	@echo "  Wrote (gitignored, staged for the aggregator that owns this cluster):"
	@echo "    $(AGGREGATOR_DIR)/secrets/kube-token"
	@echo "    $(AGGREGATOR_DIR)/secrets/kube-ca.crt"
	@echo ""
	@echo "  On the OWNING aggregator, Ansible should place those two files in"
	@echo "  aggregator/secrets/ and drop a scrape_configs.d/kube-cadvisor.yml with:"
	@if [ -n "$(APISERVER_IP)" ]; then \
		echo "    apiserver : https://$(APISERVER_IP):6443   (control-plane InternalIP — reachable from the obs-prometheus container; do NOT use 127.0.0.1)"; \
	else \
		echo "    apiserver : https://<control-plane-internal-ip>:6443   (kubectl get nodes -o wide; NOT 127.0.0.1 — that's localhost inside the container)"; \
	fi
	@echo "    one target entry per node (kubectl get nodes), label cluster=<name>"
	@echo "  Shape: aggregator/scrape_configs.d/kube-cadvisor.yml.example"
	@echo "  ───────────────────────────────────────────────────────────────"

restart-collector:
	@echo "→ Restarting collector stack..."
	docker compose -f $(COLLECTOR_DIR)/docker-compose.yml restart
	@echo "✓ Done."

restart-aggregator:
	@echo "→ Restarting aggregator stack..."
	docker compose -f $(AGGREGATOR_DIR)/docker-compose.yml restart
	@echo "✓ Done."

restart:
	@echo "→ Restarting all obs stacks..."
	@if docker compose ls 2>/dev/null | grep -q obs-aggregator; then \
		docker compose -f $(AGGREGATOR_DIR)/docker-compose.yml restart; \
	fi
	@if docker compose ls 2>/dev/null | grep -q obs-collector; then \
		docker compose -f $(COLLECTOR_DIR)/docker-compose.yml restart; \
	fi
	@echo "✓ Done."

down:
	@echo "→ Checking for running obs stacks..."
	@if docker compose ls --filter name=obs-aggregator 2>/dev/null | grep -q obs-aggregator; then \
		echo "  Stopping aggregator stack..."; \
		docker compose -f $(AGGREGATOR_DIR)/docker-compose.yml down; \
	fi
	@if docker compose ls --filter name=obs-collector 2>/dev/null | grep -q obs-collector; then \
		echo "  Stopping collector stack..."; \
		docker compose -f $(COLLECTOR_DIR)/docker-compose.yml down; \
	fi
	@echo "✓ Done."

status:
	@echo "=== Aggregator Stack ==="
	@docker compose -f $(AGGREGATOR_DIR)/docker-compose.yml ps 2>/dev/null || echo "  not running"
	@echo ""
	@echo "=== Collector Stack ==="
	@docker compose -f $(COLLECTOR_DIR)/docker-compose.yml ps 2>/dev/null || echo "  not running"

# Advisory drift check: warn if a required key from .env.example is unset
# in the corresponding .env. Never blocks — just surfaces missing config.
check-env:
	@echo "→ Checking env files..."
	@$(CHECKENV) $(COLLECTOR_DIR)/.env.example $(COLLECTOR_DIR)/.env
	@$(CHECKENV) $(AGGREGATOR_DIR)/.env.example $(AGGREGATOR_DIR)/.env
