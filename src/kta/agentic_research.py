"""Bounded multi-turn research provider for signal-driven daemon runs."""

from hashlib import sha256
from typing import Any, Dict, List

from .domain import ApiUsage, Evidence, Signal, SignalAction, decimal, utc_now
from .journal import Journal
from .market import MarketDataProvider
from .research import PerplexityResearch, ResearchProvider
from .tool_agent import AgentTool, OpenRouterToolAgent


class AgenticResearchProvider(ResearchProvider):
    def __init__(
        self,
        director: OpenRouterToolAgent,
        search: PerplexityResearch,
        market: MarketDataProvider,
        journal: Journal,
        max_research_calls: int = 4,
    ):
        self.director = director
        self.search = search
        self.market = market
        self.journal = journal
        self.max_research_calls = max_research_calls
        self._usage: List[ApiUsage] = []

    def drain_usage(self) -> List[ApiUsage]:
        usage = list(self._usage)
        self._usage.clear()
        usage.extend(self.director.drain_usage())
        usage.extend(self.search.drain_usage())
        return usage

    def gather(self, signals: List[Signal]) -> List[Evidence]:
        entries = [signal for signal in signals if signal.action == SignalAction.ENTER]
        if not entries:
            return []
        allowed = {signal.symbol for signal in entries}
        research_calls = 0
        citations: List[str] = []

        def research_web(arguments: Dict[str, Any]) -> Dict[str, Any]:
            nonlocal research_calls
            if research_calls >= self.max_research_calls:
                return {"error": "Research-call budget exhausted"}
            question = str(arguments.get("question") or "").strip()
            if not question:
                raise ValueError("question is required")
            research_calls += 1
            result = self.search.query(question)
            citations.extend(result["citations"])
            return result

        def market_context(arguments: Dict[str, Any]) -> Dict[str, Any]:
            symbol = str(arguments.get("symbol") or "").strip().upper()
            if symbol not in allowed:
                raise ValueError("symbol is outside the deterministic candidate batch")
            signal = next(item for item in entries if item.symbol == symbol)
            return {
                "symbol": symbol,
                "latest_price": self.market.latest_price(symbol),
                "signal": signal,
            }

        def create_trigger(arguments: Dict[str, Any]) -> Dict[str, Any]:
            symbol = str(arguments.get("symbol") or "").strip().upper()
            comparison = str(arguments.get("comparison") or "")
            threshold = decimal(arguments.get("threshold"))
            rationale = str(arguments.get("rationale") or "")[:1000]
            if symbol not in allowed:
                raise ValueError("symbol is outside the deterministic candidate batch")
            if comparison not in {"above", "below"} or threshold <= 0:
                raise ValueError("comparison must be above/below and threshold must be positive")
            trigger_id = self.journal.create_price_trigger(
                symbol,
                comparison,
                threshold,
                source="agent",
                one_shot=True,
                metadata={"rationale": rationale, "created_by": self.director.model},
                deduplicate_active=True,
            )
            self.journal.service_event(
                "agent_trigger_created",
                "Research agent created a validated price trigger",
                {"trigger_id": trigger_id, "symbol": symbol, "comparison": comparison, "threshold": threshold},
            )
            return {"trigger_id": trigger_id, "status": "active"}

        tools = [
            AgentTool(
                "research_web",
                "Research one focused, current financial question using cited web sources.",
                {
                    "type": "object",
                    "additionalProperties": False,
                    "properties": {"question": {"type": "string"}},
                    "required": ["question"],
                },
                research_web,
            ),
            AgentTool(
                "get_market_context",
                "Read the current deterministic signal and latest market price for an allowed candidate.",
                {
                    "type": "object",
                    "additionalProperties": False,
                    "properties": {"symbol": {"type": "string"}},
                    "required": ["symbol"],
                },
                market_context,
            ),
            AgentTool(
                "create_price_trigger",
                "Create a one-shot, validated above/below price trigger for an allowed candidate.",
                {
                    "type": "object",
                    "additionalProperties": False,
                    "properties": {
                        "symbol": {"type": "string"},
                        "comparison": {"type": "string", "enum": ["above", "below"]},
                        "threshold": {"type": "number", "exclusiveMinimum": 0},
                        "rationale": {"type": "string"},
                    },
                    "required": ["symbol", "comparison", "threshold", "rationale"],
                },
                create_trigger,
            ),
        ]
        result = self.director.run(
            (
                "You direct point-in-time research for an experimental high-risk portfolio. Use the tools "
                "iteratively: investigate material catalysts and risks, AI supply-chain relevance, attention, "
                "and official congressional disclosures with transaction and disclosure dates separated. "
                "Make at least two focused research_web calls unless the call budget is smaller. Do not invent "
                "symbols or citations. You may create a price trigger only when its threshold has a concrete, "
                "stated monitoring rationale. Finish with a concise evidence memo, not a buy/sell instruction."
            ),
            "Research this deterministic candidate batch: {}".format(
                ", ".join(sorted(allowed))
            ),
            tools,
            operation="research_director",
        )
        self._usage.extend(self.director.drain_usage())
        self._usage.extend(self.search.drain_usage())
        if research_calls == 0:
            raise RuntimeError("Research director returned without using the research tool")
        retrieved = utc_now()
        unique_citations = list(dict.fromkeys(citations))
        return [
            Evidence(
                evidence_id=sha256(
                    "{}|{}|{}".format(
                        symbol, "|".join(unique_citations), retrieved.isoformat()
                    ).encode("utf-8")
                ).hexdigest()[:24],
                symbol=symbol,
                source="agentic-research",
                title="Multi-turn research packet containing {}".format(symbol),
                summary=result.content,
                url=unique_citations[0] if unique_citations else "",
                published_at=None,
                retrieved_at=retrieved,
                metadata={
                    "model": self.director.model,
                    "turns": result.turns,
                    "research_calls": research_calls,
                    "symbol": symbol,
                    "batch_symbols": sorted(allowed),
                    "citations": unique_citations,
                    "tools": [item["tool"] for item in result.tool_calls],
                },
            )
            for symbol in sorted(allowed)
        ]
