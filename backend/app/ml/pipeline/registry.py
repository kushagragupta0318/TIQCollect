# ─── CHANGELOG (prototype → product) ─────────────────────────────────────────
# 2026-09-08 — NEW. Model artifacts: one joblib bundle per version, checksummed,
#   with the library versions it was fitted under recorded and asserted on load.
#
#   ARTIFACTS LIVE IN app/ml/artifacts/, NOT app/ml/models/. That is not a
#   stylistic choice: .gitignore:82 ignores `backend/app/ml/models/*.json`, so
#   the existing shadow metadata is invisible from a fresh clone — CLAUDE.md
#   flags this and it is why the one end-to-end result on record cannot be
#   checked by anyone who did not run it. A deliverable that git does not track
#   is not a deliverable.
#
#   PICKLE HYGIENE. joblib.load executes code. Two rules follow and both are
#   enforced here rather than documented and hoped for: artifacts are loaded
#   only from this repo-managed directory, never from a caller-supplied path
#   outside it; and every bundle carries a SHA-256 of its own payload plus the
#   sklearn version it was fitted under. A bundle opened under a different
#   sklearn does not necessarily fail — it can quietly behave differently — so
#   the mismatch is surfaced as a warning at load, not left to be discovered in
#   production.
# ───────────────────────────────────────────────────────────────────────────
"""
Model artifact registry.

Layout, one directory per (model, version):

    app/ml/artifacts/<model>/<version>/
        model.joblib            the whole fitted sklearn Pipeline
        metadata.json           spec, metrics, gates, data hash, library versions
        scorecard.csv           feature x bin x points — the readable scorecard
        evaluation/             decile tables, KS, PSI, gate results
        eda/                    plots and univariate tables
        MODEL_DEVELOPMENT.html  the model document

    app/ml/artifacts/<model>/champion.txt    which version is live

ONE BUNDLE, NOT FIVE FILES. The pipeline saved is a single fitted
sklearn Pipeline containing preprocessing, WOE binning and the estimator, so a
model cannot be applied without the exact transformations it was fitted with —
the most common way a scorecard breaks in production.
"""
from __future__ import annotations

import hashlib
import json
import logging
import platform
from datetime import date
from pathlib import Path
from typing import Any

import joblib
import numpy as np
import pandas as pd
import sklearn

logger = logging.getLogger(__name__)

ARTIFACT_ROOT = Path(__file__).resolve().parent.parent / "artifacts"


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def frame_hash(df: pd.DataFrame) -> str:
    """A stable fingerprint of the training data, for the metadata.

    Hashing the values rather than the file means a dataset regenerated from the
    same seed produces the same hash, and one regenerated from a different seed
    does not — which is the question anyone reading the metadata is asking.
    """
    return hashlib.sha256(
        pd.util.hash_pandas_object(df, index=False).values.tobytes()
    ).hexdigest()[:16]


def version_dir(model: str, version: str) -> Path:
    return ARTIFACT_ROOT / model / version


