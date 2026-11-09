from __future__ import annotations

from pathlib import Path

import pyarrow as pa
import pytest
from fastapi.testclient import TestClient

from catmod.api.app import ARROW_STREAM, create_app


@pytest.fixture()
def client(tmp_path: Path) -> TestClient:
    return TestClient(create_app(data_dir=tmp_path))


def _create(client: TestClient, n: int = 2_000) -> dict:  # type: ignore[type-arg]
    r = client.post("/api/v1/portfolios", json={"preset": "us-cat-demo", "n_locations": n})
    assert r.status_code == 201, r.text
    return r.json()  # type: ignore[no-any-return]


def test_health_and_reference(client: TestClient) -> None:
    assert client.get("/health").json()["status"] == "ok"
    assert "us-cat-demo" in client.get("/api/v1/presets").json()
    codes = client.get("/api/v1/codes").json()
    assert codes["perils"]["QEQ"]
    assert client.get("/api/v1/presets/nope").status_code == 404


def test_create_list_get_delete(client: TestClient) -> None:
    meta = _create(client)
    pid = meta["id"]
    assert meta["summary"]["n_locations"] == 2_000
    assert [m["id"] for m in client.get("/api/v1/portfolios").json()] == [pid]
    assert client.get(f"/api/v1/portfolios/{pid}").json()["name"] == "US cat demo"
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
    assert table.num_rows == 500
    assert "catmod_tiv" in table.schema.names


def test_store_reloads_from_disk(tmp_path: Path) -> None:
    c1 = TestClient(create_app(data_dir=tmp_path))
    pid = _create(c1)["id"]
    c2 = TestClient(create_app(data_dir=tmp_path))
    assert c2.get(f"/api/v1/portfolios/{pid}").status_code == 200
    assert c2.get(f"/api/v1/portfolios/{pid}/files/location.parquet").status_code == 200
