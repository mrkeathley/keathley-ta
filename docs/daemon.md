# Daemon architecture

## Process model

`kta daemon` is one long-running coordinator with independent threads and a shared durable journal:

```text
daily scheduler ─┐
price poller ────┼──> SQLite job queue ──> workers ──> research / review / execution
HTTP webhook ───┤                              │
chat / TUI ─────┘                              └──> service events + audit journal
```

The HTTP server, scheduler, price-trigger poller, and job workers make progress independently. Paid
model work is serialized inside the process so concurrent requests cannot create an accidental inference
storm. Broker submission is separately serialized and retains the existing order-idempotency ledger.

SQLite jobs have leases, attempt counts, exponential retry delays, priorities, and optional deduplication
keys. A process crash leaves the work recoverable: an expired running lease is returned to the queue. The
default four-hour lease allows long investigations while preserving crash recovery.

This is intentionally a **single active container** design. Threads are useful for HTTP responsiveness,
polling, and I/O-bound jobs; multiple Kubernetes replicas are unsafe while SQLite is the coordinator.
The supplied Deployment therefore uses one replica, `Recreate`, and a `ReadWriteOnce` volume. Move jobs,
events, and idempotency to PostgreSQL before adding replicas or separate services.

## Scheduled and event-driven work

- One daily scan is deduplicated by Eastern date, defaulting to 4:30 p.m. New York time.
- `KTA_SCAN_ON_START=false` avoids an unexpected scan merely because a container restarted.
- Price triggers are typed records: symbol, above/below comparison, positive threshold, one-shot or
  cooldown behavior. They are not arbitrary code or URLs installed by a model.
- The trigger poller uses the latest Alpaca trade in Alpaca mode. A provider or alerting system may instead
  call the authenticated `/v1/webhooks/price` endpoint.
- Trigger hits enqueue a focused scan. They never submit an order directly.

## Trade-suggestion state machine

```text
proposed
   │ critic review
   ├──> rejected
   └──> approved_awaiting_open
                │ fresh bars + research + critic at market open
                ├──> stale / rejected
                └──> approved_ready
                           │ fresh-review TTL + deterministic risk
                           ├──> pending_revalidation / risk_rejected
                           └──> executed
```

Daemon scans stop after creating durable suggestions. An off-hours critic can approve the idea, but that
approval is provisional. At market open the worker regenerates the deterministic signal, refreshes
research, and calls the critic again. Execution checks that the open-session review is no older than
`KTA_SUGGESTION_REVIEW_TTL_MINUTES` (15 by default), then reruns account/position risk and claims the
idempotent order ID.

Broker acceptance is not treated as a fill. Nonterminal orders are reconciled by broker order ID, including
after daemon restart. A confirmed filled entry arms a deterministic below-price protective trigger; exits do
not require model approval.

Alpaca paper mode uses Alpaca's market clock. Simulation uses a weekday 9:30–16:00 Eastern clock that does
not model holidays; it must not be treated as an exchange calendar.

## Continuous discovery and agentic research

When `KTA_DISCOVERY_ENABLED=true`, the scheduler periodically creates a supervisor task and a child
discovery task. Perplexity collects current sourced evidence, the discovery model expands the company and
value-chain graph, and the supervisor model independently accepts or rejects every candidate. Accepted
symbols must then pass asset identity, tradability, minimum-price, and completed-bar liquidity checks. They
form an immutable universe snapshot and automatically queue the next scan. Tasks retain progress,
checkpoints, heartbeats, parent relationships, and operator pause/cancel state in SQLite.

Set all of the following to replace the old single research request with a bounded tool loop:

```dotenv
KTA_AGENT_MODE=openrouter
KTA_RESEARCH_MODE=perplexity
KTA_AGENTIC_RESEARCH_ENABLED=true
KTA_AGENT_MAX_TURNS=64
KTA_AGENT_MAX_RESEARCH_CALLS=32
```

The OpenRouter director can use only three research tools: focused Perplexity search, current context for
a deterministic candidate, and creation of a validated price trigger for that candidate. It must use the
research tool before its memo is accepted. It cannot add a symbol, call the broker, edit code, change risk,
or approve its own trade. OpenRouter and Perplexity usage from every turn is journaled.

The numeric settings are generous emergency loop guards rather than expected research durations. Useful
work may continue for hours while producing checkpoints. A soft monthly spend threshold emits warnings;
hard application enforcement is enabled only with `KTA_ENFORCE_MONTHLY_API_BUDGET=true`.

## Container operation

For Docker Compose, set a long random `KTA_CONTROL_TOKEN` in `.env`, then:

```bash
make container-up
make tui
make container-logs
```

Compose binds the API to host loopback even though the process listens on all container interfaces. The
SQLite journal is persisted in the Compose-managed `kta-data` volume. This avoids host bind-mount
permission mismatches while the container runs as UID/GID 10001. The container also has a health check.

Kubernetes examples are under `deploy/k8s`. Copy `secret.example.yaml` to the ignored `secret.yaml`, fill
it through the cluster's actual secret-management path, configure the universe/models/modes, build or
publish the image, and apply the ConfigMap, Secret, PVC, Deployment, and Service. Do not expose the
ClusterIP publicly without TLS and an authenticated reverse proxy.
