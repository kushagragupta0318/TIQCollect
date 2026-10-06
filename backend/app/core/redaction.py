"""Strip borrower identifiers out of anything on its way to a model (F02).

    clean, counts = redact(payload, extra_values=[customer.full_name])

Two passes, because neither alone is enough. Patterns catch identifiers that
look like themselves — a PAN, an Aadhaar, a mobile number. Keys catch the ones
that do not: a name, a street address, a latitude. Whatever the caller already
knows to be sensitive goes in `extra_values` and is matched literally.

It is best-effort masking, not a guarantee, and it is deliberately eager: a
false positive costs a model some context, a false negative sends a borrower's
identity to a third party. Callers must not describe its output as anonymous.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Iterable, Mapping

#: What replaces a match. Stable and readable, so a model can still reason
#: about "the borrower's [phone]" instead of seeing a hole.
TOKENS = {
    "pan": "[pan]",
    "aadhaar": "[aadhaar]",
    "ifsc": "[ifsc]",
    "email": "[email]",
    "upi": "[upi-id]",
    "phone": "[phone]",
    "account": "[account-number]",
    "name": "[name]",
    "address": "[address]",
    "coordinate": "[coordinate]",
    "value": "[redacted]",
}

# Longest and most distinctive first: an Aadhaar must not be read as a phone
# number followed by two digits, and a VPA must not be split into a phone.
_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("pan", re.compile(r"\b[A-Z]{5}[0-9]{4}[A-Z]\b")),
    ("ifsc", re.compile(r"\b[A-Z]{4}0[A-Z0-9]{6}\b")),
    ("email", re.compile(r"\b[\w.%+-]+@[\w-]+\.[A-Za-z]{2,}\b")),
    ("upi", re.compile(r"\b[\w.-]{2,}@[A-Za-z]{2,}\b")),
    ("aadhaar", re.compile(r"(?<![\d-])\d{4}[ -]?\d{4}[ -]?\d{4}(?![\d-])")),
    ("account", re.compile(r"(?<![\d-])\d{13,19}(?![\d-])")),
    ("phone", re.compile(r"(?<![\d-])(?:\+?91[ -]?|0)?[6-9]\d{9}(?![\d-])")),
)

#: Field names whose VALUE is sensitive whatever it looks like. Matched on the
#: last path segment, case-insensitively, as a substring: `customer_full_name`
#: and `full_name` both hit "full_name".
_KEY_RULES: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("name", ("full_name", "customer_name", "borrower_name", "contact_name", "father_name",
              "guardian_name", "person_met_name", "employer_name")),
    ("address", ("address", "address_line1", "address_line2", "street", "landmark", "pincode",
                 "postal_code")),
    ("phone", ("phone", "phone_primary", "phone_secondary", "mobile", "alternate_phone", "whatsapp")),
    ("email", ("email",)),
    ("pan", ("pan", "pan_masked", "pan_number")),
    ("aadhaar", ("aadhaar", "aadhaar_masked", "aadhaar_number", "uid")),
    ("coordinate", ("latitude", "longitude", "lat", "lon", "lng", "gps_lat", "gps_lon",
                    "check_in_latitude", "check_in_longitude", "base_latitude", "base_longitude")),
    ("account", ("account_number", "loan_account_number", "bank_account", "card_number",
                 "upi_vpa", "vpa", "ifsc")),
)

#: Below this a literal from `extra_values` is too short to match safely: a
#: two-letter name would blank half the text it appears in.
MIN_LITERAL = 3


def _key_token(key: str) -> str | None:
    k = key.strip().lower()
    for token, names in _KEY_RULES:
        if any(name == k or k.endswith("_" + name) for name in names):
            return TOKENS[token]
    return None


def _literal_pattern(values: Iterable[str]) -> re.Pattern[str] | None:
    """One case-insensitive alternation of the caller's own sensitive strings,
    longest first so "Farhan Siddiqui" wins over "Farhan"."""
    literals = sorted({v.strip() for v in values if isinstance(v, str) and len(v.strip()) >= MIN_LITERAL},
                      key=len, reverse=True)
    if not literals:
        return None
    return re.compile("|".join(re.escape(v) for v in literals), re.IGNORECASE)


def redact_text(text: str, *, extra: re.Pattern[str] | None = None) -> tuple[str, dict[str, int]]:
    """The text with every identifier replaced, and a count per kind. The
    counts exist so a run can record THAT it redacted without logging what."""
    counts: dict[str, int] = {}
    out = text
    if extra is not None:
        out, n = extra.subn(TOKENS["value"], out)
        if n:
            counts["value"] = n
    for token, pattern in _PATTERNS:
        out, n = pattern.subn(TOKENS[token], out)
        if n:
            counts[token] = counts.get(token, 0) + n
    return out, counts


def _merge(into: dict[str, int], more: Mapping[str, int]) -> None:
    for k, v in more.items():
        into[k] = into.get(k, 0) + v


def redact(value: Any, *, extra_values: Iterable[str] = (), _extra: re.Pattern[str] | None = None,
           _counts: dict[str, int] | None = None) -> Any | tuple[Any, dict[str, int]]:
    """Redact a string, or every string inside a dict/list, by pattern and by
    key. Returns `(clean, counts)` at the top level; the private arguments are
    the recursion's. Numbers under a sensitive key are masked too — a latitude
    is not safer for being a float."""
    top = _counts is None
    counts: dict[str, int] = {} if top else _counts  # type: ignore[assignment]
    extra = _literal_pattern(extra_values) if top else _extra

    def walk(node: Any, key_token: str | None) -> Any:
        if isinstance(node, str):
            if key_token is not None:
                # Already masked, or nothing to mask: replacing it again would
                # report a redaction that removed nothing.
                if node == "" or node == key_token:
                    return node
                _merge(counts, {key_token.strip("[]"): 1})
                return key_token
            cleaned, found = redact_text(node, extra=extra)
            _merge(counts, found)
            return cleaned
        if isinstance(node, (int, float)) and not isinstance(node, bool) and key_token is not None:
            _merge(counts, {key_token.strip("[]"): 1})
            return key_token
        if isinstance(node, Mapping):
            return {k: walk(v, _key_token(str(k))) for k, v in node.items()}
        if isinstance(node, (list, tuple)):
            # A list inherits its key's verdict: phones[] is still phones.
            kind = type(node)
            return kind(walk(v, key_token) for v in node)
        return node

    clean = walk(value, None)
    return (clean, counts) if top else clean


# ── Reversible form, for prompts ─────────────────────────────────────────────
# A brief that says "greet [name]" is useless to an agent at the door, so a
# prompt is PSEUDONYMISED rather than blanked: each value becomes a stable
# token, and the tokens are put back in the model's answer. The provider sees
# no identifier; the reader sees the borrower's name.

@dataclass(frozen=True)
class Pseudonymised:
    prompt: str
    system: str | None
    #: token -> the original it stands for. Never logged, never persisted.
    mapping: dict[str, str]
    counts: dict[str, int]

    @property
    def changed(self) -> bool:
        return bool(self.mapping)


def pseudonymise(prompt: str, system: str | None = None, *, names: Iterable[str] = (),
                 values: Iterable[str] = ()) -> Pseudonymised:
    """Replace identifiers in `prompt`/`system` with `[kind-n]` tokens.

    `names` are person names the caller embedded (no pattern can find those);
    `values` is anything else it knows to be sensitive. The same original
    always gets the same token within one call, so the model reads a
    consistent story."""
    seen: dict[tuple[str, str], str] = {}
    mapping: dict[str, str] = {}
    counts: dict[str, int] = {}

    def take(kind: str, original: str) -> str:
        key = (kind, original)
        token = seen.get(key)
        if token is None:
            token = f"[{kind}-{sum(1 for k, _ in seen if k == kind) + 1}]"
            seen[key] = token
            mapping[token] = original
        counts[kind] = counts.get(kind, 0) + 1
        return token

    literals: list[tuple[str, re.Pattern[str]]] = []
    for kind, given in (("name", names), ("value", values)):
        pattern = _literal_pattern(given)
        if pattern is not None:
            literals.append((kind, pattern))

    def apply(text: str | None) -> str | None:
        if not text:
            return text
        out = text
        # Literals first: a name may contain something a pattern would claim.
        for kind, pattern in literals:
            out = pattern.sub(lambda m, k=kind: take(k, m.group(0)), out)
        for kind, pattern in _PATTERNS:
            out = pattern.sub(lambda m, k=kind: take(k, m.group(0)), out)
        return out

    return Pseudonymised(prompt=apply(prompt) or "", system=apply(system),
                         mapping=mapping, counts=counts)


def restore(value: Any, mapping: Mapping[str, str]) -> Any:
    """Put the originals back into a model's answer. Case-insensitive on the
    token, because a model will sometimes write `[Name-1]`."""
    if not mapping:
        return value
    token_re = re.compile("|".join(re.escape(t) for t in sorted(mapping, key=len, reverse=True)),
                          re.IGNORECASE)
    lower = {t.lower(): original for t, original in mapping.items()}

    def walk(node: Any) -> Any:
        if isinstance(node, str):
            return token_re.sub(lambda m: lower[m.group(0).lower()], node)
        if isinstance(node, Mapping):
            return {k: walk(v) for k, v in node.items()}
        if isinstance(node, (list, tuple)):
            return type(node)(walk(v) for v in node)
        return node

    return walk(value)
