"""Inference candidate (ICL) tables (design §4 item 3, LRM Annex E).

For each pool instance, which actions output into it (producers) and which
input from it (consumers): ``(component instance, action type, reference
field)`` triples, found through the pool-binding table (pools.py), so a
reference reaches the pool the planner will bind it to and no other.

Each candidate is *explicit* when its action type is traversed in an activity
(``do A``, ``a;``) and *anonymous* otherwise. Annex E tries explicit
candidates before anonymous ones; :meth:`IclTable.producers` and
:meth:`IclTable.consumers` list them in that order. The domain of every
inference decision is therefore known at compile time.

Abstract action types are never candidates: they cannot be traversed or
inferred. A traversal of a type also marks the types derived from it explicit
(a handle of a base type may hold any of them).
"""
from __future__ import annotations

import dataclasses as dc
from typing import Any, Dict, Iterable, List, Optional, Set

from ...activity import (ActivityAnonTraversal, ActivityStmt, ActivitySuper,
                         ActivityTraversal, MatchCase, SelectBranch)
from ...data_type import DataTypeClass, DataTypeComponent
from ...fields import FieldKind
from .layout import resolve
from .pools import PoolInst, PoolTable


@dc.dataclass(frozen=True)
class IclEntry:
    """A flow-object reference of an action type in a component instance."""
    inst: int          # the component instance the action runs in
    action: str        # the action type, qualified
    field: str         # its input or output reference
    explicit: bool     # the action type is traversed in some activity


def _is_action_type(dt: Any) -> bool:
    return (isinstance(dt, DataTypeClass) and not isinstance(dt, DataTypeComponent)
            and getattr(dt, "flow_kind", None) is None)


def _traversals(x) -> Iterable[Any]:
    """Every traversal and ``super;`` under activity node or list *x*."""
    if isinstance(x, list):
        for s in x:
            yield from _traversals(s)
    elif isinstance(x, (ActivityStmt, SelectBranch, MatchCase)):
        if isinstance(x, (ActivityTraversal, ActivityAnonTraversal, ActivitySuper)):
            yield x
        for f in dc.fields(x):
            v = getattr(x, f.name, None)
            if isinstance(v, (list, ActivityStmt, SelectBranch, MatchCase)):
                yield from _traversals(v)


class IclTable:
    """The ICL tables of the component tree *table* covers.

    *root_action*, if given, limits "explicit" to the action types its
    activity reaches (through compound actions and ``super;``); otherwise
    any activity in the model counts.
    """

    def __init__(self, table: PoolTable, root_action: Optional[str] = None):
        self.table = table
        self.types = table.types
        # a type registered under two names (``pkg::c::A`` and ``c::A``)
        # is one action: keep its longest name
        by_id: Dict[int, str] = {}
        for n, dt in self.types.items():
            if _is_action_type(dt) and len(n) > len(by_id.get(id(dt), "")):
                by_id[id(dt)] = n
        self._actions: Dict[str, Any] = {
            n: self.types[n] for n in self.types if n in by_id.values()}
        self.traversed: Set[str] = self._traversed(root_action)
        #: pool -> producers / consumers, explicit first
        self._prod: Dict[PoolInst, List[IclEntry]] = {}
        self._cons: Dict[PoolInst, List[IclEntry]] = {}
        #: references no bind reaches
        self.unbound: List[IclEntry] = []
        self._build()

    # -- action types ---------------------------------------------------------

    def _chain(self, qname: str) -> List[Any]:
        """Action type *qname* and its bases, base first."""
        out = []
        dt = self.types.get(qname)
        while dt is not None and not any(d is dt for d in out):
            out.append(dt)
            sup = getattr(dt, "super", None)
            dt = resolve(sup, self.types) if sup is not None else None
        return list(reversed(out))

    def _derived(self, qname: str) -> List[str]:
        """Action types that are *qname* or derive from it."""
        base = self.types.get(qname)
        return [n for n in self._actions
                if any(d is base for d in self._chain(n))]

    def _traversed(self, root: Optional[str]) -> Set[str]:
        if root is not None:
            rdt = self.types.get(root)
            root = next((n for n, dt in self._actions.items() if dt is rdt), root)
        seen: Set[str] = set()
        work = [root] if root is not None else list(self._actions)
        named: Set[str] = {root} if root is not None else set()
        while work:
            n = work.pop()
            if n in seen or n not in self._actions:
                continue
            seen.add(n)
            for dt in self._chain(n):
                for t in _traversals(getattr(dt, "activity_ir", None)):
                    q = getattr(t, "type_qname", None) or getattr(t, "action_type", None)
                    if isinstance(t, ActivitySuper) or q is None:
                        continue
                    for d in self._derived(q):
                        named.add(d)
                        if root is not None:
                            work.append(d)
        return named

    def _flow_fields(self, qname: str) -> List[Any]:
        return self.ref_fields(qname, (FieldKind.Input, FieldKind.Output))

    def ref_fields(self, qname: str, kinds=(FieldKind.Input, FieldKind.Output,
                                            FieldKind.Lock, FieldKind.Share)
                   ) -> List[Any]:
        """The flow-object references and resource claims of action type
        *qname* (inherited ones first) whose kind is in *kinds*."""
        return [f for dt in self._chain(qname) for f in getattr(dt, "fields", [])
                if f.kind in kinds]

    def actions_of(self, inst: int) -> List[str]:
        """Concrete action types component instance *inst* declares,
        with those of its type's bases."""
        chain = self.table.comps.chain(self.table.insts[inst][0])
        comps = {n for n, dt in self.types.items()
                 if any(dt is c for c in chain)}
        return [n for n, dt in self._actions.items()
                if not getattr(dt, "is_abstract", False)
                and "::" in n and n.rsplit("::", 1)[0] in comps]

    # -- the tables -----------------------------------------------------------

    def _build(self) -> None:
        for inst in sorted(self.table.insts):
            for a in self.actions_of(inst):
                for f in self._flow_fields(a):
                    e = IclEntry(inst, a, f.name, a in self.traversed)
                    pool = self.table.pool_of(inst, a, f)
                    if pool is None:
                        self.unbound.append(e)
                        continue
                    side = self._prod if f.kind == FieldKind.Output else self._cons
                    side.setdefault(pool, []).append(e)
        for side in (self._prod, self._cons):
            for k, v in side.items():
                side[k] = sorted(v, key=lambda e: not e.explicit)

    def pools(self) -> List[PoolInst]:
        """Every pool some reference reaches."""
        out = []
        for p in list(self._prod) + list(self._cons):
            if p not in out:
                out.append(p)
        return out

    def producers(self, pool: PoolInst) -> List[IclEntry]:
        """Outputs into *pool*: explicit candidates, then anonymous ones."""
        return list(self._prod.get(pool, []))

    def consumers(self, pool: PoolInst) -> List[IclEntry]:
        """Inputs from *pool*: explicit candidates, then anonymous ones."""
        return list(self._cons.get(pool, []))


__all__ = ["IclEntry", "IclTable"]
