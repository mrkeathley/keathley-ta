# Runtime and cost design

Pricing snapshot: 2026-08-27. Re-run the estimator with current model prices before changing models or cadence.

## Decision: one durable coordinator, multiple internal loops

`kta run` remains a useful one-shot diagnostic. Production-style operation uses one `kta daemon` container
with scheduler, price-trigger, HTTP, and worker threads backed by leased SQLite jobs.

Research, review, and execution still depend on prior durable state, so they are jobs rather than permanently
running model processes. Paid inference is serialized to avoid spend races; broker execution is a single
logical writer. HTTP and trigger polling remain responsive while a model call is waiting.

This is not a forever-monolith. Move the queue/journal to PostgreSQL and split services when one of these becomes true:

- intraday or streaming signals;
- more than roughly 100–200 symbols per scan;
- option-chain fan-out;
- congressional/news webhooks arriving independently of market close;
- outcome marking and shadow backtests need sustained parallel compute;
- one run can no longer finish comfortably before the next market event.

At that point the natural services are scheduler, market-event ingestor, scanner, evidence worker, decision
worker, executor/reconciler, outcome evaluator, and control API. The executor remains a single logical
writer even then.

## Trigger schedule

| Stage | Trigger | Paid model call? | Default ceiling |
|---|---|---:|---:|
| Account/position reconciliation | Every trading-day run | No | Daily |
| Daily-bar scan | About 4:30 p.m. New York time | No | Daily |
| Universe discovery | Configured interval and on demand | Perplexity + OpenRouter | Every 6 hours |
| Discovery supervisor | Every discovery snapshot | OpenRouter | One independent review batch |
| Price-trigger poll/webhook | Price crosses a typed threshold | No | Configured polling/cooldown |
| Entry research | Actionable entry whose symbol/strategy/action cooldown can be claimed | Perplexity | Once per 168 hours |
| Research director | Same claimed entry, only when agentic research is enabled | OpenRouter | Evidence-based; 64-turn guard |
| Scout | Same claimed entry batch | OpenRouter | One batch per run |
| Critic | Scout produced entry intents | OpenRouter | One batch per run |
| Protective exit | ATR exit for a held position | No | Every run until reconciled |
| Suggestion critic | Every durable suggestion; again at market open when approved off-hours | OpenRouter | Event-driven |
| Control chat | User message and remaining monthly budget | OpenRouter | Event-driven |
| Outcome evaluator | Daily after the scheduled scan | Market data only | Marks 1/5/20/60-session outcomes |
| Learning reviewer | New mature outcome marks | OpenRouter | At most once per 24 hours |
| Outcome marks | 1/5/20/60-session horizon becomes mature | No | Daily evaluator |

Decision trigger claims use a SQLite transaction. Agent jobs use a four-hour recoverable lease and durable
checkpoints. Completed work is suppressed for seven days. Failed research/agent work is retryable;
concurrent or overlapping runs cannot both claim it.

## Idle-cost behavior

A normal daily scan with no signals makes zero paid research or model calls. Perplexity receives all candidates
in one low-context Sonar request instead of one request per ticker. Thesis, scout, and critic receive candidate
batches. The reviewer runs only when counterfactual marks have matured.

The monthly application soft budget defaults to $100 and emits visible warnings without stopping useful
work. A higher hard boundary defaults to $1,000 but is enforced only when
`KTA_ENFORCE_MONTHLY_API_BUDGET=true`. Configure provider-side emergency spend limits too; the local ledger
cannot prevent a single unexpectedly large response from crossing a boundary. OpenRouter's credit-purchase
fee is included in the estimator but not in per-response journal cost because it is charged when credits
are purchased, not when a request runs.

Agent-originated downstream scans have a separate hard monthly budget controlled by
`KTA_AGENT_ORIGINATED_MONTHLY_BUDGET_USD` (default $25). It applies to scans requested by the chat agent or
fired by agent-created price triggers, without consuming the human/manual or daily scheduler allowance.

OpenRouter reasoning is disabled by default for scout, critic, and reviewer calls. If a routed endpoint
explicitly requires reasoning, the request is retried with hidden minimal reasoning and enough completion
budget for its structured answer. This keeps latency and portfolio drag bounded; any
model-specific reasoning setting should be promoted only after structured-output, duration, and cost
acceptance tests.

## Steady-state examples

Baseline assumptions below use a $3,000 portfolio, four signal-bearing cycles, two reviews per month,
7,500 input/2,800 maximum output tokens per scout+critic cycle, 6,000/1,800 maximum output tokens per
review, and one low-context Sonar request per signal cycle. Output uses request ceilings rather than an
optimistic average because reasoning tokens count as output.

| Model planning rate | Monthly API | Annual API | Annual portfolio drag |
|---|---:|---:|---:|
| $0.30/M input, $2.50/M output | $0.080 | $0.96 | 0.032% |
| $5/M input, $30/M output | $0.718 | $8.62 | 0.287% |
| Premium model called daily, reviewer daily | $4.700 | $56.40 | 1.880% |

The estimator treats configured turn and search guards as a deliberately pessimistic ceiling. With the new
64-turn/32-search emergency guards, that ceiling is not an expected usage forecast. Capacity decisions should
instead use recorded p50/p95 completed-task usage after representative discovery and research evaluations.

These are planning estimates, not price guarantees. They exclude hosting, spreads, slippage, regulatory fees, taxes, and paid market data. A $5/month VPS alone costs $60/year—2% of a $3,000 account—so run on existing hardware or infrastructure during the paper experiment if reliability permits.

Current source rates:

- [Perplexity pricing](https://docs.perplexity.ai/docs/getting-started/pricing): Search API $5/1,000 requests; low-context Sonar request fee $5/1,000 plus token charges.
- [OpenRouter pricing behavior](https://openrouter.ai/docs/faq): model-specific token rates are passed through; credit purchases have a platform fee. Responses provide usage/cost data.
- [Alpaca market-data plans](https://docs.alpaca.markets/us/docs/about-market-data-api): Basic IEX equities data is currently $0/month; full-exchange Algo Trader Plus is $99/month.

Use:

```bash
uv run --locked kta estimate-cost
uv run --locked kta estimate-cost --signal-cycles 8 --reviews 4 --portfolio-usd 3000
```

Update `KTA_ESTIMATED_LLM_INPUT_USD_PER_MILLION` and `KTA_ESTIMATED_LLM_OUTPUT_USD_PER_MILLION` whenever the configured OpenRouter models change.
