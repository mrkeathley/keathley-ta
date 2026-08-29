import os
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

from kta.api import ControlHTTPServer
from kta.config import Settings
from kta.http import ServiceError, request_json
from kta.service import DaemonService


class ControlAPITests(unittest.TestCase):
    def test_authenticated_status_trigger_and_price_webhook(self):
        with tempfile.TemporaryDirectory() as directory, patch.dict(
            os.environ,
            {
                "KTA_UNIVERSE": "TEST1",
                "KTA_DATABASE_PATH": str(Path(directory) / "api.db"),
                "KTA_CONTROL_TOKEN": "test-token",
            },
            clear=True,
        ):
            settings = Settings.from_env(Path("/missing"))
            service = DaemonService(settings)
            try:
                server = ControlHTTPServer(("127.0.0.1", 0), service)
            except PermissionError:
                self.skipTest("The execution sandbox does not permit loopback sockets")
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            base = "http://127.0.0.1:{}".format(server.server_address[1])
            headers = {"Authorization": "Bearer test-token"}
            try:
                self.assertEqual(request_json("GET", base + "/healthz"), {"status": "ok"})
                with self.assertRaises(ServiceError):
                    request_json("GET", base + "/v1/status")
                status = request_json("GET", base + "/v1/status", headers=headers)
                self.assertEqual(status["status"], "running")

                created = request_json(
                    "POST",
                    base + "/v1/triggers",
                    headers=headers,
                    body={
                        "symbol": "TEST1",
                        "comparison": "above",
                        "threshold": 100,
                        "rationale": "test",
                    },
                )
                fired = request_json(
                    "POST",
                    base + "/v1/webhooks/price",
                    headers=headers,
                    body={"symbol": "TEST1", "price": 101, "source": "test"},
                )

                self.assertIn(created["trigger_id"], fired["fired_trigger_ids"])
                self.assertEqual(service.journal.job_counts(), {"queued": 1})
            finally:
                server.shutdown()
                server.server_close()
                thread.join(timeout=2)


if __name__ == "__main__":
    unittest.main()
