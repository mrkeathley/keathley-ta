import os
import unittest
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


if __name__ == "__main__":
    unittest.main()
