# Delivery plan

## Milestone 1 — bounded paper product (implemented)

- Clean dependency-free application isolated from the legacy Django code.
- Daily signal, research, scout, critic, deterministic risk, execution, and review pipeline.
- Offline simulator and Alpaca paper adapters.
- OpenRouter and Perplexity adapters.
- Durable audit trail and cross-run order idempotency.
- Transactional decision cooldowns, batched research/reviews, usage accounting, and a monthly API budget.
- Reproducible `uv` environment and committed universal lockfile.
- High-risk portfolio configuration with strict system-risk controls.
- Unit and offline end-to-end tests.

Paper acceptance requires successful staged runs from `operations.md`; real credentials are intentionally not part of the repository.

## Milestone 1.5 — event-driven daemon (implemented)

- Single-container scheduler, price-trigger poller, leased SQLite job workers, and HTTP control plane.
- Durable trade-suggestion queue with off-hours provisional review and market-open revalidation.
- Authenticated price webhook plus typed, allowlisted agent-created triggers.
- Bounded OpenRouter/Perplexity research tool loop with per-run usage accounting.
- Interactive terminal control client and durable chat transcripts.
- Docker Compose and single-replica Kubernetes deployment assets.

Production acceptance still requires an Alpaca-data/paper soak test, container restart/lease recovery test,
and review of the selected models' tool-call reliability.

## Milestone 2 — measurable self-improvement

- Daily outcome marking and counterfactual tracking.
- Benchmark, transaction-cost, drawdown, and exposure metrics.
- Versioned experiment registry and configuration hashes.
- Walk-forward shadow runner and deterministic promotion/rollback gate.
- Research-quality evaluator and token/API cost accounting.

## Milestone 3 — richer evidence

- First-party SEC/EDGAR and FRED adapters.
- Licensed congressional-disclosure feed with raw-record retention.
- Stable Google Trends source with query normalization and caching.
- Universe construction for AI infrastructure, energy, networking, memory, space, and downstream adopters.
- Entity resolution so ambiguous tickers cannot contaminate attention features.

## Milestone 4 — options paper execution

- Point-in-time option-chain store and defined-risk contract selector.
- Liquidity-aware limit-order state machine.
- Expiration/assignment lifecycle and reconciliation.
- Premium-at-risk portfolio budget and scenario tests.

## Milestone 5 — live-readiness decision

Live execution remains a separate product decision. Require a stable paper period, reconciled fills, tested recovery procedures, current broker/API review, and current legal/tax review. If approved, add a separate live adapter with a distinct secret, explicit deployment flag, and lower initial dollar cap; never repurpose the paper URL switch.
