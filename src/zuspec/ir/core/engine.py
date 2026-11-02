"""The synthesis-engine interface.

An engine turns a block of Zuspec IR into RTL, and reports what it did as an
:class:`~zuspec.ir.core.results.Overlay` on the IR's anchors. Every engine
(XLS through zuspec-be-xls, native lowering, later others) implements
:class:`Engine`, so flows, reports and checks need not know which one built a
block. Engines register under the ``zuspec.engines`` entry-point group; each
entry point names a zero-argument callable that returns an :class:`Engine`.

The types live here, beside the results model, so an engine adapter depends
on nothing heavier than zuspec-ir-core.
"""
from __future__ import annotations

import dataclasses as dc
from typing import Any, Callable, Dict, List, Optional, Protocol, Sequence, runtime_checkable

from .anchor import AnchorTable
from .base import Base
from .results.overlay import Overlay

ENGINE_ENTRY_POINT_GROUP = "zuspec.engines"


@dc.dataclass
class Constraints:
    """What a build is asked for (intent). Engines honor what they can and
    report the rest; ``extra`` passes engine-specific options through."""
    clock_period_ps: Optional[int] = None
    pipeline_stages: Optional[int] = None
    reset: Optional[str] = None
    reset_active_low: bool = False
    module_name: Optional[str] = None
    extra: Dict[str, Any] = dc.field(default_factory=dict)

    def to_json(self) -> Dict[str, Any]:
        d = {k: v for k, v in dc.asdict(self).items() if k != "extra" and v not in (None, False)}
        d.update(self.extra)
        return d


@dc.dataclass
class Reason:
    message: str
    #: The IR node the reason is about, when known.
    anchor: Optional[str] = None


@dc.dataclass
class Verdict:
    ok: bool
    reasons: List[Reason] = dc.field(default_factory=list)


@dc.dataclass
class BuildResult:
    rtl: List[str]
    top_module: str
    overlay: Overlay
    #: The engine's own files by role (IR, schedule, signature, ...).
    artifacts: Dict[str, str] = dc.field(default_factory=dict)
    log: Optional[str] = None


@runtime_checkable
class Engine(Protocol):
    name: str

    def version(self) -> Optional[str]: ...

    def accepts(self, roots: Sequence[Base], top: str) -> Verdict:
        """Whether the engine can build *top* (with *roots*: the top and what
        it uses), and why not."""
        ...

    def build(self, roots: Sequence[Base], top: str, anchors: AnchorTable,
              constraints: Constraints, workdir: str) -> BuildResult:
        """Build *top* into RTL in *workdir*; the overlay's facts are on
        *anchors*, which must have been assigned over *roots*."""
        ...


_registry: Dict[str, Callable[[], Engine]] = {}
_loaded = False


def register_engine(name: str, factory: Callable[[], Engine]) -> None:
    _registry[name] = factory


def engines() -> Dict[str, Callable[[], Engine]]:
    global _loaded
    if not _loaded:
        _loaded = True
        import importlib.metadata as md
        for ep in md.entry_points(group=ENGINE_ENTRY_POINT_GROUP):
            _registry.setdefault(ep.name, ep.load())
    return dict(_registry)


def get_engine(name: str) -> Engine:
    factories = engines()
    if name not in factories:
        raise KeyError(f"no synthesis engine {name!r} (have: {sorted(factories)})")
    return factories[name]()
