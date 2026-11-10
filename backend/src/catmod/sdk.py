"""Minimal, dependency-free Python client for the catmod API.

from catmod.sdk import Client
c = Client("http://localhost:8000")
p = c.create_portfolio(preset="us-cat-demo", n_locations=20_000)
run = c.run(p["id"], preset="quick", wait=True)
ep = c.ep(run["id"], perspective="gross", hub=["Miami"])
s = c.scenario(p["id"], "QEQ", {"lat": 34.0, "lon": -118.3, "magnitude": 7.1})
"""

from __future__ import annotations

import json
import time
import urllib.parse
import urllib.request
from typing import Any


class ApiError(RuntimeError):
    def __init__(self, status: int, detail: Any) -> None:
        super().__init__(f"HTTP {status}: {detail}")
        self.status, self.detail = status, detail


class Client:
    def __init__(
        self,
        base_url: str = "http://127.0.0.1:8000",
        api_key: str | None = None,
        timeout: float = 600.0,
    ) -> None:
        self.base = base_url.rstrip("/")
        self.api_key = api_key
        self.timeout = timeout

    def _req(self, method: str, path: str, body: Any = None, **params: Any) -> Any:
        q = urllib.parse.urlencode(
            [
                (k, v)
                for k, vs in params.items()
                if vs is not None
                for v in (vs if isinstance(vs, list) else [vs])
            ]
        )
        url = f"{self.base}{path}" + (f"?{q}" if q else "")
        data = json.dumps(body).encode() if body is not None else None
        req = urllib.request.Request(url, data=data, method=method)
        req.add_header("content-type", "application/json")
        if self.api_key:
            req.add_header("x-api-key", self.api_key)
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as r:
                raw = r.read()
                return json.loads(raw) if raw else None
        except urllib.error.HTTPError as e:
            raise ApiError(e.code, e.read().decode()) from None

    # portfolios
    def create_portfolio(self, preset: str = "us-cat-demo", **overrides: Any) -> Any:
        return self._req("POST", "/api/v1/portfolios", {"preset": preset, **overrides})

    def portfolios(self) -> Any:
        return self._req("GET", "/api/v1/portfolios")

    # runs
    def run(
        self,
        portfolio_id: str,
        preset: str = "standard",
        wait: bool = True,
        poll_s: float = 2.0,
        **overrides: Any,
    ) -> Any:
        meta = self._req(
            "POST",
            "/api/v1/runs",
            {"portfolio_id": portfolio_id, "preset": preset, "overrides": overrides},
        )
        while wait and meta["status"] in ("queued", "running"):
            time.sleep(poll_s)
            meta = self._req("GET", f"/api/v1/runs/{meta['id']}")
        if meta["status"] == "failed":
            raise ApiError(500, meta.get("error"))
        return meta

    def ep(self, run_id: str, perspective: str = "gross", **filters: Any) -> Any:
        return self._req("GET", f"/api/v1/runs/{run_id}/ep", perspective=perspective, **filters)

    def breakdown(self, run_id: str, by: str = "hub", **filters: Any) -> Any:
        return self._req("GET", f"/api/v1/runs/{run_id}/breakdown", by=by, **filters)

    def events(self, run_id: str, **params: Any) -> Any:
        return self._req("GET", f"/api/v1/runs/{run_id}/events", **params)

    def event(self, run_id: str, event_id: int, top: int = 100) -> Any:
        return self._req("GET", f"/api/v1/runs/{run_id}/events/{event_id}", top=top)

    # scenarios / live
    def scenario(self, portfolio_id: str, peril: str, event: dict[str, Any], **kw: Any) -> Any:
        return self._req(
            "POST",
            "/api/v1/scenarios",
            {"portfolio_id": portfolio_id, "peril": peril, "event": event, **kw},
        )

    def live_events(self) -> Any:
        return self._req("GET", "/api/v1/live/events")

    def inject(self, **event: Any) -> Any:
        return self._req("POST", "/api/v1/live/inject", event)
