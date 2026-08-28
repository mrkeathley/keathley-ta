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
                """
            )

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
