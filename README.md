# Keathley Agentic Trader

An auditable, research-first experiment in agentic trading. The portfolio mandate is intentionally aggressive: the allocated account may go to zero. The software mandate is the opposite: agents cannot bypass sizing, freshness, idempotency, credential, or execution controls.

The product now supports both bounded one-shot runs and a durable event-driven daemon. It is wired for Alpaca paper trading, OpenRouter agents, and Perplexity research. Live trading and options execution are deliberately not implemented yet.

> This is experimental software, not investment, legal, or tax advice. A complete record of trades helps with accounting, but this application is not a tax engine.

## What works now

- Point-in-time daily bars from a deterministic test feed or Alpaca.
- The original Pine idea rebuilt as a no-lookahead 200-day trend / 10-day pullback / ATR-trail strategy.
- A scout proposes only from deterministic candidates.
- An independent critic can reject weak or unsupported proposals.
- A deterministic risk engine clips orders to cash, per-position, gross, and per-run limits.
- A broker adapter submits only to Alpaca's paper endpoint; a separate simulator supports offline runs.
- A learning reviewer records observations, hypotheses, and proposed shadow experiments after every run.
- SQLite stores the full decision trail and atomically prevents duplicate intents.
- Perplexity can collect catalyst, AI-supply-chain, attention, and congressional-disclosure evidence with source URLs.
- A leased SQLite job queue drives scheduled scans, price triggers, chat, suggestion review, market-open revalidation, and execution.
- An authenticated HTTP control plane and terminal client expose durable status without treating container logs as the database.

```text
daily bars ──> deterministic signals ──> evidence collector ──> scout
                                                               │
                                                               v
journal <── simulated/Alpaca paper broker <── risk gate <── critic
   │
   └──> learning reviewer ──> unpromoted experiment hypotheses
```

Models never receive broker credentials and never call the broker. A model's dollar request is advisory; the risk engine calculates the executable amount.

## Quick start

