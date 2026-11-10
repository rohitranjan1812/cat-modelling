from __future__ import annotations

import json
from itertools import pairwise
from pathlib import Path
from typing import Any

import pyarrow as pa
import pytest
from fastapi.testclient import TestClient

from catmod.api.app import ARROW_STREAM, create_app

FIX = Path(__file__).parent / "fixtures"


@pytest.fixture()
def client(tmp_path: Path) -> TestClient:
    return TestClient(create_app(data_dir=tmp_path, live_enabled=False))


def _create(client: TestClient, n: int = 2_000) -> dict[str, Any]:
    r = client.post("/api/v1/portfolios", json={"preset": "us-cat-demo", "n_locations": n})
    assert r.status_code == 201, r.text
    return r.json()  # type: ignore[no-any-return]


@pytest.fixture(scope="module")
def run_client(tmp_path_factory: pytest.TempPathFactory) -> tuple[TestClient, str, str]:
    c = TestClient(create_app(data_dir=tmp_path_factory.mktemp("api"), live_enabled=False))
    pid = _create(c, 3_000)["id"]
    r = c.post(
        "/api/v1/runs?wait=true",
        json={
            "portfolio_id": pid,
            "preset": "quick",
            "overrides": {"n_years": 3000, "workers": 1, "name": "test run"},
        },
    )
    assert r.status_code == 202, r.text
    meta = r.json()
    assert meta["status"] == "done", meta.get("error")
    return c, pid, meta["id"]


# ---------------------------------------------------------------- reference / portfolios
def test_health_and_reference(client: TestClient) -> None:
    assert client.get("/health").json()["status"] == "ok"
    assert "us-cat-demo" in client.get("/api/v1/presets").json()
    caps = client.get("/api/v1/capabilities").json()
    assert {"fft", "cholesky", "independent"} <= set(caps["spatial_fields"])
    assert {"one_factor", "spatial", "independent"} <= set(caps["copulas"])
    assert set(caps["analysis_presets"]) >= {"quick", "standard"}
    assert client.get("/api/v1/codes").json()["perils"]["QEQ"]
    assert client.get("/api/v1/presets/nope").status_code == 404


def test_portfolio_crud(client: TestClient) -> None:
    meta = _create(client)
    pid = meta["id"]
    assert meta["summary"]["n_locations"] == 2_000
    assert [m["id"] for m in client.get("/api/v1/portfolios").json()] == [pid]
    assert client.delete(f"/api/v1/portfolios/{pid}").status_code == 204
    assert client.get(f"/api/v1/portfolios/{pid}").status_code == 404


def test_create_requires_exactly_one_source(client: TestClient) -> None:
    assert client.post("/api/v1/portfolios", json={}).status_code == 422
    assert client.post("/api/v1/portfolios", json={"preset": "missing"}).status_code == 404


@pytest.mark.parametrize("by", ["hub", "occupancy", "construction", "country", "peril", "h3"])
def test_aggregate_conserves_totals(client: TestClient, by: str) -> None:
    meta = _create(client)
    rows = client.get(f"/api/v1/portfolios/{meta['id']}/aggregate", params={"by": by}).json()
    assert sum(r["n_locations"] for r in rows) == meta["summary"]["n_locations"]
    assert sum(r["tiv"] for r in rows) == pytest.approx(meta["summary"]["tiv_total"])


def test_aggregate_h3_resolution_must_exist(client: TestClient) -> None:
    pid = _create(client)["id"]
    url = f"/api/v1/portfolios/{pid}/aggregate"
    assert client.get(url, params={"by": "h3", "h3_res": 5}).status_code == 200
    assert client.get(url, params={"by": "h3", "h3_res": 9}).status_code == 422


def test_arrow_stream(client: TestClient) -> None:
    pid = _create(client, 500)["id"]
    r = client.get(f"/api/v1/portfolios/{pid}/locations.arrow")
    assert r.headers["content-type"] == ARROW_STREAM
    table = pa.ipc.open_stream(r.content).read_all()
    assert table.num_rows == 500 and "catmod_tiv" in table.schema.names


def test_store_reloads_from_disk(tmp_path: Path) -> None:
    c1 = TestClient(create_app(data_dir=tmp_path, live_enabled=False))
    pid = _create(c1)["id"]
    c2 = TestClient(create_app(data_dir=tmp_path, live_enabled=False))
    assert c2.get(f"/api/v1/portfolios/{pid}").status_code == 200
    assert c2.get(f"/api/v1/portfolios/{pid}/files/location.parquet").status_code == 200


