"""Static constraint cones (design §4 item 7, §5.3).

A traversal's *cone* is the connected component of its occurrence in the
constraint/binding graph. This module computes the part of that graph known
from the model alone, per compound action:

* **bind**: an explicit ``bind a.o b.i;`` in the activity joins ``a`` and ``b``.
* **icl**: a flow reference whose pool has exactly one candidate on the other
  side (one producer action type for an input, one consumer for an output)
  joins the traversal to the sibling traversals of that type, or, when none
  is traversed, to an *inferred* occurrence of it.
* **constraint**: a constraint of the compound action (``constraint`` block
  or activity ``constraint``) joins every handle it reads.
* **inline**: a traversal's ``with { ... }`` block joins it to every earlier
  handle it reads.

The run time adds the edges structural decisions make (an inference decided,
a resource instance chosen, state ticket order). No edge is cut here (D18):
cone-break analysis waits for telemetry, which :meth:`ConeTable.sizes` gives.
"""
from __future__ import annotations

import dataclasses as dc
from typing import Any, Dict, List, Optional, Set, Tuple

from ...activity import (ActivityAnonTraversal, ActivityBind,
                         ActivityConstraint, ActivityTraversal)
from ...expr import TypeExprRefSelf
from ...fields import FieldKind
from .icl import IclTable, _traversals
from .sample_points import _path, _reads, _walk

BIND = "bind"
ICL = "icl"
CONSTRAINT = "constraint"
INLINE = "inline"

#: the name of an occurrence Annex E inference would add
INFERRED = "<inferred>"


@dc.dataclass(frozen=True)
class Site:
    """One traversal in compound action *action*: its handle or label, or
    ``#<n>`` (its index among the activity's traversals) when unnamed;
    ``<inferred>`` for an occurrence only inference can add."""
    action: str
    name: str
    type: str

    def __str__(self) -> str:
        return "%s.%s" % (self.action.rsplit("::", 1)[-1], self.name)


@dc.dataclass(frozen=True)
class ConeEdge:
    a: Site
    b: Site
    kind: str               # BIND, ICL, CONSTRAINT or INLINE
    loc: Any = dc.field(default=None, compare=False)


class ConeTable:
    """The static cones of every compound action *icl* covers."""

    def __init__(self, icl: IclTable):
        self.icl = icl
        self.types = icl.types
        self.sites: List[Site] = []
        self.edges: List[ConeEdge] = []
        self._parent: Dict[Site, Site] = {}
        for a in sorted(icl._actions):
            self._action(a)

    # -- union-find -----------------------------------------------------------

    def _add(self, s: Site) -> None:
        if s not in self._parent:
            self._parent[s] = s
            self.sites.append(s)

    def _find(self, s: Site) -> Site:
        while self._parent[s] is not s:
            self._parent[s] = self._parent[self._parent[s]]
            s = self._parent[s]
        return s

    def _edge(self, a: Site, b: Site, kind: str, loc=None) -> None:
        if a == b:
            return
        self._add(a)
        self._add(b)
        self.edges.append(ConeEdge(a, b, kind, loc))
        ra, rb = self._find(a), self._find(b)
        if ra is not rb:
            self._parent[ra] = rb

    # -- one compound action --------------------------------------------------

    def _action(self, action: str) -> None:
        dt = self.types[action]
        act = getattr(dt, "activity_ir", None)
        if act is None:
            return
        handles: Dict[str, Site] = {}
        order: List[Tuple[Site, Any]] = []
        for i, t in enumerate(_traversals(act)):
            q = getattr(t, "type_qname", None)
            if q is None:
                continue
            if isinstance(t, ActivityTraversal):
                name = t.handle
            elif isinstance(t, ActivityAnonTraversal):
                name = t.label or "#%d" % i
            else:
                continue
            s = handles.get(name) or Site(action, name, q)
            handles[name] = s
            self._add(s)
            order.append((s, t))

        def roots(exprs) -> List[Site]:
            out = []
            for root, path, _ in _reads(exprs):
                s = handles.get(path[0]) if root is TypeExprRefSelf else None
                if s is not None and s not in out:
                    out.append(s)
            return out

        # explicit binds; the references they bind take no ICL edge
        bound: Set[Tuple[Site, str]] = set()
        for b in _walk(act):
            if not isinstance(b, ActivityBind):
                continue
            ends = []
            for e in (b.src, b.dst):
                p = _path(e)
                if p is None or p[0] is not TypeExprRefSelf:
                    continue
                s = handles.get(p[1][0])
                if s is not None:
                    ends.append(s)
                    if len(p[1]) > 1:
                        bound.add((s, p[1][1]))
            for s in ends[1:]:
                self._edge(ends[0], s, BIND, b.loc)

        # parent constraints: every handle one constraint reads
        exprs = [(fn.body, getattr(fn, "loc", None))
                 for d in self.icl._chain(action)
                 for fn in getattr(d, "functions", [])
                 if (fn.metadata or {}).get("_is_constraint")]
        exprs += [(c.constraints, c.loc) for c in _walk(act)
                  if isinstance(c, ActivityConstraint)]
        for body, loc in exprs:
            rs = roots(body)
            for s in rs[1:]:
                self._edge(rs[0], s, CONSTRAINT, loc)

        # sibling inline constraints
        for s, t in order:
            body = list(t.inline_constraints) + [v for _, v in
                                                 getattr(t, "initializers", [])]
            for r in roots(body):
                self._edge(s, r, INLINE, t.loc)

        # single-candidate ICL
        by_type: Dict[str, List[Site]] = {}
        for s in handles.values():
            by_type.setdefault(s.type, []).append(s)
        for s in list(handles.values()):
            for f in self.icl.ref_fields(s.type, (FieldKind.Input, FieldKind.Output)):
                if (s, f.name) in bound:
                    continue
                other = self._single(s.type, f)
                if other is None:
                    continue
                peers = by_type.get(other) or [Site(action, INFERRED, other)]
                for p in peers:
                    self._edge(s, p, ICL, getattr(f, "loc", None))

    def _single(self, qname: str, f) -> Optional[str]:
        """The one action type on the other side of reference *f* of
        *qname* in every pool it reaches, or None."""
        icl = self.icl
        mine = icl._cons if f.kind == FieldKind.Input else icl._prod
        theirs = icl._prod if f.kind == FieldKind.Input else icl._cons
        cands: Set[str] = set()
        reached = False
        for pool, es in mine.items():
            if any(e.action == qname and e.field == f.name for e in es):
                reached = True
                cands.update(e.action for e in theirs.get(pool, []))
        return next(iter(cands)) if reached and len(cands) == 1 else None

    # -- queries --------------------------------------------------------------

    def cone(self, site: Site) -> List[Site]:
        """The sites in *site*'s static cone (itself included)."""
        r = self._find(site)
        return [s for s in self.sites if self._find(s) is r]

    def cones(self) -> List[List[Site]]:
        out: Dict[Site, List[Site]] = {}
        for s in self.sites:
            out.setdefault(self._find(s), []).append(s)
        return list(out.values())

    def sizes(self) -> Dict[Site, int]:
        """Telemetry: each site's static cone size, in occurrences."""
        n: Dict[Site, int] = {}
        for c in self.cones():
            for s in c:
                n[s] = len(c)
        return n

    def edges_of(self, site: Site) -> List[ConeEdge]:
        return [e for e in self.edges if site in (e.a, e.b)]


__all__ = ["ConeEdge", "ConeTable", "Site", "BIND", "ICL", "CONSTRAINT",
           "INLINE", "INFERRED"]
