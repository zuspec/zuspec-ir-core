"""Sample-point analysis (design §4 item 5).

A *sample point* is an attribute an exec block writes while the test runs
(``exec body``, ``run_start``, ``run_end``, or ``post_solve`` on the target
side) that a later decision reads: a constraint, a guard, a loop count, a
``comp`` choice, or a flow-object attribute a consumer constrains.

Two kinds of read are found:

* **Activity reads.** In a compound action's activity, a traversal of handle
  ``a`` is followed (in program order) by an expression reading ``a.x``,
  where the exec blocks of ``a``'s type write ``x``.
* **Flow-object reads.** A producer's exec block writes an attribute of an
  output (``o.s = 3``), and a consumer of the same pool reads that attribute
  of its input in a constraint (``constraint y > i.s``).

Each read is classified:

* ``value-only``: it feeds only a later value solve (an inline or activity
  constraint over plain attributes). The value is pinned; nothing replans.
* ``structural``: it feeds a branch, ``select`` guard, loop count or
  condition, ``match`` subject, ``replicate`` count, a ``comp`` choice, a
  constraint over a flow-object or resource reference (which decides a
  binding), or a flow-object attribute of a pool with more than one producer
  (which producer binds depends on the run-time value).

A global plan, made before the test runs, cannot be faithful when the model
has any sample point: :meth:`SamplePoints.global_plan_problems` lists them as
located diagnostics (D6). No pssc command makes a global plan yet; a planner
mode that does must refuse, or warn, with these.
"""
from __future__ import annotations

import dataclasses as dc
from typing import Any, Dict, Iterable, List, Optional, Set, Tuple

from ...activity import (ActivityAnonTraversal, ActivityConstraint,
                         ActivityDoWhile, ActivityForeach, ActivityIfElse,
                         ActivityMatch, ActivityParallel, ActivityRepeat,
                         ActivityReplicate, ActivitySchedule, ActivitySelect,
                         ActivityStmt, ActivityTraversal, ActivityWhileDo,
                         MatchCase, SelectBranch)
from ...expr import (ExprAttribute, ExprSubscript, TypeExprRefSelf,
                     TypeExprRefTraversed)
from ...fields import FieldKind
from ...stmt import StmtAnnAssign, StmtAssign, StmtAugAssign
from .icl import IclTable

#: Exec kinds that run while the test runs (on the target side).
RUN_TIME_EXECS = ("body", "run_start", "run_end", "post_solve")

VALUE_ONLY = "value-only"
STRUCTURAL = "structural"

_REF_KINDS = (FieldKind.Input, FieldKind.Output, FieldKind.Lock, FieldKind.Share)

Path = Tuple[str, ...]


@dc.dataclass(frozen=True)
class SamplePoint:
    """One run-time-written attribute read by a later decision."""
    action: str           # where the read is: the compound action, or consumer
    reader: str           # "if", "select", "repeat", "while", "match", "replicate",
                          # "comp", "constraint", "input"
    attr: str             # what is read, dotted from the reader (``a.x``, ``i.s``)
    writer: str           # the action type whose exec writes it
    exec_kind: str        # the exec block that writes it
    kind: str             # VALUE_ONLY or STRUCTURAL
    loc: Any = dc.field(default=None, compare=False)

    def where(self) -> str:
        if self.loc is None or getattr(self.loc, "file", None) is None:
            return ""
        return "%s:%s: " % (self.loc.file, self.loc.line)


def _walk(x) -> Iterable[Any]:
    """Every dataclass node under *x* (incl. *x*)."""
    if isinstance(x, (list, tuple)):
        for e in x:
            yield from _walk(e)
    elif dc.is_dataclass(x) and not isinstance(x, type):
        yield x
        for f in dc.fields(x):
            if f.name in ("loc", "provenance", "datatype"):
                continue
            v = getattr(x, f.name, None)
            if isinstance(v, (list, tuple)) or (dc.is_dataclass(v) and not isinstance(v, type)):
                yield from _walk(v)


def _path(e) -> Optional[Tuple[type, Path]]:
    """``self.a[i].x.y`` -> (TypeExprRefSelf, ("a", "x", "y")); None when *e*
    is not an attribute chain on ``self`` or the traversed action."""
    names: List[str] = []
    while True:
        if isinstance(e, ExprAttribute):
            names.append(e.attr)
            e = e.value
        elif isinstance(e, ExprSubscript):
            e = e.value
        elif isinstance(e, (TypeExprRefSelf, TypeExprRefTraversed)):
            return (type(e), tuple(reversed(names))) if names else None
        else:
            return None


def _reads(exprs) -> List[Tuple[type, Path, Any]]:
    """Maximal attribute chains read under *exprs*, with a location."""
    out = []

    def rec(x, loc):
        if isinstance(x, (list, tuple)):
            for e in x:
                rec(e, loc)
            return
        if not (dc.is_dataclass(x) and not isinstance(x, type)):
            return
        loc = getattr(x, "loc", None) or loc
        p = _path(x)
        if p is not None:
            out.append((p[0], p[1], loc))
            return
        for f in dc.fields(x):
            if f.name not in ("loc", "provenance"):
                rec(getattr(x, f.name, None), loc)

    rec(exprs, None)
    return out


