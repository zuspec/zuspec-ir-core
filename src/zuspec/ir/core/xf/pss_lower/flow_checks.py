"""Static flow-object and resource refusals (design §4 item 8).

Models that no plan can run, found from the pool-binding and ICL tables
before any solve, each with a location:

* **stream**: a stream reference of a traversed action whose pool has no
  action on the other side, so it can never pair (13.2 c).
* **claims**: one action claims more instances of a resource pool (claim
  arrays and separate claims together) than the pool has (14.2).
* **state array**: an array of state references whose elements all reach
  one state pool, which holds one object at a time (13.4).
* **bind**: an activity ``bind`` joining references of different kinds
  (buffer/stream/state/resource, or a claim and a flow reference) or of
  different pools.
* **parallel**: the branches of a ``parallel`` lock more instances of one
  pool at their start than it has, whichever path each branch takes (14.2).

Only actions the root action reaches (``IclTable.traversed``) are checked:
an action type that is never traversed or inferred cannot fail a run.
"""
from __future__ import annotations

import dataclasses as dc
from typing import Any, Dict, List, Optional, Tuple

from ...activity import (ActivityAnonTraversal, ActivityAtomic, ActivityBind,
                         ActivityIfElse, ActivityMatch, ActivityParallel,
                         ActivitySchedule, ActivitySelect, ActivitySequenceBlock,
                         ActivityTraversal)
from ...data_type import DataTypeArray
from ...expr import TypeExprRefSelf
from ...fields import FieldKind
from ..validate import PssRuleError, UnsupportedConstructError
from .icl import IclTable, _traversals
from .layout import resolve
from .pools import PoolInst
from .sample_points import _path, _walk

STREAM = "stream"
CLAIMS = "claims"
STATE_ARRAY = "state-array"
BIND = "bind"
PARALLEL = "parallel"


@dc.dataclass(frozen=True)
class FlowProblem:
    kind: str           # STREAM, CLAIMS, STATE_ARRAY, BIND, PARALLEL or INFER (infer.py)
    message: str
    loc: Any = dc.field(default=None, compare=False)

    def error(self) -> UnsupportedConstructError:
        """The located error to raise: a PssRuleError for a parallel no pool
        can serve (illegal, 13.3.4) or an input no finite inference chain
        can feed (18.2), else an UnsupportedConstructError."""
        err = (PssRuleError if self.kind in (PARALLEL, "infer")
               else UnsupportedConstructError)
        return err(self.message, loc=self.loc)


def _simple(qname: str) -> str:
    return qname.rsplit("::", 1)[-1]


