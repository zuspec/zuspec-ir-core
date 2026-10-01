"""The elaborated component tree under the root component (P1.5).

**One object (P1-D1).** Every component instance of the tree -- the root, each
component field, each element of a component array -- owns a slot range of ONE
flattened component object. An instance's slots are its type's data
attributes (``layout.field_leaves``: a struct attribute is one slot per
scalar), with each sub-instance's subtree in place of its field, in
declaration order, base type's fields first. So a component type's subtree has
the same shape wherever it is instantiated: a sub-instance is at a static slot
offset, and a static instance-number offset, from its parent.

**Instances are numbered in pre-order.** An action's ``comp`` is an instance
number; ``comp.sub1`` is ``comp`` plus ``sub1``'s offset in the type of
``comp`` -- a linear expression, which is what lets the solve choose an
action's instance (P1-D4).

**Construction (9.1.4.1 d, 20.1.3).** Every instance's declared initial
values, then each ``exec init_down`` top-down, then each ``exec init_up``
bottom-up: the order LRM Example 281 lists.
"""
from __future__ import annotations

import dataclasses as dc
from typing import Any, Dict, List, Optional, Tuple

from ... import expr as E
from ...data_type import DataTypeArray, DataTypeComponent, DataTypeRef
from ...scenario import ScCompInit, ScCompInstance, ScComponentTree, ScField
from ...stmt import StmtAssign
from ..validate import UnsupportedConstructError
from .layout import Leaf, field_leaves, prefix_self, resolve


def _loc(n):
    return getattr(n, "loc", None)


@dc.dataclass
class Sub:
    """A component instance inside a component type's subtree."""
    type_qname: str
    slot: int          # its first slot, from the enclosing type's base
    inst: int          # its instance number, from the enclosing type's


@dc.dataclass
class CompLayout:
    """The static layout of one component type's subtree."""
    qname: str
    dt: Any
    #: every slot: (path from the type, leaf), in slot order
    slots: List[Tuple[str, Leaf]] = dc.field(default_factory=list)
    #: every instance below the type, by path (``a``, ``a.sub``, ``ch[2]``)
    subs: Dict[str, Sub] = dc.field(default_factory=dict)
    #: the component-array fields, by name: their element count
    arrays: Dict[str, int] = dc.field(default_factory=dict)
    count: int = 1

    @property
    def size(self) -> int:
        return len(self.slots)

    def slot_of(self, path: str) -> Optional[int]:
        for i, (name, _) in enumerate(self.slots):
            if name == path:
                return i
        return None


class CompLayouts:
    """Every component type's :class:`CompLayout`, computed once."""

    def __init__(self, types: Dict[str, Any]):
        self.types = types
        self._done: Dict[str, CompLayout] = {}
        self._open: List[str] = []

    # -- types ----------------------------------------------------------------

    def qname_of(self, dt: Any) -> Optional[str]:
        """The type map's name for component type object *dt*."""
        for q, t in self.types.items():
            if t is dt and "::" in q:
                return q
        for q, t in self.types.items():
            if t is dt:
                return q
        return getattr(dt, "name", None)

    def field_type(self, f) -> Optional[str]:
        """The component type field *f* instantiates, or None if it is data."""
        dt = f.datatype
        if isinstance(dt, DataTypeArray):
            dt = dt.element_type
        if isinstance(dt, DataTypeRef):
            dt = self.types.get(dt.ref_name)
        if not isinstance(dt, DataTypeComponent):
            return None
        tq = getattr(f, "type_qname", None)
        if tq is not None and isinstance(self.types.get(tq), DataTypeComponent):
            return tq
        return self.qname_of(dt)

    def chain(self, qname: str) -> List[Any]:
        """Component type *qname* and its bases, base first."""
        out = []
        dt = self.types.get(qname)
        while dt is not None:
            if any(c is dt for c in out):
                raise UnsupportedConstructError(
                    "component %r inherits from itself" % qname, loc=_loc(dt))
            out.append(dt)
            sup = getattr(dt, "super", None)
            dt = resolve(sup, self.types) if sup is not None else None
        return list(reversed(out))

    def is_a(self, qname: str, base: str) -> bool:
        """Is component type *qname* *base* or derived from it?"""
        b = self.types.get(base)
        return b is not None and any(t is b for t in self.chain(qname))

    def function(self, qname: str, name: str, functions: Dict[str, Any]):
        """Function *name* of component type *qname*: its own, else the
        nearest base's (component functions are virtual)."""
        for dt in reversed(self.chain(qname)):
            fn = functions.get("%s::%s" % (self.qname_of(dt), name))
            if fn is not None:
                return fn
        return None

    def exec_block(self, qname: str, kind: str):
        """The ``exec`` block of *kind* type *qname* runs: its own, else the
        nearest base's."""
        for dt in reversed(self.chain(qname)):
            for fn in getattr(dt, "functions", []) or []:
                meta = getattr(fn, "metadata", None) or {}
                if meta.get("exec_kind") == kind:
                    return fn
        return None

    # -- layouts --------------------------------------------------------------

    def get(self, qname: str, loc=None) -> CompLayout:
        lay = self._done.get(qname)
        if lay is not None:
            return lay
        if qname in self._open:
            chain = " -> ".join(self._open[self._open.index(qname):] + [qname])
            raise UnsupportedConstructError(
                "component %r is instantiated under its own subtree (%s; LRM "
                "9.1.4.1 a)" % (qname, chain), loc=loc)
        if not isinstance(self.types.get(qname), DataTypeComponent):
            raise UnsupportedConstructError(
                "%r is not a component type" % qname, loc=loc)
        self._open.append(qname)
        try:
            lay = self._build(qname)
        finally:
            self._open.pop()
        self._done[qname] = lay
        return lay

    def _build(self, qname: str) -> CompLayout:
        lay = CompLayout(qname=qname, dt=self.types[qname])
        for dt in self.chain(qname):
            for f in getattr(dt, "fields", []) or []:
                tq = self.field_type(f)
                if tq is None:
                    for leaf in field_leaves(f, self.types):
                        lay.slots.append((leaf.name, leaf))
                    continue
                if isinstance(f.datatype, DataTypeArray):
                    n = f.datatype.size
                    if n is None or n < 0:
                        raise UnsupportedConstructError(
                            "component array %r has no static size" % f.name,
                            loc=_loc(f))
                    lay.arrays[f.name] = n
                    keys = ["%s[%d]" % (f.name, i) for i in range(n)]
                else:
                    keys = [f.name]
                for key in keys:
                    self._place(lay, key, self.get(tq, loc=_loc(f)))
        return lay

    @staticmethod
    def _place(lay: CompLayout, key: str, sub: CompLayout) -> None:
        slot, inst = lay.size, lay.count
        lay.subs[key] = Sub(sub.qname, slot, inst)
        for path, s in sub.subs.items():
            lay.subs["%s.%s" % (key, path)] = Sub(s.type_qname, slot + s.slot, inst + s.inst)
        for name, leaf in sub.slots:
            lay.slots.append(("%s.%s" % (key, name), leaf))
        lay.count += sub.count

    def candidates(self, ctx: str, comp: str) -> List[int]:
        """The instances of component type *comp* an action may run in from
        an action of *ctx* (9.1.5.1): *ctx*'s instance itself, if it is a
        *comp*, and every *comp* in its subtree -- as instance offsets from
        *ctx*'s, in pre-order."""
        lay = self.get(ctx)
        out = [0] if self.is_a(ctx, comp) else []
        out += sorted(s.inst for s in lay.subs.values() if self.is_a(s.type_qname, comp))
        return out


