"""OpenRouter request compatibility helpers."""

from typing import Any, Dict, Tuple

from .http import ServiceError, request_json


MANDATORY_REASONING_ERROR = "Reasoning is mandatory for this endpoint and cannot be disabled"


def chat_completion(
    api_key: str,
    body: Dict[str, Any],
    *,
    timeout: int = 90,
) -> Tuple[Dict[str, Any], str, bool]:
    """Retry a mandatory-reasoning model with minimal hidden reasoning.

    OpenRouter exposes per-model capabilities, but model aliases and routed endpoints can
    change underneath a long-running daemon. Retrying the gateway's explicit capability
    rejection keeps the request correct at the endpoint that was actually selected.
    """

    configured = str((body.get("reasoning") or {}).get("effort") or "default")
    headers = {"Authorization": "Bearer {}".format(api_key)}
    try:
        return (
            request_json(
                "POST",
                "https://openrouter.ai/api/v1/chat/completions",
                headers=headers,
                body=body,
                timeout=timeout,
            ),
            configured,
            False,
        )
    except ServiceError as error:
        if configured != "none" or MANDATORY_REASONING_ERROR not in str(error):
            raise
        retry_body = dict(body)
        retry_body["reasoning"] = {"effort": "minimal", "exclude": True}
        # Anthropic-style reasoning has a 1,024-token minimum. Preserve enough room
        # for the required structured answer after that hidden reasoning budget.
        retry_body["max_tokens"] = max(4000, int(body.get("max_tokens") or 0))
        return (
            request_json(
                "POST",
                "https://openrouter.ai/api/v1/chat/completions",
                headers=headers,
                body=retry_body,
                timeout=timeout,
            ),
            "minimal",
            True,
        )
