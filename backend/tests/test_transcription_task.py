"""The background transcription task never pays twice for one recording.

2026-09-24 (the business lead's BL-2). A voice note was transcribed while the
agent waited and again by this task, and the task itself could be queued twice
for one visit. It now skips any recording whose transcript is already stored.
Database-free: a fake session and fake storage/transcription seams.
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from app.workers.tasks import transcription as task_module


class _Session:
    def __init__(self, visit):
        self.visit, self.commits = visit, 0

    def query(self, *_):
        return self

    def filter(self, *_):
        return self

    def first(self):
        return self.visit

    def commit(self):
        self.commits += 1

    def close(self):
        pass


@pytest.fixture
def calls(monkeypatch):
    seen = []
    monkeypatch.setattr("app.core.storage.download_bytes", lambda key: key.encode())
    monkeypatch.setattr("app.core.transcription.transcribe",
                        lambda audio, name: seen.append(name) or f"text of {audio.decode()}")
    return seen


def _run(monkeypatch, visit, recorder="both"):
    session = _Session(visit)
    monkeypatch.setattr("app.core.database.SessionLocal", lambda: session)
    return task_module.transcribe_visit_recording_task.run("visit-1", recorder), session


def _visit(**kw):
    base = dict(agent_recording_key=None, borrower_recording_key=None,
                agent_recording_transcript=None, borrower_recording_transcript=None)
    return SimpleNamespace(**{**base, **kw})


def test_both_recordings_are_transcribed_once_when_neither_has_text(monkeypatch, calls):
    v = _visit(agent_recording_key="a.webm", borrower_recording_key="b.webm")
    result, session = _run(monkeypatch, v)
    assert calls == ["agent.mp4", "borrower.mp4"]
    assert v.agent_recording_transcript == "text of a.webm"
    assert v.borrower_recording_transcript == "text of b.webm"
    assert session.commits == 1 and "skipped" not in result


def test_a_recording_that_already_has_a_transcript_is_not_sent_again(monkeypatch, calls):
    v = _visit(agent_recording_key="a.webm", agent_recording_transcript="already here",
               borrower_recording_key="b.webm")
    result, _ = _run(monkeypatch, v)
    assert calls == ["borrower.mp4"]
    assert v.agent_recording_transcript == "already here"
    assert result["skipped"] == ["agent"]


def test_queuing_the_same_visit_twice_costs_one_transcription_per_recording(monkeypatch, calls):
    v = _visit(agent_recording_key="a.webm")
    _run(monkeypatch, v, recorder="agent")
    _run(monkeypatch, v, recorder="agent")
    assert calls == ["agent.mp4"]


def test_the_recorder_argument_still_limits_what_is_transcribed(monkeypatch, calls):
    v = _visit(agent_recording_key="a.webm", borrower_recording_key="b.webm")
    _run(monkeypatch, v, recorder="borrower")
    assert calls == ["borrower.mp4"]
    assert v.agent_recording_transcript is None
