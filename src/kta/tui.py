"""Small terminal client for the daemon HTTP control plane."""

import json
import shlex
import time
import uuid
from typing import Any, Dict, Optional

from .http import request_json


class ControlClient:
    def __init__(self, base_url: str, token: Optional[str] = None):
        self.base_url = base_url.rstrip("/")
        self.headers = {"Authorization": "Bearer {}".format(token)} if token else {}

    def get(self, path: str) -> Dict[str, Any]:
        return request_json("GET", self.base_url + path, headers=self.headers)

    def post(self, path: str, body: Dict[str, Any]) -> Dict[str, Any]:
        return request_json("POST", self.base_url + path, headers=self.headers, body=body)


def _pretty(value: Any) -> str:
    return json.dumps(value, indent=2, sort_keys=True)


def _chat(client: ControlClient, conversation_id: str, message: str) -> None:
    accepted = client.post(
        "/v1/chat", {"conversation_id": conversation_id, "message": message}
    )
    message_id = accepted["message_id"]
    print("queued chat job {}".format(accepted["job_id"]))
    deadline = time.monotonic() + 180
    while time.monotonic() < deadline:
        thread = client.get("/v1/conversations/{}?limit=30".format(conversation_id))
        messages = thread.get("messages") or []
        seen_user = False
        for item in messages:
            if item["message_id"] == message_id:
                seen_user = True
                continue
            if seen_user and item["role"] == "assistant":
                print("\nagent> {}\n".format(item["content"]))
                return
        time.sleep(1)
    print("agent response is still queued; use /events or /jobs to inspect it")


def run_tui(
    base_url: str,
    token: Optional[str] = None,
    *,
    conversation_id: Optional[str] = None,
    message: Optional[str] = None,
) -> None:
    client = ControlClient(base_url, token)
    conversation = conversation_id or "tui-{}".format(uuid.uuid4().hex[:8])
    if message is not None:
        _chat(client, conversation, message)
        return
    print("KTA control client — {} — conversation {}".format(base_url, conversation))
    print("/status /jobs /events /suggestions /triggers /scan [SYM,...] /trigger SYM above|below PRICE /quit")
    while True:
        try:
            raw = input("kta> ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            return
        if not raw:
            continue
        if raw in {"/quit", "/exit"}:
            return
        try:
            parts = shlex.split(raw)
            command = parts[0]
            if command == "/status":
                print(_pretty(client.get("/v1/status")))
            elif command == "/jobs":
                print(_pretty(client.get("/v1/jobs?limit=20")))
            elif command == "/events":
                print(_pretty(client.get("/v1/events?limit=30")))
            elif command == "/suggestions":
                print(_pretty(client.get("/v1/suggestions?limit=30")))
            elif command == "/triggers":
                print(_pretty(client.get("/v1/triggers?limit=30")))
            elif command == "/scan":
                symbols = parts[1].split(",") if len(parts) > 1 else None
                print(_pretty(client.post("/v1/scans", {"symbols": symbols, "cause": "tui"})))
            elif command == "/trigger" and len(parts) >= 4:
                print(
                    _pretty(
                        client.post(
                            "/v1/triggers",
                            {
                                "symbol": parts[1],
                                "comparison": parts[2],
                                "threshold": parts[3],
                                "rationale": "Created from TUI",
                            },
                        )
                    )
                )
            elif command.startswith("/"):
                print("unknown or incomplete command")
            else:
                _chat(client, conversation, raw)
        except Exception as error:
            print("error: {}: {}".format(type(error).__name__, error))
