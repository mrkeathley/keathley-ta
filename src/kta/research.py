"""Research adapters. Research is evidence, never an order instruction."""

from abc import ABC, abstractmethod
from decimal import Decimal
from hashlib import sha256
from typing import List

from .domain import ApiUsage, Evidence, Signal, SignalAction, decimal, utc_now
from .http import request_json


class ResearchProvider(ABC):
    def drain_usage(self) -> List[ApiUsage]:
        return []

    @abstractmethod
    def gather(self, signals: List[Signal]) -> List[Evidence]:
        raise NotImplementedError


class NoResearch(ResearchProvider):
    def gather(self, signals: List[Signal]) -> List[Evidence]:
        return []


class PerplexityResearch(ResearchProvider):
    def __init__(self, api_key: str, model: str = "sonar"):
        self.api_key = api_key
        self.model = model
        self._usage: List[ApiUsage] = []

    def drain_usage(self) -> List[ApiUsage]:
        usage = list(self._usage)
        self._usage.clear()
        return usage

    def _usage_record(self, payload) -> ApiUsage:
        raw_usage = payload.get("usage") or {}
        input_tokens = int(raw_usage.get("prompt_tokens", 0) or 0)
        output_tokens = int(raw_usage.get("completion_tokens", 0) or 0)
        raw_cost = raw_usage.get("cost")
        try:
            cost = decimal(raw_cost) if raw_cost is not None else None
        except Exception:
            cost = None
        estimated = False
        # Published rates as of 2026-08-27 for low-context Sonar requests.
        rates = {
            "sonar": (Decimal("1"), Decimal("1"), Decimal("0.005")),
            "sonar-pro": (Decimal("3"), Decimal("15"), Decimal("0.006")),
            "sonar-reasoning-pro": (Decimal("2"), Decimal("8"), Decimal("0.006")),
        }
        if cost is None and self.model in rates:
            input_rate, output_rate, request_fee = rates[self.model]
            cost = (
                (Decimal(input_tokens) * input_rate / Decimal("1000000"))
                + (Decimal(output_tokens) * output_rate / Decimal("1000000"))
                + request_fee
            )
            estimated = True
        return ApiUsage(
            service="perplexity",
            operation="research_batch",
            model=self.model,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            request_count=1,
            cost_usd=cost,
            estimated=estimated,
            metadata={"search_context_size": "low", "pricing_date": "2026-08-27"},
        )

    def gather(self, signals: List[Signal]) -> List[Evidence]:
        entry_signals = [signal for signal in signals if signal.action == SignalAction.ENTER]
        if not entry_signals:
            return []
        symbols = list(dict.fromkeys(signal.symbol for signal in entry_signals))
        as_of = max(signal.as_of for signal in entry_signals).date().isoformat()
        prompt = (
            "Research this candidate batch as of {date}: {symbols}. Give a clearly labeled section per symbol. "
            "Focus on AI infrastructure/upstream or downstream exposure, material company catalysts and risks, "
            "unusual public attention, and recent officially disclosed US congressional transactions. "
            "Distinguish transaction date from disclosure date and state the amount range; disclosures are delayed. "
            "Use primary sources where possible. Do not give buy/sell recommendations. Every factual claim needs a URL."
        ).format(date=as_of, symbols=", ".join(symbols))
        payload = request_json(
            "POST",
            "https://api.perplexity.ai/chat/completions",
            headers={"Authorization": "Bearer {}".format(self.api_key)},
            body={
                "model": self.model,
                "messages": [
                    {"role": "system", "content": "You are a point-in-time financial research collector."},
                    {"role": "user", "content": prompt},
                ],
                "temperature": 0.1,
                "max_tokens": 1500,
                "web_search_options": {"search_context_size": "low"},
            },
            timeout=60,
        )
        self._usage.append(self._usage_record(payload))
        choices = payload.get("choices") or []
        summary = choices[0].get("message", {}).get("content", "") if choices else ""
        citations = [str(item) for item in (payload.get("citations") or [])]
        retrieved = utc_now()
        identity = "{}|{}|{}".format(",".join(symbols), "|".join(citations), retrieved.date().isoformat())
        return [
            Evidence(
                evidence_id=sha256(identity.encode("utf-8")).hexdigest()[:24],
                symbol="*",
                source="perplexity",
                title="Batched research packet for {}".format(", ".join(symbols)),
                summary=summary,
                url=citations[0] if citations else "",
                published_at=None,
                retrieved_at=retrieved,
                metadata={"model": self.model, "symbols": symbols, "citations": citations},
            )
        ]
