# Everything runs in Docker Compose; no local Python or Node needed for these targets.
COMPOSE = docker compose
RUN_API = $(COMPOSE) run --rm -v "$(CURDIR)/reports:/app/reports" api

.PHONY: up down reset seed evaluate evaluate-tuning test test-web logs

up:            ## Build and start the whole stack
	$(COMPOSE) up -d --build --wait

down:          ## Stop the stack (data is kept)
	$(COMPOSE) down

reset:         ## Stop and delete all data
	$(COMPOSE) down -v

seed:          ## Load the demo business and deliver the demo mailbox
	$(COMPOSE) exec api python -m opsinbox.seedgen.demo load
	$(COMPOSE) exec api python -m opsinbox.seedgen.demo send

evaluate:      ## Reproduce the held-out numbers from recorded model answers (no API key)
	$(RUN_API) python -m opsinbox.evaluation.harness --split heldout --replay

evaluate-tuning: ## Reproduce the tuning numbers and the threshold choice
	$(RUN_API) python -m opsinbox.evaluation.harness --split tuning --replay

test:          ## API tests against the stack's PostgreSQL
	$(COMPOSE) exec api pytest -q

test-web:      ## Web unit tests
	npm --prefix web test

logs:
	$(COMPOSE) logs -f api worker
