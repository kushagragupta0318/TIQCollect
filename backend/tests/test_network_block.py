"""The suite's network block (tests/conftest.py) and its one opening for the
Postgres suite (B19): exactly the host AND port in TIQ_PG_TEST_URL.
(bb's review of ec9f2c6: the first allowlist was host-only.)"""
from __future__ import annotations

import socket

import pytest

from tests import conftest as c


def test_the_target_is_host_and_port():
    assert c.pg_test_target("postgresql+psycopg2://u:p@pg.internal:5433/db") == ("pg.internal", 5433)
    assert c.pg_test_target("postgresql+psycopg2://u:p@pg.internal/db") == ("pg.internal", 5432)


@pytest.mark.parametrize("url", [None, "", "${TIQ_PG_TEST_URL}", "postgresql://u:p@${PGHOST}:5432/db"])
def test_an_unset_or_unresolved_url_opens_nothing(url):
    assert c.pg_test_target(url) is None
    assert c.allowed_pg_address(("10.0.0.5", 5432), c.pg_test_target(url), frozenset({"10.0.0.5"})) is False


def test_the_same_host_on_another_port_is_refused():
    target, addrs = ("pg.internal", 5432), frozenset({"pg.internal", "10.0.0.5"})
    assert c.allowed_pg_address(("10.0.0.5", 5432), target, addrs) is True
    for port in (16379, 19000, 8400):                 # Redis, MinIO, the API on the same machine
        assert c.allowed_pg_address(("10.0.0.5", port), target, addrs) is False
    assert c.allowed_pg_address(("10.0.0.6", 5432), target, addrs) is False


def test_a_non_local_connect_is_still_blocked_in_a_real_socket():
    s = socket.socket()
    try:
        with pytest.raises(ConnectionRefusedError, match="network access blocked"):
            s.connect(("203.0.113.9", 5432))          # TEST-NET-3: never routable
    finally:
        s.close()
