SHELL := /bin/bash
# Plan root: one directory per Bamboo plan, holding its spec + the code it runs.
LAB := bamboo-specs/src/main/java/lab
CLUSTER ?=
TYPE ?=
ADDONS ?=

.DEFAULT_GOAL := help

.PHONY: help
help: ## Show targets
	@grep -E '^[a-zA-Z_-]+:.*?## ' $(MAKEFILE_LIST) | awk 'BEGIN{FS=":.*?## "};{printf "  %-16s %s\n", $$1, $$2}'

.PHONY: setup
setup: ## One-time: namespace, db secret, ssh keypair, helm repos
	kubectl get ns ci >/dev/null 2>&1 || kubectl create ns ci
	kubectl -n ci get secret bamboo-db-creds >/dev/null 2>&1 || \
	  kubectl -n ci create secret generic bamboo-db-creds \
	    --from-literal=username=bamboo \
	    --from-literal=password="$$(openssl rand -hex 16)"
	[ -f ~/.forgelab/id_ed25519 ] || \
	  (mkdir -p ~/.forgelab && ssh-keygen -t ed25519 -N '' -f ~/.forgelab/id_ed25519)
	helm repo add atlassian-data-center https://atlassian.github.io/data-center-helm-charts >/dev/null
	helm repo add bitnami https://charts.bitnami.com/bitnami >/dev/null
	helm repo update >/dev/null

.PHONY: bootstrap
bootstrap: setup bamboo-secrets ## One command: full unattended stack (no setup wizard)
	helm upgrade --install postgres bitnami/postgresql -n ci -f infra/helm/postgres-values.yaml --version 18.8.0
	helm upgrade --install bamboo atlassian-data-center/bamboo -n ci -f infra/helm/bamboo-values.yaml --version 2.0.14
	@echo "Waiting for Bamboo to finish unattended setup (first boot builds the schema)..."
	kubectl -n ci rollout status statefulset/bamboo --timeout=600s
	@echo ""
	@echo "Bamboo up, licensed, broker on 54663. Login admin/admin at http://localhost:8085"
	@echo "Finish with 'make bamboo' (port-forward + host agent in one terminal),"
	@echo "then approve the agent once: Administration > Agents"

.PHONY: bamboo-secrets
bamboo-secrets: ## Create/refresh unattended-setup secrets (license, admin, agent token)
	@key="$$(infra/scripts/get_license.py)" || exit 1; \
	  kubectl -n ci create secret generic bamboo-license \
	    --from-literal=license="$$key" --dry-run=client -o yaml | kubectl apply -f -
	kubectl -n ci create secret generic bamboo-sysadmin \
	  --from-literal=username=admin --from-literal=password=admin \
	  --from-literal=displayName=Admin --from-literal=emailAddress=admin@forge.lab \
	  --dry-run=client -o yaml | kubectl apply -f -
	@# Token must stay stable so server and agent keep matching — create once only.
	@kubectl -n ci get secret bamboo-agent-token >/dev/null 2>&1 || \
	  kubectl -n ci create secret generic bamboo-agent-token \
	    --from-literal=security-token="$$(xxd -l 20 -p /dev/urandom)"

.PHONY: up
up: ## Install/upgrade Postgres + Bamboo (needs 'make bamboo-secrets' first)
	helm upgrade --install postgres bitnami/postgresql -n ci -f infra/helm/postgres-values.yaml --version 18.8.0
	helm upgrade --install bamboo atlassian-data-center/bamboo -n ci -f infra/helm/bamboo-values.yaml --version 2.0.14
	@echo "Run 'make ui' then open http://localhost:8085 (admin/admin if secrets were set)"

.PHONY: down
down: ## Uninstall CI stack (PVCs survive)
	helm uninstall bamboo -n ci || true
	helm uninstall postgres -n ci || true

