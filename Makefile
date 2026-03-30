# =============================================================================
# observability-demo
# =============================================================================
# make collector          — deploy collector stack (any node)
# make aggregator         — deploy aggregator + collector stacks (aggregator node)
# make restart            — restart all running obs stacks on this node
# make restart-collector  — restart collector stack only
# make restart-aggregator — restart aggregator stack only
# make down               — tear down whichever stacks are running on this node
# make status             — show running stack status
# make help               — show this message
# =============================================================================

COLLECTOR_DIR  := ./collector
AGGREGATOR_DIR := ./aggregator

.PHONY: help collector aggregator restart restart-collector restart-aggregator down status

help:
	@echo ""
	@echo "  observability-demo"
	@echo ""
	@echo "  make collector          deploy collector stack (any node in the fleet)"
	@echo "  make aggregator         deploy aggregator + collector stacks (aggregator node)"
	@echo "  make restart            restart all running obs stacks on this node"
	@echo "  make restart-collector  restart collector stack only"
	@echo "  make restart-aggregator restart aggregator stack only"
	@echo "  make down               tear down all running obs stacks on this node"
	@echo "  make status             show status of all obs stacks"
	@echo ""
	@echo "  Notes:"
	@echo "    - Copy .env.example to .env in each stack directory before deploying"
	@echo "    - Aggregator node: set AGGREGATOR_HOST to this node's LAN/WireGuard IP"
	@echo "    - Collector nodes: set AGGREGATOR_HOST to the aggregator's IP"
	@echo ""

collector:
	@echo "→ Deploying collector stack..."
	@if [ ! -f $(COLLECTOR_DIR)/.env ]; then \
		echo "  ERROR: $(COLLECTOR_DIR)/.env not found."; \
		echo "         cp $(COLLECTOR_DIR)/.env.example $(COLLECTOR_DIR)/.env and set AGGREGATOR_HOST"; \
		exit 1; \
	fi
	docker compose -f $(COLLECTOR_DIR)/docker-compose.yml --env-file $(COLLECTOR_DIR)/.env up -d
	@echo "✓ Collector stack running."

aggregator:
	@echo "→ Deploying aggregator stack..."
	@if [ ! -f $(AGGREGATOR_DIR)/.env ]; then \
		echo "  ERROR: $(AGGREGATOR_DIR)/.env not found."; \
		echo "         cp $(AGGREGATOR_DIR)/.env.example $(AGGREGATOR_DIR)/.env"; \
		exit 1; \
	fi
	docker compose -f $(AGGREGATOR_DIR)/docker-compose.yml --env-file $(AGGREGATOR_DIR)/.env up -d
	@echo "✓ Aggregator stack running."
	@echo "→ Deploying collector stack (self-monitoring)..."
	@if [ ! -f $(COLLECTOR_DIR)/.env ]; then \
		echo "  ERROR: $(COLLECTOR_DIR)/.env not found."; \
		echo "         cp $(COLLECTOR_DIR)/.env.example $(COLLECTOR_DIR)/.env and set AGGREGATOR_HOST to this node's IP"; \
		exit 1; \
	fi
	docker compose -f $(COLLECTOR_DIR)/docker-compose.yml --env-file $(COLLECTOR_DIR)/.env up -d
	@echo "✓ Collector stack running."

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
