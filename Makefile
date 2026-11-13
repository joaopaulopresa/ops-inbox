# Everything runs in Docker Compose; no local Python or Node needed for these targets.
COMPOSE = docker compose
# The API mounts reports/ read-only; evaluation writes through a second, writable mount.
RUN_API = $(COMPOSE) run --rm --no-deps -v "$(CURDIR)/reports:/app/reports-out" -e OPSINBOX_REPORTS_DIR=/app/reports-out api

.PHONY: up up-all down reset seed evaluate evaluate-tuning test test-web logs backup-now restore-test

up:            ## Build and start the product (database, storage, mail, API, worker, web, backups)
	$(COMPOSE) up -d --build --wait

up-all:        ## The product plus Prometheus, Grafana and Langfuse
	OPSINBOX_LANGFUSE_PUBLIC_KEY=pk-lf-ops-inbox-demo OPSINBOX_LANGFUSE_SECRET_KEY=sk-lf-ops-inbox-demo \
	  $(COMPOSE) --profile observability up -d --build --wait

backup-now:    ## Take a backup now
	$(COMPOSE) run --rm backup once

restore-test:  ## Restore the newest backup into a scratch database and compare
	$(COMPOSE) run --rm --entrypoint restore-test.sh backup

backup-export: ## Copy the backups volume to ./backups-export, owned by you
	mkdir -p backups-export && $(COMPOSE) run --rm --no-deps -v "$(CURDIR)/backups-export:/export" \
	  --user "$$(id -u):$$(id -g)" --entrypoint sh backup -c 'cp -r /backups/. /export/'

down:          ## Stop the stack (data is kept)
	$(COMPOSE) --profile observability down

reset:         ## Stop and delete all data
	$(COMPOSE) --profile observability down -v

seed:          ## Load the demo business and deliver the demo mailbox
	$(COMPOSE) exec api python -m opsinbox.seedgen.demo load
	$(COMPOSE) exec api python -m opsinbox.seedgen.demo send

evaluate:      ## Reproduce the held-out numbers from recorded model answers (no API key)
	$(RUN_API) python -m opsinbox.evaluation.harness --split heldout --replay

evaluate-tuning: ## Reproduce the tuning numbers and the threshold choice
	$(RUN_API) python -m opsinbox.evaluation.harness --split tuning --replay

test:          ## API tests against the stack's PostgreSQL
	$(COMPOSE) exec api pytest -p no:cacheprovider

test-web:      ## Web unit tests
	npm --prefix web test

logs:
	$(COMPOSE) logs -f api worker
