"""HTTP client for the target platform API (the stub by default)."""
from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any, Callable

import httpx

from .. import config


@dataclass
class Result:
    ok: bool
    status: int
    body: Any
    transient: bool

    @property
    def error_code(self) -> str:
        return self.body.get("error", "") if isinstance(self.body, dict) else ""

    @property
    def message(self) -> str:
        if isinstance(self.body, dict):
            return self.body.get("message") or self.body.get("error") or str(self.body)
        return str(self.body)[:300]


class TargetClient:
    def __init__(self, client: httpx.Client | None = None):
        self.client = client or httpx.Client(base_url=config.TARGET_API_URL, timeout=15)

    def _call(self, method: str, url: str, **kw) -> Result:
        try:
            resp = self.client.request(method, url, **kw)
        except httpx.HTTPError as exc:
            return Result(False, 0, {"error": "network_error", "message": str(exc)}, True)
        try:
            body = resp.json() if resp.content else None
        except ValueError:
            body = resp.text
        ok = 200 <= resp.status_code < 300
        return Result(ok, resp.status_code, body, (resp.status_code >= 500 or resp.status_code == 429))

    def list(self) -> Result:
        return self._call("GET", "/employees")

    def get(self, emp_id: str) -> Result:
        return self._call("GET", f"/employees/{emp_id}")

    def create(self, payload: dict) -> Result:
        return self._call("POST", "/employees", json=payload)

    def update(self, emp_id: str, payload: dict) -> Result:
        return self._call("PUT", f"/employees/{emp_id}", json=payload)

    def delete(self, emp_id: str) -> Result:
        return self._call("DELETE", f"/employees/{emp_id}")


def _default_client() -> TargetClient:
    """No TARGET_API_URL configured: talk to the mock target mounted in this process.

    That keeps the agent independent of the port the server happens to run on (and of the network entirely),
    while still going through the same HTTP request/response path.
    """
    if os.getenv("TARGET_API_URL"):
        return TargetClient()
    from starlette.testclient import TestClient

    from mock_api.app import app as mock_app
    return TargetClient(TestClient(mock_app))


# Tests swap this factory for one that returns a client bound to their own mock app instance.
factory: Callable[[], TargetClient] = _default_client


def get_client() -> TargetClient:
    return factory()