.PHONY: reset
reset: ## DESTRUCTIVE: wipe Bamboo state (PVCs + DB) and reinstall clean
	@echo "This deletes Bamboo local-home, shared-home, and the bamboo database."
	@echo "Plans are safe (re-publish with 'make specs-publish'). Ctrl-C to abort."
	@read -p "Type 'wipe' to continue: " a; [ "$$a" = wipe ] || (echo aborted; exit 1)
	helm uninstall bamboo -n ci || true
	kubectl -n ci wait --for=delete pod/bamboo-0 --timeout=120s || true
	kubectl -n ci delete pvc local-home-bamboo-0 bamboo-shared-home --ignore-not-found
	@pw=$$(kubectl -n ci get secret bamboo-db-creds -o jsonpath='{.data.password}' | base64 -d); \
	  kubectl -n ci exec postgres-postgresql-0 -- env PGPASSWORD="$$pw" \
	    psql -U bamboo -d postgres \
	    -c "DROP DATABASE IF EXISTS bamboo WITH (FORCE);" \
	    -c "CREATE DATABASE bamboo OWNER bamboo;"
	@# Fresh DB => unattended setup re-runs on boot; refresh the timebomb license first.
	$(MAKE) bamboo-secrets
	helm upgrade --install bamboo atlassian-data-center/bamboo -n ci -f infra/helm/bamboo-values.yaml --version 2.0.14
	kubectl -n ci rollout status statefulset/bamboo --timeout=600s
	@echo "Reset done. Bamboo re-provisioned unattended (admin/admin, broker up). Run 'make bamboo'."

.PHONY: status
status: ## Pods in ns ci
	kubectl -n ci get pods,pvc,svc

.PHONY: ui
ui: ## Port-forward Bamboo UI (8085) + agent JMS broker (54663)
	kubectl -n ci port-forward pod/bamboo-0 8085:8085 54663:54663

.PHONY: bamboo
bamboo: up ## Everything for a local plan run: stack up, ports forwarded, host agent running
	infra/scripts/start_lab.py

.PHONY: license
license: ## Fetch + copy the 24h Bamboo timebomb key (for the setup wizard)
	@key="$$(infra/scripts/get_license.py)" || exit 1; \
	  printf '%s\n\n' "$$key"; \
	  if command -v pbcopy >/dev/null; then printf '%s' "$$key" | pbcopy; \
	    echo "(copied to clipboard — paste into the Bamboo setup wizard license field)"; \
	  fi

.PHONY: relicense
relicense: ## Fetch + copy the 24h key and open Bamboo license admin (after expiry)
	infra/scripts/relicense.py

.PHONY: bamboo-restart
bamboo-restart: ## Re-arm the 24h timebomb: restart bamboo-0 (PVCs + DB untouched)
	kubectl -n ci delete pod bamboo-0 --wait=false
	kubectl -n ci rollout status statefulset/bamboo --timeout=600s
	@echo "Bamboo restarted — timebomb licence re-armed for 24h. Re-run 'make ui'."

.PHONY: agent-install
agent-install: ## Install host-local Bamboo agent (needs AGENT_TOKEN)
	infra/agent/install_agent.py

.PHONY: agent-run
agent-run: ## Run host-local Bamboo agent in console mode
	infra/agent/run_agent.py

.PHONY: specs-publish
specs-publish: ## Publish all Bamboo Specs plans to the server
	$(LAB)/specspublish/scripts/publish_specs.py

.PHONY: hooks-install
hooks-install: ## Point git at infra/githooks (pushes to main republish the plans)
	git config core.hooksPath infra/githooks
	@echo "core.hooksPath=infra/githooks — a push to main now publishes the specs"

.PHONY: provision
provision: ## Provision cluster: make provision CLUSTER=lab1 [CONFIG=lab1]
	@[ -n "$(CLUSTER)" ] || (echo "CLUSTER required"; exit 1)
	$(LAB)/provisioncluster/scripts/provision.py $(CLUSTER) "$(CONFIG)"

.PHONY: addons
addons: ## Re-run the install stage only: make addons CLUSTER=lab1 [CONFIG=lab1]
	@[ -n "$(CLUSTER)" ] || (echo "CLUSTER required"; exit 1)
	$(LAB)/provisioncluster/scripts/install.py $(CLUSTER) "$(CONFIG)"

.PHONY: deprovision
deprovision: ## Tear down cluster: make deprovision CLUSTER=lab1
	@[ -n "$(CLUSTER)" ] || (echo "CLUSTER required"; exit 1)
	$(LAB)/deprovisioncluster/scripts/deprovision.py $(CLUSTER)

.PHONY: lint
lint: ## All static checks
	pytest bamboo-specs/src/test/python
	terraform -chdir=$(LAB)/shared/terraform fmt -check -recursive
	terraform -chdir=$(LAB)/shared/terraform init -backend=false -input=false >/dev/null
	terraform -chdir=$(LAB)/shared/terraform validate
	cd $(LAB)/shared/ansible && ansible-lint
	mvn -f bamboo-specs/pom.xml -q test
