"""Operator CLIs and the ingestion engine.

A package marker, not a module. `scripts/` was an implicit namespace package,
which pytest could import (it puts rootdir on sys.path) but the Celery worker
could not: `from scripts.ingest_daily import run_ingestion` inside
workers/tasks/feed_ingestion raised ModuleNotFoundError at runtime, so every
uploaded feed retried three times and was rejected. Found by an end-to-end run
against the real worker, not by the test suite — the suite imports the same
module successfully under pytest's own path handling.
"""
