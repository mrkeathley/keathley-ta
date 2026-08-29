"""Command-line entry point for local, simulated, and Alpaca paper runs."""

import argparse
import json
import logging
import tempfile
from dataclasses import replace
from decimal import Decimal
from pathlib import Path
from typing import List, Optional

from .config import Settings
from .costs import estimate_monthly_cost
from .domain import RunStatus, jsonable
from .journal import Journal
from .progress import ProgressReporter
from .runtime import build_loop
from .service import DaemonService
from .tui import run_tui


def _json(value: object) -> str:
    return json.dumps(jsonable(value), indent=2, sort_keys=True)


def _settings() -> Settings:
    return Settings.from_env(Path(".env"))


def _build(settings: Settings, progress=None):
    return build_loop(settings, progress=progress)


def _validate(settings: Settings) -> bool:
    errors = settings.validate()
    if errors:
        print(_json({"ready": False, "errors": errors, "config": settings.safe_dict()}))
        return False
    return True


def command_doctor(settings: Settings) -> int:
    errors = settings.validate()
    print(_json({"ready": not errors, "errors": errors, "config": settings.safe_dict()}))
    return 0 if not errors else 2


def command_run(settings: Settings, universe: Optional[str], quiet: bool = False) -> int:
    if universe is not None:
        symbols = [item.strip().upper() for item in universe.split(",") if item.strip()]
        settings = replace(settings, universe=symbols)
    if not _validate(settings):
        return 2
    reporter = ProgressReporter(enabled=not quiet)
    reporter.update("Preparing {} run for {} symbol(s)".format(settings.broker_mode, len(settings.universe)))
    try:
        result = _build(settings, progress=reporter.update).run(settings.universe)
    except Exception as error:
        message = "{}: {}".format(type(error).__name__, error)
        reporter.finish("Run could not start — {}".format(message), success=False)
        print(_json({"status": "failed", "run_id": None, "errors": [message]}))
        return 1
    reporter.finish(
        "Run {} {}{} — {} signal(s), {} order(s), API cost ${}".format(
            result.run_id,
            result.status.value,
            " with {} warning(s)".format(len(result.errors)) if result.errors else "",
            result.signal_count,
            result.submitted_count,
            result.api_cost_usd,
        ),
        success=result.status == RunStatus.COMPLETE,
    )
    print(_json(result))
    return 0 if result.status == RunStatus.COMPLETE else 1


def command_smoke(settings: Settings, quiet: bool = False) -> int:
    """Run an isolated, offline simulation without using configured providers."""

    with tempfile.TemporaryDirectory(prefix="kta-smoke-") as directory:
        smoke_settings = replace(
            settings,
            broker_mode="simulation",
            market_data_mode="synthetic",
            agent_mode="heuristic",
            research_mode="none",
            database_path=Path(directory) / "smoke.db",
            universe=["SYNTH1", "SYNTH2", "SYNTH3"],
            options_enabled=False,
            shorting_enabled=False,
            agentic_research_enabled=False,
        )
        return command_run(smoke_settings, universe=None, quiet=quiet)


def command_runs(settings: Settings, limit: int) -> int:
    print(_json(Journal(settings.database_path).recent_runs(limit)))
    return 0


def command_events(settings: Settings, run_id: str) -> int:
    journal = Journal(settings.database_path)
    print(_json({"events": journal.events(run_id), "api_usage": journal.usage_for_run(run_id)}))
    return 0


def command_estimate_cost(
    settings: Settings,
    signal_cycles: Optional[int],
    reviews: Optional[int],
    portfolio_usd: Optional[Decimal],
) -> int:
    print(_json(estimate_monthly_cost(settings, signal_cycles, reviews, portfolio_usd)))
    return 0


def command_daemon(settings: Settings) -> int:
    if not _validate(settings):
        return 2
    from .api import serve_control_api

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )

    print(
        "KTA daemon listening on http://{}:{} (control token: {})".format(
            settings.daemon_host,
            settings.daemon_port,
            "configured" if settings.control_token else "not configured",
        ),
        flush=True,
    )
    try:
        serve_control_api(DaemonService(settings))
    except KeyboardInterrupt:
        return 0
    return 0


def command_tui(
    settings: Settings,
    url: Optional[str],
    token: Optional[str],
    conversation: Optional[str],
    message: Optional[str],
) -> int:
    run_tui(
        url or "http://{}:{}".format(settings.daemon_host, settings.daemon_port),
        token or settings.control_token,
        conversation_id=conversation,
        message=message,
    )
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="kta", description="Auditable agentic trading experiment")
    subparsers = parser.add_subparsers(dest="command", required=True)
    subparsers.add_parser("doctor", help="validate configuration without connecting to services")
    run_parser = subparsers.add_parser("run", help="execute one configured trading/review cycle")
    run_parser.add_argument("--universe", help="comma-separated symbol override")
    run_parser.add_argument("--quiet", action="store_true", help="suppress stderr progress output")
    smoke_parser = subparsers.add_parser(
        "smoke", help="run an isolated offline simulation; never calls configured APIs or brokers"
    )
    smoke_parser.add_argument("--quiet", action="store_true", help="suppress stderr progress output")
    subparsers.add_parser("daemon", help="run the durable scheduler, workers, and HTTP control plane")
    tui_parser = subparsers.add_parser("tui", help="connect an interactive terminal client to the daemon")
    tui_parser.add_argument("--url", help="daemon base URL")
    tui_parser.add_argument("--token", help="control-plane bearer token; defaults to KTA_CONTROL_TOKEN")
    tui_parser.add_argument("--conversation", help="conversation ID to resume")
    tui_parser.add_argument("--message", help="send one message and exit after the response")
    runs_parser = subparsers.add_parser("runs", help="show recent journaled runs")
    runs_parser.add_argument("--limit", type=int, default=20)
    events_parser = subparsers.add_parser("events", help="show the complete audit trail for a run")
    events_parser.add_argument("run_id")
    cost_parser = subparsers.add_parser("estimate-cost", help="estimate steady-state API portfolio drag")
    cost_parser.add_argument("--signal-cycles", type=int, help="signal-bearing decision cycles per month")
    cost_parser.add_argument("--reviews", type=int, help="batched learning reviews per month")
    cost_parser.add_argument("--portfolio-usd", type=Decimal, help="portfolio value used for drag percentage")
    return parser


def main(argv: Optional[List[str]] = None) -> None:
    args = build_parser().parse_args(argv)
    settings = _settings()
    if args.command == "doctor":
        status = command_doctor(settings)
    elif args.command == "run":
        status = command_run(settings, args.universe, args.quiet)
    elif args.command == "smoke":
        status = command_smoke(settings, args.quiet)
    elif args.command == "daemon":
        status = command_daemon(settings)
    elif args.command == "tui":
        status = command_tui(settings, args.url, args.token, args.conversation, args.message)
    elif args.command == "runs":
        status = command_runs(settings, args.limit)
    elif args.command == "events":
        status = command_events(settings, args.run_id)
    elif args.command == "estimate-cost":
        status = command_estimate_cost(settings, args.signal_cycles, args.reviews, args.portfolio_usd)
    else:
        status = 2
    raise SystemExit(status)
