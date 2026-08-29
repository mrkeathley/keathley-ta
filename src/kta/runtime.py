"""Dependency construction shared by the one-shot CLI and daemon."""

from dataclasses import dataclass

from .agentic_research import AgenticResearchProvider
from .agents import AgentSuite, HeuristicAgentSuite, OpenRouterAgentSuite
from .brokers import AlpacaPaperBroker, Broker, SimulatedBroker
from .clock import AlpacaMarketClock, MarketClock, WeekdayMarketClock
from .config import Settings
from .journal import Journal
from .loop import TradingLoop
from .market import AlpacaMarketData, MarketDataProvider, SyntheticMarketData
from .research import NoResearch, PerplexityResearch, ResearchProvider
from .risk import RiskEngine, RiskPolicy
from .tool_agent import OpenRouterToolAgent


@dataclass(frozen=True)
class RuntimeComponents:
    journal: Journal
    market: MarketDataProvider
    broker: Broker
    research: ResearchProvider
    agents: AgentSuite
    risk: RiskEngine
    market_clock: MarketClock


def build_components(settings: Settings) -> RuntimeComponents:
    journal = Journal(settings.database_path)
    if settings.market_data_mode == "alpaca":
        market: MarketDataProvider = AlpacaMarketData(
            settings.alpaca_data_url,
            settings.alpaca_api_key or "",
            settings.alpaca_api_secret or "",
            settings.alpaca_data_feed,
        )
    else:
        market = SyntheticMarketData()
    if settings.broker_mode == "paper":
        broker: Broker = AlpacaPaperBroker(
            settings.alpaca_trading_url,
            settings.alpaca_api_key or "",
            settings.alpaca_api_secret or "",
        )
        market_clock: MarketClock = AlpacaMarketClock(
            settings.alpaca_trading_url,
            settings.alpaca_api_key or "",
            settings.alpaca_api_secret or "",
        )
    else:
        broker = SimulatedBroker(settings.initial_simulated_cash)
        market_clock = WeekdayMarketClock()
    if settings.research_mode == "perplexity":
        research: ResearchProvider = PerplexityResearch(
            settings.perplexity_api_key or "", settings.perplexity_model
        )
    else:
        research = NoResearch()
    if settings.agent_mode == "openrouter":
        agents: AgentSuite = OpenRouterAgentSuite(
            settings.openrouter_api_key or "",
            settings.scout_model or "",
            settings.critic_model or "",
            settings.reviewer_model or "",
            settings.openrouter_reasoning_effort,
        )
    else:
        agents = HeuristicAgentSuite()
    if settings.agentic_research_enabled:
        research = AgenticResearchProvider(
            OpenRouterToolAgent(
                settings.openrouter_api_key or "",
                settings.scout_model or "",
                reasoning_effort=settings.openrouter_reasoning_effort,
                max_turns=settings.agent_max_turns,
            ),
            PerplexityResearch(settings.perplexity_api_key or "", settings.perplexity_model),
            market,
            journal,
            max_research_calls=settings.agent_max_research_calls,
        )
    risk = RiskEngine(
        RiskPolicy(
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
    )
    return RuntimeComponents(journal, market, broker, research, agents, risk, market_clock)


def build_loop(
    settings: Settings,
    *,
    progress=None,
    defer_execution: bool = False,
    components: RuntimeComponents = None,
) -> TradingLoop:
    runtime = components or build_components(settings)
    return TradingLoop(
        market_data=runtime.market,
        research=runtime.research,
        agents=runtime.agents,
        risk=runtime.risk,
        broker=runtime.broker,
        journal=runtime.journal,
        mode=settings.broker_mode,
        safe_config=settings.safe_dict(),
        decision_cooldown_hours=settings.decision_cooldown_hours,
        review_min_interval_hours=settings.review_min_interval_hours,
        monthly_api_budget_usd=settings.monthly_api_budget_usd,
        paid_api_enabled=(settings.agent_mode == "openrouter" or settings.research_mode == "perplexity"),
        defer_execution=defer_execution,
        progress=progress,
    )
