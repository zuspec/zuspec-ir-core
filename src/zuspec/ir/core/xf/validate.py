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

        has_body = any(getattr(f, "name", None) == "body" for f in dt.functions)
        if dt.activity_ir is None and not has_body:
            raise UnsupportedConstructError(
                "action %r has neither an exec body nor an activity" % qname,
                loc=dt.getLoc())

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
