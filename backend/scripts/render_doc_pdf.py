"""Render a repo HTML document to PDF, using the installed Chrome.

WHY CHROME AND NOT REPORTLAB
----------------------------
scripts/generate_ml_architecture_pdf.py builds its PDF by hand out of reportlab
flowables, which means the document exists twice: once as the thing people read
and once as a pile of Paragraph() calls that has to be kept in step with it. The
two drift, and the reportlab copy is always the stale one.

The documents under docs/ are already HTML (docs/recovery-calibration.html was
the first). Printing that same file is the only way the PDF cannot disagree with
the page — there is one source, and this is a renderer rather than a second
author.

The cost is a Chrome dependency, which is why this fails loudly with the paths it
looked in rather than silently producing nothing. Chrome ships on every machine
this repo is developed on; if that stops being true, `--chrome` takes a path.

WHAT IT DOES NOT DO
-------------------
No JavaScript is required by these documents, and none is relied on here beyond
Chrome's own layout. Web fonts ARE fetched from Google Fonts, so the first run
needs a network — without one Chrome falls back to the local stack and the PDF
is still correct, just set in different type.

USAGE
    python scripts/render_doc_pdf.py docs/case-allocation.html
    python scripts/render_doc_pdf.py docs/case-allocation.html --out /tmp/x.pdf
"""
from __future__ import annotations

import argparse
import os
import pathlib
import shutil
import subprocess
import sys
import tempfile
import time

# Ordered by how likely each is to be the one that exists. shutil.which comes
# first so a PATH entry or a --chrome override always wins over a guess.
_CHROME_CANDIDATES = (
    r"C:\Program Files\Google\Chrome\Application\chrome.exe",
    r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
    "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
    "/usr/bin/google-chrome",
    "/usr/bin/chromium",
    "/usr/bin/chromium-browser",
)


def find_chrome(explicit: str | None = None) -> str:
    if explicit:
        if not pathlib.Path(explicit).exists():
            raise SystemExit(f"--chrome path does not exist: {explicit}")
        return explicit
    for name in ("chrome", "google-chrome", "chromium", "msedge"):
        found = shutil.which(name)
        if found:
            return found
    for path in _CHROME_CANDIDATES:
        if pathlib.Path(path).exists():
            return path
    raise SystemExit(
        "Could not find Chrome. Looked on PATH for chrome/google-chrome/"
        "chromium/msedge, and at:\n  " + "\n  ".join(_CHROME_CANDIDATES) +
        "\nPass one explicitly with --chrome <path>."
    )


def render(html_path: pathlib.Path, pdf_path: pathlib.Path, chrome: str,
           wait_ms: int = 1200) -> None:
    # Chrome refuses to write into a directory it does not own on some
    # installs, and it will not overwrite silently either — so it renders to a
    # scratch profile dir and the result is moved into place.
    with tempfile.TemporaryDirectory(prefix="tiq-pdf-") as tmp:
        cmd = [
            chrome,
            "--headless=new",
            "--disable-gpu",
            "--no-sandbox",
            f"--user-data-dir={tmp}",
            # Fonts arrive over the network; without a delay Chrome can print
            # before they land and the PDF silently uses fallback type.
            f"--virtual-time-budget={wait_ms}",
            "--no-pdf-header-footer",
            # ABSOLUTE. Chrome resolves this against its own working
            # directory, not the shell's, so a relative path fails with a
            # bare "cannot find the path" and exit code 0.
            f"--print-to-pdf={pdf_path.resolve()}",
            html_path.resolve().as_uri(),
        ]
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=120)

    if not pdf_path.exists():
        raise SystemExit(
            f"Chrome exited {proc.returncode} without writing a PDF.\n"
            f"stderr:\n{(proc.stderr or '(empty)')[:2000]}"
        )


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("html", help="path to the HTML document to render")
    ap.add_argument("--out", default=None,
                    help="output PDF path (default: same name, .pdf)")
    ap.add_argument("--chrome", default=None, help="explicit Chrome/Edge binary")
    ap.add_argument("--wait-ms", type=int, default=1200,
                    help="virtual time budget, so web fonts finish loading")
    args = ap.parse_args()

    html_path = pathlib.Path(args.html)
    if not html_path.exists():
        raise SystemExit(f"no such file: {html_path}")
    pdf_path = pathlib.Path(args.out) if args.out else html_path.with_suffix(".pdf")
    pdf_path.parent.mkdir(parents=True, exist_ok=True)
    if pdf_path.exists():
        pdf_path.unlink()

    chrome = find_chrome(args.chrome)
    print(f"chrome : {chrome}")
    print(f"source : {html_path}")
    started = time.time()
    render(html_path, pdf_path, chrome, args.wait_ms)
    size_kb = pdf_path.stat().st_size / 1024
    print(f"wrote  : {pdf_path}  ({size_kb:,.0f} KB, {time.time() - started:.1f}s)")


if __name__ == "__main__":
    main()
