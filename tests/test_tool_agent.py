import unittest
from unittest.mock import patch

from kta.tool_agent import AgentTool, OpenRouterToolAgent


class OpenRouterToolAgentTests(unittest.TestCase):
    @patch("kta.openrouter.request_json")
    def test_executes_allowlisted_tool_and_continues_to_final_answer(self, request):
        request.side_effect = [
            {
                "id": "first",
                "usage": {"prompt_tokens": 10, "completion_tokens": 5, "cost": "0.001"},
                "choices": [
                    {
                        "finish_reason": "tool_calls",
                        "message": {
                            "content": "",
                            "tool_calls": [
                                {
                                    "id": "call-one",
                                    "type": "function",
                                    "function": {"name": "lookup", "arguments": '{"symbol":"TEST1"}'},
                                }
                            ],
                        },
                    }
                ],
            },
            {
                "id": "second",
                "usage": {"prompt_tokens": 20, "completion_tokens": 8, "cost": "0.002"},
                "choices": [
                    {"finish_reason": "stop", "message": {"content": "Research complete."}}
                ],
            },
        ]
        seen = []
        tool = AgentTool(
            "lookup",
            "Look up a configured symbol.",
            {
                "type": "object",
                "properties": {"symbol": {"type": "string"}},
                "required": ["symbol"],
            },
            lambda arguments: seen.append(arguments) or {"price": "100"},
        )
        agent = OpenRouterToolAgent("key", "model", max_turns=3)

        result = agent.run("system", "research", [tool])

        self.assertEqual(result.content, "Research complete.")
        self.assertEqual(result.turns, 2)
        self.assertEqual(seen, [{"symbol": "TEST1"}])
        self.assertEqual(len(agent.drain_usage()), 2)
        second_messages = request.call_args_list[1].kwargs["body"]["messages"]
        self.assertEqual(second_messages[-1]["role"], "tool")
        self.assertEqual(second_messages[-1]["tool_call_id"], "call-one")


if __name__ == "__main__":
    unittest.main()
