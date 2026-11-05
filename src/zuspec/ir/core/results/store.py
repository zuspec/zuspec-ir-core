"""ResultStore: overlays held beside a model, looked up by node or anchor."""
from __future__ import annotations

import dataclasses as dc
from typing import Dict, List, Optional, Union

from ..anchor import AnchorTable, split_qualifier
from ..base import Base
from .fact import Fact
from .kinds import latency_range
from .overlay import Overlay, rank

#: A fact's standing against the current model.
VALID, STALE, ORPHAN = "valid", "stale", "orphan"


@dc.dataclass(frozen=True)
class FactView:
    fact: Fact
    overlay: Overlay
    #: valid: its scope is unchanged since the engine ran; stale: the scope
    #: changed; orphan: the anchor no longer exists.
    status: str


#: Kinds :meth:`ResultStore.conflicts` compares, and the value fields that say
#: which facts are about the same thing (the ports).
CONFLICT_KEYS = {
    "iface.latency": ("src", "dst"),
    "iface.ii": ("port",),
    "iface.throughput": ("port",),
    "iface.capacity": ("src", "dst"),
    "iface.registered": ("port",),
    "timing.io_delay_ps": ("port",),
}


@dc.dataclass(frozen=True)
class Conflict:
    """A fact from a more trusted overlay (*higher*) that contradicts one from
    a less trusted overlay (*lower*) on the same anchor and ports."""
    kind: str
    anchor: str
    higher: FactView
    lower: FactView

    def __str__(self) -> str:
        h, l = self.higher, self.lower
        return (f"{self.kind} on {self.anchor}: {h.overlay.header.provenance} "
                f"({h.overlay.header.engine}) says {h.fact.value!r}, but "
                f"{l.overlay.header.provenance} ({l.overlay.header.engine}) says "
                f"{l.fact.value!r}")


def agrees(kind: str, higher, lower) -> bool:
    """True when *higher* (the more trusted value) does not contradict *lower*."""
    if kind == "iface.latency":
        hlo, hhi = latency_range(higher)
        llo, lhi = latency_range(lower)
        return hlo >= llo and (lhi is None or (hhi is not None and hhi <= lhi))
    return higher == lower


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

    def conflicts(self) -> List[Conflict]:
        """Facts that contradict a fact of a more trusted overlay: a latency
        range must lie inside the less trusted one's; other compared kinds
        (:data:`CONFLICT_KEYS`) must be equal. Overlays of the same provenance
        are not compared."""
        groups: Dict[tuple, List[FactView]] = {}
        for o in self.overlays():
            for f in o.facts():
                keys = CONFLICT_KEYS.get(f.kind)
                if keys is None:
                    continue
                g = (f.kind, f.anchor, tuple(f.value.get(k) for k in keys))
                groups.setdefault(g, []).append(FactView(f, o, self.status(f, o)))
        out = []
        for (kind, anchor, _), views in groups.items():
            views.sort(key=lambda v: rank(v.overlay.header.provenance))
            for i, hv in enumerate(views):
                for lv in views[i + 1:]:
                    if rank(hv.overlay.header.provenance) == rank(lv.overlay.header.provenance):
                        continue
                    if not agrees(kind, hv.fact.value, lv.fact.value):
                        out.append(Conflict(kind, anchor, hv, lv))
        return out
