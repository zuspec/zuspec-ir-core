"""Inference candidates, precomputed (design §5.2, LRM 18.2, Annex E).

A buffer input no explicit action can feed may be fed by an inferred
action: one the activity does not traverse, added by the planner. Which
actions could be inferred is known at compile time: the producers the ICL
tables (icl.py) list for the input's pool, whose output type fits the
input's, and, for each, the producers of its own buffer inputs, and so on.

:class:`InferPlanner` builds that tree for every demand, bounded so the
executor only picks among a few candidates (decision D5):

* a chain is at most :data:`MAX_DEPTH` inferred actions deep;
* one demand has at most :data:`MAX_SLOTS` candidates in its whole tree;
* an action already on the chain (a cycle) is not a candidate again.

A candidate is *viable* when it is explicit (traversed: the activity may
feed the input from it) or every buffer input it has is fed by a viable
candidate. State inputs read the pool's current (or initial) object and
stream inputs pair with their partner, so neither stops a chain here.

A traversal whose buffer input has producers but no viable one can never
run: an ``INFER`` :class:`FlowProblem` located at the traversal.
"""
from __future__ import annotations

import dataclasses as dc
from typing import Any, Dict, List, Optional, Tuple

from ...activity import ActivityAnonTraversal, ActivityTraversal
from ...data_type import DataTypeArray
from ...fields import FieldKind
from ..validate import UnsupportedConstructError
from .flow_checks import FlowProblem, _simple
from .icl import IclEntry, IclTable, _traversals
from .layout import resolve

INFER = "infer"
#: inferred actions on one chain (D5)
MAX_DEPTH = 8
#: candidates in the tree of one demand (D5)
MAX_SLOTS = 32


@dc.dataclass(frozen=True)
class InferDemand:
    """A buffer input *field* of a candidate, and the candidates that may
    feed it (viable ones only, explicit first)."""
    field: str
    cands: Tuple['InferCand', ...] = ()


@dc.dataclass(frozen=True)
class InferCand:
    """Action *action* in component instance *inst* outputs *field* into the
    demand's pool. An explicit candidate is traversed by the activity and
    has no *needs*; an inferred one lists a demand per buffer input."""
    inst: int
    action: str
    field: str
    explicit: bool
    needs: Tuple[InferDemand, ...] = ()