# ---------------------------------------------------------------- runs
def test_run_summary(run_client: tuple[TestClient, str, str]) -> None:
    c, pid, rid = run_client
    meta = c.get(f"/api/v1/runs/{rid}").json()
    m = meta["summary"]["metrics"]
    assert m["gu"]["aal"] >= m["gross"]["aal"] > 0
    assert [r["id"] for r in c.get("/api/v1/runs", params={"portfolio_id": pid}).json()] == [rid]


def test_run_ep_filters(run_client: tuple[TestClient, str, str]) -> None:
    c, _, rid = run_client
    url = f"/api/v1/runs/{rid}/ep"
    full = c.get(url, params={"perspective": "gu"}).json()
    eq = c.get(url, params={"perspective": "gu", "peril": "QEQ"}).json()
    tc = c.get(url, params={"perspective": "gu", "peril": "WTC"}).json()
    assert eq["metrics"]["aal"] + tc["metrics"]["aal"] == pytest.approx(full["metrics"]["aal"])
    hubs = c.get(f"/api/v1/runs/{rid}/breakdown", params={"by": "hub"}).json()
    parts = [
        c.get(url, params={"perspective": "gu", "hub": h["key"]}).json()["metrics"]["aal"]
        for h in hubs
    ]
    assert sum(parts) == pytest.approx(full["metrics"]["aal"], rel=1e-6)
    # segment-filtered EP agrees with location AAL breakdown up to MC error
    big = max(hubs, key=lambda h: h["aal_gu"])
    seg = c.get(url, params={"perspective": "gu", "hub": big["key"]}).json()["metrics"]
    assert seg["aal"] == pytest.approx(big["aal_gu"], rel=0.25)
    assert c.get(url, params={"perspective": "net", "hub": big["key"]}).status_code == 422
    curve = full["curve"]
    assert all(a["oep"] <= b["oep"] for a, b in pairwise(curve))


def test_run_breakdown_conserves(run_client: tuple[TestClient, str, str]) -> None:
    c, _, rid = run_client
    meta = c.get(f"/api/v1/runs/{rid}").json()
    for by in ("hub", "occupancy", "construction", "peril", "h3"):
        rows = c.get(f"/api/v1/runs/{rid}/breakdown", params={"by": by}).json()
        assert sum(r["aal_gu"] for r in rows) == pytest.approx(meta["summary"]["aal_elt"]["gu"])
    only = c.get(f"/api/v1/runs/{rid}/breakdown", params={"by": "hub", "occupancy": 1051}).json()
    assert sum(r["aal_gu"] for r in only) < meta["summary"]["aal_elt"]["gu"]


def test_run_events_and_drill(run_client: tuple[TestClient, str, str]) -> None:
    c, _, rid = run_client
    page = c.get(f"/api/v1/runs/{rid}/events", params={"limit": 5}).json()
    assert page["total"] > 0 and len(page["items"]) == 5
    losses = [e["mean_gross"] for e in page["items"]]
    assert losses == sorted(losses, reverse=True)
    top = page["items"][0]
    d = c.get(f"/api/v1/runs/{rid}/events/{top['event_id']}", params={"top": 10}).json()
    assert d["gross"]["mean"] == pytest.approx(top["mean_gross"])  # deterministic recompute
    assert len(d["top_locations"]) == 10 and d["footprint"]
    assert d["geometry"]["type"] == "LineString"
    at = c.get(f"/api/v1/runs/{rid}/events-at-rp", params={"rp": 50, "window": 0.3}).json()
    assert at["level"] > 0
    assert c.get(f"/api/v1/runs/{rid}/events/1").status_code == 404


def test_run_files_and_arrow(run_client: tuple[TestClient, str, str]) -> None:
    c, _, rid = run_client
    r = c.get(f"/api/v1/runs/{rid}/locations.arrow")
    t = pa.ipc.open_stream(r.content).read_all()
    assert {"aal_gu", "aal_gross", "aal_gu_QEQ", "aal_gu_WTC"} <= set(t.schema.names)
    assert c.get(f"/api/v1/runs/{rid}/files/ylt.parquet").status_code == 200
    assert c.get(f"/api/v1/runs/{rid}/files/secret.parquet").status_code == 404


def test_run_validation(client: TestClient) -> None:
    pid = _create(client, 200)["id"]
    r = client.post(
        "/api/v1/runs",
        json={"portfolio_id": pid, "preset": "quick", "overrides": {"perils": ["XXX"]}},
    )
    assert r.status_code == 422
    assert (
        client.post("/api/v1/runs", json={"portfolio_id": pid, "preset": "nope"}).status_code == 404
    )
    assert client.post("/api/v1/runs", json={"portfolio_id": "missing"}).status_code == 404


