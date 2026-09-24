# ─── CHANGELOG (prototype → product) ───
# New file, 2026-09-24 (hotfix/spa-containment). Covers app/main.py's
# _spa_target, the decision the SPA catch-all makes for an unmatched path:
# static file serving could read outside the static root, and now anything
# that resolves outside static/ is the normal 404. Pure function calls against
# a tmp_path standing in for static/ — no built frontend, no running app.
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
    "../../../outside/file.txt",            # deeper than the filesystem allows
    "..",                                   # the parent directory itself
])
def test_parent_segments_out_of_static_are_refused(static_dir, path):
    """Percent-encoded parent segments reach this function already decoded,
    so these literal forms are what an encoded request becomes."""
    assert _spa_target(path, static_dir) is None


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
