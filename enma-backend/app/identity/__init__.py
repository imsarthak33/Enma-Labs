"""Identity resolution package.

Phase 6: the 5-stage cascade that maps an inbound document to the
client it belongs to. See :mod:`app.identity.resolver`.
"""

from app.identity.resolver import (
    ResolutionConfidence,
    ResolutionOutcome,
    ResolutionStage,
    resolve_identity,
)

__all__ = [
    "ResolutionConfidence",
    "ResolutionOutcome",
    "ResolutionStage",
    "resolve_identity",
]
