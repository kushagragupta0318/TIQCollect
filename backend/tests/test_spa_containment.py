# ─── CHANGELOG (prototype → product) ───
# New file, 2026-09-24 (hotfix/spa-containment). Covers app/main.py's
# _spa_target, the decision the SPA catch-all makes for an unmatched path:
# static file serving could read outside the static root, and now anything
# that resolves outside static/ is the normal 404. Mostly pure function calls
# against a tmp_path standing in for static/ — no built frontend — plus one
# test through HTTP on the real route (_mount_spa over the same directory).
import os

import pytest

from app.main import _spa_target


def _touch(path: str, content: str = "") -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write(content)


@pytest.fixture
def static_dir(tmp_path):
    """static/ with an index and one asset, and a file sitting BESIDE it."""
    root = os.path.join(str(tmp_path), "static")
    _touch(os.path.join(root, "index.html"), "<html>shell</html>")
    _touch(os.path.join(root, "assets", "app-abc123.js"), "// chunk")
    _touch(os.path.join(str(tmp_path), "outside.txt"), "not for the web")
    return root


def _index(root: str) -> str:
    return os.path.join(os.path.realpath(root), "index.html")


# ── refused ──────────────────────────────────────────────────────────────────
@pytest.mark.parametrize("path", [
    "../outside.txt",                       # literal parent segment
    "assets/../../outside.txt",             # parent segments after a real dir
    "../../../outside/file.txt",            # more parent segments than the path has
    "..",                                   # the parent directory itself
])
def test_parent_segments_out_of_static_are_refused(static_dir, path):
    """Percent-encoded parent segments reach this function already decoded,
    so these literal forms are what an encoded request becomes."""
    assert _spa_target(path, static_dir) is None


def test_a_sibling_directory_sharing_the_prefix_is_refused(static_dir, tmp_path):
    """static-evil/ starts with the same characters as static/; containment
    must compare whole path segments, not string prefixes."""
    _touch(os.path.join(str(tmp_path), "static-evil", "secret.txt"), "not for the web")
    assert _spa_target("../static-evil/secret.txt", static_dir) is None


@pytest.mark.parametrize("path", ["\x00", "a\x00/../index.html", "index.html\x00"])
def test_a_nul_byte_is_refused_not_an_error(static_dir, path):
    """realpath raises ValueError on a NUL byte on Linux; the answer must be
    the normal 404, never a 500. (Runs in the Linux test container.)"""
    assert _spa_target(path, static_dir) is None


def test_a_static_dir_that_cannot_be_resolved_is_a_404_not_an_error():
    """The root's own realpath sits inside the same guard as the candidate's.
    static_dir is server configuration, so this is robustness, not exposure."""
    assert _spa_target("index.html", "bad\x00dir") is None


def test_an_absolute_path_is_refused(static_dir, tmp_path):
    """os.path.join discards the root when the second part is absolute."""
    assert _spa_target(os.path.join(str(tmp_path), "outside.txt"), static_dir) is None
    assert _spa_target("/outside/file.txt", static_dir) is None


def test_a_symlink_pointing_out_of_static_is_refused(static_dir, tmp_path):
    try:
        os.symlink(os.path.join(str(tmp_path), "outside.txt"),
                   os.path.join(static_dir, "innocent.txt"))
    except (OSError, NotImplementedError):
        pytest.skip("symlinks unavailable on this filesystem")
    assert _spa_target("innocent.txt", static_dir) is None


@pytest.mark.parametrize("path", ["api/v1/nope", "ws/x"])
def test_api_and_ws_paths_are_still_refused(static_dir, path):
    assert _spa_target(path, static_dir) is None


# ── still served ─────────────────────────────────────────────────────────────
def test_a_real_asset_is_served(static_dir):
    assert _spa_target("assets/app-abc123.js", static_dir) == os.path.realpath(
        os.path.join(static_dir, "assets", "app-abc123.js"))


def test_a_parent_segment_that_stays_inside_static_is_served(static_dir):
    """Containment, not a ban on "..": a path resolving inside static/ is fine."""
    assert _spa_target("assets/../index.html", static_dir) == _index(static_dir)


@pytest.mark.parametrize("path", ["", "agent/home", "manager/cases/42", "login"])
def test_deep_spa_routes_get_the_shell(static_dir, path):
    assert _spa_target(path, static_dir) == _index(static_dir)


# ── through HTTP, on the real route ──────────────────────────────────────────
def test_the_mounted_route_answers_over_http(static_dir):
    """_mount_spa on a fresh app over the temporary static/: the same route
    main.py mounts in production, exercised as requests rather than calls."""
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from app.main import _mount_spa

    app = FastAPI()
    _mount_spa(app, static_dir)
    client = TestClient(app)

    assert client.get("/%2e%2e/outside.txt").status_code == 404
    assert client.get("/%2e%2e/%2e%2e/outside/file.txt").status_code == 404
    assert client.get("/a%00b").status_code == 404
    assert client.get("/api/v1/nope").status_code == 404

    shell = client.get("/agent/home")
    assert shell.status_code == 200 and "shell" in shell.text
    asset = client.get("/assets/app-abc123.js")
    assert asset.status_code == 200 and "chunk" in asset.text
