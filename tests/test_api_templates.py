"""Tests for the HTMX / Jinja UI surface.

Covers:

- GET / renders the filter form + empty-state message + HTMX bootstrap.
- Static assets (bundled htmx.min.js + CSS) are served under /static.
- POST / with valid form data submits a scan and returns the scan card
  partial with the polling ``hx-get`` attribute.
- POST / with invalid form data (e.g. --all + --hostname) returns the
  ``form_error.html`` partial with the CLI wording.
- GET /scans/{id}/partial returns:
    - the polling card while running,
    - the done card (with result rows, no hx-trigger) once complete,
    - an empty body once the job has been swept.
"""

from __future__ import annotations

import re
import time
from typing import Any, Iterator

import pytest
from fastapi.testclient import TestClient

import fh_symbol_check.pipeline as pipeline_module
import fh_symbol_check.validator as validator_module
from fh_symbol_check.api.server import build_app
from fh_symbol_check.creds import DBCreds
from fh_symbol_check.models import FeedHandlerRow


def _fake_fh(*args: Any, **kwargs: Any) -> list[FeedHandlerRow]:
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


def _fake_rp(*args: Any, **kwargs: Any) -> list[FeedHandlerRow]:
    return []


def _fake_ccxt(_ccxt_id: str) -> tuple[Any, dict[str, Any]]:
    return object(), {
        "BTC/USDT": {"active": True},
        "ETH/USDT": {"active": False},
    }