Python 3.13 and [uv](https://docs.astral.sh/uv/) are enough; the core product has no third-party runtime dependencies. `uv sync` installs the pinned Python line when needed, while `uv.lock` is committed as the dependency source of truth.

```bash
uv sync --locked
make test
make smoke
make cost
```

Run `make` by itself to see every target and whether it can contact external services. `make smoke`
performs a temporary end-to-end simulation with synthetic placeholder symbols; it ignores configured
API and broker modes and deletes its journal afterward.

For a configured run, copy `.env.example` to `.env`, choose the deployment's comma-separated
`KTA_UNIVERSE`, then validate and execute:

```bash
make doctor
make run
```

Or override the configured watchlist for one cycle:

```bash
uv run --locked kta run --universe SYMBOL1,SYMBOL2
uv run --locked kta runs
uv run --locked kta events RUN_ID
uv run --locked kta estimate-cost
```

There is intentionally no default investment universe. The code owns eligibility and safety rules; the
deployment owns its watchlist. See [universe policy](docs/universe-policy.md) for the current boundary and
the planned research-driven discovery process.

Progress is written to stderr as one updating terminal line while the final JSON stays on stdout. When
output is redirected, each stage becomes a normal log line. Use `kta run --quiet` only when another
process is monitoring the journal.

## Run as a service

The daemon is the primary mode for dynamic intraday activation:

```bash
make daemon
# another terminal
make tui
```

Or run it isolated in Docker after setting `KTA_CONTROL_TOKEN`:

```bash
make container-up
make tui
```

It runs independent scheduler, trigger-poller, HTTP, and worker threads backed by a durable SQLite queue.
Price alerts may be polled from Alpaca or delivered to an authenticated webhook. Agent-created alerts are
validated data records—not arbitrary installed webhooks—and can only queue research/scan work.

Daemon trade flow is asynchronous: discovery creates a suggestion, a critic reviews it, off-hours approval
waits in the queue, and market open forces fresh signal/research/critic validation before deterministic risk
and idempotent submission. See [daemon architecture](docs/daemon.md) and the [control API](docs/control-api.md).

The configured mode defaults are offline—synthetic bars, heuristic agents, no research network call,
and a $3,000 simulated account—but `KTA_UNIVERSE` is still required. Simulator holdings reset when the
process exits; the audit and duplicate-order ledger in `var/kta.db` do not.

## Configure paper trading

Copy `.env.example` to `.env`, then supply paper-account keys:

```dotenv
KTA_BROKER_MODE=paper
KTA_MARKET_DATA_MODE=alpaca
ALPACA_API_KEY=...
ALPACA_API_SECRET=...
ALPACA_TRADING_URL=https://paper-api.alpaca.markets
```

`AlpacaPaperBroker` refuses any URL that does not contain `paper-api`. There is no live broker class or `live` configuration value in this release.

Enable the research and model loop independently:

```dotenv
KTA_RESEARCH_MODE=perplexity
PERPLEXITY_API_KEY=...

KTA_AGENT_MODE=openrouter
OPENROUTER_API_KEY=...
KTA_SCOUT_MODEL=provider/scout-model
KTA_CRITIC_MODEL=provider/critic-model
KTA_REVIEWER_MODEL=provider/reviewer-model
KTA_OPENROUTER_REASONING_EFFORT=none
```

The original fast behavior is expected when `KTA_RESEARCH_MODE=none` or when a signal is inside its
cooldown. Enable the bounded multi-turn research director explicitly:

```dotenv
KTA_RESEARCH_MODE=perplexity
KTA_AGENT_MODE=openrouter
KTA_AGENTIC_RESEARCH_ENABLED=true
KTA_AGENT_MAX_TURNS=6
KTA_AGENT_MAX_RESEARCH_CALLS=4
```

This lets the director make focused Perplexity tool calls, inspect allowed candidate context, and create
validated price triggers before returning its evidence memo. Every turn and search is costed; the loop is
bounded because useful research—not wall-clock duration—is the objective.

Choose current OpenRouter model IDs rather than copying the placeholders. Use separate models or providers for scout and critic when practical. The application requests structured JSON and fails closed when an intent is omitted or malformed.

Reasoning tokens count against the model's completion limit. These calls need short, auditable JSON more
than a long hidden trace, so reasoning is disabled by default. Enable it only after acceptance-testing the
specific model and latency budget. If a model still consumes the whole limit or returns no content, the
run records an actionable error with the operation, model, finish reason, output-token count, and active
reasoning setting.

Run one bounded cycle after the daily bar is final:

```bash
uv run --locked kta doctor
uv run --locked kta run
```

An external scheduler should call this command once per trading day around 4:30 p.m. `America/New_York`. Keeping scheduling outside the trading process prevents duplicate in-process schedulers during deploys. The daily deterministic scan does not cause an LLM call unless a signal clears its trigger cooldown.

## Timing and API cost

For one-shot operation, use one short-lived coordinator. The daemon adds independent I/O loops but keeps
one durable coordinator and one logical executor. Do not run multiple replicas against SQLite.

Paid calls are event-driven:

- No signal at all: no Perplexity, scout, critic, or reviewer call.
- New or refreshed entry signal: baseline mode uses one batched Perplexity request, one scout call, and one critic call; agentic research uses up to its configured turn/search ceilings.
- Same symbol/strategy/action inside seven days: decision work is suppressed transactionally.
- Protective exit: deterministic path; no research or LLM approval.
- Learning review: at most weekly and only after material activity.
- Monthly recorded API cost at the configured limit: new entries stop; deterministic monitoring and exits continue.

With four signal-bearing cycles and two review batches per month, baseline research projects roughly **$0.080/month** using the default planning rates, or about **$0.96/year / 0.032% of a $3,000 portfolio**. Enabling the six-turn/four-search agentic ceiling raises that conservative projection to about **$0.278/month**. A deliberately expensive $5/M input, $30/M output mix raises the baseline cadence to about $0.72/month and the agentic ceiling to about $2.35/month—where the default $2 application budget would stop new work. Run `kta estimate-cost` with the prices of the exact models selected; actual OpenRouter and Perplexity usage is stored with each run.

See [runtime and cost design](docs/runtime-and-costs.md) for the trigger table, multiprocess threshold, and more conservative scenarios.

## High-risk mandate

The starting profile allows a 40% single position and 60% new exposure in one run. Gross exposure can reach 100% of account equity. These are concentrated, high-volatility settings—not capital-preservation defaults.

The engine nevertheless ignores broker margin buying power and sizes buys from cash. Shorts and options execution remain disabled. This distinction is intentional:

- **Accepted market risk:** concentration, high volatility, thesis failure, and loss of the dedicated capital.
- **Rejected system risk:** duplicate orders, accidental leverage, stale signals, hallucinated symbols, agent access to credentials, or silent policy mutation.

See [risk policy](docs/risk-policy.md) before changing limits.

## Congress and trends research

The Perplexity collector explicitly asks for official-source congressional disclosures, transaction dates, disclosure dates, amount ranges, and citations. A disclosed trade is treated as delayed contextual evidence—not a real-time copy signal. The journal preserves retrieved evidence so later evaluation uses what was knowable at decision time.

Google Trends is not yet a production adapter. It belongs in the evidence layer after a stable, licensed API source and revision/caching policy are selected. Search attention should be normalized by ticker ambiguity and treated as a feature, not a trade trigger.

The thematic vision—AI infrastructure and its upstream/downstream dependencies—guides universe
construction but is not a ticker list in source code. Symbols can be changed without a release.

## Self-improvement boundary

The reviewer can create falsifiable hypotheses and shadow-experiment suggestions. It cannot edit code, loosen risk, change the promoted strategy, or submit orders. Automatic promotion comes only after the outcome evaluator and walk-forward comparison gate described in [the learning loop](docs/learning-loop.md) are implemented.

That boundary matters even when a full loss is acceptable: otherwise the experiment cannot tell model improvement from strategy drift, leakage, or luck.

## Repository map

- `src/kta/` — new application and external adapters.
- `tests/` — dependency-free unit and end-to-end tests.
- `docs/` — mandate, learning, operating, and delivery gates.
- `keathley-bot.pine` — the original Pine v5 strategy, preserved unchanged.
- `kta/` — the old Django strategy tester, retained as reference only.

The old Django application is not imported by the new product. The root `docker-compose.yml` still belongs
to that legacy application; the new service uses `compose.daemon.yml` and `deploy/k8s/`.
