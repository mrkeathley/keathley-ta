"""Small JSON HTTP client used by external service adapters."""

import json
from typing import Any, Dict, Optional
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen


class ServiceError(RuntimeError):
    pass


def request_json(
    method: str,
    url: str,
    headers: Optional[Dict[str, str]] = None,
    query: Optional[Dict[str, Any]] = None,
    body: Optional[Dict[str, Any]] = None,
    timeout: int = 30,
) -> Dict[str, Any]:
    if query:
        encoded = urlencode({key: value for key, value in query.items() if value is not None})
        url = "{}{}{}".format(url, "&" if "?" in url else "?", encoded)
    request_headers = {"Accept": "application/json", "User-Agent": "kta-agentic-trader/0.1"}
    request_headers.update(headers or {})
    payload = None
    if body is not None:
        request_headers.setdefault("Content-Type", "application/json")
        payload = json.dumps(body).encode("utf-8")
    request = Request(url, data=payload, headers=request_headers, method=method.upper())
    try:
        with urlopen(request, timeout=timeout) as response:
            raw = response.read().decode("utf-8")
            return json.loads(raw) if raw else {}
    except HTTPError as error:
        detail = error.read().decode("utf-8", errors="replace")[:1000]
        raise ServiceError("{} {} returned {}: {}".format(method, url, error.code, detail)) from error
    except (URLError, TimeoutError) as error:
        raise ServiceError("{} {} failed: {}".format(method, url, error)) from error
    except json.JSONDecodeError as error:
        raise ServiceError("{} {} returned invalid JSON".format(method, url)) from error

