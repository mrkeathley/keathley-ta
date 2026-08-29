import json
import unittest
from unittest.mock import patch

from kta.discovery import DiscoveryEngine


class FakeResearch:
    def query(self, question, max_tokens=1200):
        return {
            "answer": "New Corp (NEW) supplies cooling systems. Bad symbol is unsupported.",
            "citations": ["https://example.test/new"],
        }

    def drain_usage(self):
        return []


class DiscoveryTests(unittest.TestCase):
    @patch("kta.discovery.request_json")
    def test_discovery_expands_beyond_seeds_and_validates_symbols(self, request):
        discovery_response = {
            "choices": [
                {
                    "finish_reason": "stop",
                    "message": {
                        "content": json.dumps(
                            {
                                "candidates": [
                                    {
                                        "symbol": "NEW",
                                        "name": "New Corp",
                                        "theme": "cooling",
                                        "rationale": "Supplier",
                                        "confidence": 0.8,
                                    },
                                    {
                                        "symbol": "NOT VALID!",
                                        "name": "Bad",
                                        "theme": "bad",
                                        "rationale": "bad",
                                        "confidence": 1,
                                    },
                                ]
                            }
                        )
                    },
                }
            ],
            "usage": {"prompt_tokens": 10, "completion_tokens": 5, "cost": "0.001"},
        }
        supervisor_response = {
            "choices": [
                {
                    "finish_reason": "stop",
                    "message": {
                        "content": json.dumps(
                            {
                                "reviews": [
                                    {"symbol": "NEW", "approved": True, "reason": "supported", "priority": 80},
                                    {"symbol": "SEED", "approved": True, "reason": "seed", "priority": 0},
                                ]
                            }
                        )
                    },
                }
            ],
            "usage": {"prompt_tokens": 8, "completion_tokens": 4, "cost": "0.002"},
        }
        request.side_effect = [discovery_response, supervisor_response]
        engine = DiscoveryEngine(
            openrouter_api_key="key",
            model="discovery/model",
            supervisor_model="supervisor/model",
            research=FakeResearch(),
            max_candidates=10,
        )

        result = engine.discover({"themes": ["cooling"]}, ["SEED"])

        self.assertEqual({item["symbol"] for item in result.candidates}, {"NEW", "SEED"})
        self.assertEqual(result.citations, ["https://example.test/new"])
        self.assertEqual([item.operation for item in result.usage], ["universe_discovery", "discovery_supervisor"])


if __name__ == "__main__":
    unittest.main()
