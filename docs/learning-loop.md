# Learning loop

## Current cycle

Each run records:

1. Sanitized configuration, account, and positions.
2. Point-in-time market inputs and deterministic signals.
3. Retrieved research and its citations.
4. Scout intents and independent critic decisions.
5. Risk decisions, idempotency claims, and broker receipts.
6. A reviewer report separating observations, hypotheses, and proposed changes.

The previous three reviews are included in the next scout and reviewer context. Reviews are batched no more than weekly and only after actionable activity. This gives the agents bounded memory without paying for idle narration or allowing them to rewrite executable policy.

## Why suggestions are not applied yet

A reviewer immediately after submission has no outcome evidence. Automatically applying its suggestion would reward persuasive narration rather than performance. Suggested changes are therefore stored as unpromoted hypotheses.

## Next implementation: counterfactual evaluator

The evaluator will mark every proposed intent—including rejected ones—at 1, 5, 20, and 60 trading days. It will calculate forward return, maximum favorable excursion, maximum adverse excursion, benchmark-relative return, spread/slippage estimate, and whether the original stop would have triggered.

This creates three comparable groups:

- proposed and executed;
- proposed and rejected by the critic;
- critic-approved and rejected/clipped by risk.

Without the rejected groups, the system cannot determine whether a gate adds value.

## Promotion gate

Agent suggestions become versioned shadow candidates. A candidate can replace the promoted policy only when it:

- uses only information available at the historical decision timestamp;
- is tested with walk-forward splits and transaction costs;
- has enough independent observations for the chosen cadence;
- improves a predeclared primary metric without violating drawdown and exposure limits;
- survives a paper-trading observation window;
- has a reproducible configuration hash and rollback target.

Promotion should be automatic only after these checks are executable code. Agents may propose experiments; deterministic evaluation decides promotion.

## Research-quality evaluation

Score the research layer separately from returns:

- source and citation coverage;
- primary-source ratio;
- claim-to-source entailment;
- disclosure delay correctly represented;
- contradiction and duplicate detection;
- retrieval cost and latency;
- stability when the same point-in-time query is replayed.

This makes it possible to improve research even during periods with too few trades for a reliable performance conclusion.