# ---------------------------------------------------------------- scenarios
def test_scenarios(client: TestClient) -> None:
    pid = _create(client, 3_000)["id"]
    eq = client.post(
        "/api/v1/scenarios",
        json={
            "portfolio_id": pid,
            "peril": "QEQ",
            "n_samples": 32,
            "event": {"lat": 34.0, "lon": -118.3, "magnitude": 7.0},
        },
    ).json()
    assert eq["n_affected"] > 0 and eq["gu"]["mean"] > 0 and len(eq["samples_gu"]) == 32
    small = client.post(
        "/api/v1/scenarios",
        json={
            "portfolio_id": pid,
            "peril": "QEQ",
            "n_samples": 32,
            "event": {"lat": 34.0, "lon": -118.3, "magnitude": 6.0},
        },
    ).json()
    assert small["gu"]["mean"] < eq["gu"]["mean"]
    tc = client.post(
        "/api/v1/scenarios",
        json={
            "portfolio_id": pid,
            "peril": "WTC",
            "event": {
                "track": [
                    {"lat": 24.5, "lon": -78.5, "vmax_ms": 65},
                    {"lat": 27.5, "lon": -82.5, "vmax_ms": 60},
                ]
            },
        },
    ).json()
    assert tc["gu"]["mean"] > 0 and tc["geometry"]["type"] == "LineString"


# ---------------------------------------------------------------- live
def test_live_feeds_nowcast_and_alerts(client: TestClient) -> None:
    pid = _create(client, 3_000)["id"]
    live = client.app.state.live  # type: ignore[attr-defined]
    rule = client.post(
        "/api/v1/alerts/rules",
        json={"name": "Big EQ", "peril": "QEQ", "metric": "mean_gu", "threshold": 1.0},
    ).json()
    docs = {
        "usgs": json.loads((FIX / "usgs_sample.json").read_text()),
        "nhc": json.loads((FIX / "nhc_sample.json").read_text()),
    }
    live.refresh(docs)
    events = client.get("/api/v1/live/events").json()
    ids = {e["id"] for e in events}
    assert "usgs:ci_fixture1" in ids and "nhc:al992026" in ids
    la = next(e for e in events if e["id"] == "usgs:ci_fixture1")
    assert la["nowcasts"] and la["nowcasts"][0]["portfolio_id"] == pid
    assert la["nowcasts"][0]["gu"]["mean"] > 0
    alerts = client.get("/api/v1/alerts").json()
    assert any(
        a["rule_id"] == rule["id"] and a["live_event_id"] == "usgs:ci_fixture1" for a in alerts
    )
    # refresh with identical docs does not re-fire (dedupe)
    live.refresh(docs)
    assert len(client.get("/api/v1/alerts").json()) == len(alerts)
    # simulated event injection
    sim = client.post(
        "/api/v1/live/inject",
        json={
            "peril": "WTC",
            "name": "Sim Cat 4",
            "lat": 25.5,
            "lon": -80.0,
            "vmax_ms": 62,
            "heading_deg": 300,
        },
    ).json()
    assert sim["source"] == "simulated" and sim["nowcasts"]
    assert client.delete(f"/api/v1/live/events/{sim['id']}").status_code == 204
    assert client.delete(f"/api/v1/alerts/rules/{rule['id']}").status_code == 204
    assert client.get("/api/v1/live/status").json()["enabled"] is False


# ---------------------------------------------------------------- dashboards / ws / auth
def test_dashboards_crud(client: TestClient) -> None:
    tpl = client.get("/api/v1/dashboards/template").json()
    assert len(tpl["widgets"]) >= 5
    d = client.post("/api/v1/dashboards", json=tpl).json()
    tpl["name"] = "Renamed"
    assert client.put(f"/api/v1/dashboards/{d['id']}", json=tpl).json()["name"] == "Renamed"
    assert [x["id"] for x in client.get("/api/v1/dashboards").json()] == [d["id"]]
    assert client.delete(f"/api/v1/dashboards/{d['id']}").status_code == 204
    assert client.get("/api/v1/dashboards/..%2Fetc").status_code == 404


def test_websocket_receives_events(tmp_path: Path) -> None:
    app = create_app(data_dir=tmp_path, live_enabled=False)
    with TestClient(app) as c, c.websocket_connect("/ws") as ws:
        c.post("/api/v1/portfolios", json={"preset": "us-cat-demo", "n_locations": 100})
        msg = ws.receive_json()
        assert msg["topic"] == "portfolio.created"


def test_api_key(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CATMOD_API_KEY", "s3cret")
    c = TestClient(create_app(data_dir=tmp_path, live_enabled=False))
    assert c.get("/health").status_code == 200
    assert c.get("/api/v1/presets").status_code == 401
    assert c.get("/api/v1/presets", headers={"x-api-key": "s3cret"}).status_code == 200
