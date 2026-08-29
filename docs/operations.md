# Operations

## Paper rollout

1. Create a dedicated Alpaca paper account and API key.
2. Put credentials only in local `.env` or a deployment secret manager.
3. Run `uv sync --locked`, `make test`, `make smoke`, and `make doctor`.
4. Run with Alpaca data but the simulated broker first.
5. Inspect the complete event trail with `kta events RUN_ID`.
6. Change only `KTA_BROKER_MODE=paper`, rerun `doctor`, then execute one cycle.
7. Reconcile journal receipts against the broker dashboard before scheduling.

Recommended staged configurations:

| Stage | Market data | Broker | Research | Agents |
|---|---|---|---|---|
| Offline acceptance | synthetic | simulation | none | heuristic |
| Data acceptance | alpaca | simulation | none | heuristic |
| Research acceptance | alpaca | simulation | perplexity | openrouter |
| Paper shadow | alpaca | paper | perplexity | openrouter |

Change one boundary at a time so failures are attributable.

## Scheduling

The daemon schedules its deduplicated scan around 4:30 p.m. `America/New_York` on weekdays. Do not also
run an external `kta run` schedule against the same account and journal. Deterministic scanning is daily so
held positions are not ignored, while entry LLM work and research are throttled by the configured seven-day
decision cooldown.

The daemon workers remain alive, but scout, critic, director, and reviewer calls are request/response jobs,
not autonomous model subprocesses consuming tokens while idle.

The command returns nonzero when critical research, scout, critic, journal, or broker work fails. A
post-decision learning-review failure is recorded as `learning_review_error` and returned as a warning,
but it does not relabel already completed execution as failed. Alert on nonzero exit, warnings, and a run
left in `running` state.

`uv run --locked kta estimate-cost` reports modeled monthly and annual API drag. `kta events RUN_ID` includes the actual/estimated usage ledger for that run. Set provider-side key limits as a second boundary in addition to `KTA_MONTHLY_API_BUDGET_USD`.

## Incident response

For unexpected paper orders:

1. Disable the external scheduler.
2. Preserve `.env`, `var/kta.db`, command output, and broker order history.
3. Inspect the run's `intent_proposed`, `critic_*`, `risk_*`, and `order_*` events.
4. Close a position through the broker interface if necessary; do not alter the journal.
5. Add a failing regression test before changing the control that allowed the order.

For a compromised key, revoke it at the provider before investigating application behavior.

## Known limitations

- Simulated-broker holdings are process-local and are not a fill/slippage model.
- Daily-bar strategy only; no intraday scheduling or real-time risk monitor.
- Alpaca orders are journaled when accepted and reconciled by broker order ID until terminal. A filled entry
  arms a deterministic below-price protective trigger; pending orders are recovered for reconciliation when
  the daemon restarts. Partial fills are recorded, but sophisticated partial-fill resizing remains future work.
- No outcome/counterfactual evaluator yet.
- No corporate-action reconciliation.
- No Google Trends production adapter.
- No options execution.
- SQLite is suitable for one active container with internal worker threads; move the journal and jobs to
  PostgreSQL before multiple pods or hosts.