def build_comp_tree(layouts: CompLayouts, root: str) -> ScComponentTree:
    """The :class:`ScComponentTree` of root component *root*."""
    lay = layouts.get(root, loc=_loc(layouts.types.get(root)))
    tree = ScComponentTree(root=root, size=lay.size)
    tree.instances.append(ScCompInstance(id=0, path="", type_qname=root, base=0,
                                         size=lay.size, count=lay.count))
    by_inst: Dict[int, str] = {0: ""}
    for path, sub in sorted(lay.subs.items(), key=lambda kv: kv[1].inst):
        sl = layouts.get(sub.type_qname)
        parent = path.rsplit(".", 1)[0] if "." in path else ""
        pid = next(i for i, p in by_inst.items() if p == parent)
        tree.instances.append(ScCompInstance(
            id=sub.inst, path=path, type_qname=sub.type_qname, base=sub.slot,
            size=sl.size, count=sl.count, parent=pid))
        by_inst[sub.inst] = path
    tree.instances.sort(key=lambda i: i.id)
    for slot, (name, leaf) in enumerate(lay.slots):
        tree.fields.append(ScField(name=name, slot=slot, datatype=leaf.datatype,
                                   rand=False))

    # Initial values: each instance's own attributes, written against it.
    for inst in tree.instances:
        stmts = []
        for dt in layouts.chain(inst.type_qname):
            for f in getattr(dt, "fields", []) or []:
                if layouts.field_type(f) is not None:
                    continue
                for leaf in field_leaves(f, layouts.types):
                    init = getattr(leaf.field, "initial_value", None)
                    if init is None:
                        continue
                    target: Any = E.TypeExprRefSelf()
                    for p in leaf.path:
                        target = E.ExprAttribute(value=target, attr=p)
                    stmts.append(StmtAssign(targets=[target],
                                            value=prefix_self(init, leaf.path[:-1])))
        if stmts:
            tree.init.append(ScCompInit(instance=inst.id, kind="init", stmts=stmts))
    for inst in tree.instances:
        fn = layouts.exec_block(inst.type_qname, "init_down")
        if fn is not None and fn.body:
            tree.init.append(ScCompInit(instance=inst.id, kind="init_down",
                                        stmts=list(fn.body)).copy_loc(fn))
    for inst in _post_order(tree):
        fn = layouts.exec_block(inst.type_qname, "init_up")
        if fn is not None and fn.body:
            tree.init.append(ScCompInit(instance=inst.id, kind="init_up",
                                        stmts=list(fn.body)).copy_loc(fn))
    return tree


def _post_order(tree: ScComponentTree) -> List[ScCompInstance]:
    children: Dict[Optional[int], List[ScCompInstance]] = {}
    for inst in tree.instances:
        children.setdefault(inst.parent, []).append(inst)
    out: List[ScCompInstance] = []

    def walk(inst):
        for c in children.get(inst.id, []):
            walk(c)
        out.append(inst)
    walk(tree.instances[0])
    return out


__all__ = ["CompLayout", "CompLayouts", "Sub", "build_comp_tree"]
