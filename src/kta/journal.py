"""Append-oriented SQLite journal and order idempotency ledger."""

import json
import sqlite3
import uuid
from contextlib import contextmanager
from datetime import datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any, Dict, List, Optional

from .domain import ApiUsage, RunStatus, jsonable, utc_now


class Journal:
    def __init__(self, path: Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.initialize()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(str(self.path), timeout=10)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA journal_mode = WAL")
        return connection

    @contextmanager
    def _connection(self):
        connection = self._connect()
        try:
            with connection:
                yield connection
        finally:
            connection.close()

    def initialize(self) -> None:
        with self._connection() as connection:
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS runs (
                    run_id TEXT PRIMARY KEY,
                    started_at TEXT NOT NULL,
                    finished_at TEXT,
                    status TEXT NOT NULL,
                    mode TEXT NOT NULL,
                    config_json TEXT NOT NULL,
                    error TEXT
                );
                CREATE TABLE IF NOT EXISTS events (
                    event_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    run_id TEXT NOT NULL REFERENCES runs(run_id),
                    event_type TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    payload_json TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_events_run ON events(run_id, event_id);
                CREATE TABLE IF NOT EXISTS order_ledger (
                    intent_id TEXT PRIMARY KEY,
                    client_order_id TEXT NOT NULL UNIQUE,
                    run_id TEXT NOT NULL REFERENCES runs(run_id),
                    status TEXT NOT NULL,
                    payload_json TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS reviews (
                    review_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    run_id TEXT NOT NULL REFERENCES runs(run_id),
                    created_at TEXT NOT NULL,
                    payload_json TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS api_usage (
                    usage_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    run_id TEXT NOT NULL REFERENCES runs(run_id),
                    created_at TEXT NOT NULL,
                    service TEXT NOT NULL,
                    operation TEXT NOT NULL,
                    model TEXT NOT NULL,
                    input_tokens INTEGER NOT NULL,
                    output_tokens INTEGER NOT NULL,
                    request_count INTEGER NOT NULL,
                    cost_usd TEXT,
                    estimated INTEGER NOT NULL,
                    payload_json TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_api_usage_created ON api_usage(created_at);
                CREATE TABLE IF NOT EXISTS decision_triggers (
                    trigger_key TEXT PRIMARY KEY,
                    symbol TEXT NOT NULL,
                    strategy_version TEXT NOT NULL,
                    action TEXT NOT NULL,
                    status TEXT NOT NULL,
                    run_id TEXT NOT NULL REFERENCES runs(run_id),
                    last_completed_at TEXT,
                    updated_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS jobs (
                    job_id TEXT PRIMARY KEY,
                    kind TEXT NOT NULL,
                    status TEXT NOT NULL,
                    priority INTEGER NOT NULL,
                    payload_json TEXT NOT NULL,
                    result_json TEXT,
                    available_at TEXT NOT NULL,
                    lease_expires_at TEXT,
                    worker_id TEXT,
                    attempts INTEGER NOT NULL,
                    max_attempts INTEGER NOT NULL,
                    dedupe_key TEXT UNIQUE,
                    error TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_jobs_due
                    ON jobs(status, available_at, priority DESC, created_at);
                CREATE TABLE IF NOT EXISTS service_events (
                    service_event_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    event_type TEXT NOT NULL,
                    level TEXT NOT NULL,
                    message TEXT NOT NULL,
                    payload_json TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS price_triggers (
                    trigger_id TEXT PRIMARY KEY,
                    symbol TEXT NOT NULL,
                    comparison TEXT NOT NULL,
                    threshold TEXT NOT NULL,
                    status TEXT NOT NULL,
                    source TEXT NOT NULL,
                    one_shot INTEGER NOT NULL,
                    cooldown_seconds INTEGER NOT NULL,
                    last_price TEXT,
                    last_fired_at TEXT,
                    metadata_json TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_price_triggers_active
                    ON price_triggers(status, symbol);
                CREATE TABLE IF NOT EXISTS trade_suggestions (
                    suggestion_id TEXT PRIMARY KEY,
                    intent_id TEXT NOT NULL UNIQUE,
                    run_id TEXT NOT NULL REFERENCES runs(run_id),
                    symbol TEXT NOT NULL,
                    side TEXT NOT NULL,
                    status TEXT NOT NULL,
                    review_count INTEGER NOT NULL,
                    last_review_at TEXT,
                    execute_after TEXT,
                    payload_json TEXT NOT NULL,
                    decision_json TEXT,
                    error TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_trade_suggestions_status
                    ON trade_suggestions(status, execute_after, created_at);
                CREATE TABLE IF NOT EXISTS conversations (
                    message_id TEXT PRIMARY KEY,
                    conversation_id TEXT NOT NULL,
                    role TEXT NOT NULL,
                    content TEXT NOT NULL,
                    status TEXT NOT NULL,
                    metadata_json TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_conversations_thread
                    ON conversations(conversation_id, created_at);
                CREATE TABLE IF NOT EXISTS universe_snapshots (
                    snapshot_id TEXT PRIMARY KEY,
                    source TEXT NOT NULL,
                    mandate_json TEXT NOT NULL,
                    citations_json TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS universe_candidates (
                    snapshot_id TEXT NOT NULL REFERENCES universe_snapshots(snapshot_id),
                    symbol TEXT NOT NULL,
                    name TEXT NOT NULL,
                    theme TEXT NOT NULL,
                    rationale TEXT NOT NULL,
                    confidence TEXT NOT NULL,
                    status TEXT NOT NULL,
                    metadata_json TEXT NOT NULL,
                    PRIMARY KEY(snapshot_id, symbol)
                );
                CREATE INDEX IF NOT EXISTS idx_universe_candidates_status
                    ON universe_candidates(status, symbol);
                CREATE TABLE IF NOT EXISTS agent_tasks (
                    task_id TEXT PRIMARY KEY,
                    parent_task_id TEXT REFERENCES agent_tasks(task_id),
                    role TEXT NOT NULL,
                    model TEXT,
                    objective TEXT NOT NULL,
                    status TEXT NOT NULL,
                    progress TEXT NOT NULL,
                    checkpoint_json TEXT NOT NULL,
                    heartbeat_at TEXT NOT NULL,
                    started_at TEXT,
                    finished_at TEXT,
                    error TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_agent_tasks_status
                    ON agent_tasks(status, updated_at);
                CREATE TABLE IF NOT EXISTS learning_experiments (
                    experiment_id TEXT PRIMARY KEY,
                    run_id TEXT NOT NULL REFERENCES runs(run_id),
                    parameter TEXT NOT NULL,
                    proposal TEXT NOT NULL,
                    status TEXT NOT NULL,
                    metrics_json TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_learning_experiments_status
                    ON learning_experiments(status, created_at);
                CREATE TABLE IF NOT EXISTS intent_outcomes (
                    intent_id TEXT NOT NULL,
                    horizon_sessions INTEGER NOT NULL,
                    run_id TEXT NOT NULL REFERENCES runs(run_id),
                    symbol TEXT NOT NULL,
                    side TEXT NOT NULL,
                    signal_at TEXT NOT NULL,
                    baseline_price TEXT NOT NULL,
                    disposition TEXT NOT NULL,
                    status TEXT NOT NULL,
                    marked_at TEXT,
                    mark_price TEXT,
                    raw_return TEXT,
                    side_adjusted_return TEXT,
                    maximum_favorable_excursion TEXT,
                    maximum_adverse_excursion TEXT,
                    metadata_json TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    PRIMARY KEY(intent_id, horizon_sessions)
                );
                CREATE INDEX IF NOT EXISTS idx_intent_outcomes_pending
                    ON intent_outcomes(status, symbol, signal_at);
                CREATE TABLE IF NOT EXISTS position_trails (
                    symbol TEXT PRIMARY KEY,
                    high_since_entry TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS agent_origin_costs (
                    origin_cost_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    run_id TEXT,
                    cause TEXT NOT NULL,
                    cost_usd TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_agent_origin_costs_created
                    ON agent_origin_costs(created_at);
                """
            )

    def record_agent_origin_cost(
        self, run_id: Optional[str], cause: str, cost_usd: Decimal
    ) -> None:
        with self._connection() as connection:
            connection.execute(
                """
                INSERT INTO agent_origin_costs(run_id, cause, cost_usd, created_at)
                VALUES (?, ?, ?, ?)
                """,
                (run_id, cause[:500], str(cost_usd), utc_now().isoformat()),
            )

    def agent_origin_cost_since(self, since: datetime) -> Decimal:
        with self._connection() as connection:
            rows = connection.execute(
                "SELECT cost_usd FROM agent_origin_costs WHERE created_at >= ?",
                (since.isoformat(),),
            ).fetchall()
        return sum((Decimal(row["cost_usd"]) for row in rows), Decimal("0"))

    def create_agent_task(
        self,
        role: str,
        objective: str,
        *,
        model: Optional[str] = None,
        parent_task_id: Optional[str] = None,
    ) -> str:
        task_id = uuid.uuid4().hex
        now = utc_now().isoformat()
        with self._connection() as connection:
            connection.execute(
                """
                INSERT INTO agent_tasks(
                    task_id, parent_task_id, role, model, objective, status, progress,
                    checkpoint_json, heartbeat_at, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, 'queued', '', '{}', ?, ?, ?)
                """,
                (task_id, parent_task_id, role, model, objective, now, now, now),
            )
        return task_id

    def update_agent_task(
        self,
        task_id: str,
        *,
        status: Optional[str] = None,
        progress: Optional[str] = None,
        checkpoint: Optional[Dict[str, Any]] = None,
        error: Optional[str] = None,
    ) -> None:
        allowed = {"queued", "running", "complete", "failed", "paused", "cancelled"}
        if status is not None and status not in allowed:
            raise ValueError("Invalid agent task status {}".format(status))
        now = utc_now().isoformat()
        assignments = ["heartbeat_at = ?", "updated_at = ?"]
        values: List[Any] = [now, now]
        if status is not None:
            assignments.append("status = ?")
            values.append(status)
            if status == "running":
                assignments.append("started_at = COALESCE(started_at, ?)")
                values.append(now)
            if status in {"complete", "failed", "cancelled"}:
                assignments.append("finished_at = ?")
                values.append(now)
        if progress is not None:
            assignments.append("progress = ?")
            values.append(progress[:4000])
        if checkpoint is not None:
            assignments.append("checkpoint_json = ?")
            values.append(json.dumps(jsonable(checkpoint), sort_keys=True))
        if error is not None:
            assignments.append("error = ?")
            values.append(error[:4000])
        values.append(task_id)
        with self._connection() as connection:
            changed = connection.execute(
                "UPDATE agent_tasks SET {} WHERE task_id = ?".format(", ".join(assignments)),
                values,
            )
        if changed.rowcount != 1:
            raise KeyError("Unknown agent task {}".format(task_id))

    def agent_tasks(self, limit: int = 100) -> List[Dict[str, Any]]:
        with self._connection() as connection:
            rows = connection.execute(
                "SELECT * FROM agent_tasks ORDER BY created_at DESC LIMIT ?", (limit,)
            ).fetchall()
        result = []
        for row in rows:
            item = dict(row)
            item["checkpoint"] = json.loads(item.pop("checkpoint_json"))
            result.append(item)
        return result

    def set_agent_task_status(self, task_id: str, status: str) -> None:
        if status not in {"paused", "queued", "cancelled"}:
            raise ValueError("Operator status must be paused, queued, or cancelled")
        self.update_agent_task(task_id, status=status, progress="Set to {} by operator".format(status))
        now = utc_now().isoformat()
        with self._connection() as connection:
            connection.execute(
                """
                UPDATE jobs SET status = ?, worker_id = NULL, lease_expires_at = NULL, updated_at = ?
                WHERE json_extract(payload_json, '$.task_id') = ?
                  AND status IN ('queued', 'running', 'paused')
                """,
                (status, now, task_id),
            )

    def agent_task(self, task_id: str) -> Optional[Dict[str, Any]]:
        with self._connection() as connection:
            row = connection.execute(
                "SELECT * FROM agent_tasks WHERE task_id = ?", (task_id,)
            ).fetchone()
        if row is None:
            return None
        item = dict(row)
        item["checkpoint"] = json.loads(item.pop("checkpoint_json"))
        return item

    def save_universe_snapshot(
        self,
        candidates: List[Dict[str, Any]],
        *,
        source: str,
        mandate: Dict[str, Any],
        citations: Optional[List[str]] = None,
    ) -> str:
        snapshot_id = uuid.uuid4().hex
        now = utc_now().isoformat()
        with self._connection() as connection:
            connection.execute(
                """
                INSERT INTO universe_snapshots(snapshot_id, source, mandate_json, citations_json, created_at)
                VALUES (?, ?, ?, ?, ?)
                """,
                (
                    snapshot_id,
                    source,
                    json.dumps(jsonable(mandate), sort_keys=True),
                    json.dumps(citations or [], sort_keys=True),
                    now,
                ),
            )
            for item in candidates:
                connection.execute(
                    """
                    INSERT INTO universe_candidates(
                        snapshot_id, symbol, name, theme, rationale, confidence, status, metadata_json
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        snapshot_id,
                        str(item["symbol"]).upper(),
                        str(item.get("name") or ""),
                        str(item.get("theme") or ""),
                        str(item.get("rationale") or "")[:4000],
                        str(item.get("confidence") or "0"),
                        str(item.get("status") or "active"),
                        json.dumps(jsonable(item.get("metadata") or {}), sort_keys=True),
                    ),
                )
        return snapshot_id

    def current_universe(self) -> List[str]:
        with self._connection() as connection:
            snapshot = connection.execute(
                "SELECT snapshot_id FROM universe_snapshots ORDER BY created_at DESC LIMIT 1"
            ).fetchone()
            if snapshot is None:
                return []
            rows = connection.execute(
                """
                SELECT symbol FROM universe_candidates
                WHERE snapshot_id = ? AND status = 'active'
                ORDER BY confidence DESC, symbol
                """,
                (snapshot["snapshot_id"],),
            ).fetchall()
        return [str(row["symbol"]) for row in rows]

    def current_universe_candidates(self, include_rejected: bool = True) -> List[Dict[str, Any]]:
        with self._connection() as connection:
            snapshot = connection.execute(
                "SELECT snapshot_id FROM universe_snapshots ORDER BY created_at DESC LIMIT 1"
            ).fetchone()
            if snapshot is None:
                return []
            clause = "" if include_rejected else "AND status = 'active'"
            rows = connection.execute(
                """
                SELECT symbol, name, theme, rationale, confidence, status, metadata_json
                FROM universe_candidates WHERE snapshot_id = ? {}
                ORDER BY status, confidence DESC, symbol
                """.format(clause),
                (snapshot["snapshot_id"],),
            ).fetchall()
        result = []
        for row in rows:
            item = dict(row)
            item["metadata"] = json.loads(item.pop("metadata_json"))
            result.append(item)
        return result

    def universe_snapshots(self, limit: int = 20) -> List[Dict[str, Any]]:
        with self._connection() as connection:
            rows = connection.execute(
                """
                SELECT s.*, COUNT(c.symbol) AS candidate_count
                FROM universe_snapshots s
                LEFT JOIN universe_candidates c ON c.snapshot_id = s.snapshot_id
                GROUP BY s.snapshot_id ORDER BY s.created_at DESC LIMIT ?
                """,
                (limit,),
            ).fetchall()
        return [
            {
                "snapshot_id": row["snapshot_id"],
                "source": row["source"],
                "mandate": json.loads(row["mandate_json"]),
                "citations": json.loads(row["citations_json"]),
                "candidate_count": int(row["candidate_count"]),
                "created_at": row["created_at"],
            }
            for row in rows
        ]

    def enqueue_job(
        self,
        kind: str,
        payload: Dict[str, Any],
        *,
        available_at: Optional[datetime] = None,
        priority: int = 0,
        max_attempts: int = 3,
        dedupe_key: Optional[str] = None,
    ) -> str:
        job_id = uuid.uuid4().hex
        now = utc_now()
        try:
            with self._connection() as connection:
                connection.execute(
                    """
                    INSERT INTO jobs(
                        job_id, kind, status, priority, payload_json, available_at,
                        attempts, max_attempts, dedupe_key, created_at, updated_at
                    ) VALUES (?, ?, 'queued', ?, ?, ?, 0, ?, ?, ?, ?)
                    """,
                    (
                        job_id,
                        kind,
                        priority,
                        json.dumps(jsonable(payload), sort_keys=True),
                        (available_at or now).isoformat(),
                        max_attempts,
                        dedupe_key,
                        now.isoformat(),
                        now.isoformat(),
                    ),
                )
            return job_id
        except sqlite3.IntegrityError:
            if dedupe_key is None:
                raise
            with self._connection() as connection:
                row = connection.execute(
                    "SELECT job_id FROM jobs WHERE dedupe_key = ?", (dedupe_key,)
                ).fetchone()
            if row is None:
                raise
            return str(row["job_id"])

    def job_for_dedupe(self, dedupe_key: str) -> Optional[str]:
        with self._connection() as connection:
            row = connection.execute(
                "SELECT job_id FROM jobs WHERE dedupe_key = ?", (dedupe_key,)
            ).fetchone()
        return str(row["job_id"]) if row is not None else None

    def has_active_job_for_suggestion(self, kind: str, suggestion_id: str) -> bool:
        with self._connection() as connection:
            rows = connection.execute(
                """
                SELECT payload_json FROM jobs
                WHERE kind = ? AND status IN ('queued', 'running', 'paused')
                """,
                (kind,),
            ).fetchall()
        return any(
            str((json.loads(row["payload_json"]) or {}).get("suggestion_id")) == suggestion_id
            for row in rows
        )

    def claim_job(self, worker_id: str, lease_seconds: int = 300) -> Optional[Dict[str, Any]]:
        now = utc_now()
        lease_until = now + timedelta(seconds=lease_seconds)
        connection = self._connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            connection.execute(
                """
                UPDATE jobs SET status = 'queued', worker_id = NULL, lease_expires_at = NULL,
                    updated_at = ?
                WHERE status = 'running' AND lease_expires_at <= ?
                """,
                (now.isoformat(), now.isoformat()),
            )
            row = connection.execute(
                """
                SELECT * FROM jobs
                WHERE status = 'queued' AND available_at <= ? AND attempts < max_attempts
                ORDER BY priority DESC, available_at, created_at LIMIT 1
                """,
                (now.isoformat(),),
            ).fetchone()
            if row is None:
                connection.rollback()
                return None
            updated = connection.execute(
                """
                UPDATE jobs SET status = 'running', worker_id = ?, lease_expires_at = ?,
                    attempts = attempts + 1, updated_at = ?
                WHERE job_id = ? AND status = 'queued'
                """,
                (worker_id, lease_until.isoformat(), now.isoformat(), row["job_id"]),
            )
            if updated.rowcount != 1:
                connection.rollback()
                return None
            connection.commit()
            result = dict(row)
            result["status"] = "running"
            result["attempts"] = int(row["attempts"]) + 1
            result["payload"] = json.loads(row["payload_json"])
            return result
        finally:
            connection.close()

    def finish_job(self, job_id: str, result: Optional[Dict[str, Any]] = None) -> None:
        now = utc_now().isoformat()
        with self._connection() as connection:
            connection.execute(
                """
                UPDATE jobs SET status = 'complete', result_json = ?, worker_id = NULL,
                    lease_expires_at = NULL, error = NULL, updated_at = ?
                WHERE job_id = ? AND status NOT IN ('cancelled', 'paused')
                """,
                (json.dumps(jsonable(result or {}), sort_keys=True), now, job_id),
            )

    def fail_job(self, job_id: str, error: str, retry_delay_seconds: int = 30) -> str:
        now = utc_now()
        with self._connection() as connection:
            row = connection.execute(
                "SELECT attempts, max_attempts FROM jobs WHERE job_id = ?", (job_id,)
            ).fetchone()
            if row is None:
                raise KeyError("Unknown job {}".format(job_id))
            current = connection.execute(
                "SELECT status FROM jobs WHERE job_id = ?", (job_id,)
            ).fetchone()
            if current is not None and current["status"] == "cancelled":
                return "cancelled"
            status = "failed" if row["attempts"] >= row["max_attempts"] else "queued"
            connection.execute(
                """
                UPDATE jobs SET status = ?, error = ?, available_at = ?, worker_id = NULL,
                    lease_expires_at = NULL, updated_at = ? WHERE job_id = ?
                """,
                (
                    status,
                    error[:4000],
                    (now + timedelta(seconds=retry_delay_seconds)).isoformat(),
                    now.isoformat(),
                    job_id,
                ),
            )
        return status

    def job_counts(self) -> Dict[str, int]:
        with self._connection() as connection:
            rows = connection.execute(
                "SELECT status, COUNT(*) AS count FROM jobs GROUP BY status"
            ).fetchall()
        return {str(row["status"]): int(row["count"]) for row in rows}

    def recent_jobs(self, limit: int = 20) -> List[Dict[str, Any]]:
        with self._connection() as connection:
            rows = connection.execute(
                """
                SELECT job_id, kind, status, priority, available_at, attempts, max_attempts,
                    error, created_at, updated_at, payload_json, result_json
                FROM jobs ORDER BY created_at DESC LIMIT ?
                """,
                (limit,),
            ).fetchall()
        result = []
        for row in rows:
            item = dict(row)
            item["payload"] = json.loads(item.pop("payload_json"))
            raw_result = item.pop("result_json")
            item["result"] = json.loads(raw_result) if raw_result else None
            result.append(item)
        return result

    def service_event(
        self,
        event_type: str,
        message: str,
        payload: Optional[Dict[str, Any]] = None,
        level: str = "info",
    ) -> None:
        with self._connection() as connection:
            connection.execute(
                """
                INSERT INTO service_events(event_type, level, message, payload_json, created_at)
                VALUES (?, ?, ?, ?, ?)
                """,
                (event_type, level, message, json.dumps(jsonable(payload or {}), sort_keys=True), utc_now().isoformat()),
            )

    def recent_service_events(self, limit: int = 50) -> List[Dict[str, Any]]:
        with self._connection() as connection:
            rows = connection.execute(
                """
                SELECT service_event_id, event_type, level, message, payload_json, created_at
                FROM service_events ORDER BY service_event_id DESC LIMIT ?
                """,
                (limit,),
            ).fetchall()
        return [
            {
                "event_id": row["service_event_id"],
                "event_type": row["event_type"],
                "level": row["level"],
                "message": row["message"],
                "payload": json.loads(row["payload_json"]),
                "created_at": row["created_at"],
            }
            for row in rows
        ]

    def start_run(self, mode: str, config: Dict[str, Any]) -> str:
        run_id = uuid.uuid4().hex
        with self._connection() as connection:
            connection.execute(
                "INSERT INTO runs(run_id, started_at, status, mode, config_json) VALUES (?, ?, ?, ?, ?)",
                (
                    run_id,
                    utc_now().isoformat(),
                    RunStatus.RUNNING.value,
                    mode,
                    json.dumps(jsonable(config), sort_keys=True),
                ),
            )
        return run_id

    def finish_run(self, run_id: str, status: RunStatus, error: Optional[str] = None) -> None:
        with self._connection() as connection:
            connection.execute(
                "UPDATE runs SET finished_at = ?, status = ?, error = ? WHERE run_id = ?",
                (utc_now().isoformat(), status.value, error, run_id),
            )

    def event(self, run_id: str, event_type: str, payload: Any) -> None:
        with self._connection() as connection:
            connection.execute(
                "INSERT INTO events(run_id, event_type, created_at, payload_json) VALUES (?, ?, ?, ?)",
                (run_id, event_type, utc_now().isoformat(), json.dumps(jsonable(payload), sort_keys=True)),
            )

    def events(self, run_id: str) -> List[Dict[str, Any]]:
        with self._connection() as connection:
            rows = connection.execute(
                "SELECT event_type, created_at, payload_json FROM events WHERE run_id = ? ORDER BY event_id",
                (run_id,),
            ).fetchall()
        return [
            {
                "event_type": row["event_type"],
                "created_at": row["created_at"],
                "payload": json.loads(row["payload_json"]),
            }
            for row in rows
        ]

    def claim_order(self, run_id: str, intent_id: str, client_order_id: str, payload: Any) -> bool:
        try:
            with self._connection() as connection:
                connection.execute(
                    """
                    INSERT INTO order_ledger(intent_id, client_order_id, run_id, status, payload_json, updated_at)
                    VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    (
                        intent_id,
                        client_order_id,
                        run_id,
                        "claimed",
                        json.dumps(jsonable(payload), sort_keys=True),
                        utc_now().isoformat(),
                    ),
                )
            return True
        except sqlite3.IntegrityError:
            return False

    def update_order(self, intent_id: str, status: str, payload: Any) -> None:
        with self._connection() as connection:
            connection.execute(
                "UPDATE order_ledger SET status = ?, payload_json = ?, updated_at = ? WHERE intent_id = ?",
                (status, json.dumps(jsonable(payload), sort_keys=True), utc_now().isoformat(), intent_id),
            )

    def order_record(self, intent_id: str) -> Optional[Dict[str, Any]]:
        with self._connection() as connection:
            row = connection.execute(
                "SELECT * FROM order_ledger WHERE intent_id = ?", (intent_id,)
            ).fetchone()
        if row is None:
            return None
        item = dict(row)
        item["payload"] = json.loads(item.pop("payload_json"))
        return item

    def pending_order_records(self, limit: int = 500) -> List[Dict[str, Any]]:
        terminal = ("filled", "canceled", "cancelled", "rejected", "expired", "failed")
        placeholders = ",".join("?" for _ in terminal)
        with self._connection() as connection:
            rows = connection.execute(
                """
                SELECT * FROM order_ledger WHERE status NOT IN ({})
                ORDER BY updated_at LIMIT ?
                """.format(placeholders),
                (*terminal, limit),
            ).fetchall()
        result = []
        for row in rows:
            item = dict(row)
            item["payload"] = json.loads(item.pop("payload_json"))
            result.append(item)
        return result

    def has_order(self, intent_id: str) -> bool:
        with self._connection() as connection:
            row = connection.execute(
                "SELECT 1 FROM order_ledger WHERE intent_id = ?", (intent_id,)
            ).fetchone()
        return row is not None

    def save_review(self, run_id: str, review: Any) -> None:
        with self._connection() as connection:
            connection.execute(
                "INSERT INTO reviews(run_id, created_at, payload_json) VALUES (?, ?, ?)",
                (run_id, utc_now().isoformat(), json.dumps(jsonable(review), sort_keys=True)),
            )

    def propose_learning_experiments(self, run_id: str, changes: List[Dict[str, Any]]) -> List[str]:
        experiment_ids = []
        now = utc_now().isoformat()
        with self._connection() as connection:
            for change in changes:
                experiment_id = uuid.uuid4().hex
                connection.execute(
                    """
                    INSERT INTO learning_experiments(
                        experiment_id, run_id, parameter, proposal, status, metrics_json,
                        created_at, updated_at
                    ) VALUES (?, ?, ?, ?, 'proposed', '{}', ?, ?)
                    """,
                    (
                        experiment_id,
                        run_id,
                        str(change.get("parameter") or "unspecified")[:500],
                        str(change.get("proposal") or "")[:4000],
                        now,
                        now,
                    ),
                )
                experiment_ids.append(experiment_id)
        return experiment_ids

    def register_intent_outcomes(
        self,
        run_id: str,
        intent: Any,
        baseline_price: Decimal,
        horizons: tuple[int, ...] = (1, 5, 20, 60),
    ) -> None:
        now = utc_now().isoformat()
        with self._connection() as connection:
            for horizon in horizons:
                connection.execute(
                    """
                    INSERT OR IGNORE INTO intent_outcomes(
                        intent_id, horizon_sessions, run_id, symbol, side, signal_at,
                        baseline_price, disposition, status, metadata_json, created_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, 'proposed', 'pending', ?, ?, ?)
                    """,
                    (
                        intent.intent_id,
                        horizon,
                        run_id,
                        intent.symbol,
                        intent.side.value,
                        intent.signal_as_of.isoformat(),
                        str(baseline_price),
                        json.dumps(
                            jsonable(
                                {
                                    "strategy_version": intent.strategy_version,
                                    "stop_price": intent.stop_price,
                                    "confidence": intent.confidence,
                                }
                            ),
                            sort_keys=True,
                        ),
                        now,
                        now,
                    ),
                )

    def update_intent_disposition(self, intent_id: str, disposition: str) -> None:
        with self._connection() as connection:
            connection.execute(
                "UPDATE intent_outcomes SET disposition = ?, updated_at = ? WHERE intent_id = ?",
                (disposition, utc_now().isoformat(), intent_id),
            )

    def pending_intent_outcomes(self, limit: int = 1000) -> List[Dict[str, Any]]:
        with self._connection() as connection:
            rows = connection.execute(
                """
                SELECT * FROM intent_outcomes WHERE status = 'pending'
                ORDER BY signal_at, horizon_sessions LIMIT ?
                """,
                (limit,),
            ).fetchall()
        result = []
        for row in rows:
            item = dict(row)
            item["metadata"] = json.loads(item.pop("metadata_json"))
            result.append(item)
        return result

    def mark_intent_outcome(
        self,
        intent_id: str,
        horizon_sessions: int,
        *,
        marked_at: datetime,
        mark_price: Decimal,
        raw_return: Decimal,
        side_adjusted_return: Decimal,
        maximum_favorable_excursion: Decimal,
        maximum_adverse_excursion: Decimal,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> None:
        now = utc_now().isoformat()
        with self._connection() as connection:
            connection.execute(
                """
                UPDATE intent_outcomes SET status = 'marked', marked_at = ?, mark_price = ?,
                    raw_return = ?, side_adjusted_return = ?, maximum_favorable_excursion = ?,
                    maximum_adverse_excursion = ?, metadata_json = ?, updated_at = ?
                WHERE intent_id = ? AND horizon_sessions = ? AND status = 'pending'
                """,
                (
                    marked_at.isoformat(),
                    str(mark_price),
                    str(raw_return),
                    str(side_adjusted_return),
                    str(maximum_favorable_excursion),
                    str(maximum_adverse_excursion),
                    json.dumps(jsonable(metadata or {}), sort_keys=True),
                    now,
                    intent_id,
                    horizon_sessions,
                ),
            )

    def recent_outcome_marks(self, limit: int = 250) -> List[Dict[str, Any]]:
        with self._connection() as connection:
            rows = connection.execute(
                """
                SELECT * FROM intent_outcomes WHERE status = 'marked'
                ORDER BY marked_at DESC LIMIT ?
                """,
                (limit,),
            ).fetchall()
        result = []
        for row in rows:
            item = dict(row)
            item["metadata"] = json.loads(item.pop("metadata_json"))
            result.append(item)
        return result

    def update_position_trail(
        self, symbol: str, session_high: Decimal, held: bool
    ) -> Decimal:
        normalized = symbol.strip().upper()
        if not held:
            with self._connection() as connection:
                connection.execute("DELETE FROM position_trails WHERE symbol = ?", (normalized,))
            return session_high
        with self._connection() as connection:
            row = connection.execute(
                "SELECT high_since_entry FROM position_trails WHERE symbol = ?", (normalized,)
            ).fetchone()
            high = (
                max(session_high, Decimal(row["high_since_entry"]))
                if row
                else session_high
            )
            connection.execute(
                """
                INSERT INTO position_trails(symbol, high_since_entry, updated_at) VALUES (?, ?, ?)
                ON CONFLICT(symbol) DO UPDATE SET high_since_entry = excluded.high_since_entry,
                    updated_at = excluded.updated_at
                """,
                (normalized, str(high), utc_now().isoformat()),
            )
        return high

    def learning_experiments(self, limit: int = 100) -> List[Dict[str, Any]]:
        with self._connection() as connection:
            rows = connection.execute(
                "SELECT * FROM learning_experiments ORDER BY created_at DESC LIMIT ?", (limit,)
            ).fetchall()
        result = []
        for row in rows:
            item = dict(row)
            item["metrics"] = json.loads(item.pop("metrics_json"))
            result.append(item)
        return result

    def recent_reviews(self, limit: int = 5) -> List[Dict[str, Any]]:
        with self._connection() as connection:
            rows = connection.execute(
                "SELECT payload_json FROM reviews ORDER BY review_id DESC LIMIT ?", (limit,)
            ).fetchall()
        return [json.loads(row["payload_json"]) for row in rows]

    def last_review_at(self) -> Optional[datetime]:
        with self._connection() as connection:
            row = connection.execute(
                "SELECT created_at FROM reviews ORDER BY review_id DESC LIMIT 1"
            ).fetchone()
        return datetime.fromisoformat(row["created_at"]) if row else None

    def material_events_since(self, since: Optional[datetime], limit: int = 250) -> List[Dict[str, Any]]:
        event_types = (
            "signal",
            "evidence",
            "intent_proposed",
            "critic_approved",
            "critic_rejected",
            "risk_approved",
            "risk_rejected",
            "order_submitted",
            "order_submission_error",
        )
        placeholders = ",".join("?" for _ in event_types)
        parameters: List[Any] = list(event_types)
        where = "event_type IN ({})".format(placeholders)
        if since is not None:
            where += " AND created_at > ?"
            parameters.append(since.isoformat())
        parameters.append(limit)
        with self._connection() as connection:
            rows = connection.execute(
                """
                SELECT run_id, event_type, created_at, payload_json
                FROM events WHERE {} ORDER BY event_id DESC LIMIT ?
                """.format(where),
                parameters,
            ).fetchall()
        return [
            {
                "run_id": row["run_id"],
                "event_type": row["event_type"],
                "created_at": row["created_at"],
                "payload": json.loads(row["payload_json"]),
            }
            for row in reversed(rows)
        ]

    def record_api_usage(self, run_id: str, usage: ApiUsage) -> None:
        with self._connection() as connection:
            connection.execute(
                """
                INSERT INTO api_usage(
                    run_id, created_at, service, operation, model, input_tokens,
                    output_tokens, request_count, cost_usd, estimated, payload_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    run_id,
                    utc_now().isoformat(),
                    usage.service,
                    usage.operation,
                    usage.model,
                    usage.input_tokens,
                    usage.output_tokens,
                    usage.request_count,
                    str(usage.cost_usd) if usage.cost_usd is not None else None,
                    int(usage.estimated),
                    json.dumps(jsonable(usage), sort_keys=True),
                ),
            )

    def api_cost_since(self, since: datetime) -> Decimal:
        with self._connection() as connection:
            rows = connection.execute(
                "SELECT cost_usd FROM api_usage WHERE created_at >= ? AND cost_usd IS NOT NULL",
                (since.isoformat(),),
            ).fetchall()
        return sum((Decimal(row["cost_usd"]) for row in rows), Decimal("0"))

    def usage_for_run(self, run_id: str) -> List[Dict[str, Any]]:
        with self._connection() as connection:
            rows = connection.execute(
                "SELECT payload_json FROM api_usage WHERE run_id = ? ORDER BY usage_id", (run_id,)
            ).fetchall()
        return [json.loads(row["payload_json"]) for row in rows]

    def claim_decision_trigger(
        self,
        run_id: str,
        trigger_key: str,
        symbol: str,
        strategy_version: str,
        action: str,
        now: datetime,
        cooldown_hours: int,
        lease_minutes: int = 30,
    ) -> bool:
        connection = self._connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                "SELECT status, last_completed_at, updated_at FROM decision_triggers WHERE trigger_key = ?",
                (trigger_key,),
            ).fetchone()
            if row:
                last_completed = (
                    datetime.fromisoformat(row["last_completed_at"]) if row["last_completed_at"] else None
                )
                updated_at = datetime.fromisoformat(row["updated_at"])
                if row["status"] == "complete" and last_completed is not None:
                    if now - last_completed < timedelta(hours=cooldown_hours):
                        connection.rollback()
                        return False
                if row["status"] == "claimed" and now - updated_at < timedelta(minutes=lease_minutes):
                    connection.rollback()
                    return False
                connection.execute(
                    """
                    UPDATE decision_triggers
                    SET status = 'claimed', run_id = ?, updated_at = ? WHERE trigger_key = ?
                    """,
                    (run_id, now.isoformat(), trigger_key),
                )
            else:
                connection.execute(
                    """
                    INSERT INTO decision_triggers(
                        trigger_key, symbol, strategy_version, action, status, run_id, updated_at
                    ) VALUES (?, ?, ?, ?, 'claimed', ?, ?)
                    """,
                    (trigger_key, symbol, strategy_version, action, run_id, now.isoformat()),
                )
            connection.commit()
            return True
        finally:
            connection.close()

    def finish_decision_trigger(self, trigger_key: str, now: datetime, success: bool) -> None:
        with self._connection() as connection:
            connection.execute(
                """
                UPDATE decision_triggers
                SET status = ?, last_completed_at = CASE WHEN ? THEN ? ELSE last_completed_at END,
                    updated_at = ? WHERE trigger_key = ?
                """,
                (
                    "complete" if success else "failed",
                    int(success),
                    now.isoformat(),
                    now.isoformat(),
                    trigger_key,
                ),
            )

    def create_price_trigger(
        self,
        symbol: str,
        comparison: str,
        threshold: Decimal,
        *,
        source: str = "user",
        one_shot: bool = True,
        cooldown_seconds: int = 3600,
        metadata: Optional[Dict[str, Any]] = None,
        deduplicate_active: bool = False,
    ) -> str:
        trigger_id = uuid.uuid4().hex
        now = utc_now().isoformat()
        if deduplicate_active:
            with self._connection() as connection:
                existing = connection.execute(
                    """
                    SELECT trigger_id FROM price_triggers
                    WHERE symbol = ? AND comparison = ? AND threshold = ?
                        AND source = ? AND status = 'active'
                    ORDER BY created_at DESC LIMIT 1
                    """,
                    (symbol.strip().upper(), comparison, str(threshold), source),
                ).fetchone()
            if existing:
                return str(existing["trigger_id"])
        with self._connection() as connection:
            connection.execute(
                """
                INSERT INTO price_triggers(
                    trigger_id, symbol, comparison, threshold, status, source, one_shot,
                    cooldown_seconds, metadata_json, created_at, updated_at
                ) VALUES (?, ?, ?, ?, 'active', ?, ?, ?, ?, ?, ?)
                """,
                (
                    trigger_id,
                    symbol.strip().upper(),
                    comparison,
                    str(threshold),
                    source,
                    int(one_shot),
                    cooldown_seconds,
                    json.dumps(jsonable(metadata or {}), sort_keys=True),
                    now,
                    now,
                ),
            )
        return trigger_id

    def active_price_triggers(self) -> List[Dict[str, Any]]:
        with self._connection() as connection:
            rows = connection.execute(
                "SELECT * FROM price_triggers WHERE status = 'active' ORDER BY created_at"
            ).fetchall()
        return [self._price_trigger_row(row) for row in rows]

    def price_triggers(self, limit: int = 100) -> List[Dict[str, Any]]:
        with self._connection() as connection:
            rows = connection.execute(
                "SELECT * FROM price_triggers ORDER BY created_at DESC LIMIT ?", (limit,)
            ).fetchall()
        return [self._price_trigger_row(row) for row in rows]

    @staticmethod
    def _price_trigger_row(row: sqlite3.Row) -> Dict[str, Any]:
        item = dict(row)
        item["threshold"] = str(item["threshold"])
        item["one_shot"] = bool(item["one_shot"])
        item["metadata"] = json.loads(item.pop("metadata_json"))
        return item

    def record_price_check(self, trigger_id: str, price: Decimal, fired: bool) -> None:
        now = utc_now().isoformat()
        with self._connection() as connection:
            row = connection.execute(
                "SELECT one_shot FROM price_triggers WHERE trigger_id = ?", (trigger_id,)
            ).fetchone()
            if row is None:
                raise KeyError("Unknown price trigger {}".format(trigger_id))
            status = "fired" if fired and bool(row["one_shot"]) else "active"
            connection.execute(
                """
                UPDATE price_triggers SET last_price = ?, status = ?,
                    last_fired_at = CASE WHEN ? THEN ? ELSE last_fired_at END,
                    updated_at = ? WHERE trigger_id = ?
                """,
                (str(price), status, int(fired), now, now, trigger_id),
            )

    def disable_price_trigger(self, trigger_id: str) -> bool:
        with self._connection() as connection:
            result = connection.execute(
                "UPDATE price_triggers SET status = 'disabled', updated_at = ? WHERE trigger_id = ?",
                (utc_now().isoformat(), trigger_id),
            )
        return result.rowcount == 1

    def disable_protective_stops(self, symbol: str) -> int:
        with self._connection() as connection:
            result = connection.execute(
                """
                UPDATE price_triggers SET status = 'disabled', updated_at = ?
                WHERE symbol = ? AND source = 'protective-stop' AND status = 'active'
                """,
                (utc_now().isoformat(), symbol.strip().upper()),
            )
        return result.rowcount

    def upsert_protective_stop(
        self, symbol: str, threshold: Decimal, metadata: Optional[Dict[str, Any]] = None
    ) -> str:
        """Arm or ratchet a long-position stop upward; never loosen it."""

        normalized = symbol.strip().upper()
        now = utc_now().isoformat()
        with self._connection() as connection:
            existing = connection.execute(
                """
                SELECT trigger_id, threshold FROM price_triggers
                WHERE symbol = ? AND source = 'protective-stop' AND status = 'active'
                ORDER BY created_at DESC LIMIT 1
                """,
                (normalized,),
            ).fetchone()
            if existing:
                ratcheted = max(Decimal(existing["threshold"]), Decimal(threshold))
                connection.execute(
                    """
                    UPDATE price_triggers SET threshold = ?, metadata_json = ?, updated_at = ?
                    WHERE trigger_id = ?
                    """,
                    (
                        str(ratcheted),
                        json.dumps(jsonable(metadata or {}), sort_keys=True),
                        now,
                        existing["trigger_id"],
                    ),
                )
                return str(existing["trigger_id"])
            trigger_id = uuid.uuid4().hex
            connection.execute(
                """
                INSERT INTO price_triggers(
                    trigger_id, symbol, comparison, threshold, status, source, one_shot,
                    cooldown_seconds, metadata_json, created_at, updated_at
                ) VALUES (?, ?, 'below', ?, 'active', 'protective-stop', 1, 3600, ?, ?, ?)
                """,
                (
                    trigger_id,
                    normalized,
                    str(threshold),
                    json.dumps(jsonable(metadata or {}), sort_keys=True),
                    now,
                    now,
                ),
            )
        return trigger_id

    def create_trade_suggestion(
        self,
        run_id: str,
        intent_id: str,
        symbol: str,
        side: str,
        payload: Dict[str, Any],
    ) -> str:
        suggestion_id = uuid.uuid4().hex
        now = utc_now().isoformat()
        try:
            with self._connection() as connection:
                connection.execute(
                    """
                    INSERT INTO trade_suggestions(
                        suggestion_id, intent_id, run_id, symbol, side, status, review_count,
                        payload_json, created_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?, 'proposed', 0, ?, ?, ?)
                    """,
                    (
                        suggestion_id,
                        intent_id,
                        run_id,
                        symbol,
                        side,
                        json.dumps(jsonable(payload), sort_keys=True),
                        now,
                        now,
                    ),
                )
            return suggestion_id
        except sqlite3.IntegrityError:
            with self._connection() as connection:
                row = connection.execute(
                    "SELECT suggestion_id, status FROM trade_suggestions WHERE intent_id = ?", (intent_id,)
                ).fetchone()
                if row is not None and row["status"] != "executed":
                    connection.execute(
                        """
                        UPDATE trade_suggestions SET run_id = ?, symbol = ?, side = ?,
                            status = 'proposed', execute_after = NULL, payload_json = ?,
                            decision_json = NULL, error = NULL, updated_at = ?
                        WHERE suggestion_id = ?
                        """,
                        (
                            run_id,
                            symbol,
                            side,
                            json.dumps(jsonable(payload), sort_keys=True),
                            utc_now().isoformat(),
                            row["suggestion_id"],
                        ),
                    )
            if row is None:
                raise
            return str(row["suggestion_id"])

    def trade_suggestion(self, suggestion_id: str) -> Optional[Dict[str, Any]]:
        with self._connection() as connection:
            row = connection.execute(
                "SELECT * FROM trade_suggestions WHERE suggestion_id = ?", (suggestion_id,)
            ).fetchone()
        return self._suggestion_row(row) if row else None

    def trade_suggestions(self, limit: int = 100) -> List[Dict[str, Any]]:
        with self._connection() as connection:
            rows = connection.execute(
                "SELECT * FROM trade_suggestions ORDER BY created_at DESC LIMIT ?", (limit,)
            ).fetchall()
        return [self._suggestion_row(row) for row in rows]

    @staticmethod
    def _suggestion_row(row: sqlite3.Row) -> Dict[str, Any]:
        item = dict(row)
        item["payload"] = json.loads(item.pop("payload_json"))
        raw_decision = item.pop("decision_json")
        item["decision"] = json.loads(raw_decision) if raw_decision else None
        return item

    def update_trade_suggestion(
        self,
        suggestion_id: str,
        status: str,
        *,
        decision: Optional[Dict[str, Any]] = None,
        execute_after: Optional[datetime] = None,
        error: Optional[str] = None,
        reviewed: bool = False,
        payload: Optional[Dict[str, Any]] = None,
    ) -> None:
        now = utc_now().isoformat()
        with self._connection() as connection:
            current = connection.execute(
                "SELECT payload_json, decision_json FROM trade_suggestions WHERE suggestion_id = ?",
                (suggestion_id,),
            ).fetchone()
            if current is None:
                raise KeyError("Unknown trade suggestion {}".format(suggestion_id))
            connection.execute(
                """
                UPDATE trade_suggestions SET status = ?, decision_json = ?, execute_after = ?,
                    error = ?, review_count = review_count + ?,
                    last_review_at = CASE WHEN ? THEN ? ELSE last_review_at END,
                    payload_json = ?, updated_at = ? WHERE suggestion_id = ?
                """,
                (
                    status,
                    json.dumps(jsonable(decision), sort_keys=True) if decision is not None else current["decision_json"],
                    execute_after.isoformat() if execute_after else None,
                    error[:4000] if error else None,
                    int(reviewed),
                    int(reviewed),
                    now,
                    json.dumps(jsonable(payload), sort_keys=True) if payload is not None else current["payload_json"],
                    now,
                    suggestion_id,
                ),
            )

    def add_message(
        self,
        conversation_id: str,
        role: str,
        content: str,
        *,
        status: str = "complete",
        metadata: Optional[Dict[str, Any]] = None,
    ) -> str:
        message_id = uuid.uuid4().hex
        with self._connection() as connection:
            connection.execute(
                """
                INSERT INTO conversations(
                    message_id, conversation_id, role, content, status, metadata_json, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    message_id,
                    conversation_id,
                    role,
                    content,
                    status,
                    json.dumps(jsonable(metadata or {}), sort_keys=True),
                    utc_now().isoformat(),
                ),
            )
        return message_id

    def execution_totals_for_run(self, run_id: str) -> Dict[str, Any]:
        with self._connection() as connection:
            rows = connection.execute(
                """
                SELECT decision_json FROM trade_suggestions
                WHERE run_id = ? AND status IN ('executed', 'submitted_awaiting_fill')
                """,
                (run_id,),
            ).fetchall()
        notional = Decimal("0")
        for row in rows:
            decision = json.loads(row["decision_json"]) if row["decision_json"] else {}
            risk = decision.get("risk") or decision
            if risk.get("approved_notional") is not None:
                notional += Decimal(str(risk["approved_notional"]))
        return {"order_count": len(rows), "new_exposure_notional": notional}

    def conversation(self, conversation_id: str, limit: int = 100) -> List[Dict[str, Any]]:
        with self._connection() as connection:
            rows = connection.execute(
                """
                SELECT message_id, conversation_id, role, content, status, metadata_json, created_at
                FROM conversations WHERE conversation_id = ? ORDER BY created_at DESC LIMIT ?
                """,
                (conversation_id, limit),
            ).fetchall()
        return [
            {
                "message_id": row["message_id"],
                "conversation_id": row["conversation_id"],
                "role": row["role"],
                "content": row["content"],
                "status": row["status"],
                "metadata": json.loads(row["metadata_json"]),
                "created_at": row["created_at"],
            }
            for row in reversed(rows)
        ]

    def recent_runs(self, limit: int = 20) -> List[Dict[str, Any]]:
        with self._connection() as connection:
            rows = connection.execute(
                """
                SELECT run_id, started_at, finished_at, status, mode, error
                FROM runs ORDER BY started_at DESC LIMIT ?
                """,
                (limit,),
            ).fetchall()
        return [dict(row) for row in rows]
