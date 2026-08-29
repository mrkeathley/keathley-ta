# Learning loop

## Current cycle

Each run records:

1. Sanitized configuration, account, and positions.
2. Point-in-time market inputs and deterministic signals.
3. Retrieved research and its citations.
4. Scout intents and independent critic decisions.
5. Risk decisions, idempotency claims, and broker receipts.
6. Counterfactual outcome marks at 1, 5, 20, and 60 completed sessions.
7. A reviewer report based on mature marks, separating observations, hypotheses, and proposed changes.

Narrative reviews are not included in the next scout context. The daemon evaluates all proposed intents,
including critic rejections and risk clips, after the daily scan. When new marks mature, the reviewer runs no
more than daily. This prevents a persuasive prior review from becoming evidence for its own claims.

## Why suggestions are not applied yet

A reviewer immediately after submission has no outcome evidence. Automatically applying its suggestion would reward persuasive narration rather than performance. Suggested changes are therefore stored as unpromoted hypotheses.

## Counterfactual evaluator

The evaluator marks every proposed intent—including rejected ones—at 1, 5, 20, and 60 completed trading
sessions. It calculates raw and side-adjusted forward return, maximum favorable and adverse excursion,
whether the original stop would have triggered, and a conservative ten-basis-point round-trip cost estimate.
Benchmark-relative return and a calibrated symbol-specific spread/slippage model remain future work.

This creates three comparable groups:

- proposed and executed;
- proposed and rejected by the critic;
- critic-approved and rejected/clipped by risk.

Without the rejected groups, the system cannot determine whether a gate adds value.

## Promotion gate

Agent suggestions remain unpromoted experiments. A future versioned shadow candidate can replace promoted
policy only when it:

- uses only information available at the historical decision timestamp;
- is tested with walk-forward splits and transaction costs;
- has enough independent observations for the chosen cadence;
- improves a predeclared primary metric without violating drawdown and exposure limits;
- survives a paper-trading observation window;
- has a reproducible configuration hash and rollback target.

Automatic promotion is still disabled because these gates are not all executable. Agents may propose
experiments; neither agents nor the reviewer can change the trading policy.

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
