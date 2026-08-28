import unittest
from unittest.mock import patch

from kta.agents import OpenRouterAgentSuite
from kta.http import ServiceError


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

    @patch("kta.agents.request_json")
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

    @patch("kta.agents.request_json")
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


if __name__ == "__main__":
    unittest.main()
