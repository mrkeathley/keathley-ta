# Risk policy

## Mandate

This is a high-risk research portfolio funded only with capital that may be lost in full. The experiment optimizes for learning quality and auditable autonomy, not low volatility.

The default $3,000 profile permits:

- 40% of equity in one symbol.
- 100% gross long exposure.
- 60% new exposure in one cycle.
- Up to five submitted orders in a cycle.
- A 20% daily equity loss kill switch for new entries.

The final numbers are environment configuration, but each run journals the active values.

## Non-negotiable operational controls

- Paper execution only until promotion criteria pass.
- Buy sizing uses cash, never the broker's margin buying power.
- No short positions.
- No option orders in the current release.
- A valid recent daily signal and stop reference are required for entry.
- A deterministic client order ID is claimed transactionally before submission.
- Existing positions, gross exposure, cash, and orders already approved in the run all reduce capacity.
- Every held symbol is scanned even when it is absent from the entry universe; missing held-symbol data fails the run.
- Protective exits are deterministic and cannot be vetoed by a language model.
- Models cannot access broker credentials or broker tools.
- Model output can only narrow a deterministic candidate set; it cannot add a symbol.

## Options promotion gate

The domain can identify option intents, but the risk engine rejects them while `KTA_OPTIONS_ENABLED=false`, and the paper executor is not certified for them. Do not flip the flag yet.

Before options execution is added, implement and test:

1. Contract-chain snapshots and point-in-time Greeks/quotes.
2. Limit-order-only execution, spread width, open interest, and volume constraints.
3. Defined-risk structures with worst-case debit/loss known before submission.
4. Expiration, exercise, assignment, corporate-action, and early-close handling.
5. Contract-level idempotency and position reconciliation.
6. Options buying-power and broker-approval state validation.
7. Paper scenarios for partial fills, stale chains, and assignment.

The first supported option should be a long call or long put with premium-at-risk capped deterministically. Naked short options are outside the mandate.

## Tax and account boundaries

Use a separate taxable brokerage account for the experiment. Preserve broker confirms and journal exports. Trading-frequency, margin, wash-sale, and reporting rules change and depend on the account and taxpayer; obtain current broker and tax-professional guidance before live funding. The strategy should not encode tax assumptions as execution logic.
