from __future__ import annotations

import json
import os
import sys
import threading
import unittest
from pathlib import Path
from unittest.mock import patch
from urllib.request import Request, urlopen


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import local_server  # noqa: E402


class LocalRefreshApiTest(unittest.TestCase):
    def setUp(self) -> None:
        self.persist_patcher = patch.object(local_server, "write_persisted_state")
        self.read_persist_patcher = patch.object(local_server, "read_persisted_state", return_value=None)
        self.persist_patcher.start()
        self.read_persist_patcher.start()
        local_server.ACTIVE_THREAD = None
        local_server.STATE.update(
            {
                "local_refresh_api": True,
                "state": "idle",
                "running": False,
                "progress": 0,
                "step": "等待刷新",
                "message": "ready",
                "started_at": None,
                "finished_at": None,
                "reason": None,
                "failed_steps": [],
            }
        )
        self.server = local_server.LocalHTTPServer(
            ("127.0.0.1", 0),
            local_server.LocalRequestHandler,
        )
        self.server_thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.server_thread.start()
        self.base_url = f"http://127.0.0.1:{self.server.server_address[1]}"

    def tearDown(self) -> None:
        self.server.shutdown()
        self.server.server_close()
        self.server_thread.join(timeout=5)
        self.persist_patcher.stop()
        self.read_persist_patcher.stop()

    def read_json(self, path: str, method: str = "GET") -> dict:
        request = Request(
            f"{self.base_url}{path}",
            method=method,
            headers={"Accept": "application/json"},
        )
        with urlopen(request, timeout=5) as response:
            return json.loads(response.read().decode("utf-8"))

    def test_health_and_manual_refresh(self) -> None:
        health = self.read_json("/api/health")
        self.assertTrue(health["ok"])
        self.assertTrue(health["local_refresh_api"])

        completed = threading.Event()

        def fake_refresh(reason: str) -> bool:
            local_server.update_state(
                state="success",
                running=False,
                progress=100,
                step="刷新完成",
                message="test complete",
                reason=reason,
            )
            completed.set()
            return True

        with patch.object(local_server, "run_refresh", side_effect=fake_refresh):
            accepted = self.read_json("/api/refresh", method="POST")
            self.assertTrue(accepted["accepted"])
            self.assertTrue(completed.wait(timeout=5))

        status = self.read_json("/api/refresh-status")
        self.assertEqual(status["state"], "success")
        self.assertEqual(status["reason"], "manual")
        self.assertEqual(status["progress"], 100)

    def test_ascii_ca_bundle_for_unicode_project_path(self) -> None:
        environment = os.environ.copy()
        local_server.configure_ascii_ca_bundle(environment)
        bundle = Path(environment["CURL_CA_BUNDLE"])
        self.assertTrue(bundle.is_file())
        self.assertEqual(environment["SSL_CERT_FILE"], str(bundle))
        self.assertEqual(environment["REQUESTS_CA_BUNDLE"], str(bundle))


if __name__ == "__main__":
    unittest.main()
