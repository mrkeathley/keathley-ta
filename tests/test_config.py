import os
import unittest
import tempfile
from pathlib import Path
from unittest.mock import patch

from kta.config import Settings


class SettingsTests(unittest.TestCase):
    def test_universe_has_no_implicit_default(self):
        with patch.dict(os.environ, {}, clear=True):
            settings = Settings.from_env(Path("/path/that/does/not/exist"))

        self.assertEqual(settings.universe, [])
        self.assertTrue(any("KTA_UNIVERSE" in error for error in settings.validate()))

    def test_configured_offline_mode_is_valid(self):
        with patch.dict(os.environ, {"KTA_UNIVERSE": "TEST1,TEST2"}, clear=True):
            settings = Settings.from_env(Path("/path/that/does/not/exist"))

        self.assertEqual(settings.validate(), [])
        self.assertEqual(settings.universe, ["TEST1", "TEST2"])

    def test_paper_mode_rejects_synthetic_data(self):
        with patch.dict(
            os.environ,
            {
                "KTA_BROKER_MODE": "paper",
                "KTA_MARKET_DATA_MODE": "synthetic",
                "ALPACA_API_KEY": "paper-key",
                "ALPACA_API_SECRET": "paper-secret",
            },
            clear=True,
        ):
            settings = Settings.from_env(Path("/path/that/does/not/exist"))

        self.assertTrue(any("synthetic" in error for error in settings.validate()))

    def test_unimplemented_execution_capabilities_fail_validation(self):
        with patch.dict(
            os.environ,
            {"KTA_OPTIONS_ENABLED": "true", "KTA_SHORTING_ENABLED": "true"},
            clear=True,
        ):
            settings = Settings.from_env(Path("/path/that/does/not/exist"))

        errors = settings.validate()
        self.assertTrue(any("Options" in error for error in errors))
        self.assertTrue(any("Short" in error for error in errors))

    def test_non_loopback_daemon_requires_control_token(self):
        with patch.dict(
            os.environ,
            {"KTA_UNIVERSE": "TEST1", "KTA_DAEMON_HOST": "0.0.0.0"},
            clear=True,
        ):
            settings = Settings.from_env(Path("/path/that/does/not/exist"))

        self.assertTrue(any("KTA_CONTROL_TOKEN" in error for error in settings.validate()))

    def test_toml_configuration_is_separate_from_secrets_and_environment_wins(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "config").mkdir()
            (root / "config" / "mandate.toml").write_text(
                '[mandate]\nthemes = ["power", "cooling"]\nregions = ["US"]\n', encoding="utf-8"
            )
            (root / "config" / "runtime.toml").write_text(
                '[runtime]\ndiscovery_enabled = true\n[models]\ndiscovery = "cheap/model"\n',
                encoding="utf-8",
            )
            (root / "secrets.env").write_text(
                "OPENROUTER_API_KEY=secret\nPERPLEXITY_API_KEY=search-secret\n", encoding="utf-8"
            )
            with patch.dict(
                os.environ,
                {
                    "KTA_AGENT_MODE": "openrouter",
                    "KTA_RESEARCH_MODE": "perplexity",
                    "KTA_TRADE_MODEL": "trade/model",
                    "KTA_CRITIC_MODEL": "critic/model",
                    "KTA_REVIEWER_MODEL": "review/model",
                    "KTA_MANDATE_TIME_HORIZON": "years",
                },
                clear=True,
            ):
                settings = Settings.from_env(root / ".env")

            self.assertEqual(settings.mandate_themes, ["power", "cooling"])
            self.assertEqual(settings.mandate_time_horizon, "years")
            self.assertTrue(settings.discovery_enabled)
            self.assertEqual(settings.openrouter_api_key, "secret")
            self.assertNotIn("secret", str(settings.safe_dict()))


if __name__ == "__main__":
    unittest.main()
