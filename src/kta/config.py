"""Environment configuration with no import-time side effects."""

import os
import tomllib
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path
from typing import Dict, List, Optional

from .domain import decimal


def load_dotenv(path: Path, *, override: bool = False, preserve: Optional[set[str]] = None) -> None:
    if not path.exists():
        return
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key:
            if override and key not in (preserve or set()):
                os.environ[key] = value
            else:
                os.environ.setdefault(key, value)


def _bool(value: str) -> bool:
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _toml_values(base: Path) -> Dict[str, str]:
    """Load non-secret configuration. Environment variables remain authoritative."""

    result: Dict[str, str] = {}
    mappings = {
        "mandate.toml": {
            ("mandate", "themes"): "KTA_MANDATE_THEMES",
            ("mandate", "regions"): "KTA_MANDATE_REGIONS",
            ("mandate", "asset_classes"): "KTA_MANDATE_ASSET_CLASSES",
            ("mandate", "excluded_industries"): "KTA_MANDATE_EXCLUDED_INDUSTRIES",
            ("mandate", "time_horizon"): "KTA_MANDATE_TIME_HORIZON",
        },
        "runtime.toml": {
            ("runtime", "database_path"): "KTA_DATABASE_PATH",
            ("runtime", "discovery_enabled"): "KTA_DISCOVERY_ENABLED",
            ("runtime", "discovery_interval_hours"): "KTA_DISCOVERY_INTERVAL_HOURS",
            ("runtime", "discovery_on_start"): "KTA_DISCOVERY_ON_START",
            ("runtime", "scan_after_discovery"): "KTA_SCAN_AFTER_DISCOVERY",
            ("runtime", "discovery_min_price"): "KTA_DISCOVERY_MIN_PRICE",
            ("runtime", "discovery_min_daily_dollar_volume"): "KTA_DISCOVERY_MIN_DAILY_DOLLAR_VOLUME",
            ("runtime", "agent_max_turns"): "KTA_AGENT_MAX_TURNS",
            ("runtime", "agent_max_research_calls"): "KTA_AGENT_MAX_RESEARCH_CALLS",
            ("runtime", "agent_max_tokens_per_turn"): "KTA_AGENT_MAX_TOKENS_PER_TURN",
            ("runtime", "monthly_api_soft_budget_usd"): "KTA_MONTHLY_API_SOFT_BUDGET_USD",
            ("models", "supervisor"): "KTA_SUPERVISOR_MODEL",
            ("models", "discovery"): "KTA_DISCOVERY_MODEL",
            ("models", "researcher"): "KTA_RESEARCHER_MODEL",
            ("models", "thesis"): "KTA_THESIS_MODEL",
            ("models", "trade"): "KTA_TRADE_MODEL",
            ("models", "critic"): "KTA_CRITIC_MODEL",
            ("models", "reviewer"): "KTA_REVIEWER_MODEL",
        },
        "policy.toml": {
            ("risk", "max_position_pct"): "KTA_MAX_POSITION_PCT",
            ("risk", "max_gross_exposure_pct"): "KTA_MAX_GROSS_EXPOSURE_PCT",
            ("risk", "max_new_exposure_per_run_pct"): "KTA_MAX_NEW_EXPOSURE_PER_RUN_PCT",
            ("risk", "max_orders_per_run"): "KTA_MAX_ORDERS_PER_RUN",
            ("risk", "daily_loss_kill_pct"): "KTA_DAILY_LOSS_KILL_PCT",
            ("risk", "risk_per_trade_pct"): "KTA_RISK_PER_TRADE_PCT",
            ("risk", "minimum_order_notional"): "KTA_MINIMUM_ORDER_NOTIONAL",
        },
    }
    for filename, fields in mappings.items():
        path = base / "config" / filename
        if not path.exists():
            continue
        with path.open("rb") as handle:
            document = tomllib.load(handle)
        for keys, environment_name in fields.items():
            value = document
            for key in keys:
                if not isinstance(value, dict) or key not in value:
                    value = None
                    break
                value = value[key]
            if value is None:
                continue
            if isinstance(value, list):
                result[environment_name] = ",".join(str(item) for item in value)
            elif isinstance(value, bool):
                result[environment_name] = "true" if value else "false"
            else:
                result[environment_name] = str(value)
    return result


