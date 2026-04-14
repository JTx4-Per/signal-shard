"""Verify the scheduler registers all four background jobs on build."""

from __future__ import annotations

from fastapi import FastAPI

from email_intel.scheduler import build_scheduler


def test_scheduler_registers_four_jobs() -> None:
    app = FastAPI()
    app.state.session_factory = None
    app.state.graph = None
    app.state.settings = None

    scheduler = build_scheduler(app)
    try:
        job_ids = {j.id for j in scheduler.get_jobs()}
        assert job_ids == {
            "subscription_renewal",
            "delta_fallback_poll",
            "defer_sweeper",
            "dead_letter_health",
        }
    finally:
        if scheduler.running:
            scheduler.shutdown(wait=False)


def test_scheduler_intervals_are_correct() -> None:
    app = FastAPI()
    app.state.session_factory = None
    app.state.graph = None
    app.state.settings = None

    scheduler = build_scheduler(app)
    try:
        jobs = {j.id: j for j in scheduler.get_jobs()}

        # APScheduler IntervalTrigger exposes .interval (timedelta).
        assert jobs["subscription_renewal"].trigger.interval.total_seconds() == 2 * 3600
        assert jobs["delta_fallback_poll"].trigger.interval.total_seconds() == 10 * 60
        assert jobs["defer_sweeper"].trigger.interval.total_seconds() == 1 * 60
        assert jobs["dead_letter_health"].trigger.interval.total_seconds() == 30 * 60
    finally:
        if scheduler.running:
            scheduler.shutdown(wait=False)
