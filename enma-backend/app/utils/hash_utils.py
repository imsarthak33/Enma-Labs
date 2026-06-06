"""Stable hashing helpers — the only place SHA-256 hashing happens.

Used by:

* Filing approval — :func:`sha256_canonical_json` produces the snapshot
  hash that gets locked into ``filing_approvals.approval_hash``.
* Idempotency middleware (already implemented) — payload hashing of
  base64 envelopes for the duplicate-detection table.

We canonicalise JSON before hashing so that semantically equivalent
inputs (different key orders, whitespace) produce the same hash. The
reference is RFC 8785 (JSON Canonicalization Scheme) but our needs are
narrower — we control both producers — so we use
``json.dumps(sort_keys=True, separators=(",", ":"), ensure_ascii=False)``.

No floats are emitted to JSON in our hot paths; if a caller does pass a
float the canonical form will reflect Python's ``repr`` of it. The
filing-snapshot builder converts everything to ``str`` before this
helper sees it.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any

__all__ = [
    "sha256_canonical_json",
    "sha256_hex",
]


def sha256_hex(payload: bytes) -> str:
    """Return the hex SHA-256 of ``payload``."""
    return hashlib.sha256(payload).hexdigest()


def sha256_canonical_json(value: Any) -> str:
    """Canonicalise ``value`` to JSON, hash with SHA-256, return hex.

    The serialiser uses ``sort_keys=True`` and the compact
    ``(",", ":")`` separators so the canonical form is unique. UTF-8
    is the wire encoding.
    """
    canonical = json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        default=str,
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()
