import unittest
from datetime import datetime, timezone
from decimal import Decimal
from unittest.mock import patch

from kta.agents import OpenRouterAgentSuite
from kta.http import ServiceError
from kta.agents import HeuristicAgentSuite
from kta.domain import AssetClass, Side, TradeIntent


class OpenRouterAgentSuiteTests(unittest.TestCase):
    def setUp(self):
        self.agents = OpenRouterAgentSuite(
            "test-key",
            "provider/scout",
            "provider/critic",
            "provider/reviewer",
            reasoning_effort="minimal",
        )
        self.schema = {
            "type": "object",
            "additionalProperties": False,
            "properties": {"ok": {"type": "boolean"}},
            "required": ["ok"],
        }

    @patch("kta.openrouter.request_json")
    def test_empty_length_response_becomes_actionable_service_error(self, request):
        request.return_value = {
            "id": "generation-id",
            "usage": {"prompt_tokens": 553, "completion_tokens": 1200, "cost": "0.00013659"},
            "choices": [
                {
                    "finish_reason": "length",
                    "message": {"content": "", "reasoning": "private model reasoning"},
                }
            ],
        }

        with self.assertRaises(ServiceError) as raised:
            self.agents._complete(
                "scout", "provider/scout", "system", {"input": True}, self.schema, 1200
            )

        message = str(raised.exception)
        self.assertIn("provider/scout", message)
        self.assertIn("exhausted its 1200-token completion budget", message)
        self.assertIn("output_tokens=1200", message)
        self.assertIn("reasoning_effort=minimal", message)
        self.assertNotIn("private model reasoning", message)
        self.assertEqual(self.agents.drain_usage()[0].cost_usd.__str__(), "0.00013659")

    @patch("kta.openrouter.request_json")
    def test_structured_response_requests_bounded_reasoning(self, request):
        request.return_value = {
            "usage": {"prompt_tokens": 10, "completion_tokens": 8},
            "choices": [{"finish_reason": "stop", "message": {"content": '{"ok": true}'}}],
        }

        result = self.agents._complete(
            "scout", "provider/scout", "system", {"input": True}, self.schema, 3000
        )

        self.assertEqual(result, {"ok": True})
        body = request.call_args.kwargs["body"]
        self.assertEqual(body["reasoning"], {"effort": "minimal", "exclude": True})
        self.assertEqual(body["max_tokens"], 3000)
        self.assertEqual(body["response_format"]["type"], "json_schema")

    @patch("kta.openrouter.request_json")
    def test_mandatory_reasoning_retries_with_room_for_structured_output(self, request):
        self.agents.reasoning_effort = "none"
        request.side_effect = [
            ServiceError(
                "POST endpoint returned 400: Reasoning is mandatory for this endpoint and cannot be disabled."
            ),
            {
                "usage": {"prompt_tokens": 10, "completion_tokens": 8},
                "choices": [{"finish_reason": "stop", "message": {"content": '{"ok": true}'}}],
            },
        ]

        result = self.agents._complete(
            "critic", "provider/mandatory", "system", {"input": True}, self.schema, 1200
        )

        self.assertEqual(result, {"ok": True})
        self.assertEqual(request.call_count, 2)
        retry_body = request.call_args_list[1].kwargs["body"]
        self.assertEqual(retry_body["reasoning"], {"effort": "minimal", "exclude": True})
        self.assertEqual(retry_body["max_tokens"], 4000)
        usage = self.agents.drain_usage()[0]
        self.assertEqual(usage.metadata["reasoning_effort"], "minimal")
        self.assertTrue(usage.metadata["reasoning_fallback"])


class HeuristicCriticTests(unittest.TestCase):
    def test_low_confidence_entry_is_vetoed(self):
        intent = TradeIntent.create(
            symbol="TEST",
            asset_class=AssetClass.EQUITY,
            side=Side.BUY,
            signal_as_of=datetime(2026, 8, 28, tzinfo=timezone.utc),
            strategy_version="test-v1",
            thesis="weak fixture",
            confidence=Decimal("0.54"),
            requested_notional=Decimal("100"),
            generated_by="fixture",
        )

        decision = HeuristicAgentSuite().criticize([intent], [], [])[0]

        self.assertFalse(decision.approved)


if __name__ == "__main__":
    unittest.main()
