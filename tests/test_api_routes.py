"""End-to-end tests for the FastAPI JSON surface.

Uses ``TestClient`` so the app is exercised through real HTTP semantics
(status codes, headers, JSON body shape). The pipeline is stubbed at the
DB + ccxt layer so scans run offline in milliseconds.
"""

from __future__ import annotations

import time
from typing import Any, Iterator

import pytest
from fastapi.testclient import TestClient

import fh_symbol_check.pipeline as pipeline_module
import fh_symbol_check.validator as validator_module
from fh_symbol_check.api.server import build_app
from fh_symbol_check.creds import DBCreds
from fh_symbol_check.db import DBError
from fh_symbol_check.models import FeedHandlerRow


# ---------------------------------------------------------------------------
# Fixtures: stub DB + ccxt so scans complete synchronously
# ---------------------------------------------------------------------------


def _fake_fh_rows(*, error: Exception | None = None) -> Any:
    def _fetch(creds, *, hostname_pattern=None, exchange_name=None):
        if error is not None:
            raise error
        return [
            FeedHandlerRow(
                service_id=4002,
                fh_name="fh_htx_4002",
                hostname="TA-TKY-A-41_LOCAL",
                exchange_name="HUOBI",
                symbols=("BTC/USDT", "ETH/USDT"),
                source="fh",
            )
        ]
    return _fetch


def _fake_rp_rows() -> Any:
    def _fetch(creds, *, hostname_pattern=None, exchange_name=None):
        return []
    return _fetch


def _fake_ccxt_loader(ccxt_id: str):
    return object(), {
        "BTC/USDT": {"active": True},
        "ETH/USDT": {"active": False},
    }


