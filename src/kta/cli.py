"""Command-line entry point for local, simulated, and Alpaca paper runs."""

import argparse
import json
import tempfile
from dataclasses import replace
from decimal import Decimal
from pathlib import Path
from typing import List, Optional

from .agents import HeuristicAgentSuite, OpenRouterAgentSuite
from .brokers import AlpacaPaperBroker, SimulatedBroker
from .config import Settings
from .costs import estimate_monthly_cost
from .domain import RunStatus, jsonable
from .journal import Journal
from .loop import TradingLoop
from .market import AlpacaMarketData, SyntheticMarketData
from .progress import ProgressReporter
from .research import NoResearch, PerplexityResearch
from .risk import RiskEngine, RiskPolicy


def _json(value: object) -> str:
    return json.dumps(jsonable(value), indent=2, sort_keys=True)


def _settings() -> Settings:
    return Settings.from_env(Path(".env"))


def _build(settings: Settings, progress=None) -> TradingLoop:
    journal = Journal(settings.database_path)
    if settings.market_data_mode == "alpaca":
        market = AlpacaMarketData(
            settings.alpaca_data_url,
            settings.alpaca_api_key or "",
            settings.alpaca_api_secret or "",
            settings.alpaca_data_feed,
        )
    else:
        market = SyntheticMarketData()
    if settings.broker_mode == "paper":
        broker = AlpacaPaperBroker(
            settings.alpaca_trading_url,
            settings.alpaca_api_key or "",
            settings.alpaca_api_secret or "",
        )
    else:
        broker = SimulatedBroker(settings.initial_simulated_cash)
    if settings.research_mode == "perplexity":
        research = PerplexityResearch(settings.perplexity_api_key or "", settings.perplexity_model)
    else:
        research = NoResearch()
    if settings.agent_mode == "openrouter":
        agents = OpenRouterAgentSuite(
            settings.openrouter_api_key or "",
            settings.scout_model or "",
            settings.critic_model or "",
            settings.reviewer_model or "",
            settings.openrouter_reasoning_effort,
        )
    else:
        agents = HeuristicAgentSuite()
    policy = RiskPolicy(
        max_position_pct=settings.max_position_pct,
        max_gross_exposure_pct=settings.max_gross_exposure_pct,
        max_new_exposure_per_run_pct=settings.max_new_exposure_per_run_pct,
        max_orders_per_run=settings.max_orders_per_run,
        max_data_age_days=settings.max_data_age_days,
        minimum_confidence=settings.minimum_confidence,
        minimum_order_notional=settings.minimum_order_notional,
        daily_loss_kill_pct=settings.daily_loss_kill_pct,
        options_enabled=settings.options_enabled,
        shorting_enabled=settings.shorting_enabled,
    )
    return TradingLoop(
        market_data=market,
        research=research,
        agents=agents,
        risk=RiskEngine(policy),
        broker=broker,
        journal=journal,
        mode=settings.broker_mode,
        safe_config=settings.safe_dict(),
        decision_cooldown_hours=settings.decision_cooldown_hours,
        review_min_interval_hours=settings.review_min_interval_hours,
        monthly_api_budget_usd=settings.monthly_api_budget_usd,
        paid_api_enabled=(settings.agent_mode == "openrouter" or settings.research_mode == "perplexity"),
        progress=progress,
    )


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
    elif args.command == "runs":
        status = command_runs(settings, args.limit)
    elif args.command == "events":
        status = command_events(settings, args.run_id)
    elif args.command == "estimate-cost":
        status = command_estimate_cost(settings, args.signal_cycles, args.reviews, args.portfolio_usd)
    else:
        status = 2
    raise SystemExit(status)
