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
| Price-trigger poll/webhook | Price crosses a typed threshold | No | Configured polling/cooldown |
| Entry research | Actionable entry whose symbol/strategy/action cooldown can be claimed | Perplexity | Once per 168 hours |
| Research director | Same claimed entry, only when agentic research is enabled | OpenRouter | 1–6 bounded turns |
| Scout | Same claimed entry batch | OpenRouter | One batch per run |
| Critic | Scout produced entry intents | OpenRouter | One batch per run |
| Protective exit | ATR exit for a held position | No | Every run until reconciled |
| Suggestion critic | Every durable suggestion; again at market open when approved off-hours | OpenRouter | Event-driven |
| Control chat | User message and remaining monthly budget | OpenRouter | Event-driven |
| Learning reviewer | Material activity and interval elapsed in one-shot mode | OpenRouter | Once per 168 hours |
| Outcome marks | 1/5/20/60-session horizon becomes mature | No initially | Daily evaluator, upcoming |

Decision trigger claims use a SQLite transaction and a 30-minute lease. Completed work is suppressed for seven days. Failed research/agent work is immediately retryable; concurrent or overlapping runs cannot both claim it.

## Idle-cost behavior

A normal daily scan with no signals makes zero paid research or model calls. A persistent entry condition is reevaluated no more than weekly. Perplexity receives all candidates in one low-context Sonar request instead of one request per ticker. Scout and critic also receive candidate batches.

The monthly application budget defaults to $2.00. Once recorded inference/search usage reaches it, the coordinator continues account reconciliation, data scans, and protective exits but suppresses new entry research and decisions. Configure hard provider-side spend limits too; the local ledger cannot prevent a single unexpectedly large response from crossing the remaining budget. OpenRouter's credit-purchase fee is included in the estimator but not in per-response journal cost because it is charged when credits are purchased, not when a request runs.

OpenRouter reasoning is disabled by default for scout, critic, and reviewer calls. Their job is to return
bounded structured decisions, not long hidden traces. This reduces both latency and portfolio drag; any
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

Agentic research adds up to six director turns (modeled at 5,000 input/1,200 output tokens each) and four
Sonar requests per signal cycle. At the first row's token rates this ceiling is about $0.278/month; at
$5/M input and $30/M output it is about $2.346/month. The latter would hit the default $2 local budget,
so provider-side limits and lower turn/search bounds are required.

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