class InferPlanner:
    """Inference candidates and limit errors for the model *icl* covers."""

    def __init__(self, icl: IclTable):
        self.icl = icl
        self.table = icl.table
        self.types = icl.types
        self.problems: List[FlowProblem] = []
        self._memo: Dict[Tuple[int, str, str], Tuple[InferCand, ...]] = {}
        self._where: Dict[str, List[int]] = {}
        for inst in sorted(self.table.insts):
            for a in icl.actions_of(inst):
                self._where.setdefault(a, []).append(inst)
        for a in sorted(icl.traversed):
            for inst in self._where.get(a, []):
                self._sites(inst, a)

    # -- helpers --------------------------------------------------------------

    def _type(self, x) -> Any:
        try:
            return resolve(x, self.types)
        except UnsupportedConstructError:
            return x

    def _elem(self, f) -> Any:
        t = self._type(f.datatype)
        return self._type(t.element_type) if isinstance(t, DataTypeArray) else t

    def _fits(self, out_t, in_t) -> bool:
        """An object of type *out_t* may be input as *in_t* (same or derived)."""
        seen = []
        t = out_t
        while t is not None and not any(t is s for s in seen):
            if t is in_t:
                return True
            seen.append(t)
            sup = getattr(t, "super", None)
            t = self._type(sup) if sup is not None else None
        return False

    def _field(self, action: str, name: str):
        return next((f for f in self.icl.ref_fields(action) if f.name == name), None)

    def _buffer_inputs(self, action: str) -> List[Any]:
        return [f for f in self.icl.ref_fields(action, (FieldKind.Input,))
                if getattr(getattr(self._elem(f), "flow_kind", None), "name", None)
                == "BUFFER"]

    def _producers(self, inst: int, action: str, f) -> Optional[List[IclEntry]]:
        """Type-fitting producers of input *f*; None when it has no pool."""
        pool = self.table.pool_of(inst, action, f)
        if pool is None:
            return None
        want = self._elem(f)
        out = []
        for e in self.icl.producers(pool):
            of = self._field(e.action, e.field)
            if of is not None and self._fits(self._elem(of), want):
                out.append(e)
        return out

    # -- candidate trees ------------------------------------------------------

    def slots(self, inst: int, action: str, field: str) -> Tuple[InferCand, ...]:
        """The viable candidates feeding input *field* of *action* run in
        component instance *inst* (explicit first; at most MAX_SLOTS in
        the whole tree)."""
        key = (inst, action, field)
        if key not in self._memo:
            f = self._field(action, field)
            budget = [MAX_SLOTS]
            self._memo[key] = (self._cands(inst, action, f, ((inst, action),), budget)
                               if f is not None else ())
        return self._memo[key]

    def state_slots(self, inst: int, action: str, field: str) -> Tuple[InferCand, ...]:
        """M9a5: the writers that may be inferred before state input
        *field* of *action* run in *inst* when the pool's current object
        cannot satisfy it: every writer of the pool, traversed or not (one
        the activity traverses cannot help a read due now)."""
        key = (inst, action, "$state." + field)
        if key not in self._memo:
            f = self._field(action, field)
            budget = [MAX_SLOTS]
            self._memo[key] = (self._cands(inst, action, f, ((inst, action),), budget,
                                           state=True)
                               if f is not None else ())
        return self._memo[key]

    def stream_slots(self, inst: int, action: str, field: str) -> Tuple[InferCand, ...]:
        """M9a6: the partners that may be inferred for stream reference
        *field* of *action* run in *inst* when the activity gives it none:
        the type-fitting consumers of an output, or producers of an input,
        traversed or not (each is a new node, so none is *explicit*). A
        partner's buffer inputs are demands, as in :meth:`slots`."""
        key = (inst, action, "$stream." + field)
        if key not in self._memo:
            f = self._field(action, field)
            out = []
            pool = self.table.pool_of(inst, action, f) if f is not None else None
            if pool is not None:
                budget = [MAX_SLOTS]
                mine = self._elem(f)
                ents = (self.icl.consumers(pool) if f.kind == FieldKind.Output
                        else self.icl.producers(pool))
                seen = set()
                for e in ents:
                    pf = self._field(e.action, e.field)
                    if pf is None or (e.inst, e.action, e.field) in seen or budget[0] <= 0:
                        continue
                    if not (self._fits(mine, self._elem(pf)) if f.kind == FieldKind.Output
                            else self._fits(self._elem(pf), mine)):
                        continue
                    seen.add((e.inst, e.action, e.field))
                    budget[0] -= 1
                    needs = []
                    for g in self._buffer_inputs(e.action):
                        sub = self._cands(e.inst, e.action, g,
                                          ((inst, action), (e.inst, e.action)), budget)
                        if not sub:
                            break
                        needs.append(InferDemand(g.name, sub))
                    else:
                        out.append(InferCand(e.inst, e.action, e.field, False, tuple(needs)))
            self._memo[key] = tuple(out)
        return self._memo[key]

    def _cands(self, inst, action, f, path, budget, state=False) -> Tuple[InferCand, ...]:
        out = []
        seen = set()
        for e in self._producers(inst, action, f) or []:
            if budget[0] <= 0:
                break
            if state:
                if (e.inst, e.action, e.field) in seen:
                    continue
                seen.add((e.inst, e.action, e.field))
            elif e.explicit:
                budget[0] -= 1
                out.append(InferCand(e.inst, e.action, e.field, True))
                continue
            if (not state and (e.inst, e.action) in path) or len(path) > MAX_DEPTH:
                # a state chain may repeat an action (M9a5c: a counter)
                continue
            budget[0] -= 1
            needs = []
            for g in self._buffer_inputs(e.action):
                sub = self._cands(e.inst, e.action, g, path + ((e.inst, e.action),), budget)
                if not sub:
                    break
                needs.append(InferDemand(g.name, sub))
            else:
                out.append(InferCand(e.inst, e.action, e.field, False, tuple(needs)))
        return tuple(out)

    # -- traversal sites ------------------------------------------------------

    def _sites(self, inst: int, a: str) -> None:
        for t in _traversals(getattr(self.types[a], "activity_ir", None)):
            if not isinstance(t, (ActivityTraversal, ActivityAnonTraversal)):
                continue
            q = getattr(t, "type_qname", None)
            if q is None or q not in self.types:
                continue
            for k in self._where.get(q, []):
                if self.table._in_subtree(k, inst):
                    self._check(k, q, getattr(t, "loc", None))

    def _check(self, inst: int, q: str, loc) -> None:
        for f in self._buffer_inputs(q):
            prods = self._producers(inst, q, f)
            if not prods or self.slots(inst, q, f.name):
                continue
            p = FlowProblem(INFER, "no inference chain can feed input %r of %r: "
                            "its producers (%s) need each other or a chain deeper "
                            "than %d actions (18.2)" % (
                                f.name, _simple(q),
                                ", ".join(sorted({_simple(e.action) for e in prods})),
                                MAX_DEPTH),
                            loc or getattr(f, "loc", None))
            if p not in self.problems:
                self.problems.append(p)


__all__ = ["INFER", "MAX_DEPTH", "MAX_SLOTS", "InferCand", "InferDemand",
           "InferPlanner"]
