"""Dependency-free HTTP control plane for the daemon."""

import json
import secrets
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Dict
from urllib.parse import parse_qs, urlparse

from .domain import jsonable
from .service import DaemonService


class ControlHTTPServer(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, address, service: DaemonService):
        self.service = service
        super().__init__(address, ControlRequestHandler)


class ControlRequestHandler(BaseHTTPRequestHandler):
    server: ControlHTTPServer
    protocol_version = "HTTP/1.1"

    def log_message(self, format: str, *args) -> None:
        self.server.service.journal.service_event(
            "http_access",
            format % args,
            {"client": self.client_address[0], "method": self.command, "path": self.path},
        )

    def _authorized(self) -> bool:
        expected = self.server.service.settings.control_token
        if not expected:
            return True
        supplied = self.headers.get("Authorization", "")
        if supplied.startswith("Bearer "):
            supplied = supplied[7:]
        return secrets.compare_digest(supplied, expected)

    def _json_body(self) -> Dict[str, Any]:
        raw_length = self.headers.get("Content-Length", "0")
        try:
            length = int(raw_length)
        except ValueError as error:
            raise ValueError("Invalid Content-Length") from error
        if length < 0 or length > 1_000_000:
            raise ValueError("Request body exceeds 1 MB")
        raw = self.rfile.read(length) if length else b"{}"
        value = json.loads(raw.decode("utf-8"))
        if not isinstance(value, dict):
            raise ValueError("JSON body must be an object")
        return value

    def _send(self, status: int, value: Any) -> None:
        payload = json.dumps(jsonable(value), sort_keys=True).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(payload)

    def _route(self) -> tuple[str, Dict[str, list[str]]]:
        parsed = urlparse(self.path)
        return parsed.path.rstrip("/") or "/", parse_qs(parsed.query)

    def do_GET(self) -> None:
        path, query = self._route()
        if path == "/healthz":
            self._send(HTTPStatus.OK, {"status": "ok"})
            return
        if not self._authorized():
            self._send(HTTPStatus.UNAUTHORIZED, {"error": "unauthorized"})
            return
        try:
            limit = min(200, max(1, int(query.get("limit", ["50"])[0])))
            if path == "/v1/status":
                self._send(HTTPStatus.OK, self.server.service.status())
            elif path == "/v1/jobs":
                self._send(HTTPStatus.OK, {"jobs": self.server.service.journal.recent_jobs(limit)})
            elif path == "/v1/events":
                self._send(
                    HTTPStatus.OK,
                    {"events": self.server.service.journal.recent_service_events(limit)},
                )
            elif path == "/v1/suggestions":
                self._send(
                    HTTPStatus.OK,
                    {"suggestions": self.server.service.journal.trade_suggestions(limit)},
                )
            elif path == "/v1/triggers":
                self._send(
                    HTTPStatus.OK,
                    {"triggers": self.server.service.journal.price_triggers(limit)},
                )
            elif path.startswith("/v1/conversations/"):
                conversation_id = path.split("/")[-1]
                self._send(
                    HTTPStatus.OK,
                    {
                        "conversation_id": conversation_id,
                        "messages": self.server.service.journal.conversation(
                            conversation_id, limit
                        ),
                    },
                )
            else:
                self._send(HTTPStatus.NOT_FOUND, {"error": "not_found"})
        except Exception as error:
            self._send(
                HTTPStatus.BAD_REQUEST,
                {"error": "{}: {}".format(type(error).__name__, error)},
            )

    def do_POST(self) -> None:
        path, _ = self._route()
        if not self._authorized():
            self._send(HTTPStatus.UNAUTHORIZED, {"error": "unauthorized"})
            return
        try:
            body = self._json_body()
            if path == "/v1/scans":
                symbols = body.get("symbols")
                if symbols is not None and not isinstance(symbols, list):
                    raise ValueError("symbols must be an array")
                job_id = self.server.service.enqueue_scan(
                    [str(item) for item in symbols] if symbols else None,
                    cause=str(body.get("cause") or "http"),
                )
                self._send(HTTPStatus.ACCEPTED, {"job_id": job_id})
            elif path == "/v1/triggers":
                trigger_id = self.server.service.create_price_trigger(
                    str(body["symbol"]),
                    str(body["comparison"]),
                    body["threshold"],
                    source="http",
                    one_shot=bool(body.get("one_shot", True)),
                    rationale=str(body.get("rationale") or ""),
                )
                self._send(HTTPStatus.CREATED, {"trigger_id": trigger_id})
            elif path == "/v1/chat":
                result = self.server.service.submit_chat(
                    str(body.get("conversation_id") or "default"), str(body["message"])
                )
                self._send(HTTPStatus.ACCEPTED, result)
            elif path == "/v1/webhooks/price":
                result = self.server.service.ingest_price(
                    str(body["symbol"]), body["price"], source=str(body.get("source") or "webhook")
                )
                self._send(HTTPStatus.ACCEPTED, result)
            else:
                self._send(HTTPStatus.NOT_FOUND, {"error": "not_found"})
        except KeyError as error:
            self._send(HTTPStatus.BAD_REQUEST, {"error": "Missing field {}".format(error)})
        except (ValueError, json.JSONDecodeError) as error:
            self._send(HTTPStatus.BAD_REQUEST, {"error": str(error)})
        except Exception as error:
            self.server.service.journal.service_event(
                "http_error",
                "HTTP request failed",
                {"path": path, "error": "{}: {}".format(type(error).__name__, error)},
                level="error",
            )
            self._send(
                HTTPStatus.INTERNAL_SERVER_ERROR,
                {"error": "{}: {}".format(type(error).__name__, error)},
            )


def serve_control_api(service: DaemonService) -> None:
    server = ControlHTTPServer(
        (service.settings.daemon_host, service.settings.daemon_port), service
    )
    service.start_background_loops()
    service.journal.service_event(
        "http_started",
        "HTTP control plane listening",
        {"host": service.settings.daemon_host, "port": service.settings.daemon_port},
    )
    try:
        server.serve_forever(poll_interval=0.5)
    finally:
        server.server_close()
        service.stop()
