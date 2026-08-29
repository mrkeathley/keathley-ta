# Control API and terminal client

The control API is the operational source of truth. Container logs remain useful for startup or crash
diagnostics, but state questions should use the API because jobs and events survive restarts.

All `/v1` endpoints require `Authorization: Bearer <KTA_CONTROL_TOKEN>` when a token is configured.
`/healthz` is deliberately unauthenticated and returns no portfolio data. A token is mandatory when the
daemon binds beyond loopback.

| Method | Path | Purpose |
|---|---|---|
| `GET` | `/healthz` | Minimal liveness/readiness check |
| `GET` | `/v1/status` | Threads, queue counts, market session, suggestions, recent runs, safe config |
| `GET` | `/v1/jobs?limit=20` | Durable job attempts, errors, and results |
| `GET` | `/v1/events?limit=50` | Scheduler, worker, trigger, and HTTP activity |
| `GET` | `/v1/suggestions` | Trade-suggestion states and review decisions |
| `GET` | `/v1/triggers` | Active/fired/disabled price triggers |
| `GET` | `/v1/conversations/{id}` | Durable chat transcript |
| `POST` | `/v1/scans` | Queue a configured-universe scan |
| `POST` | `/v1/triggers` | Create a validated price trigger |
| `POST` | `/v1/webhooks/price` | Ingest an authenticated external price event |
| `POST` | `/v1/chat` | Queue a control-assistant response |

The chat assistant has allowlisted status, suggestion, trigger, scan, and trigger-creation tools. It can
explain or enqueue work when clearly requested. It cannot approve a suggestion, submit an order, expand
the universe, or mutate executable policy.

Run `make tui` for an interactive client. Normal text is sent to chat; slash commands query or enqueue
directly:

```text
/status
/jobs
/events
/suggestions
/triggers
/scan
/scan SYMBOL1,SYMBOL2
/trigger SYMBOL1 above 125.50
/quit
```

For scripting, keep the token outside shell history when possible. A representative request is:

```bash
curl -H "Authorization: Bearer $KTA_CONTROL_TOKEN" http://127.0.0.1:8787/v1/status
```