@pytest.fixture
def patched_pipeline(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(pipeline_module, "fetch_feed_handlers", _fake_fh_rows())
    monkeypatch.setattr(pipeline_module, "fetch_repeaters", _fake_rp_rows())
    monkeypatch.setattr(
        validator_module, "load_exchange_markets_safe", _fake_ccxt_loader
    )


@pytest.fixture
def client(patched_pipeline: None) -> Iterator[TestClient]:
    app = build_app(
        creds=DBCreds(host="h", user="u", password="p", database="d"),
        exchange_map={"HUOBI": "htx"},
        job_ttl_seconds=60.0,
        max_concurrent_scans=2,
        sweep_interval_seconds=3600.0,  # keep the sweeper out of these tests
    )
    with TestClient(app) as c:
        yield c


def _wait_for_done(
    client: TestClient, job_id: str, *, timeout: float = 5.0
) -> dict[str, Any]:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        r = client.get(f"/scans/{job_id}")
        assert r.status_code == 200
        payload = r.json()
        if payload["state"] in ("done", "failed"):
            return payload
        time.sleep(0.02)
    raise AssertionError(f"job {job_id} did not finish within {timeout}s")


# ---------------------------------------------------------------------------
# Meta endpoints
# ---------------------------------------------------------------------------


def test_health_reports_ok_and_version(client: TestClient) -> None:
    r = client.get("/health")
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "ok"
    assert body["version"]  # exact value comes from importlib.metadata
    assert body["active_scans"] == 0
    assert body["recent_scans"] == 0


def test_openapi_schema_is_served(client: TestClient) -> None:
    r = client.get("/openapi.json")
    assert r.status_code == 200
    schema = r.json()
    assert schema["info"]["title"] == "find-expired-symbols API"
    # Locking in the API surface — schedulers/UIs pin against these.
    paths = schema["paths"]
    assert "/health" in paths
    assert "/scans" in paths
    assert "/scans/{job_id}" in paths


# ---------------------------------------------------------------------------
# POST /scans validation
# ---------------------------------------------------------------------------


def test_post_scans_empty_body_returns_422_with_cli_wording(
    client: TestClient,
) -> None:
    r = client.post("/scans", json={})
    assert r.status_code == 422
    detail = "\n".join(err["msg"] for err in r.json()["detail"])
    assert "specify --hostname, --exchange-name, --symbol, or --all" in detail


def test_post_scans_all_plus_hostname_returns_422(client: TestClient) -> None:
    r = client.post("/scans", json={"all": True, "hostname": "TA-TKY-A-41"})
    assert r.status_code == 422
    detail = "\n".join(err["msg"] for err in r.json()["detail"])
    assert "--all cannot be combined" in detail


def test_post_scans_unknown_field_returns_422(client: TestClient) -> None:
    r = client.post("/scans", json={"all": True, "typo": True})
    assert r.status_code == 422


# ---------------------------------------------------------------------------
# POST /scans happy path
# ---------------------------------------------------------------------------


def test_post_scans_returns_202_and_location_header(client: TestClient) -> None:
    r = client.post("/scans", json={"all": True})
    assert r.status_code == 202
    body = r.json()
    assert body["state"] == "queued"
    assert body["id"]
    assert r.headers["Location"] == f"/scans/{body['id']}"


def test_get_scans_lists_recently_created_jobs(client: TestClient) -> None:
    r = client.post("/scans", json={"all": True})
    assert r.status_code == 202
    job_id = r.json()["id"]
    r2 = client.get("/scans")
    assert r2.status_code == 200
    ids = [j["id"] for j in r2.json()["jobs"]]
    assert job_id in ids


def test_scan_progresses_to_done_with_expected_result_rows(
    client: TestClient,
) -> None:
    r = client.post("/scans", json={"all": True})
    job_id = r.json()["id"]

    done = _wait_for_done(client, job_id)
    assert done["state"] == "done"
    assert done["error"] is None
    assert done["completed_at"] is not None

    # Same shape the CLI's --output json would produce; source stays on rows.
    result = done["result"]
    assert isinstance(result, list)
    statuses = {(row["original_symbol"], row["status"]) for row in result}
    assert statuses == {("BTC/USDT", "LISTED"), ("ETH/USDT", "INACTIVE")}
    for row in result:
        assert row["source"] == "fh"
        assert row["ccxt_id"] == "htx"


def test_progress_reports_completed_over_total_before_done(
    client: TestClient,
) -> None:
    r = client.post("/scans", json={"all": True})
    job_id = r.json()["id"]
    done = _wait_for_done(client, job_id)
    assert done["progress"]["phase"] == "done"
    assert done["progress"]["total_producers"] == 1
    assert done["progress"]["completed_producers"] == 1
    assert done["progress"]["rows_scanned"] == 1


# ---------------------------------------------------------------------------
# Failure path: DB error surfaces as state=failed with a readable message
# ---------------------------------------------------------------------------


def test_db_error_lands_the_job_in_failed_state(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        pipeline_module,
        "fetch_feed_handlers",
        _fake_fh_rows(error=DBError("connection refused")),
    )
    monkeypatch.setattr(pipeline_module, "fetch_repeaters", _fake_rp_rows())
    monkeypatch.setattr(
        validator_module, "load_exchange_markets_safe", _fake_ccxt_loader
    )

    app = build_app(
        creds=DBCreds(host="h", user="u", password="p", database="d"),
        exchange_map={"HUOBI": "htx"},
        job_ttl_seconds=60.0,
        sweep_interval_seconds=3600.0,
    )
    with TestClient(app) as c:
        r = c.post("/scans", json={"all": True, "source": "fh"})
        job_id = r.json()["id"]
        done = _wait_for_done(c, job_id)
        assert done["state"] == "failed"
        assert done["result"] is None
        assert "connection refused" in done["error"]


# ---------------------------------------------------------------------------
# GET /scans/{id} unknown
# ---------------------------------------------------------------------------


def test_get_unknown_job_returns_404(client: TestClient) -> None:
    r = client.get("/scans/nope-not-a-uuid")
    assert r.status_code == 404
    assert "not found" in r.json()["detail"]
