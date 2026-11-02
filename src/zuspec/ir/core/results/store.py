"""ResultStore: overlays held beside a model, looked up by node or anchor."""
from __future__ import annotations

import dataclasses as dc
from typing import Dict, List, Optional, Union

from ..anchor import AnchorTable, split_qualifier
from ..base import Base
from .fact import Fact
from .overlay import Overlay

#: A fact's standing against the current model.
VALID, STALE, ORPHAN = "valid", "stale", "orphan"


@dc.dataclass(frozen=True)
class FactView:
    fact: Fact
    overlay: Overlay
    #: valid: its scope is unchanged since the engine ran; stale: the scope
    #: changed; orphan: the anchor no longer exists.
    status: str


class ResultStore:
    """Overlays against one model's :class:`AnchorTable`."""

    def __init__(self, anchors: AnchorTable):
        self.anchors = anchors
        self._overlays: Dict[str, Overlay] = {}
        self._index: Dict[str, Dict[str, List[Fact]]] = {}   # overlay id -> base anchor -> facts

    def add(self, overlay: Overlay) -> Overlay:
        self._overlays[overlay.header.overlay_id] = overlay
        self._index.pop(overlay.header.overlay_id, None)
        return overlay

    def load(self, path: str) -> Overlay:
        return self.add(Overlay.load(path))

    def overlays(self) -> List[Overlay]:
        return list(self._overlays.values())

    def get(self, overlay_id: str) -> Overlay:
        return self._overlays[overlay_id]

    def status(self, fact: Fact, overlay: Overlay) -> str:
        base = split_qualifier(fact.anchor)[0]
        if base not in self.anchors:
            return ORPHAN
        scope = self.anchors.scope_of(base)
        then = overlay.header.scope_hashes.get(scope)
        return VALID if then is not None and then == self.anchors.scope_hashes.get(scope) else STALE

    def facts(self, target: Union[str, Base], kind: Optional[str] = None,
              overlay: Optional[Union[str, Overlay]] = None,
              derived: bool = True) -> List[FactView]:
        """Facts on *target* (an anchor or an anchored node). With *derived*,
        facts on its derived anchors (``target@reg:2``) are included too."""
        anchor = target if isinstance(target, str) else target.anchor
        if anchor is None:
            return []
        base = split_qualifier(anchor)[0]
        exact_only = base != anchor or not derived
        out = []
        for o in self._select(overlay):
            for f in self._indexed(o).get(base, ()):
                if kind is not None and f.kind != kind:
                    continue
                if exact_only and f.anchor != anchor:
                    continue
                out.append(FactView(f, o, self.status(f, o)))
        return out

    def _select(self, overlay) -> List[Overlay]:
        if overlay is None:
            return self.overlays()
        if isinstance(overlay, Overlay):
            return [overlay]
        return [self._overlays[overlay]]

    def _indexed(self, o: Overlay) -> Dict[str, List[Fact]]:
        idx = self._index.get(o.header.overlay_id)
        if idx is None:
            idx = {}
            for f in o.facts():
                idx.setdefault(split_qualifier(f.anchor)[0], []).append(f)
            self._index[o.header.overlay_id] = idx
        return idx
