"""Scenario-lowering validator (C4).

Fail early, with a precise message and source :class:`~..base.Loc`, on Layer-0
constructs that the current iteration cannot lower — never emit a silent stub
(``docs/error_handling_improvements.md``).

This is a *partial* validator in Phase 1: it catches the action-shape cases that
the lowering pass would otherwise mishandle.  Phase 6 grows it into a single
pre-flight pass enumerating every unsupported construct with a remedy hint.
"""
from __future__ import annotations

from typing import List, Optional, Tuple

from ..base import Loc
from ..data_type import DataTypeClass, DataTypeComponent
from ..expr import ExprConstant
from ..scenario import (
    ScSeq, ScAtomic, ScLoop, ScIf, ScMatch, ScSelect, ScPar, ScInvoke,
)


class UnsupportedConstructError(Exception):
    """Raised when a Layer-0 construct is not supported by the current
    PSS-lowering iteration.

    Attributes:
        loc:    Source location of the offending construct, when known.
        remedy: Optional hint describing how to work around the limitation.
    """

    def __init__(self, message: str, loc: Optional[Loc] = None,
                 remedy: Optional[str] = None):
        self.loc = loc
        self.remedy = remedy
        full = message
        if loc is not None and loc.file is not None:
            full = "%s:%d: %s" % (loc.file, loc.line, message)
        if remedy:
            full = "%s (%s)" % (full, remedy)
        super().__init__(full)


class PssRuleError(UnsupportedConstructError):
    """The PSS itself breaks an LRM rule (an illegal model, not a limit of
    this lowering): callers report it as a compile error. A subclass, so a
    caller that only knows :class:`UnsupportedConstructError` still stops."""


_CLAIM_TYPES = ("addr_claim_base_s", "addr_claim_s",
                "transparent_addr_claim_s")


def _is_claim_type(name: str) -> bool:
    """Is *name* one of addr_reg_pkg's claim structs, or a specialization of
    one (``addr_reg_pkg::transparent_addr_claim_s_<params>``)?"""
    pkg, _, base = (name or "").rpartition("::")
    if pkg.rpartition("::")[2] != "addr_reg_pkg":
        return False
    return any(base == t or base.startswith(t + "_") for t in _CLAIM_TYPES)


def _claim_fields(dt, path=(), _seen=()):
    """``(path, field)`` for every address-claim field of *dt*, including
    inside struct fields (a claim in a struct is still a claim)."""
    from ..data_type import DataTypeRef, DataTypeStruct
    for f in getattr(dt, "fields", None) or []:
        ft = getattr(f, "datatype", None)
        name = (getattr(ft, "ref_name", None) if isinstance(ft, DataTypeRef)
                else getattr(ft, "name", None))
        if name and _is_claim_type(name):
            yield path + (f.name,), f
        elif (isinstance(ft, DataTypeStruct)
              and not isinstance(ft, DataTypeClass)
              and id(ft) not in _seen):
            yield from _claim_fields(ft, path + (f.name,), _seen + (id(ft),))


class ScenarioValidator:
    """Validates Layer-0 actions against the iteration-1 supported subset."""

    def check_action(self, qname: str, dt: DataTypeClass) -> None:
        """Validate a single action type prior to lowering.

        Raises :class:`UnsupportedConstructError` for shapes the lowering pass
        cannot yet handle.
        """
        if isinstance(dt, DataTypeComponent):
            raise UnsupportedConstructError(
                "%r is a component, not an action" % qname, loc=dt.getLoc())

        if getattr(dt, "is_abstract", False):
            raise UnsupportedConstructError(
                "abstract action %r cannot be lowered directly" % qname,
                loc=dt.getLoc(),
                remedy="lower a concrete derived action instead")

        if getattr(dt, "flow_kind", None) is not None:
            raise UnsupportedConstructError(
                "flow object %r (flow_kind=%s) is out of scope in iteration 1"
                % (qname, dt.flow_kind),
                loc=dt.getLoc(),
                remedy="flow objects/pools/resources are a follow-on")

        # An action with neither an exec body nor an activity is legal PSS:
        # it lowers to a coroutine that only solves (and runs its pre/post
        # solve), so a traversal of it runs *it*, not some other action.

        # Address claims (LRM 24.8) are allocated at solve time; until that
        # exists (F7.3) a claim would be solved as a plain struct, with no
        # region, alignment or non-overlap, so it is refused here by name,
        # unless the front end placed it (a ``_claim_alloc`` constraint).
        placed = {(g.metadata or {}).get("_claim_alloc")
                  for g in getattr(dt, "functions", None) or []}
        for path, f in _claim_fields(dt):
            if ".".join(path) in placed:
                continue
            raise UnsupportedConstructError(
                "address claim %r in action %r: address claims are not "
                "allocated yet" % (".".join(path), qname),
                loc=f.getLoc() if hasattr(f, "getLoc") else dt.getLoc(),
                remedy="give the buffer a fixed address, or wait for claim "
                       "allocation (address-space-bc-design.md)")

    # ------------------------------------------------------------------
    # Layer-1 pre-flight: enumerate *every* unsupported construct at once.
    # ------------------------------------------------------------------
    def validate_module(self, module) -> List[UnsupportedConstructError]:
        """Walk the lowered module and collect a diagnostic per unsupported
        Layer-1 construct (does not stop at the first).  Returns ``[]`` when the
        module is fully supported."""
        diags: List[UnsupportedConstructError] = []
        for coro in module.coroutines.values():
            self._walk(coro.name, coro.body, diags)
        return diags

    def check_module(self, module) -> None:
        """Raise a single consolidated :class:`UnsupportedConstructError` if the
        module contains any unsupported construct."""
        diags = self.validate_module(module)
        if not diags:
            return
        if len(diags) == 1:
            raise diags[0]
        lines = "\n".join("  - %s" % str(d) for d in diags)
        raise UnsupportedConstructError(
            "%d unsupported construct(s) for iteration-1 PSS→C:\n%s"
            % (len(diags), lines))

    def _walk(self, coro_name: str, stmts, diags) -> None:
        for s in stmts:
            if isinstance(s, ScLoop):
                if s.kind != "repeat" or s.count is None:
                    diags.append(UnsupportedConstructError(
                        "in %r: only counted `repeat` loops are supported "
                        "(got kind=%r)" % (coro_name, s.kind), loc=s.getLoc(),
                        remedy="rewrite as `repeat (N) { ... }`"))
                self._walk(coro_name, s.body, diags)
            elif isinstance(s, ScSelect):
                for b in s.branches:
                    if b.weight is not None and not isinstance(b.weight, ExprConstant):
                        diags.append(UnsupportedConstructError(
                            "in %r: select branch weights must be constant "
                            "integers" % coro_name, loc=s.getLoc()))
                    self._walk(coro_name, b.body, diags)
            elif isinstance(s, ScPar):
                for j, b in enumerate(s.branches):
                    if not isinstance(b, ScInvoke):
                        diags.append(UnsupportedConstructError(
                            "in %r: parallel branch %d must be a single "
                            "traversal" % (coro_name, j), loc=s.getLoc(),
                            remedy="wrap multi-step branches as a sub-action"))
            elif isinstance(s, ScMatch):
                for c in s.cases:
                    self._walk(coro_name, c.body, diags)
            elif isinstance(s, (ScSeq, ScAtomic, ScIf)):
                for attr in ("body", "then_body", "else_body"):
                    sub = getattr(s, attr, None)
                    if isinstance(sub, list):
                        self._walk(coro_name, sub, diags)