@dataclass(frozen=True)
class Settings:
    environment: str
    broker_mode: str
    market_data_mode: str
    agent_mode: str
    research_mode: str
    database_path: Path
    universe: List[str]
    mandate_themes: List[str]
    mandate_regions: List[str]
    mandate_asset_classes: List[str]
    mandate_excluded_industries: List[str]
    mandate_time_horizon: str
    discovery_enabled: bool
    discovery_interval_hours: int
    discovery_on_start: bool
    scan_after_discovery: bool
    discovery_max_candidates: int
    discovery_min_price: Decimal
    discovery_min_daily_dollar_volume: Decimal
    initial_simulated_cash: Decimal
    alpaca_api_key: Optional[str]
    alpaca_api_secret: Optional[str]
    alpaca_trading_url: str
    alpaca_data_url: str
    alpaca_data_feed: str
    openrouter_api_key: Optional[str]
    scout_model: Optional[str]
    critic_model: Optional[str]
    reviewer_model: Optional[str]
    supervisor_model: Optional[str]
    discovery_model: Optional[str]
    researcher_model: Optional[str]
    thesis_model: Optional[str]
    trade_model: Optional[str]
    openrouter_reasoning_effort: str
    perplexity_api_key: Optional[str]
    perplexity_model: str
    decision_cooldown_hours: int
    review_min_interval_hours: int
    monthly_api_budget_usd: Decimal
    monthly_api_soft_budget_usd: Decimal
    enforce_monthly_api_budget: bool
    estimated_llm_input_usd_per_million: Decimal
    estimated_llm_output_usd_per_million: Decimal
    openrouter_credit_fee_pct: Decimal
    expected_signal_cycles_per_month: int
    expected_reviews_per_month: int
    max_position_pct: Decimal
    max_gross_exposure_pct: Decimal
    max_new_exposure_per_run_pct: Decimal
    max_orders_per_run: int
    max_data_age_days: int
    minimum_confidence: Decimal
    minimum_order_notional: Decimal
    daily_loss_kill_pct: Decimal
    risk_per_trade_pct: Decimal
    options_enabled: bool
    shorting_enabled: bool
    daemon_host: str
    daemon_port: int
    control_token: Optional[str]
    daemon_workers: int
    job_poll_seconds: int
    job_lease_seconds: int
    trigger_poll_seconds: int
    daily_scan_hour_et: int
    daily_scan_minute_et: int
    scan_on_start: bool
    agentic_research_enabled: bool
    agent_max_turns: int
    agent_max_research_calls: int
    agent_max_tokens_per_turn: int
    suggestion_review_ttl_minutes: int

    @classmethod
    def from_env(cls, env_file: Optional[Path] = None) -> "Settings":
        selected_env = env_file or Path(".env")
        external_environment = set(os.environ)
        load_dotenv(selected_env)
        base = Path.cwd() if selected_env == Path(".env") else selected_env.parent
        secrets_file = os.environ.get("KTA_SECRETS_FILE", str(base / "secrets.env"))
        load_dotenv(Path(secrets_file), override=True, preserve=external_environment)
        configured = _toml_values(base)

        def get(name: str, default: Optional[str] = None) -> Optional[str]:
            return os.environ.get(name, configured.get(name, default))

        def csv(name: str, default: str = "") -> List[str]:
            return [item.strip() for item in (get(name, default) or "").split(",") if item.strip()]

        universe = [
            symbol.strip().upper()
            for symbol in (get("KTA_UNIVERSE", "") or "").split(",")
            if symbol.strip()
        ]
        return cls(
            environment=get("KTA_ENVIRONMENT", "development"),
            broker_mode=get("KTA_BROKER_MODE", "simulation").lower(),
            market_data_mode=get("KTA_MARKET_DATA_MODE", "synthetic").lower(),
            agent_mode=get("KTA_AGENT_MODE", "heuristic").lower(),
            research_mode=get("KTA_RESEARCH_MODE", "none").lower(),
            database_path=Path(get("KTA_DATABASE_PATH", "var/kta.db")),
            universe=universe,
            mandate_themes=csv("KTA_MANDATE_THEMES", "AI infrastructure,power and energy,data-center systems"),
            mandate_regions=csv("KTA_MANDATE_REGIONS", "US"),
            mandate_asset_classes=[item.lower() for item in csv("KTA_MANDATE_ASSET_CLASSES", "equity,etf")],
            mandate_excluded_industries=csv("KTA_MANDATE_EXCLUDED_INDUSTRIES"),
            mandate_time_horizon=get("KTA_MANDATE_TIME_HORIZON", "weeks_to_months") or "weeks_to_months",
            discovery_enabled=_bool(get("KTA_DISCOVERY_ENABLED", "false") or "false"),
            discovery_interval_hours=int(get("KTA_DISCOVERY_INTERVAL_HOURS", "6") or "6"),
            discovery_on_start=_bool(get("KTA_DISCOVERY_ON_START", "true") or "true"),
            scan_after_discovery=_bool(get("KTA_SCAN_AFTER_DISCOVERY", "true") or "true"),
            discovery_max_candidates=int(get("KTA_DISCOVERY_MAX_CANDIDATES", "250") or "250"),
            discovery_min_price=decimal(get("KTA_DISCOVERY_MIN_PRICE", "2")),
            discovery_min_daily_dollar_volume=decimal(
                get("KTA_DISCOVERY_MIN_DAILY_DOLLAR_VOLUME", "2000000")
            ),
            initial_simulated_cash=decimal(get("KTA_INITIAL_SIMULATED_CASH", "3000")),
            alpaca_api_key=get("ALPACA_API_KEY"),
            alpaca_api_secret=get("ALPACA_API_SECRET"),
            alpaca_trading_url=get("ALPACA_TRADING_URL", "https://paper-api.alpaca.markets"),
            alpaca_data_url=get("ALPACA_DATA_URL", "https://data.alpaca.markets"),
            alpaca_data_feed=get("ALPACA_DATA_FEED", "iex"),
            openrouter_api_key=get("OPENROUTER_API_KEY"),
            scout_model=get("KTA_SCOUT_MODEL"),
            critic_model=get("KTA_CRITIC_MODEL"),
            reviewer_model=get("KTA_REVIEWER_MODEL"),
            supervisor_model=get("KTA_SUPERVISOR_MODEL") or get("KTA_REVIEWER_MODEL"),
            discovery_model=get("KTA_DISCOVERY_MODEL") or get("KTA_SCOUT_MODEL"),
            researcher_model=get("KTA_RESEARCHER_MODEL") or get("KTA_SCOUT_MODEL"),
            thesis_model=get("KTA_THESIS_MODEL") or get("KTA_SCOUT_MODEL"),
            trade_model=get("KTA_TRADE_MODEL") or get("KTA_SCOUT_MODEL"),
            openrouter_reasoning_effort=get("KTA_OPENROUTER_REASONING_EFFORT", "none").lower(),
            perplexity_api_key=get("PERPLEXITY_API_KEY"),
            perplexity_model=get("KTA_PERPLEXITY_MODEL", "sonar"),
            decision_cooldown_hours=int(get("KTA_DECISION_COOLDOWN_HOURS", "168")),
            review_min_interval_hours=int(get("KTA_REVIEW_MIN_INTERVAL_HOURS", "24")),
            monthly_api_budget_usd=decimal(get("KTA_MONTHLY_API_BUDGET_USD", "1000.00")),
            monthly_api_soft_budget_usd=decimal(get("KTA_MONTHLY_API_SOFT_BUDGET_USD", "100.00")),
            enforce_monthly_api_budget=_bool(get("KTA_ENFORCE_MONTHLY_API_BUDGET", "false") or "false"),
            estimated_llm_input_usd_per_million=decimal(
                get("KTA_ESTIMATED_LLM_INPUT_USD_PER_MILLION", "0.30")
            ),
            estimated_llm_output_usd_per_million=decimal(
                get("KTA_ESTIMATED_LLM_OUTPUT_USD_PER_MILLION", "2.50")
            ),
            openrouter_credit_fee_pct=decimal(get("KTA_OPENROUTER_CREDIT_FEE_PCT", "0.055")),
            expected_signal_cycles_per_month=int(get("KTA_EXPECTED_SIGNAL_CYCLES_PER_MONTH", "4")),
            expected_reviews_per_month=int(get("KTA_EXPECTED_REVIEWS_PER_MONTH", "2")),
            max_position_pct=decimal(get("KTA_MAX_POSITION_PCT", "0.40")),
            max_gross_exposure_pct=decimal(get("KTA_MAX_GROSS_EXPOSURE_PCT", "1.00")),
            max_new_exposure_per_run_pct=decimal(get("KTA_MAX_NEW_EXPOSURE_PER_RUN_PCT", "0.60")),
            max_orders_per_run=int(get("KTA_MAX_ORDERS_PER_RUN", "5")),
            max_data_age_days=int(get("KTA_MAX_DATA_AGE_DAYS", "5")),
            minimum_confidence=decimal(get("KTA_MINIMUM_CONFIDENCE", "0.55")),
            minimum_order_notional=decimal(get("KTA_MINIMUM_ORDER_NOTIONAL", "25")),
            daily_loss_kill_pct=decimal(get("KTA_DAILY_LOSS_KILL_PCT", "0.20")),
            risk_per_trade_pct=decimal(get("KTA_RISK_PER_TRADE_PCT", "0.01")),
            options_enabled=_bool(get("KTA_OPTIONS_ENABLED", "false")),
            shorting_enabled=_bool(get("KTA_SHORTING_ENABLED", "false")),
            daemon_host=get("KTA_DAEMON_HOST", "127.0.0.1"),
            daemon_port=int(get("KTA_DAEMON_PORT", "8787")),
            control_token=get("KTA_CONTROL_TOKEN"),
            daemon_workers=int(get("KTA_DAEMON_WORKERS", "1")),
            job_poll_seconds=int(get("KTA_JOB_POLL_SECONDS", "2")),
            job_lease_seconds=int(get("KTA_JOB_LEASE_SECONDS", "14400")),
            trigger_poll_seconds=int(get("KTA_TRIGGER_POLL_SECONDS", "30")),
            daily_scan_hour_et=int(get("KTA_DAILY_SCAN_HOUR_ET", "16")),
            daily_scan_minute_et=int(get("KTA_DAILY_SCAN_MINUTE_ET", "30")),
            scan_on_start=_bool(get("KTA_SCAN_ON_START", "false")),
            agentic_research_enabled=_bool(get("KTA_AGENTIC_RESEARCH_ENABLED", "false")),
            agent_max_turns=int(get("KTA_AGENT_MAX_TURNS", "64")),
            agent_max_research_calls=int(get("KTA_AGENT_MAX_RESEARCH_CALLS", "32")),
            agent_max_tokens_per_turn=int(get("KTA_AGENT_MAX_TOKENS_PER_TURN", "8000")),
            suggestion_review_ttl_minutes=int(get("KTA_SUGGESTION_REVIEW_TTL_MINUTES", "15")),
        )

    def validate(self) -> List[str]:
        errors: List[str] = []
        if self.broker_mode not in {"simulation", "paper"}:
            errors.append("KTA_BROKER_MODE must be simulation or paper; live trading is not implemented")
        if self.market_data_mode not in {"synthetic", "alpaca"}:
            errors.append("KTA_MARKET_DATA_MODE must be synthetic or alpaca")
        if self.agent_mode not in {"heuristic", "openrouter"}:
            errors.append("KTA_AGENT_MODE must be heuristic or openrouter")
        if self.research_mode not in {"none", "perplexity"}:
            errors.append("KTA_RESEARCH_MODE must be none or perplexity")
        if self.broker_mode == "paper" and not (self.alpaca_api_key and self.alpaca_api_secret):
            errors.append("Alpaca credentials are required for paper mode")
        if self.broker_mode == "paper" and self.market_data_mode != "alpaca":
            errors.append("Paper execution requires Alpaca market data; synthetic data cannot reach a broker")
        if self.broker_mode == "paper" and "paper-api" not in self.alpaca_trading_url:
            errors.append("Paper execution requires the Alpaca paper-api endpoint")
        if self.market_data_mode == "alpaca" and not (self.alpaca_api_key and self.alpaca_api_secret):
            errors.append("Alpaca credentials are required for Alpaca market data")
        if self.agent_mode == "openrouter" and not self.openrouter_api_key:
            errors.append("OPENROUTER_API_KEY is required for OpenRouter mode")
        if self.agent_mode == "openrouter" and not all(
            [self.trade_model, self.critic_model, self.reviewer_model]
        ):
            errors.append("Scout, critic, and reviewer model names are required for OpenRouter mode")
        if self.openrouter_reasoning_effort not in {
            "none",
            "minimal",
            "low",
            "medium",
            "high",
            "xhigh",
            "max",
        }:
            errors.append(
                "KTA_OPENROUTER_REASONING_EFFORT must be none, minimal, low, medium, high, xhigh, or max"
            )
        if self.research_mode == "perplexity" and not self.perplexity_api_key:
            errors.append("PERPLEXITY_API_KEY is required for Perplexity research")
        if self.options_enabled:
            errors.append("Options execution is modeled but not implemented in this release")
        if self.shorting_enabled:
            errors.append("Short execution is not implemented in this release")
        if self.daemon_host not in {"127.0.0.1", "localhost", "::1"} and not self.control_token:
            errors.append("KTA_CONTROL_TOKEN is required when the daemon listens beyond loopback")
        if self.daemon_port < 1 or self.daemon_port > 65535:
            errors.append("KTA_DAEMON_PORT must be between 1 and 65535")
        if self.daemon_workers < 1 or self.daemon_workers > 8:
            errors.append("KTA_DAEMON_WORKERS must be between 1 and 8")
        if self.job_poll_seconds < 1 or self.job_lease_seconds < 30 or self.trigger_poll_seconds < 5:
            errors.append("Daemon poll intervals or job lease are below their safe minimum")
        if not 0 <= self.daily_scan_hour_et <= 23 or not 0 <= self.daily_scan_minute_et <= 59:
            errors.append("Daily scan hour/minute must describe a valid Eastern time")
        if self.agent_max_turns < 1 or self.agent_max_turns > 512:
            errors.append("KTA_AGENT_MAX_TURNS must be between 1 and 512")
        if self.agent_max_research_calls < 0 or self.agent_max_research_calls > 256:
            errors.append("KTA_AGENT_MAX_RESEARCH_CALLS must be between 0 and 256")
        if self.agent_max_tokens_per_turn < 256 or self.agent_max_tokens_per_turn > 128000:
            errors.append("KTA_AGENT_MAX_TOKENS_PER_TURN must be between 256 and 128000")
        if self.suggestion_review_ttl_minutes < 1 or self.suggestion_review_ttl_minutes > 60:
            errors.append("KTA_SUGGESTION_REVIEW_TTL_MINUTES must be between 1 and 60")
        if self.agentic_research_enabled and self.agent_mode != "openrouter":
            errors.append("Agentic research requires KTA_AGENT_MODE=openrouter")
        if self.agentic_research_enabled and self.research_mode != "perplexity":
            errors.append("Agentic research requires KTA_RESEARCH_MODE=perplexity")
        if self.review_min_interval_hours < 0:
            errors.append("KTA_REVIEW_MIN_INTERVAL_HOURS cannot be negative")
        if self.decision_cooldown_hours < 1:
            errors.append("KTA_DECISION_COOLDOWN_HOURS must be at least one")
        if self.monthly_api_budget_usd <= 0 or self.monthly_api_soft_budget_usd <= 0:
            errors.append("KTA_MONTHLY_API_BUDGET_USD must be greater than zero")
        if self.discovery_enabled and self.research_mode != "perplexity":
            errors.append("Continuous discovery requires KTA_RESEARCH_MODE=perplexity")
        if self.discovery_enabled and (self.agent_mode != "openrouter" or not self.discovery_model):
            errors.append("Continuous discovery requires OpenRouter mode and a discovery model")
        if self.discovery_interval_hours < 1:
            errors.append("KTA_DISCOVERY_INTERVAL_HOURS must be at least one")
        if self.discovery_min_price <= 0 or self.discovery_min_daily_dollar_volume <= 0:
            errors.append("Discovery price and daily dollar-volume minimums must be positive")
        if self.estimated_llm_input_usd_per_million < 0 or self.estimated_llm_output_usd_per_million < 0:
            errors.append("Estimated LLM token prices cannot be negative")
        for name, value in {
            "max_position_pct": self.max_position_pct,
            "max_gross_exposure_pct": self.max_gross_exposure_pct,
            "max_new_exposure_per_run_pct": self.max_new_exposure_per_run_pct,
            "daily_loss_kill_pct": self.daily_loss_kill_pct,
            "risk_per_trade_pct": self.risk_per_trade_pct,
        }.items():
            if value <= 0 or value > 1:
                errors.append("{} must be greater than 0 and at most 1".format(name))
        if not self.universe and not self.discovery_enabled:
            errors.append("KTA_UNIVERSE must contain at least one symbol")
        return errors

    def safe_dict(self) -> Dict[str, object]:
        return {
            "environment": self.environment,
            "broker_mode": self.broker_mode,
            "market_data_mode": self.market_data_mode,
            "agent_mode": self.agent_mode,
            "research_mode": self.research_mode,
            "database_path": str(self.database_path),
            "universe": self.universe,
            "mandate": {
                "themes": self.mandate_themes,
                "regions": self.mandate_regions,
                "asset_classes": self.mandate_asset_classes,
                "excluded_industries": self.mandate_excluded_industries,
                "time_horizon": self.mandate_time_horizon,
            },
            "discovery_enabled": self.discovery_enabled,
            "discovery_interval_hours": self.discovery_interval_hours,
            "scan_after_discovery": self.scan_after_discovery,
            "discovery_min_price": str(self.discovery_min_price),
            "discovery_min_daily_dollar_volume": str(self.discovery_min_daily_dollar_volume),
            "model_roles": {
                "supervisor": self.supervisor_model,
                "discovery": self.discovery_model,
                "researcher": self.researcher_model,
                "thesis": self.thesis_model,
                "trade": self.trade_model,
                "critic": self.critic_model,
                "reviewer": self.reviewer_model,
            },
            "initial_simulated_cash": str(self.initial_simulated_cash),
            "alpaca_credentials_present": bool(self.alpaca_api_key and self.alpaca_api_secret),
            "openrouter_key_present": bool(self.openrouter_api_key),
            "openrouter_reasoning_effort": self.openrouter_reasoning_effort,
            "perplexity_key_present": bool(self.perplexity_api_key),
            "max_position_pct": str(self.max_position_pct),
            "max_gross_exposure_pct": str(self.max_gross_exposure_pct),
            "max_new_exposure_per_run_pct": str(self.max_new_exposure_per_run_pct),
            "max_orders_per_run": self.max_orders_per_run,
            "risk_per_trade_pct": str(self.risk_per_trade_pct),
            "review_min_interval_hours": self.review_min_interval_hours,
            "decision_cooldown_hours": self.decision_cooldown_hours,
            "monthly_api_budget_usd": str(self.monthly_api_budget_usd),
            "monthly_api_soft_budget_usd": str(self.monthly_api_soft_budget_usd),
            "enforce_monthly_api_budget": self.enforce_monthly_api_budget,
            "options_enabled": self.options_enabled,
            "shorting_enabled": self.shorting_enabled,
            "daemon_host": self.daemon_host,
            "daemon_port": self.daemon_port,
            "daemon_workers": self.daemon_workers,
            "control_token_present": bool(self.control_token),
            "daily_scan_time_et": "{:02d}:{:02d}".format(
                self.daily_scan_hour_et, self.daily_scan_minute_et
            ),
            "scan_on_start": self.scan_on_start,
            "agentic_research_enabled": self.agentic_research_enabled,
            "agent_max_turns": self.agent_max_turns,
            "agent_max_research_calls": self.agent_max_research_calls,
            "agent_max_tokens_per_turn": self.agent_max_tokens_per_turn,
            "suggestion_review_ttl_minutes": self.suggestion_review_ttl_minutes,
        }
