"""The fact-kind registry: the standardized core kinds and their value schemas.

Core kinds live in the reserved namespaces (``iface``, ``sched``, ``timing``,
``rtl``, ``area``, ``sim``) and are validated. A kind in any other namespace
(``xls.node_name``, ``native.…``) is an engine extra: stored as is, never
validated. Packages add kinds through the ``zuspec.fact_kinds`` entry-point
group; each entry point names a callable that returns a list of
:class:`FactKind`.
"""
from __future__ import annotations

import dataclasses as dc
from typing import Any, Callable, Dict, Iterable, Optional, Tuple

from .fact import QUALITIES, Fact, FactError

#: Namespaces whose kinds must be registered.
CORE_NAMESPACES = frozenset({"iface", "sched", "timing", "rtl", "area", "sim"})

ENTRY_POINT_GROUP = "zuspec.fact_kinds"


@dc.dataclass(frozen=True)
class FactKind:
    name: str
    #: True when a value is acceptable.
    check: Callable[[Any], bool]
    doc: str = ""


def _int(lo: int = 0) -> Callable[[Any], bool]:
    return lambda v: isinstance(v, int) and not isinstance(v, bool) and v >= lo


def _dict(**fields: Callable[[Any], bool]) -> Callable[[Any], bool]:
    """A dict with exactly the required *fields*; a key ending in ``_`` is optional."""
    req = {k: f for k, f in fields.items() if not k.endswith("_")}
    opt = {k[:-1]: f for k, f in fields.items() if k.endswith("_")}

    def check(v: Any) -> bool:
        return (isinstance(v, dict) and all(k in v and f(v[k]) for k, f in req.items())
                and all(k in req or (k in opt and (v[k] is None or opt[k](v[k]))) for k in v))
    return check


_str = lambda v: isinstance(v, str) and v != ""
_bool = lambda v: isinstance(v, bool)
_strs = lambda v: isinstance(v, list) and all(isinstance(x, str) for x in v)
_num = lambda v: isinstance(v, (int, float)) and not isinstance(v, bool) and v >= 0

#: Units an ``area.estimate`` may be given in.
AREA_UNITS = frozenset({"um2", "mm2", "ge", "cells", "luts", "ffs", "brams", "dsps"})


def _latency(v: Any) -> bool:
    """``{src, dst}`` plus either ``fixed`` or ``min`` (and an optional ``max``,
    absent meaning unbounded)."""
    if not _dict(src=_str, dst=_str, fixed_=_int(), min_=_int(), max_=_int())(v):
        return False
    fixed, lo, hi = v.get("fixed"), v.get("min"), v.get("max")
    if fixed is not None:
        return lo is None and hi is None
    return lo is not None and (hi is None or hi >= lo)


def latency_range(v: Dict[str, Any]) -> Tuple[int, Optional[int]]:
    """``(min, max)`` cycles of an ``iface.latency`` value; max None is unbounded."""
    if v.get("fixed") is not None:
        return v["fixed"], v["fixed"]
    return v["min"], v.get("max")

CORE_KINDS = [
    FactKind("iface.block",
             lambda v: isinstance(v, dict) and v.get("format") == "zuspec.block-signature",
             "The block's interface: a BlockSignature in its JSON form."),
    FactKind("iface.channel_stage", _int(), "Pipeline stage of the block's operation on a channel."),
    FactKind("iface.channel_flop", lambda v: v in ("none", "flop", "skid", "zero_latency"),
             "I/O flopping of a channel's ports."),
    FactKind("iface.latency", _latency,
             "Cycles from a value entering at port src to its effect leaving at port dst: "
             "fixed, or min..max (no max: unbounded)."),
    FactKind("iface.ii", _dict(port=_str, n=_int(1)),
             "The port accepts at most one value every n cycles."),
    FactKind("iface.throughput", lambda v: _dict(port=_str, k=_int(1), n=_int(1))(v)
             and v["k"] <= v["n"], "The port sustains k values every n cycles."),
    FactKind("iface.capacity", _dict(src=_str, dst=_str, n=_int()),
             "Values that can be in flight from src to dst."),
    FactKind("iface.comb_path", _dict(src=_str, dst=_str),
             "A combinational path from port src to port dst."),
    FactKind("iface.stall_coupling", _dict(a=_str, b=_str),
             "A stall on port a can stall port b."),
    FactKind("iface.registered", _dict(port=_str), "The port's outputs come from flops."),
    FactKind("timing.io_delay_ps", _dict(port=_str, ps=_int()),
             "Delay inside the block between the port and the nearest flop."),
    FactKind("area.estimate", _dict(value=_num, unit=lambda u: u in AREA_UNITS,
                                    source_=_str, cond_=_str),
             "An area figure, with its unit and where it comes from."),
    FactKind("sched.stage", _int(), "Pipeline stage (0-based) an operation is scheduled in."),
    FactKind("sched.stage_count", _int(1), "Number of pipeline stages of a function or process."),
    FactKind("sched.ii", _dict(value=_int(1), reliable=_bool),
             "Initiation interval; reliable=False when the engine only defaults it."),
    FactKind("timing.node_delay_ps", _int(), "Combinational delay of an operation."),
    FactKind("timing.path_delay_ps", _int(),
             "Delay from the start of its stage to the operation's output."),
    FactKind("timing.critical_path",
             _dict(stage=_int(), anchors=_strs, delay_ps=_int(), slack_ps_=lambda v: isinstance(v, int)),
             "The longest path of a stage, as an ordered list of anchors."),
    FactKind("timing.clock_period_ps", _int(1), "Clock period the schedule was made for."),
    FactKind("rtl.signal", _dict(module=_str, name=_str), "The RTL net that carries a value."),
    FactKind("rtl.register", _dict(module=_str, name=_str, width=_int(1), stage=_int()),
             "A pipeline or state register (on a derived anchor)."),
]

_registry: Dict[str, FactKind] = {}
_loaded_entry_points = False


def register(kinds: Iterable[FactKind]) -> None:
    for k in kinds:
        _registry[k.name] = k


def _ensure_loaded() -> None:
    global _loaded_entry_points
    if _loaded_entry_points:
        return
    _loaded_entry_points = True
    register(CORE_KINDS)
    import importlib.metadata as md
    for ep in md.entry_points(group=ENTRY_POINT_GROUP):
        register(ep.load()())


def get(name: str) -> Optional[FactKind]:
    _ensure_loaded()
    return _registry.get(name)


def namespace(kind: str) -> str:
    return kind.split(".", 1)[0]


def validate(fact: Fact) -> None:
    """Raise :class:`FactError` unless *fact* fits its kind."""
    if fact.quality not in QUALITIES:
        raise FactError(f"{fact.kind} on {fact.anchor}: unknown quality {fact.quality!r}")
    if "." not in fact.kind:
        raise FactError(f"fact kind {fact.kind!r} has no namespace")
    k = get(fact.kind)
    if k is None:
        if namespace(fact.kind) in CORE_NAMESPACES:
            raise FactError(f"unknown core fact kind {fact.kind!r}")
        return
    if not k.check(fact.value):
        raise FactError(f"{fact.kind} on {fact.anchor}: bad value {fact.value!r}")