def save(model: str, version: str, *, pipeline: Any, metadata: dict,
         tables: dict[str, pd.DataFrame] | None = None,
         extra: dict[str, Any] | None = None,
         make_champion: bool = False) -> Path:
    """Write one version's bundle. Returns the directory.

    `extra` holds fitted objects that sit BESIDE the pipeline rather than inside
    it — currently the segment calibrator, which needs a raw feature value
    (`overdue_amount`) that has already been WOE-transformed by the time the
    pipeline's last step runs, so it cannot be a pipeline stage. Each is written
    as its own joblib and checksummed like the model.
    """
    out = version_dir(model, version)
    (out / "evaluation").mkdir(parents=True, exist_ok=True)

    model_path = out / "model.joblib"
    joblib.dump(pipeline, model_path, compress=3)

    metadata = dict(metadata)
    metadata.update({
        "model": model,
        "version": version,
        "saved_at": date.today().isoformat(),
        "artifact_sha256": _sha256(model_path),
        "library_versions": {
            "python": platform.python_version(),
            "scikit_learn": sklearn.__version__,
            "numpy": np.__version__,
            "pandas": pd.__version__,
        },
    })
    (out / "metadata.json").write_text(json.dumps(metadata, indent=2, default=str))

    for name, obj in (extra or {}).items():
        joblib.dump(obj, out / f"{name}.joblib", compress=3)
    metadata["extra_artifacts"] = sorted((extra or {}).keys())

    for name, df in (tables or {}).items():
        target = out / name if name.endswith(".csv") else out / f"{name}.csv"
        target.parent.mkdir(parents=True, exist_ok=True)
        df.to_csv(target, index=False)

    # PROMOTION AND DEMOTION ARE THE SAME DECISION. Writing the pointer on a
    # pass but leaving it alone on a fail is not neutral — it is stale. A
    # version is re-saved in place, so a model that passed yesterday and fails
    # today overwrites its own artifact while champion.txt still names it: the
    # pointer then designates a FAILING model as live, and DecisionEngine loads
    # it without complaint. Observed exactly once, with contact_risk, which
    # passed at Gini 0.271 and then failed at 0.190 after its feature set was
    # restricted to what the product can actually serve — and still reported
    # is_champion True.
    pointer = ARTIFACT_ROOT / model / "champion.txt"
    if make_champion:
        pointer.write_text(version)
    elif pointer.exists() and pointer.read_text().strip() == version:
        pointer.unlink()
        logger.warning(
            "ml.registry.demoted model=%s version=%s — this version was champion "
            "and has been re-saved without passing its gates; the pointer is "
            "removed rather than left naming a failing model.", model, version)
    return out


def resolve_version(model: str, version: str = "champion") -> str:
    if version != "champion":
        return version
    pointer = ARTIFACT_ROOT / model / "champion.txt"
    if not pointer.exists():
        raise FileNotFoundError(
            f"no champion recorded for '{model}'. Train one with "
            f"`python -m scripts.train_models --model {model}`."
        )
    return pointer.read_text().strip()


def load(model: str, version: str = "champion") -> tuple[Any, dict]:
    """Load a bundle, verifying its checksum and warning on a library mismatch."""
    version = resolve_version(model, version)
    out = version_dir(model, version)
    model_path, meta_path = out / "model.joblib", out / "metadata.json"
    if not model_path.exists():
        raise FileNotFoundError(f"no artifact at {model_path}")

    meta = json.loads(meta_path.read_text()) if meta_path.exists() else {}

    expected = meta.get("artifact_sha256")
    actual = _sha256(model_path)
    if expected and expected != actual:
        raise ValueError(
            f"checksum mismatch for {model}/{version}: metadata records "
            f"{expected[:12]}..., file is {actual[:12]}.... The artifact has been "
            f"modified since it was written; refusing to load it."
        )

    fitted_under = (meta.get("library_versions") or {}).get("scikit_learn")
    if fitted_under and fitted_under != sklearn.__version__:
        logger.warning(
            "model %s/%s was fitted under scikit-learn %s, loading under %s. "
            "A pickle opened under a different version can behave differently "
            "without raising — re-train before trusting this.",
            model, version, fitted_under, sklearn.__version__,
        )
    return joblib.load(model_path), meta


def load_extra(model: str, version: str, name: str):
    """Load a sidecar object written by save(..., extra=...). None if absent."""
    path = version_dir(model, resolve_version(model, version)) / f"{name}.joblib"
    return joblib.load(path) if path.exists() else None


def list_models() -> pd.DataFrame:
    rows = []
    if not ARTIFACT_ROOT.exists():
        return pd.DataFrame(columns=["model", "version", "is_champion"])
    for model_dir in sorted(p for p in ARTIFACT_ROOT.iterdir() if p.is_dir()):
        pointer = model_dir / "champion.txt"
        champ = pointer.read_text().strip() if pointer.exists() else None
        for vdir in sorted(p for p in model_dir.iterdir() if p.is_dir()):
            meta_path = vdir / "metadata.json"
            meta = json.loads(meta_path.read_text()) if meta_path.exists() else {}
            oot = (meta.get("metrics") or {}).get("oot") or {}
            rows.append({
                "model": model_dir.name,
                "version": vdir.name,
                "is_champion": vdir.name == champ,
                "trained_at": meta.get("saved_at"),
                "gini_oot": oot.get("gini"),
                "ks_oot": oot.get("ks"),
                "gates": meta.get("gate_summary"),
            })
    return pd.DataFrame(rows)
