"""Every Celery task runs under a wall-clock ceiling.

2026-09-24. No task had time_limit or soft_time_limit, so a hung OSRM, LLM or
database call could hold one of the worker's two slots through the nightly
window (ingest 19:30, allocation 20:00, beat push 06:00).
"""
from __future__ import annotations

import importlib

import pytest

from app.workers.celery_app import celery_app


def _app_tasks():
    for module in celery_app.conf.include:
        importlib.import_module(module)
    celery_app.finalize()
    tasks = {name: t for name, t in celery_app.tasks.items() if name.startswith("app.workers.tasks.")}
    assert tasks, "no app tasks registered: the include list or the import changed"
    return tasks


def _limits(task):
    soft = task.soft_time_limit or celery_app.conf.task_soft_time_limit
    hard = task.time_limit or celery_app.conf.task_time_limit
    return soft, hard


@pytest.mark.parametrize("name", sorted(_app_tasks()))
def test_every_task_has_a_soft_and_a_hard_ceiling(name):
    soft, hard = _limits(_app_tasks()[name])
    assert soft and hard, f"{name} has no time limit"
    assert soft < hard, f"{name}: the soft limit must fire before the hard kill"


def test_the_long_tasks_get_their_longer_ceilings():
    tasks = _app_tasks()
    assert _limits(tasks["app.workers.tasks.allocation.run_nightly_allocation"]) == (1800, 2100)
    assert _limits(tasks["app.workers.tasks.model_retraining.run_candidate_training"]) == (3600, 3900)


def test_every_scheduled_task_exists():
    tasks = _app_tasks()
    for entry in celery_app.conf.beat_schedule.values():
        assert entry["task"] in tasks, entry["task"]
