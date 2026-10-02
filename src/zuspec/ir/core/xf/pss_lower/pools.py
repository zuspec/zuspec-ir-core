"""The pool-binding table (design §4.2, LRM 12.3).

Which pool an action's flow-object reference or resource claim uses depends on
the component instance the action runs in: ``(component instance, action
type, reference field) -> pool instance``. This module is the ONE walk that
answers it, so no consumer folds 12.3's precedence rules a second way.

A pool instance is a pool declaration in a component instance: the pools of
``lane_c`` are four pools in ``lanes[0..3]``. A ``bind`` in a component
instance J reaches:

* ``bind p *`` -- every reference of ``p``'s object type in actions of J's
  subtree (a *default* binding);
* ``bind p {sub.*}`` -- the same, in the subtree of instance ``sub`` of J;
* ``bind p {sub.A.f}`` -- reference ``f`` of action type ``A`` in instance
  ``sub`` of J (an *explicit* binding).

Explicit bindings take precedence over default ones (12.3 c); among bindings
of one kind, the one in the top-most instance does (12.3 e). Two explicit
bindings of one reference in one instance (d), or two default ones (f), are
errors, as is binding a reference to a pool of another type (g).
"""
from __future__ import annotations

import dataclasses as dc
from typing import Any, Dict, List, Optional, Tuple

from ..validate import UnsupportedConstructError
from .layout import resolve


def _loc(n):
    return getattr(n, "loc", None)


@dc.dataclass(frozen=True)
class PoolInst:
    """A pool declaration in a component instance."""
    inst: int          # the component instance declaring it
    name: str
    decl: Any = dc.field(compare=False, hash=False)     # ir.Pool

    @property
    def size(self) -> Optional[int]:
        return getattr(self.decl, "capacity", None)

    def __str__(self) -> str:
        return "%s@%d" % (self.name, self.inst)


class PoolTable:
    """The pool-binding table of the component tree under *root*.

    *comps* is the scenario pass's ``comp_tree.CompLayouts``.
    """

    def __init__(self, comps, root: str):
        self.comps = comps
        self.types = comps.types
        self.root = root
        lay = comps.get(root)
        #: instance id -> (type, path from the root, subtree count)
        self.insts: Dict[int, Tuple[str, str, int]] = {0: (root, "", lay.count)}
        for path, sub in lay.subs.items():
            self.insts[sub.inst] = (sub.type_qname, path,
                                    comps.get(sub.type_qname).count)
        self._by_path = {p: i for i, (_, p, _) in self.insts.items()}
        self._parent: Dict[int, Optional[int]] = {0: None}
        for i, (_, path, _) in self.insts.items():
            if i:
                self._parent[i] = self._by_path[path.rsplit(".", 1)[0]
                                                if "." in path else ""]
        self._memo: Dict[Tuple[int, str, str], Optional[PoolInst]] = {}

    # -- instances ----------------------------------------------------------

    def _ancestors(self, inst: int) -> List[int]:
        """*inst* and the instances above it, the root first."""
        out = []
        while inst is not None:
            out.append(inst)
            inst = self._parent[inst]
        return list(reversed(out))

    def _under(self, base: int, rel: List[str]) -> Optional[int]:
        """The instance *rel* names from instance *base*."""
        if not rel:
            return base
        path = self.insts[base][1]
        full = ".".join(([path] if path else []) + rel)
        return self._by_path.get(full)

    def _in_subtree(self, inst: int, top: int) -> bool:
        return top <= inst < top + self.insts[top][2]

    def pools(self, inst: int) -> List[PoolInst]:
        """The pools component instance *inst* declares (bases' first)."""
        out = []
        for dt in self.comps.chain(self.insts[inst][0]):
            for p in getattr(dt, "pools", []) or []:
                out.append(PoolInst(inst, p.name, p))
        return out

    def _pool(self, inst: int, path: List[str], loc) -> PoolInst:
        owner = self._under(inst, path[:-1])
        found = [p for p in self.pools(owner)] if owner is not None else []
        for p in found:
            if p.name == path[-1]:
                return p
        raise UnsupportedConstructError(
            "bind names pool %r, which component %r does not declare"
            % (".".join(path), self.insts[inst][0]), loc=loc)

    # -- types --------------------------------------------------------------

    def _type_of(self, x) -> Any:
        try:
            return resolve(x, self.types)
        except UnsupportedConstructError:
            return x

    def _pool_type(self, p: PoolInst) -> Any:
        if p.decl.element_type is not None:
            return self._type_of(p.decl.element_type)
        return self.types.get(p.decl.element_type_name)

    def _same_type(self, p: PoolInst, ref_type: Any) -> bool:
        return self._pool_type(p) is self._type_of(ref_type)

    # -- the table ------------------------------------------------------------

    def _binds(self, inst: int):
        for dt in self.comps.chain(self.insts[inst][0]):
            for b in getattr(dt, "pool_binds", []) or []:
                yield b

    def pool_of(self, inst: int, action: str, field: Any) -> Optional[PoolInst]:
        """The pool reference *field* of an action of type *action* uses when
        the action runs in component instance *inst*; None if no bind
        reaches it."""
        key = (inst, action, field.name)
        if key not in self._memo:
            self._memo[key] = self._resolve(inst, action, field)
        return self._memo[key]

    def _resolve(self, inst: int, action: str, field: Any) -> Optional[PoolInst]:
        simple = action.rsplit("::", 1)[-1]
        explicit: Optional[Tuple[int, PoolInst]] = None
        default: Optional[Tuple[int, PoolInst]] = None
        for top in self._ancestors(inst):
            for b in self._binds(top):
                loc = _loc(b)
                pool = self._pool(top, list(b.pool_path or [b.pool_name]), loc)
                hits = []
                if b.is_wildcard and self._in_subtree(inst, top):
                    hits.append(False)
                for fp in b.field_paths:
                    parts = fp.split(".")
                    if parts[-1] == "*":
                        sub = self._under(top, parts[:-1])
                        if sub is not None and self._in_subtree(inst, sub):
                            hits.append(False)
                    elif (len(parts) >= 2 and parts[-1] == field.name
                          and parts[-2] == simple
                          and self._under(top, parts[:-2]) == inst):
                        hits.append(True)
                for is_explicit in hits:
                    if is_explicit:
                        if not self._same_type(pool, field.datatype):
                            raise UnsupportedConstructError(
                                "bind of %s.%s to pool %r: the pool holds "
                                "another type (12.3 g)" % (simple, field.name, b.pool_name),
                                loc=loc)
                        if explicit is not None and explicit[0] == top \
                                and explicit[1] != pool:
                            raise UnsupportedConstructError(
                                "%s.%s is bound explicitly to two pools, %s and "
                                "%s (12.3 d)" % (simple, field.name, explicit[1], pool),
                                loc=loc)
                        if explicit is None:
                            explicit = (top, pool)
                    elif self._same_type(pool, field.datatype):
                        if default is not None and default[0] == top \
                                and default[1] != pool:
                            raise UnsupportedConstructError(
                                "%s.%s is bound by default to two pools of "
                                "component %r, %s and %s (12.3 f)"
                                % (simple, field.name, self.insts[top][0],
                                   default[1], pool), loc=loc)
                        if default is None:
                            default = (top, pool)
        if explicit is not None:
            return explicit[1]
        return default[1] if default is not None else None


__all__ = ["PoolInst", "PoolTable"]