@pytest.fixture(autouse=True)
def patch_deps(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(pipeline_module, "fetch_feed_handlers", _fake_fh)
    monkeypatch.setattr(pipeline_module, "fetch_repeaters", _fake_rp)
    monkeypatch.setattr(validator_module, "load_exchange_markets_safe", _fake_ccxt)


@pytest.fixture
def client() -> Iterator[TestClient]:
    app = build_app(
        creds=DBCreds(host="h", user="u", password="p", database="d"),
        exchange_map={"HUOBI": "htx"},
        job_ttl_seconds=60.0,
        sweep_interval_seconds=3600.0,
    )
    with TestClient(app) as c:
        yield c


def _wait_for_done_html(
    client: TestClient, job_id: str, *, timeout: float = 5.0
) -> str:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        r = client.get(f"/scans/{job_id}/partial")
        assert r.status_code == 200
        if "state-done" in r.text or "state-failed" in r.text:
            return r.text
        time.sleep(0.02)
    raise AssertionError(f"job {job_id} did not finish within {timeout}s")


# ---------------------------------------------------------------------------
# GET /
# ---------------------------------------------------------------------------


def test_home_renders_filter_form(client: TestClient) -> None:
    r = client.get("/")
    assert r.status_code == 200
    assert "Run a scan" in r.text
    assert '<form id="scan-form"' in r.text
    assert 'hx-post="/"' in r.text
    assert 'name="hostname"' in r.text
    assert 'name="exchange_name"' in r.text
    assert 'name="symbol"' in r.text
    assert 'name="all"' in r.text


def test_home_bootstraps_htmx(client: TestClient) -> None:
    r = client.get("/")
    assert '/static/htmx.min.js' in r.text
    assert '/static/app.css' in r.text


def test_home_shows_empty_state_when_no_jobs(client: TestClient) -> None:
    r = client.get("/")
    assert "No scans yet" in r.text


# ---------------------------------------------------------------------------
# Static assets
# ---------------------------------------------------------------------------


def test_static_htmx_bundle_served(client: TestClient) -> None:
    r = client.get("/static/htmx.min.js")
    assert r.status_code == 200
    # HTMX 1.9.x is ~48 KB; guard against an accidental empty/truncated file.
    assert len(r.content) > 30_000
    assert b"htmx" in r.content.lower()


def test_static_css_served(client: TestClient) -> None:
    r = client.get("/static/app.css")
    assert r.status_code == 200
    assert b".scan-card" in r.content


# ---------------------------------------------------------------------------
# POST / — happy path
# ---------------------------------------------------------------------------


def test_post_form_returns_scan_card_with_polling_trigger(
    client: TestClient,
) -> None:
    r = client.post("/", data={"all": "1", "source": "fh", "concurrency": "1"})
    assert r.status_code == 200
    # Structural markers.
    assert 'class="scan-card' in r.text
    match = re.search(r'id="scan-([a-f0-9]+)"', r.text)
    assert match, r.text
    job_id = match.group(1)
    # While queued/running, the card carries hx-get so HTMX polls itself.
    assert f'hx-get="/scans/{job_id}/partial"' in r.text
    assert 'hx-trigger="every 1s"' in r.text


def test_post_form_flows_through_to_done_card_with_result_rows(
    client: TestClient,
) -> None:
    r = client.post("/", data={"all": "1", "source": "fh", "concurrency": "1"})
    job_id = re.search(r'id="scan-([a-f0-9]+)"', r.text).group(1)  # type: ignore[union-attr]

    done_html = _wait_for_done_html(client, job_id)
    # Once done: no polling trigger anymore, and the result table shows.
    assert 'hx-trigger="every 1s"' not in done_html
    assert "state-done" in done_html
    assert "BTC/USDT" in done_html
    assert "ETH/USDT" in done_html
    assert "LISTED" in done_html
    assert "INACTIVE" in done_html
    # Result rows should be tagged for the client-side status filter.
    assert 'data-status="LISTED"' in done_html
    assert 'data-status="INACTIVE"' in done_html


def test_post_form_recent_jobs_include_new_job_on_next_get(
    client: TestClient,
) -> None:
    r = client.post("/", data={"all": "1", "source": "fh"})
    job_id = re.search(r'id="scan-([a-f0-9]+)"', r.text).group(1)  # type: ignore[union-attr]
    _wait_for_done_html(client, job_id)

    home = client.get("/").text
    # Full-page reload should now show the finished card in the list.
    assert f'id="scan-{job_id}"' in home
    assert "No scans yet" not in home


# ---------------------------------------------------------------------------
# POST / — invalid form
# ---------------------------------------------------------------------------


def test_post_form_all_plus_hostname_returns_error_partial(
    client: TestClient,
) -> None:
    r = client.post("/", data={"all": "1", "hostname": "TA-TKY-A-41"})
    assert r.status_code == 200  # error is rendered inline, not as HTTP error
    assert "invalid form" in r.text
    assert "--all cannot be combined" in r.text
    # Must NOT have submitted a scan.
    assert 'class="scan-card scan-queued"' not in r.text
    assert 'class="scan-card scan-running"' not in r.text


def test_post_form_empty_returns_error_partial(client: TestClient) -> None:
    r = client.post("/", data={})
    assert r.status_code == 200
    assert "specify --hostname" in r.text


# ---------------------------------------------------------------------------
# GET /scans/{id}/partial
# ---------------------------------------------------------------------------


def test_partial_returns_empty_body_for_unknown_id(client: TestClient) -> None:
    # Simulates a card that was TTL-swept during polling: empty body swap
    # makes HTMX replace the card with nothing, stopping the poll cleanly.
    r = client.get("/scans/nope-not-a-uuid/partial")
    assert r.status_code == 200
    assert r.text == ""


def test_partial_shows_progress_while_running(client: TestClient) -> None:
    # Post + immediately fetch the partial before the worker finishes.
    r = client.post("/", data={"all": "1", "source": "fh", "concurrency": "1"})
    job_id = re.search(r'id="scan-([a-f0-9]+)"', r.text).group(1)  # type: ignore[union-attr]

    # Even if the worker races us to done, either "state-running" OR
    # "state-done" is acceptable — but the polling attribute must be
    # present ONLY on the running state.
    r2 = client.get(f"/scans/{job_id}/partial")
    if "state-done" not in r2.text and "state-failed" not in r2.text:
        assert 'hx-trigger="every 1s"' in r2.text
    # Whatever state, the partial should be a full scan-card article.
    assert 'class="scan-card' in r2.text