def _overlap(a: Path, b: Path) -> bool:
    n = min(len(a), len(b))
    return a[:n] == b[:n]


class SamplePoints:
    """The sample points of the model *icl* covers (its types and pools)."""

    def __init__(self, icl: IclTable):
        self.icl = icl
        self.types = icl.types
        self._writes_memo: Dict[str, Dict[Path, str]] = {}
        self.points: List[SamplePoint] = []
        for a in sorted(icl._actions):
            self._activity_points(a)
        self._flow_points()

    # -- writes ---------------------------------------------------------------

    def writes(self, qname: str) -> Dict[Path, str]:
        """Attribute paths of action type *qname* its run-time exec blocks
        write (inherited blocks too) -> the exec kind that writes it."""
        if qname in self._writes_memo:
            return self._writes_memo[qname]
        out: Dict[Path, str] = {}
        for dt in self.icl._chain(qname):
            for fn in getattr(dt, "functions", []):
                ek = (fn.metadata or {}).get("exec_kind")
                if ek not in RUN_TIME_EXECS:
                    continue
                for s in _walk(fn.body):
                    if isinstance(s, StmtAssign):
                        tgts = s.targets
                    elif isinstance(s, (StmtAugAssign, StmtAnnAssign)):
                        tgts = [s.target]
                    else:
                        continue
                    for t in tgts:
                        p = _path(t)
                        if p is not None and p[0] is TypeExprRefSelf:
                            out.setdefault(p[1], ek)
        self._writes_memo[qname] = out
        return out

    def _written(self, qname: str, path: Path) -> Optional[str]:
        for w, ek in self.writes(qname).items():
            if _overlap(w, path):
                return ek
        return None

    def _field(self, qname: str, name: str):
        for dt in self.icl._chain(qname):
            for f in getattr(dt, "fields", []):
                if f.name == name:
                    return f
        return None

    # -- activity reads -------------------------------------------------------

    def _activity_points(self, action: str) -> None:
        dt = self.types[action]
        act = getattr(dt, "activity_ir", None)
        if act is None:
            return
        #: handle name -> action type, for handles traversed so far
        self._cur = action
        self._walk_act(act.stmts if hasattr(act, "stmts") else act, {})
        # the compound action's own constraints read handles too; when they
        # are solved is the planner's choice, so every traversed handle counts
        handles = self._handles(act)
        for fn in getattr(dt, "functions", []):
            if (fn.metadata or {}).get("_is_constraint"):
                self._check(fn.body, handles, "constraint", None)
        for s in _walk(act):
            if isinstance(s, ActivityConstraint):
                self._check(s.constraints, handles, "constraint", None)

    def _handles(self, act) -> Dict[str, str]:
        out = {}
        for s in _walk(act):
            h = self._handle_of(s)
            if h is not None:
                out[h[0]] = h[1]
        return out

    @staticmethod
    def _handle_of(s) -> Optional[Tuple[str, str]]:
        if isinstance(s, ActivityTraversal) and s.type_qname:
            return s.handle, s.type_qname
        if isinstance(s, ActivityAnonTraversal) and s.label and s.type_qname:
            return s.label, s.type_qname
        return None

    def _check(self, exprs, seen: Dict[str, str], reader: str,
               traversed: Optional[str], loc=None) -> None:
        """Record a sample point for each read under *exprs* of a handle in
        *seen* that its type's exec blocks write. *traversed*: the action a
        ``with`` block constrains (reads rooted at it decide its references)."""
        reads = _reads(exprs)
        # a constraint that also names a reference of the traversed action
        # (``i.v == a.x``) or of the compound action decides a binding
        binds = False
        if reader == "constraint":
            for root, path, _ in reads:
                q = traversed if root is TypeExprRefTraversed else self._cur
                f = self._field(q, path[0]) if q else None
                if f is not None and f.kind in _REF_KINDS:
                    binds = True
        for root, path, rloc in reads:
            if root is not TypeExprRefSelf or len(path) < 2 or path[0] not in seen:
                continue
            ek = self._written(seen[path[0]], path[1:])
            if ek is None:
                continue
            structural = reader != "constraint" or binds
            self.points.append(SamplePoint(
                self._cur, reader, ".".join(path), seen[path[0]], ek,
                STRUCTURAL if structural else VALUE_ONLY, rloc or loc))

    def _walk_act(self, x, seen: Dict[str, str]) -> Dict[str, str]:
        """Walk activity *x* in program order; *seen* holds the handles
        traversed before it. Returns the handles traversed after it."""
        if isinstance(x, list):
            for s in x:
                seen = self._walk_act(s, seen)
            return seen
        loc = getattr(x, "loc", None)
        h = self._handle_of(x)
        if isinstance(x, (ActivityTraversal, ActivityAnonTraversal)):
            if x.comp_expr is not None:
                self._check([x.comp_expr], seen, "comp", None, loc)
            self._check(list(x.inline_constraints)
                        + [v for _, v in x.initializers],
                        seen, "constraint", x.type_qname, loc)
            if h is not None:
                seen = {**seen, h[0]: h[1]}
            return seen
        if isinstance(x, ActivityIfElse):
            self._check([x.condition], seen, "if", None, loc)
            a = self._walk_act(x.if_body, seen)
            b = self._walk_act(x.else_body, seen)
            return {**seen, **a, **b}
        if isinstance(x, ActivitySelect):
            out = dict(seen)
            for br in x.branches:
                if br.guard is not None:
                    self._check([br.guard], seen, "select", None, br.loc or loc)
                if br.weight is not None:
                    self._check([br.weight], seen, "select", None, br.loc or loc)
                out.update(self._walk_act(br.body, seen))
            return out
        if isinstance(x, ActivityMatch):
            self._check([x.subject], seen, "match", None, loc)
            out = dict(seen)
            for c in x.cases:
                out.update(self._walk_act(c.body, seen))
            return out
        if isinstance(x, (ActivityRepeat, ActivityReplicate)):
            reader = "repeat" if isinstance(x, ActivityRepeat) else "replicate"
            self._check([x.count], seen, reader, None, loc)
            return self._walk_act(x.body, self._walk_act(x.body, seen))
        if isinstance(x, ActivityForeach):
            self._check([x.collection], seen, "repeat", None, loc)
            return self._walk_act(x.body, self._walk_act(x.body, seen))
        if isinstance(x, ActivityWhileDo):
            self._check([x.condition], seen, "while", None, loc)
            after = self._walk_act(x.body, seen)
            self._check([x.condition], after, "while", None, loc)
            return self._walk_act(x.body, after)
        if isinstance(x, ActivityDoWhile):
            after = self._walk_act(x.body, seen)
            self._check([x.condition], after, "while", None, loc)
            return self._walk_act(x.body, after)
        if isinstance(x, (ActivityParallel, ActivitySchedule)):
            # branches run at once: one branch's writes are not "earlier"
            # for another
            out = dict(seen)
            for s in x.stmts:
                out.update(self._walk_act(s, seen))
            return out
        if isinstance(x, ActivityStmt):
            # sequence blocks, atomic, chain, fill: their statements in order
            for f in dc.fields(x):
                v = getattr(x, f.name, None)
                if isinstance(v, list) and v and isinstance(v[0], ActivityStmt):
                    seen = self._walk_act(v, seen)
        return seen

    # -- flow-object reads ----------------------------------------------------

    def _flow_points(self) -> None:
        icl = self.icl
        for pool in icl.pools():
            prods = icl.producers(pool)
            cons = icl.consumers(pool)
            if not prods or not cons:
                continue
            written: Dict[Path, Tuple[str, str]] = {}
            for e in prods:
                for p, ek in self.writes(e.action).items():
                    if p[0] == e.field and len(p) > 1:
                        written.setdefault(p[1:], (e.action, ek))
            if not written:
                continue
            producers = {(e.inst, e.action, e.field) for e in prods}
            kind = STRUCTURAL if len(producers) > 1 else VALUE_ONLY
            done: Set[Tuple[str, str]] = set()
            for e in cons:
                if e.action in done:
                    continue
                done.add(e.action)
                for dt in icl._chain(e.action):
                    for fn in getattr(dt, "functions", []):
                        if not (fn.metadata or {}).get("_is_constraint"):
                            continue
                        for root, path, rloc in _reads(fn.body):
                            if root is not TypeExprRefSelf or path[0] != e.field:
                                continue
                            w = next((v for p, v in written.items()
                                      if _overlap(p, path[1:])), None)
                            if w is None:
                                continue
                            self.points.append(SamplePoint(
                                e.action, "input", ".".join(path), w[0], w[1],
                                kind, rloc or getattr(fn, "loc", None)))

    # -- queries --------------------------------------------------------------

    def structural(self) -> List[SamplePoint]:
        return [p for p in self.points if p.kind == STRUCTURAL]

    def value_only(self) -> List[SamplePoint]:
        return [p for p in self.points if p.kind == VALUE_ONLY]

    def global_plan_problems(self) -> List[str]:
        """Located diagnostics for a plan made before the test runs: one per
        sample point (D6). Empty when a global plan is faithful."""
        out = []
        for p in self.points:
            out.append(
                "%sa global plan cannot see '%s' (%s): '%s' writes it in "
                "'exec %s' while the test runs, and %s reads it in %s" % (
                    p.where(), p.attr, p.kind, p.writer, p.exec_kind,
                    p.action, "a constraint" if p.reader in ("constraint", "input")
                    else "a '%s'" % p.reader))
        return out


__all__ = ["SamplePoint", "SamplePoints", "STRUCTURAL", "VALUE_ONLY",
           "RUN_TIME_EXECS"]
