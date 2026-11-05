"""Overlays: the facts from one engine run against one version of a model."""
from __future__ import annotations

import dataclasses as dc
import datetime
import hashlib
import json
from typing import Any, Dict, Iterator, List, Optional

from .fact import EXACT, Fact
from .kinds import validate

OVERLAY_FORMAT = "zuspec.overlay"
OVERLAY_VERSION = 1

#: Who stands behind an overlay's facts, most trusted first: an engine that
#: built the block, a library generator, a characterization run, a person.
PROVENANCES = ("engine", "generator", "measured", "declared")


def rank(provenance: str) -> int:
    """0 for the most trusted provenance."""
    return PROVENANCES.index(provenance)


def _hash(obj: Any) -> str:
    data = json.dumps(obj, sort_keys=True, separators=(",", ":"), default=str).encode("utf-8")
    return hashlib.sha256(data).hexdigest()[:16]


@dc.dataclass
class OverlayHeader:
    """Where an overlay's facts came from."""
    engine: str
    engine_version: Optional[str] = None
    delay_model: Optional[str] = None
    #: The intent the engine ran with (clock, stages, reset, ...).
    constraints: Dict[str, Any] = dc.field(default_factory=dict)
    command: Optional[str] = None
    created: str = dc.field(default_factory=lambda: datetime.datetime.now(
        datetime.timezone.utc).isoformat(timespec="seconds"))
    #: The anchor table's scope hashes when the engine ran: what makes a fact
    #: valid or stale against a later model.
    scope_hashes: Dict[str, str] = dc.field(default_factory=dict)
    #: How much of the design maps back to source (engine adapters fill it).
    coverage: Dict[str, Any] = dc.field(default_factory=dict)
    #: The engine's own files (IR, schedule, RTL, ...), by role.
    artifacts: Dict[str, str] = dc.field(default_factory=dict)
    overlay_id: Optional[str] = None
    #: One of :data:`PROVENANCES`.
    provenance: str = "engine"

    @property
    def constraints_hash(self) -> str:
        return _hash(self.constraints)

    def __post_init__(self):
        if self.provenance not in PROVENANCES:
            raise ValueError(f"unknown overlay provenance {self.provenance!r}")
        if self.overlay_id is None:
            key = [self.engine, self.engine_version, self.constraints, self.created,
                   self.scope_hashes]
            if self.provenance != "engine":
                key.append(self.provenance)
            self.overlay_id = _hash(key)[:12]


class Overlay:
    """A header and its facts, stored by kind in columns."""

    def __init__(self, header: OverlayHeader):
        self.header = header
        self._cols: Dict[str, Dict[str, List[Any]]] = {}

    def add(self, kind: str, anchor: str, value: Any, quality: str = EXACT) -> Fact:
        f = Fact(kind, anchor, value, quality)
        validate(f)
        col = self._cols.setdefault(kind, {"anchor": [], "value": [], "quality": []})
        col["anchor"].append(anchor)
        col["value"].append(value)
        col["quality"].append(quality)
        return f

    def kinds(self) -> List[str]:
        return sorted(self._cols)

    def column(self, kind: str) -> Dict[str, List[Any]]:
        """``{"anchor": [...], "value": [...], "quality": [...]}`` for *kind*."""
        return self._cols.get(kind, {"anchor": [], "value": [], "quality": []})

    def facts(self, kind: Optional[str] = None) -> Iterator[Fact]:
        for k in ([kind] if kind is not None else self.kinds()):
            col = self.column(k)
            for a, v, q in zip(col["anchor"], col["value"], col["quality"]):
                yield Fact(k, a, v, q)

    def __len__(self) -> int:
        return sum(len(c["anchor"]) for c in self._cols.values())

    @property
    def filename(self) -> str:
        return f"{self.header.engine}-{self.header.overlay_id}.json"

    # -- file form ----------------------------------------------------------

    def to_json(self) -> Dict[str, Any]:
        h = dc.asdict(self.header)
        h["constraints_hash"] = self.header.constraints_hash
        return {"format": OVERLAY_FORMAT, "version": OVERLAY_VERSION, "header": h,
                "facts": {k: self._cols[k] for k in self.kinds()}}

    @classmethod
    def from_json(cls, d: Dict[str, Any], validate_facts: bool = True) -> "Overlay":
        if d.get("format") != OVERLAY_FORMAT:
            raise ValueError(f"not an overlay (format {d.get('format')!r})")
        if d.get("version") != OVERLAY_VERSION:
            raise ValueError(f"unsupported overlay version {d.get('version')!r}")
        h = dict(d["header"])
        h.pop("constraints_hash", None)
        o = cls(OverlayHeader(**h))
        for kind, col in d.get("facts", {}).items():
            if not (len(col["anchor"]) == len(col["value"]) == len(col["quality"])):
                raise ValueError(f"overlay column {kind!r} has columns of different lengths")
            if validate_facts:
                for a, v, q in zip(col["anchor"], col["value"], col["quality"]):
                    validate(Fact(kind, a, v, q))
            o._cols[kind] = {"anchor": list(col["anchor"]), "value": list(col["value"]),
                             "quality": list(col["quality"])}
        return o

    def save(self, path: str) -> None:
        with open(path, "w") as f:
            json.dump(self.to_json(), f, separators=(",", ":"))
            f.write("\n")

    @classmethod
    def load(cls, path: str, validate_facts: bool = True) -> "Overlay":
        with open(path) as f:
            return cls.from_json(json.load(f), validate_facts)
