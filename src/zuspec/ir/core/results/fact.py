"""Facts: typed engine results attached to anchors (``docs/results.md``)."""
from __future__ import annotations

import dataclasses as dc
from typing import Any

#: How well an engine's node maps back to the anchor a fact is recorded on.
EXACT, MERGED, DERIVED, PARTIAL, UNMAPPED = "exact", "merged", "derived", "partial", "unmapped"
QUALITIES = (EXACT, MERGED, DERIVED, PARTIAL, UNMAPPED)


class FactError(ValueError):
    """A fact that does not fit its kind's schema."""


@dc.dataclass(frozen=True)
class Fact:
    """One result: *kind* (``sched.stage``, ``xls.node_name``, …) of the node
    *anchor* names (possibly a derived anchor, ``…@reg:2``) is *value*."""
    kind: str
    anchor: str
    value: Any
    quality: str = EXACT