class FlowChecks:
    """The static refusals of the model *icl* covers (`problems`)."""

    def __init__(self, icl: IclTable):
        self.icl = icl
        self.table = icl.table
        self.types = icl.types
        self.problems: List[FlowProblem] = []
        self._where: Dict[str, List[int]] = {}
        for inst in sorted(self.table.insts):
            for a in icl.actions_of(inst):
                self._where.setdefault(a, []).append(inst)
        for a in sorted(icl.traversed):
            for inst in self._where.get(a, []):
                self._refs(inst, a)
                self._activity(inst, a)

    def raise_first(self) -> None:
        """Raise the first problem's located error (`FlowProblem.error`)."""
        if self.problems:
            raise self.problems[0].error()

    def _add(self, kind: str, message: str, loc) -> None:
        p = FlowProblem(kind, message, loc)
        if p not in self.problems:
            self.problems.append(p)

    # -- helpers --------------------------------------------------------------

    def _type(self, x) -> Any:
        try:
            return resolve(x, self.types)
        except UnsupportedConstructError:
            return x

    def _elem(self, f) -> Tuple[Any, int]:
        """The type a reference *f* holds and how many (1, or its array size)."""
        t = self._type(f.datatype)
        if isinstance(t, DataTypeArray):
            return self._type(t.element_type), max(t.size, 0)
        return t, 1

    def _flow_kind(self, f) -> Optional[str]:
        k = getattr(self._elem(f)[0], "flow_kind", None)
        return k.name if k is not None else None

    def _pool_name(self, p: PoolInst) -> str:
        return "%s@%s" % (p.name, self.table.insts[p.inst][1] or "<root>")

    def _field(self, action: str, name: str):
        return next((f for f in self.icl.ref_fields(action) if f.name == name), None)

    def _inst_of(self, inst: int, action: Optional[str]) -> Optional[int]:
        """The one instance a sub-action of type *action* traversed by an
        action of instance *inst* runs in, or None when it may run in several."""
        if action is None:
            return None
        if inst in self._where.get(action, []):
            return inst
        under = [i for i in self._where.get(action, []) if self.table._in_subtree(i, inst)]
        return under[0] if len(under) == 1 else None

    # -- per action -----------------------------------------------------------

    def _refs(self, inst: int, a: str) -> None:
        demand: Dict[PoolInst, Tuple[int, Any]] = {}
        for f in self.icl.ref_fields(a):
            pool = self.table.pool_of(inst, a, f)
            if pool is None:
                continue
            loc = getattr(f, "loc", None) or getattr(self.types[a], "loc", None)
            kind = self._flow_kind(f)
            if f.kind in (FieldKind.Lock, FieldKind.Share):
                n, _ = demand.get(pool, (0, loc))
                demand[pool] = (n + self._elem(f)[1], loc)
            elif kind == "STREAM":
                other = (self.icl.producers(pool) if f.kind == FieldKind.Input
                         else self.icl.consumers(pool))
                if not other:
                    self._add(STREAM, "stream %s %r of %r can never pair: no action "
                              "%s pool %s (13.2 c)" % (
                                  "input" if f.kind == FieldKind.Input else "output",
                                  f.name, _simple(a),
                                  "outputs into" if f.kind == FieldKind.Input
                                  else "inputs from", self._pool_name(pool)), loc)
            elif kind == "STATE" and self._elem(f)[1] > 1:
                self._add(STATE_ARRAY, "state reference array %r of %r: every element "
                          "is bound to pool %s, which holds one state object at a "
                          "time (13.4)" % (f.name, _simple(a), self._pool_name(pool)), loc)
        for pool, (n, loc) in demand.items():
            if pool.size is not None and n > pool.size:
                self._add(CLAIMS, "%r claims %d instances of resource pool %s, which "
                          "has %d (14.2)" % (_simple(a), n, self._pool_name(pool),
                                             pool.size), loc)

    # -- compound actions -----------------------------------------------------

    def _activity(self, inst: int, a: str) -> None:
        dt = self.types[a]
        act = getattr(dt, "activity_ir", None)
        if act is None:
            return
        handles: Dict[str, str] = {}
        for t in _traversals(act):
            q = getattr(t, "type_qname", None)
            if isinstance(t, ActivityTraversal) and q is not None:
                handles[t.handle] = q
            elif isinstance(t, ActivityAnonTraversal) and q is not None and t.label:
                handles[t.label] = q
        for b in _walk(act):
            if isinstance(b, ActivityBind):
                self._bind(inst, a, handles, b)
            elif isinstance(b, ActivityParallel):
                self._parallel(inst, a, handles, b)

    def _end(self, inst: int, a: str, handles, e):
        """(action type, field, its instance) a bind operand names, or None."""
        p = _path(e)
        if p is None or p[0] is not TypeExprRefSelf or not p[1]:
            return None
        path = p[1]
        if path[0] in handles and len(path) > 1:
            t = handles[path[0]]
            f = self._field(t, path[1])
            return (t, f, self._inst_of(inst, t)) if f is not None else None
        f = self._field(a, path[0])
        return (a, f, inst) if f is not None else None

    def _bind(self, inst: int, a: str, handles, b) -> None:
        ends = [self._end(inst, a, handles, e) for e in (b.src, b.dst)]
        if None in ends:
            return
        (ta, fa, ia), (tb, fb, ib) = ends
        claim_a = fa.kind in (FieldKind.Lock, FieldKind.Share)
        claim_b = fb.kind in (FieldKind.Lock, FieldKind.Share)
        ka, kb = self._flow_kind(fa), self._flow_kind(fb)
        if claim_a != claim_b or ka != kb:
            self._add(BIND, "bind of %s.%s (%s) and %s.%s (%s): references of "
                      "different kinds" % (_simple(ta), fa.name, (ka or "?").lower(),
                                           _simple(tb), fb.name, (kb or "?").lower()),
                      b.loc)
            return
        if ia is None or ib is None:
            return
        pa = self.table.pool_of(ia, ta, fa)
        pb = self.table.pool_of(ib, tb, fb)
        if pa is not None and pb is not None and pa != pb:
            self._add(BIND, "bind of %s.%s and %s.%s: they are bound to different "
                      "pools, %s and %s" % (_simple(ta), fa.name, _simple(tb), fb.name,
                                            self._pool_name(pa), self._pool_name(pb)),
                      b.loc)

    def _locks(self, inst: int, q: Optional[str],
               kinds=(FieldKind.Lock,)) -> Dict[PoolInst, int]:
        """Instances of each pool one traversal of type *q* locks (or claims
        with another of *kinds*)."""
        out: Dict[PoolInst, int] = {}
        k = self._inst_of(inst, q)
        if k is None:
            return out
        for f in self.icl.ref_fields(q, kinds):
            pool = self.table.pool_of(k, q, f)
            if pool is not None:
                out[pool] = out.get(pool, 0) + self._elem(f)[1]
        return out

    def _start(self, inst: int, handles, s, kinds=(FieldKind.Lock,)) -> Dict[PoolInst, int]:
        """Locks (or claims of *kinds*) held at the start of statement (or
        list) *s* on every path: a lower bound, so 0 where unsure (loops,
        run-time choices)."""
        if isinstance(s, list):
            return self._start(inst, handles, s[0], kinds) if s else {}
        if isinstance(s, ActivityTraversal):
            return self._locks(inst, s.type_qname or handles.get(s.handle), kinds)
        if isinstance(s, ActivityAnonTraversal):
            return self._locks(inst, s.type_qname, kinds)
        if isinstance(s, ActivityParallel):
            # (a schedule's members need not overlap: no lower bound)
            out: Dict[PoolInst, int] = {}
            for br in s.stmts:
                for p, n in self._start(inst, handles, br, kinds).items():
                    out[p] = out.get(p, 0) + n
            return out
        if isinstance(s, (ActivitySequenceBlock, ActivityAtomic)):
            return self._start(inst, handles, s.stmts, kinds)
        alts = None
        if isinstance(s, ActivitySelect) and not s.allow_none:
            alts = [br.body for br in s.branches]
        elif isinstance(s, ActivityIfElse) and s.else_body:
            alts = [s.if_body, s.else_body]
        elif isinstance(s, ActivityMatch):
            alts = [c.body for c in s.cases]
        if not alts:
            return {}
        starts = [self._start(inst, handles, b, kinds) for b in alts]
        return {p: min(st.get(p, 0) for st in starts) for p in starts[0]}

    def _parallel(self, inst: int, a: str, handles, par) -> None:
        locks = self._start(inst, handles, par)
        # Shares may all use one instance, but not one a branch locks (15.2).
        shares = self._start(inst, handles, par, (FieldKind.Share,))
        for p in list(locks) + [p for p in shares if p not in locks]:
            n = locks.get(p, 0)
            need = n + (1 if shares.get(p, 0) else 0)
            if p.size is not None and need > p.size:
                self._add(PARALLEL, "the branches of this parallel in %r lock %d "
                          "instances of resource pool %s at once on every path%s; it "
                          "has %d (14.2)" % (_simple(a), n, self._pool_name(p),
                                             " and share another" if need > n else "",
                                             p.size),
                          par.loc)

    # -- select branches that can never run (M8b5a) ---------------------------

    def dead_branches(self) -> Dict[int, Tuple[Any, Any, str]]:
        """``id(branch) -> (select, branch, why)`` for each select branch
        that can never run in any instance its action runs in: one that
        traverses an action whose buffer input no action type can produce
        (13.4.1), or one that locks more of a pool than the parallel
        branches beside it leave (14.2). Run-time cases are not decided."""
        out: Dict[int, Tuple[Any, Any, str]] = {}
        for a in sorted(self._where):
            act = getattr(self.types[a], "activity_ir", None)
            insts = self._where.get(a, [])
            if act is None or not insts:
                continue
            handles: Dict[str, str] = {}
            for t in _traversals(act):
                q = getattr(t, "type_qname", None)
                if isinstance(t, ActivityTraversal) and q is not None:
                    handles[t.handle] = q
                elif isinstance(t, ActivityAnonTraversal) and q is not None and t.label:
                    handles[t.label] = q
            seen: Dict[int, List[Any]] = {}
            for inst in insts:
                dead: Dict[int, Tuple[Any, Any, str]] = {}
                self._concur(inst, handles, act, {}, dead)
                for k, v in dead.items():
                    seen.setdefault(k, []).append(v)
            # one lowering serves every instance: dead only if dead in each
            out.update((k, v[0]) for k, v in seen.items() if len(v) == len(insts))
        return out

    def _must(self, stmts) -> List[str]:
        """Action types a statement list traverses on every path."""
        out: List[str] = []
        for s in stmts if isinstance(stmts, list) else [stmts]:
            if isinstance(s, (ActivityTraversal, ActivityAnonTraversal)):
                if s.type_qname is not None:
                    out.append(s.type_qname)
            elif isinstance(s, (ActivitySequenceBlock, ActivityAtomic, ActivityParallel,
                                ActivitySchedule)):
                out += self._must(s.stmts)
        return out

    def _no_producer(self, inst: int, stmts) -> Optional[str]:
        for q in self._must(stmts):
            k = self._inst_of(inst, q)
            if k is None:
                continue
            for f in self.icl.ref_fields(q, (FieldKind.Input,)):
                if self._flow_kind(f) != "BUFFER":
                    continue
                pool = self.table.pool_of(k, q, f)
                if pool is not None and not self.icl.producers(pool):
                    return ("%r needs buffer input %r from pool %s, which no action "
                            "outputs to" % (_simple(q), f.name, self._pool_name(pool)))
        return None

    def _concur(self, inst: int, handles, s, outside: Dict[PoolInst, int], dead) -> None:
        """Find dead select branches under *s*, which starts while the locks
        *outside* (other parallel branches') are held."""
        if isinstance(s, list):
            for i, st in enumerate(s):
                self._concur(inst, handles, st, outside if i == 0 else {}, dead)
        elif isinstance(s, ActivitySelect):
            for br in s.branches:
                why = self._no_producer(inst, br.body)
                if why is None and outside:
                    for p, n in self._start(inst, handles, br.body).items():
                        if p.size is not None and n + outside.get(p, 0) > p.size:
                            why = ("it locks %d of resource pool %s while the parallel "
                                   "branches beside it hold %d; it has %d" % (
                                       n, self._pool_name(p), outside[p], p.size))
                            break
                if why is not None:
                    dead[id(br)] = (s, br, why)
                else:
                    self._concur(inst, handles, br.body, outside, dead)
        elif isinstance(s, ActivityParallel):
            starts = [self._start(inst, handles, br) for br in s.stmts]
            for i, br in enumerate(s.stmts):
                o = dict(outside)
                for j, st in enumerate(starts):
                    if j != i:
                        for p, n in st.items():
                            o[p] = o.get(p, 0) + n
                self._concur(inst, handles, br, o, dead)
        elif isinstance(s, (ActivitySequenceBlock, ActivityAtomic)):
            self._concur(inst, handles, s.stmts, outside, dead)
        elif isinstance(s, ActivityIfElse):
            self._concur(inst, handles, s.if_body or [], outside, dead)
            self._concur(inst, handles, s.else_body or [], outside, dead)
        elif isinstance(s, ActivityMatch):
            for c in s.cases:
                self._concur(inst, handles, c.body, outside, dead)
        else:
            for f in dc.fields(s) if dc.is_dataclass(s) else []:
                v = getattr(s, f.name, None)
                if f.name in ("body", "stmts") and isinstance(v, list):
                    self._concur(inst, handles, v, {}, dead)


__all__ = ["FlowChecks", "FlowProblem", "STREAM", "CLAIMS", "STATE_ARRAY",
           "BIND", "PARALLEL"]
