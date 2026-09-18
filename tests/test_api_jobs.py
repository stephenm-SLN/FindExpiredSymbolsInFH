"""Tests for the in-memory JobStore.

Covers the state machine (queued → running → done / failed), the TTL
sweep, and basic thread-safety under concurrent update_progress calls.
"""

from __future__ import annotations

import threading
from datetime import datetime, timedelta, timezone

from fh_symbol_check.api.jobs import Job, JobStore
from fh_symbol_check.pipeline import ScanProgress


# ---------------------------------------------------------------------------
# Basic lifecycle
# ---------------------------------------------------------------------------


def test_create_returns_queued_job() -> None:
    store = JobStore()
    job = store.create(filters={"all": True})
    assert isinstance(job, Job)
    assert job.state == "queued"
    assert job.result_json is None
    assert job.completed_at is None
    assert job.filters == {"all": True}
    # UUID4 hex is 32 chars
    assert len(job.id) == 32


def test_get_returns_none_for_unknown_id() -> None:
    assert JobStore().get("not-a-real-id") is None


def test_mark_running_transitions_state() -> None:
    store = JobStore()
    job = store.create(filters={})
    store.mark_running(job.id)
    got = store.get(job.id)
    assert got is not None and got.state == "running"


def test_update_progress_replaces_progress_snapshot() -> None:
    store = JobStore()
    job = store.create(filters={})
    p1 = ScanProgress(phase="classifying", total_producers=3, completed_producers=1)
    store.update_progress(job.id, p1)
    got = store.get(job.id)
    assert got is not None and got.progress == p1


def test_mark_done_records_result_and_completed_at() -> None:
    store = JobStore()
    job = store.create(filters={})
    payload = [{"status": "LISTED", "original_symbol": "BTC/USDT"}]
    store.mark_done(job.id, result_json=payload)
    got = store.get(job.id)
    assert got is not None
    assert got.state == "done"
    assert got.result_json == payload
    assert got.completed_at is not None


def test_mark_failed_records_error_and_completed_at() -> None:
    store = JobStore()
    job = store.create(filters={})
    store.mark_failed(job.id, error="DBError: boom")
    got = store.get(job.id)
    assert got is not None
    assert got.state == "failed"
    assert got.error == "DBError: boom"
    assert got.completed_at is not None


def test_update_on_unknown_job_is_noop() -> None:
    # Race case: worker updates progress after the sweeper evicted the job.
    # Must not raise; the caller is a background thread and there's
    # nothing user-visible left to update.
    store = JobStore()
    store.update_progress("ghost-id", ScanProgress(phase="classifying"))
    store.mark_done("ghost-id", result_json=[])
    store.mark_failed("ghost-id", error="x")
    # No exceptions == pass.


# ---------------------------------------------------------------------------
# TTL sweep
# ---------------------------------------------------------------------------


def test_sweep_evicts_completed_jobs_older_than_ttl() -> None:
    store = JobStore(job_ttl_seconds=60.0)
    old = store.create(filters={"a": 1})
    fresh = store.create(filters={"a": 2})
    active = store.create(filters={"a": 3})

    # Force `old` to have completed 2 hours ago; `fresh` just now; `active`
    # still queued.
    store.mark_done(old.id, result_json=[])
    store.mark_done(fresh.id, result_json=[])
    # Retroactively rewrite the completed_at for `old`.
    with store._lock:  # type: ignore[attr-defined]
        old_rec = store._jobs[old.id]  # type: ignore[attr-defined]
        store._jobs[old.id] = old_rec.__class__(  # type: ignore[attr-defined]
            **{**old_rec.__dict__, "completed_at": datetime.now(timezone.utc) - timedelta(hours=2)}
        )

    evicted = store.sweep()
    assert evicted == 1
    assert store.get(old.id) is None
    assert store.get(fresh.id) is not None
    assert store.get(active.id) is not None


def test_sweep_preserves_queued_and_running_jobs_indefinitely() -> None:
    # TTL is a *completion* TTL — nothing sweeps live jobs.
    store = JobStore(job_ttl_seconds=0.001)
    queued = store.create(filters={})
    running = store.create(filters={})
    store.mark_running(running.id)
    evicted = store.sweep()
    assert evicted == 0
    assert store.get(queued.id) is not None
    assert store.get(running.id) is not None


# ---------------------------------------------------------------------------
# Counters
# ---------------------------------------------------------------------------


def test_active_count_and_recent_count_reflect_state() -> None:
    store = JobStore()
    a = store.create(filters={})
    b = store.create(filters={})
    store.create(filters={})  # third job, still queued — contributes to active_count
    store.mark_running(a.id)
    store.mark_done(b.id, result_json=[])
    assert store.recent_count() == 3
    # third job still queued, `a` running -> both active. `b` done.
    assert store.active_count() == 2


# ---------------------------------------------------------------------------
# Thread safety smoke test
# ---------------------------------------------------------------------------


def test_concurrent_update_progress_is_safe() -> None:
    store = JobStore()
    job = store.create(filters={})
    N = 200

    def hammer() -> None:
        for i in range(N):
            store.update_progress(
                job.id,
                ScanProgress(
                    phase="classifying",
                    total_producers=10,
                    completed_producers=i % 10,
                ),
            )

    threads = [threading.Thread(target=hammer) for _ in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    got = store.get(job.id)
    assert got is not None
    # Doesn't matter which final value we land on; we're checking that
    # no exception fired and the store didn't corrupt state.
    assert got.progress.phase == "classifying"
