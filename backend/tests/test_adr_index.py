"""The ADR index lists every ADR, and only real ones.

Written after `docs/adr/README.md` was committed as a 0-byte file: an append conflict in
the index was resolved by a script that truncated it, and the check afterwards
(`grep -c '<<<<<<<'` returning 0) passed perfectly on an empty file. Reconstructing it also
showed both sides of that merge had *already* dropped a row each, so the index had been
quietly wrong before the truncation too.

An index nobody can trust is worse than no index, and every lane appends to this one file,
so drift here is structural rather than accidental.
"""
from __future__ import annotations

import re
from pathlib import Path

ADR_DIR = Path(__file__).resolve().parents[2] / "docs" / "adr"
INDEX = ADR_DIR / "README.md"
ADR_FILE = re.compile(r"^(\d{4})-[a-z0-9-]+\.md$")


def _adr_files() -> dict[str, str]:
    return {m.group(1): p.name for p in ADR_DIR.iterdir() if (m := ADR_FILE.match(p.name))}


def _indexed() -> dict[str, str]:
    """Number -> link target, for every `| [NNNN](file.md) |` row."""
    rows = re.findall(r"\|\s*\[(\d{4})\]\(([^)]+)\)", INDEX.read_text(encoding="utf-8"))
    return dict(rows)


def test_the_index_is_not_empty():
    """The failure that prompted this file: truncation passes a marker check."""
    assert INDEX.stat().st_size > 0, f"{INDEX} is empty"
    assert _indexed(), "the index has no ADR rows"


def test_every_adr_has_an_index_row():
    missing = sorted(set(_adr_files()) - set(_indexed()))
    assert not missing, f"ADRs with no row in docs/adr/README.md: {missing}"


def test_every_index_row_has_an_adr_and_the_link_resolves():
    files, indexed = _adr_files(), _indexed()
    dangling = sorted(n for n in indexed if n not in files)
    assert not dangling, f"index rows with no ADR file: {dangling}"
    broken = sorted(f"{n} -> {t}" for n, t in indexed.items() if not (ADR_DIR / t).exists())
    assert not broken, f"index links that do not resolve: {broken}"


def test_the_numbers_are_unique_and_gapless():
    """A reused or skipped number means two ADRs collide, or one was lost. 0012 was
    renumbered from 0010 at integration precisely because two lanes both wrote 0010."""
    numbers = sorted(int(n) for n in _adr_files())
    assert numbers, "no ADR files found"
    duplicates = {n for n in numbers if numbers.count(n) > 1}
    assert not duplicates, f"duplicate ADR numbers: {sorted(duplicates)}"
    expected = list(range(1, max(numbers) + 1))
    assert numbers == expected, f"gap in ADR numbering: missing {sorted(set(expected) - set(numbers))}"
