# ─── CHANGELOG (prototype → product) ───
# New file, 2026-09-24. Covers app/main.py's `_spa_target` — the pure helper
# the SPA catch-all route calls to decide what to serve for an unmatched path.
# Pulled out specifically so this is testable without a built frontend (there
# is no `static/` directory in this repo's test environment) and without
# standing up the FastAPI app: every case here is a plain function call
# against a tmp_path standing in for `static/`.
#
# Covers the 2026-09-24 fix: a missing *.js path (case-insensitive) now 404s
# instead of being served index.html with a 200 — see app/main.py's own
# CHANGELOG note at `_spa_target` for why (a stale hashed chunk after a
# deploy, or /sw.js after a service-worker rollback, getting HTML where it
# expected JavaScript). Every other unknown path is unchanged.
#
# And the containment fix beside it (same as hotfix/spa-containment): static
# file serving could read outside the static root; anything that resolves
# outside static/ is now a 404.
import os

from app.main import _spa_target


def _touch(path: str, content: str = "") -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write(content)


def test_api_prefix_is_refused(tmp_path):
    assert _spa_target("api/x", str(tmp_path)) is None


def test_ws_prefix_is_refused(tmp_path):
    assert _spa_target("ws/x", str(tmp_path)) is None


def test_missing_hashed_js_chunk_is_a_404(tmp_path):
    """The defect this fix closes: a stale client asking for a chunk that no
    longer exists after a deploy used to get index.html with a 200."""
    assert _spa_target("assets/old-abc123.js", str(tmp_path)) is None


def test_missing_service_worker_is_a_404(tmp_path):
    """A rolled-back PWA still has old clients polling /sw.js. It must read as
    gone, not as a page of HTML the browser tries to execute as a script."""
    assert _spa_target("sw.js", str(tmp_path)) is None


def test_missing_js_is_refused_case_insensitively(tmp_path):
    assert _spa_target("assets/old-abc123.JS", str(tmp_path)) is None


def test_an_existing_js_file_is_served_regardless_of_extension(tmp_path):
    """The isfile check wins over the extension check: a real file is always
    served, .js included."""
    sw = os.path.join(str(tmp_path), "sw.js")
    _touch(sw, "// service worker")
    assert _spa_target("sw.js", str(tmp_path)) == sw


def test_a_deep_link_falls_back_to_index_html(tmp_path):
    """Client-side routing: no server-side /agent/home route exists, so a
    hard refresh or a pasted link must still get the SPA shell."""
    index = os.path.join(str(tmp_path), "index.html")
    _touch(index, "<html>shell</html>")
    assert _spa_target("agent/home", str(tmp_path)) == index


def test_a_missing_non_js_asset_still_falls_back_to_index_html(tmp_path):
    """Unchanged behaviour: only *.js gets the new 404 treatment. A missing
    .css (or any other extension) is still handed the SPA shell, exactly as
    before this fix."""
    index = os.path.join(str(tmp_path), "index.html")
    _touch(index, "<html>shell</html>")
    assert _spa_target("assets/app.css", str(tmp_path)) == index


def _static_beside_a_secret(tmp_path):
    secret = os.path.join(str(tmp_path), "secret.txt")
    _touch(secret, "TOP SECRET")
    static_dir = os.path.join(str(tmp_path), "static")
    _touch(os.path.join(static_dir, "index.html"), "<html>shell</html>")
    return static_dir, secret


def test_dot_dot_out_of_static_is_refused(tmp_path):
    """Parent segments, as the server decodes them before routing."""
    static_dir, _ = _static_beside_a_secret(tmp_path)
    assert _spa_target("../secret.txt", static_dir) is None
    assert _spa_target("assets/../../secret.txt", static_dir) is None
    assert _spa_target("../../../outside/file.txt", static_dir) is None


def test_an_absolute_path_is_refused(tmp_path):
    """os.path.join discards the root when the second part is absolute."""
    static_dir, secret = _static_beside_a_secret(tmp_path)
    assert _spa_target(secret, static_dir) is None
    assert _spa_target("/outside/file.txt", static_dir) is None


def test_a_symlink_pointing_out_of_static_is_refused(tmp_path):
    static_dir, secret = _static_beside_a_secret(tmp_path)
    try:
        os.symlink(secret, os.path.join(static_dir, "innocent.txt"))
    except (OSError, NotImplementedError):
        import pytest
        pytest.skip("symlinks unavailable on this filesystem")
    assert _spa_target("innocent.txt", static_dir) is None


def test_dot_dot_that_stays_inside_static_is_still_served(tmp_path):
    """Containment, not a blanket ban on "..": a path that resolves inside
    static/ is an ordinary request."""
    static_dir, _ = _static_beside_a_secret(tmp_path)
    _touch(os.path.join(static_dir, "favicon.svg"), "<svg/>")
    target = _spa_target("assets/../favicon.svg", static_dir)
    assert target == os.path.realpath(os.path.join(static_dir, "favicon.svg"))
