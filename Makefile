.DEFAULT_GOAL := help
.PHONY: help sync lock test doctor smoke run daemon tui runs cost build container-build container-up container-logs container-down

UV ?= uv
RUN := $(UV) run --locked

help:
	@printf '%s\n' \
		'Keathley Agentic Trader' \
		'' \
		'Set KTA_UNIVERSE in .env before a configured run.' \
		'' \
		'  make sync    Install exactly what uv.lock specifies' \
		'  make lock    Refresh uv.lock after dependency changes' \
		'  make test    Run the unit and offline integration tests' \
		'  make doctor  Validate .env without contacting providers' \
		'  make smoke   Run an isolated offline simulation (no APIs/broker)' \
		'  make run     Execute one cycle using .env (may call APIs/broker)' \
		'  make daemon  Run the durable scheduler/workers/API in foreground' \
		'  make tui     Connect the interactive terminal control client' \
		'  make runs    Show recent journaled runs' \
		'  make cost    Estimate steady-state API cost and portfolio drag' \
		'  make build   Check the lockfile and build the package' \
		'  make container-up    Build and start the isolated daemon' \
		'  make container-logs  Follow daemon container logs' \
		'  make container-down  Stop the daemon container'

sync:
	$(UV) sync --locked

lock:
	$(UV) lock

test:
	$(RUN) python -m unittest discover -s tests -v

doctor:
	$(RUN) kta doctor

smoke:
	$(RUN) kta smoke

run:
	$(RUN) kta run

daemon:
	$(RUN) kta daemon

tui:
	$(RUN) kta tui

runs:
	$(RUN) kta runs

cost:
	$(RUN) kta estimate-cost

build:
	$(UV) lock --check
	$(UV) build

container-build:
	docker compose -f compose.daemon.yml build

container-up:
	docker compose -f compose.daemon.yml up --build -d

container-logs:
	docker compose -f compose.daemon.yml logs -f trader

container-down:
	docker compose -f compose.daemon.yml down
