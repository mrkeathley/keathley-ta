"""Bounded OpenRouter tool loop used by daemon research and chat jobs."""

import json
from dataclasses import dataclass
from decimal import Decimal
from typing import Any, Callable, Dict, List, Optional

from .domain import ApiUsage, decimal, jsonable
from .http import ServiceError
from .openrouter import chat_completion


@dataclass(frozen=True)
class AgentTool:
    name: str
    description: str
    parameters: Dict[str, Any]
    handler: Callable[[Dict[str, Any]], Any]

    def api_definition(self) -> Dict[str, Any]:
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": self.parameters,
            },
        }


@dataclass(frozen=True)
class ToolLoopResult:
    content: str
    turns: int
    tool_calls: List[Dict[str, Any]]


class OpenRouterToolAgent:
    def __init__(
        self,
        api_key: str,
        model: str,
        *,
        reasoning_effort: str = "none",
        max_turns: int = 6,
        max_tokens_per_turn: int = 1200,
    ):
        self.api_key = api_key
        self.model = model
        self.reasoning_effort = reasoning_effort
        self.max_turns = max_turns
        self.max_tokens_per_turn = max_tokens_per_turn
        self._usage: List[ApiUsage] = []

    def drain_usage(self) -> List[ApiUsage]:
        usage = list(self._usage)
        self._usage.clear()
        return usage

    def _record_usage(
        self,
        response: Dict[str, Any],
        operation: str,
        turn: int,
        effective_reasoning_effort: str,
        reasoning_fallback: bool,
    ) -> None:
        raw = response.get("usage") or {}
        raw_cost = raw.get("cost")
        try:
            cost: Optional[Decimal] = decimal(raw_cost) if raw_cost is not None else None
        except Exception:
            cost = None
        details = raw.get("completion_tokens_details") or {}
        choices = response.get("choices") or []
        self._usage.append(
            ApiUsage(
                service="openrouter",
                operation=operation,
                model=self.model,
                input_tokens=int(raw.get("prompt_tokens", raw.get("input_tokens", 0)) or 0),
                output_tokens=int(raw.get("completion_tokens", raw.get("output_tokens", 0)) or 0),
                request_count=1,
                cost_usd=cost,
                estimated=False,
                metadata={
                    "response_id": response.get("id"),
                    "turn": turn,
                    "finish_reason": choices[0].get("finish_reason") if choices else None,
                    "reasoning_tokens": details.get("reasoning_tokens"),
                    "reasoning_effort": effective_reasoning_effort,
                    "configured_reasoning_effort": self.reasoning_effort,
                    "reasoning_fallback": reasoning_fallback,
                },
            )
        )

    def run(
        self,
        system: str,
        user: str,
        tools: List[AgentTool],
        *,
        history: Optional[List[Dict[str, Any]]] = None,
        operation: str = "tool_loop",
    ) -> ToolLoopResult:
        tool_map = {tool.name: tool for tool in tools}
        messages: List[Dict[str, Any]] = [{"role": "system", "content": system}]
        messages.extend(history or [])
        messages.append({"role": "user", "content": user})
        executed: List[Dict[str, Any]] = []
        for turn in range(1, self.max_turns + 1):
            response, effective_effort, reasoning_fallback = chat_completion(
                self.api_key,
                {
                    "model": self.model,
                    "messages": [dict(item) for item in messages],
                    "tools": [tool.api_definition() for tool in tools],
                    "tool_choice": "auto",
                    "parallel_tool_calls": False,
                    "temperature": 0.1,
                    "max_tokens": self.max_tokens_per_turn,
                    "reasoning": {"effort": self.reasoning_effort, "exclude": True},
                    "usage": {"include": True},
                },
            )
            self._record_usage(
                response, operation, turn, effective_effort, reasoning_fallback
            )
            choices = response.get("choices") or []
            if not choices:
                raise ServiceError("OpenRouter tool loop returned no choices on turn {}".format(turn))
            message = choices[0].get("message") or {}
            tool_calls = message.get("tool_calls") or []
            assistant_message: Dict[str, Any] = {
                "role": "assistant",
                "content": message.get("content") or "",
            }
            if tool_calls:
                assistant_message["tool_calls"] = tool_calls
            messages.append(assistant_message)
            if not tool_calls:
                content = str(message.get("content") or "").strip()
                if not content:
                    raise ServiceError(
                        "OpenRouter tool loop returned no final content on turn {}".format(turn)
                    )
                return ToolLoopResult(content=content, turns=turn, tool_calls=executed)
            for call in tool_calls:
                function = call.get("function") or {}
                name = str(function.get("name") or "")
                tool = tool_map.get(name)
                arguments: Dict[str, Any] = {}
                if tool is None:
                    result: Any = {"error": "Tool is not allowlisted: {}".format(name)}
                else:
                    try:
                        arguments = json.loads(function.get("arguments") or "{}")
                        if not isinstance(arguments, dict):
                            raise ValueError("Tool arguments must be a JSON object")
                        result = tool.handler(arguments)
                    except Exception as error:
                        result = {"error": "{}: {}".format(type(error).__name__, error)}
                serialized = json.dumps(jsonable(result), sort_keys=True)
                if len(serialized) > 12000:
                    serialized = serialized[:12000] + "...<truncated>"
                executed.append({"turn": turn, "tool": name, "arguments": arguments if tool else {}, "result": result})
                messages.append(
                    {
                        "role": "tool",
                        "tool_call_id": str(call.get("id") or ""),
                        "name": name,
                        "content": serialized,
                    }
                )
        raise ServiceError("Agent tool loop reached its {}-turn limit".format(self.max_turns))
