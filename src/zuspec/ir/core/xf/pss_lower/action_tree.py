"""The action tree of an exported action, and its solve cones (P1.2).

**The tree (P1-D1).** Every action an activation can run -- the root, each
handle (an attribute ``B b1;``, an element of ``B bs[2];``, a declaration in
an activity block), each anonymous traversal site (``do B``), and each
iteration's instance under a labeled ``replicate`` -- is a node with its own
slot range in ONE flattened object. A node's slots are its type's layout
(``layout.object_layout``); its children follow it, in declaration order. So
an action type's subtree has the same shape wherever it is instantiated, and
the offset of a child from its parent is static (``ScInvoke.child_base``).
An action that traverses itself, directly or not, has no finite tree and is a
located error.

A site inside a loop (``repeat``, ``foreach``, an unlabeled ``replicate``) is
ONE node, reset on each iteration (13.4.8); only a labeled ``replicate``
names each iteration's instances (``R[0].#0``), so only it multiplies nodes,
and only with a constant count (O4).

**The cones (P1-D2, P1-D3).** Every constraint that can be in force on the
activation -- each node type's own (and its struct attributes'), each
activity ``constraint`` (13.1.9), each inline ``with`` (13.1.4) -- is
resolved to slots of the object and tagged with when it holds. Nodes tied by a
constraint form a cone, an :class:`~...scenario.ScScopeProblem`. A node
nothing ties to another, with only its type's constraints, is in no cone: it
keeps the solve its coroutine already has.
"""
from __future__ import annotations

import dataclasses as dc
from typing import Any, Dict, List, Optional, Tuple

from ... import expr as E
from ...activity import (
    ActivityAnonTraversal, ActivityAtomic, ActivityConstraint, ActivityDoWhile,
    ActivityFieldDecl, ActivityForeach, ActivityIfElse, ActivityMatch,
    ActivityParallel, ActivityRepeat, ActivityReplicate, ActivitySchedule,
    ActivitySelect, ActivitySequenceBlock, ActivityTraversal, ActivityWhileDo,
)
from ...data_type import DataTypeArray, DataTypeClass, DataTypeComponent, DataTypeRef
from ...scenario import (
    ScActionNode, ScActionTree, ScActivityScope, ScScopeConstraint,
    ScScopeProblem, ScScopeVar, ScTraversalSite, ScopeConstraintKind, ScopeKind,
)
from ..validate import UnsupportedConstructError
from .constraints import (constraint_sites, expr_to_constraints,
                          stmt_to_constraints, type_constraints)
from .layout import object_layout

_LOOPS = (ActivityRepeat, ActivityForeach, ActivityDoWhile, ActivityWhileDo)


def _loc(n):
    return getattr(n, "loc", None)


def _is_action_type(dt: Any) -> bool:
    return (isinstance(dt, DataTypeClass) and not isinstance(dt, DataTypeComponent)
            and getattr(dt, "flow_kind", None) is None)


# --------------------------------------------------------------------------- #
# Per-type layout: the same wherever the type is instantiated
# --------------------------------------------------------------------------- #

@dc.dataclass
class _Decl:
    """A child an action type declares."""
    key: str                  # its path element(s): "b1", "bs[1]", "#0", "R[0].#0"
    type_qname: str
    rel_base: int = 0         # its subtree's offset from the parent's base
    scope: int = 0            # local scope declaring it (0: the activity)


@dc.dataclass
class _Site:
    """A traversal statement of the type's activity."""
    stmt: Any
    key: Optional[str]        # the child it traverses; None: no single one
    scope: int                # local scope holding it
    names: Dict[str, str]     # handle name -> key, where the statement is


@dc.dataclass
class _Constraint:
    stmt: ActivityConstraint
    scope: int
    names: Dict[str, str]


@dc.dataclass
class TypeLayout:
    """The static layout of one action type's subtree.

    ``scopes`` are the type's activity blocks in walk order, local index 0 the
    activity itself: ``(kind, local parent)``.
    """
    qname: str
    dt: Any
    own: list                                   # layout.Leaf, slot order
    decls: List[_Decl] = dc.field(default_factory=list)
    sites: List[_Site] = dc.field(default_factory=list)
    constraints: List[_Constraint] = dc.field(default_factory=list)
    scopes: List[Tuple[ScopeKind, Optional[int]]] = dc.field(default_factory=list)
    fields: Dict[str, str] = dc.field(default_factory=dict)  # handle field -> key
    size: int = 0

    def site_keys(self, stmt) -> List[Optional[str]]:
        return [s.key for s in self.sites if s.stmt is stmt]

    def child_base(self, stmt) -> Optional[int]:
        """``ScInvoke.child_base`` of traversal *stmt*, or None if it has no
        single node."""
        keys = self.site_keys(stmt)
        if len(keys) != 1 or keys[0] is None:
            return None
        for d in self.decls:
            if d.key == keys[0]:
                return d.rel_base
        return None


class Layouts:
    """Every action type's :class:`TypeLayout`, computed once."""

    def __init__(self, types: Dict[str, Any]):
        self.types = types
        self._done: Dict[str, TypeLayout] = {}
        self._open: List[str] = []

    def get(self, qname: str, loc=None) -> TypeLayout:
        lay = self._done.get(qname)
        if lay is not None:
            return lay
        if qname in self._open:
            chain = " -> ".join(self._open[self._open.index(qname):] + [qname])
            raise UnsupportedConstructError(
                "action %r traverses itself (%s): a recursive activity has no "
                "finite action tree" % (qname, chain), loc=loc)
        self._open.append(qname)
        try:
            lay = self._build(qname)
        finally:
            self._open.pop()
        self._done[qname] = lay
        return lay

    def try_get(self, qname: Optional[str]) -> Optional[TypeLayout]:
        """The layout, or None if there is none (no such action, or one that
        is recursive -- refused where a tree is built)."""
        if qname is None or not _is_action_type(self.types.get(qname)):
            return None
        try:
            return self.get(qname)
        except UnsupportedConstructError:
            return None

    # -- building -----------------------------------------------------------

    def handle_type(self, f) -> Optional[str]:
        """The action type of handle field *f*, or None if it is not one."""
        tq = getattr(f, "type_qname", None)
        if tq is None:
            dt = f.datatype
            if isinstance(dt, DataTypeArray):
                dt = dt.element_type
            if isinstance(dt, DataTypeRef):
                tq = dt.ref_name
        return tq if _is_action_type(self.types.get(tq)) else None

    def _build(self, qname: str) -> TypeLayout:
        dt = self.types[qname]
        lay = TypeLayout(qname=qname, dt=dt,
                         own=object_layout(getattr(dt, "fields", []) or [], self.types))
        for f in getattr(dt, "fields", []) or []:
            tq = self.handle_type(f)
            if tq is None:
                continue
            lay.fields[f.name] = f.name
            if isinstance(f.datatype, DataTypeArray):
                if f.datatype.size is None or f.datatype.size < 0:
                    raise UnsupportedConstructError(
                        "handle array %r has no static size" % f.name, loc=_loc(f))
                for i in range(f.datatype.size):
                    lay.decls.append(_Decl("%s[%d]" % (f.name, i), tq))
            else:
                lay.decls.append(_Decl(f.name, tq))
        act = getattr(dt, "activity_ir", None)
        if act is not None:
            lay.scopes.append((ScopeKind.ACTIVITY, None))
            stmts = act.stmts if isinstance(act, ActivitySequenceBlock) else [act]
            _Walker(self, lay).walk(stmts, "", [dict(lay.fields)], 0)
        rel = len(lay.own)
        for d in lay.decls:
            d.rel_base = rel
            rel += self.get(d.type_qname, loc=_loc(dt)).size
        lay.size = rel
        return lay


class _Walker:
    """One walk of a type's activity: its declarations, traversal sites,
    activity constraints and blocks."""

    def __init__(self, layouts: Layouts, lay: TypeLayout):
        self.layouts = layouts
        self.lay = lay
        self.anon: Dict[str, int] = {}

    def _scope(self, kind: ScopeKind, parent: int) -> int:
        self.lay.scopes.append((kind, parent))
        return len(self.lay.scopes) - 1

    def _key(self, want: str) -> str:
        taken = {d.key for d in self.lay.decls}
        key, n = want, 1
        while key in taken:
            key, n = "%s@%d" % (want, n), n + 1
        return key

    @staticmethod
    def _names(chain) -> Dict[str, str]:
        out: Dict[str, str] = {}
        for scope in chain:
            out.update(scope)
        return out

    def walk(self, stmts, prefix: str, chain, scope: int) -> None:
        chain = chain + [{}]
        for s in stmts or []:
            self.stmt(s, prefix, chain, scope)

    def stmt(self, s, prefix, chain, scope) -> None:
        lay = self.lay
        if isinstance(s, ActivityFieldDecl):
            if s.type_qname is not None:
                key = self._key(prefix + s.field.name)
                lay.decls.append(_Decl(key, s.type_qname, scope=scope))
                chain[-1][s.field.name] = key
            return
        if isinstance(s, ActivityAnonTraversal):
            tq = s.type_qname
            if tq is None and _is_action_type(self.layouts.types.get(s.action_type)):
                tq = s.action_type
            if tq is None:
                return                          # the pass refuses it
            if s.label is not None:
                key = self._key(prefix + s.label)
            else:
                n = self.anon.get(prefix, 0)
                self.anon[prefix] = n + 1
                key = self._key("%s#%d" % (prefix, n))
            lay.decls.append(_Decl(key, tq, scope=scope))
            lay.sites.append(_Site(s, key, scope, self._names(chain)))
            return
        if isinstance(s, ActivityTraversal):
            base = self._names(chain).get(s.handle)
            key = base
            if base is not None and s.index is not None:
                idx = s.index
                key = ("%s[%d]" % (base, idx.value)
                       if isinstance(idx, E.ExprConstant) and isinstance(idx.value, int)
                       else None)
            lay.sites.append(_Site(s, key, scope, self._names(chain)))
            return
        if isinstance(s, ActivityConstraint):
            lay.constraints.append(_Constraint(s, scope, self._names(chain)))
            return
        if isinstance(s, ActivityReplicate) and s.label is not None:
            count = s.count
            if not (isinstance(count, E.ExprConstant) and isinstance(count.value, int)):
                raise UnsupportedConstructError(
                    "replicate with an iteration label (%s[]) needs a constant "
                    "count: each iteration's actions are nodes of the action "
                    "tree" % s.label, loc=_loc(s))
            for i in range(count.value):
                sub = self._scope(ScopeKind.REPLICATE_ITER, scope)
                self.walk(s.body, "%s%s[%d]." % (prefix, s.label, i), chain, sub)
            return
        if isinstance(s, ActivityReplicate) or isinstance(s, _LOOPS):
            self.walk(s.body, prefix, chain, self._scope(ScopeKind.LOOP_BODY, scope))
            return
        kinds = {ActivitySequenceBlock: ScopeKind.SEQUENCE,
                 ActivityParallel: ScopeKind.PARALLEL,
                 ActivitySchedule: ScopeKind.SCHEDULE,
                 ActivityAtomic: ScopeKind.ATOMIC}
        if type(s) in kinds:
            self.walk(s.stmts, prefix, chain, self._scope(kinds[type(s)], scope))
            return
        if isinstance(s, ActivitySelect):
            for br in s.branches:
                self.walk(br.body, prefix, chain,
                          self._scope(ScopeKind.SELECT_BRANCH, scope))
            return
        if isinstance(s, ActivityIfElse):
            self.walk(s.if_body, prefix, chain, self._scope(ScopeKind.IF_THEN, scope))
            self.walk(s.else_body, prefix, chain, self._scope(ScopeKind.IF_ELSE, scope))
            return
        if isinstance(s, ActivityMatch):
            for case in s.cases:
                self.walk(case.body, prefix, chain,
                          self._scope(ScopeKind.MATCH_CASE, scope))
            return
        # Anything else holds no action and no constraint the tree needs; the
        # scenario pass lowers or refuses it.


# --------------------------------------------------------------------------- #
# The tree of one export, and its cones
# --------------------------------------------------------------------------- #

class _Find:
    """Union-find over node ids."""

    def __init__(self):
        self.up: Dict[int, int] = {}

    def find(self, x: int) -> int:
        self.up.setdefault(x, x)
        while self.up[x] != x:
            self.up[x] = self.up[self.up[x]]
            x = self.up[x]
        return x

    def union(self, a: int, b: int) -> None:
        self.up[self.find(a)] = self.find(b)


class TreeBuilder:
    """Builds the :class:`ScActionTree` of one exported action."""

    def __init__(self, layouts: Layouts):
        self.layouts = layouts

    def build(self, root: str, qname: str) -> ScActionTree:
        self.tree = ScActionTree(root=root, type_qname=qname)
        self._lay: Dict[int, TypeLayout] = {}
        self._scope_base: Dict[int, int] = {}
        self._child: Dict[Tuple[int, str], int] = {}
        self._site_src: Dict[int, _Site] = {}
        lay = self.layouts.get(qname, loc=_loc(self.layouts.types.get(qname)))
        self._node(lay, 0, "", None, None, None)
        self.tree.size = lay.size
        self._cones()
        return self.tree

    # -- nodes, scopes, sites -------------------------------------------------

    def _node(self, lay: TypeLayout, base: int, path: str, parent: Optional[int],
              decl_scope: Optional[int], activity_parent: Optional[int]) -> int:
        tree = self.tree
        nid = len(tree.nodes)
        tree.nodes.append(ScActionNode(id=nid, path=path, type_qname=lay.qname,
                                       base=base, size=len(lay.own), parent=parent,
                                       decl_scope=decl_scope))
        self._lay[nid] = lay
        sbase = len(tree.scopes)
        self._scope_base[nid] = sbase
        for i, (kind, lparent) in enumerate(lay.scopes):
            tree.scopes.append(ScActivityScope(
                id=sbase + i, kind=kind, node=nid,
                parent=(sbase + lparent) if lparent is not None else activity_parent))
        # A site's scope, for the ACTIVITY scope of the child it first reaches.
        first_site: Dict[str, int] = {}
        for st in lay.sites:
            if st.key is not None:
                first_site.setdefault(st.key, sbase + st.scope)
        for d in lay.decls:
            # A handle declared in the action body is reset on entry to the
            # activity; one declared in a block, on entry to that block.
            dscope = (sbase + d.scope) if lay.scopes else None
            cid = self._node(self.layouts.get(d.type_qname), base + d.rel_base,
                             (path + "." if path else "") + d.key, nid, dscope,
                             first_site.get(d.key))
            tree.nodes[nid].children.append(cid)
            self._child[(nid, d.key)] = cid
        for st in lay.sites:
            if st.key is None:
                continue
            self._site_src[len(tree.sites)] = st
            tree.sites.append(ScTraversalSite(
                id=len(tree.sites), owner=nid, target=self._child[(nid, st.key)],
                scope=sbase + st.scope))
        return nid

    # -- resolution ---------------------------------------------------------

    def _resolver(self, nid: int, names: Dict[str, str], target: Optional[int] = None):
        def resolve(root, path):
            if root == "traversed":
                if target is None:
                    return None
                lay = self._lay[target]
                return self._lookup(target, path, dict(lay.fields))
            return self._lookup(nid, path, names)
        return resolve

    def _lookup(self, nid: int, path, names: Dict[str, str]) -> Optional[int]:
        """The absolute slot *path* names from node *nid*, or None."""
        node = self.tree.nodes[nid]
        lay = self._lay[nid]
        name = ".".join(path)
        for i, leaf in enumerate(lay.own):
            if leaf.name == name:
                return node.base + i
        head = path[0]
        base, br, idx = head.partition("[")
        key = names.get(base)
        if key is None or len(path) < 2:
            return None
        key = key + br + idx
        child = self._child.get((nid, key))
        if child is None:
            return None
        return self._lookup(child, path[1:], dict(self._lay[child].fields))

    def _owner_of(self, slot: int) -> Optional[int]:
        for n in self.tree.nodes:
            if n.base <= slot < n.base + n.size:
                return n.id
        return None

    @staticmethod
    def _slots(c) -> List[int]:
        out: List[int] = []

        def walk(x):
            if isinstance(x, E.ExprRefField):
                out.append(x.index)
                return
            if dc.is_dataclass(x) and not isinstance(x, type):
                for f in dc.fields(x):
                    v = getattr(x, f.name)
                    if isinstance(v, list):
                        for y in v:
                            walk(y)
                    else:
                        walk(v)
        walk(c)
        return out

    # -- constraints and cones ----------------------------------------------

    def _constraints(self) -> List[ScScopeConstraint]:
        tree = self.tree
        out: List[ScScopeConstraint] = []

        def add(cs, kind, owner, scope=None, site=None):
            for c in cs:
                nodes = sorted({self._owner_of(s) for s in self._slots(c)} - {None})
                out.append(ScScopeConstraint(constraint=c, nodes=nodes, kind=kind,
                                             owner=owner, scope=scope, site=site))

        for node in tree.nodes:
            lay = self._lay[node.id]
            sbase = self._scope_base[node.id]
            resolve = self._resolver(node.id, dict(lay.fields))
            for prefix, fn in constraint_sites(type_constraints(lay.dt), lay.dt,
                                               self.layouts.types):
                for kind, where in (getattr(fn, "metadata", None) or {}).get(
                        "untranslated", ()):
                    raise UnsupportedConstructError(
                        "%s'%s' constraint in %r is not supported yet"
                        % (where, kind, getattr(fn, "name", "?")), loc=_loc(fn))
                for st in getattr(fn, "body", []) or []:
                    add(stmt_to_constraints(st, resolve, fn, prefix),
                        ScopeConstraintKind.TYPE, node.id)
            for ac in lay.constraints:
                r = self._resolver(node.id, ac.names)
                for e in ac.stmt.constraints:
                    add(expr_to_constraints(e, r), ScopeConstraintKind.ACTIVITY,
                        node.id, scope=sbase + ac.scope)
        for site in tree.sites:
            stmt = self._site_src[site.id]
            if not getattr(stmt.stmt, "inline_constraints", None):
                continue
            r = self._resolver(site.owner, stmt.names, target=site.target)
            for e in stmt.stmt.inline_constraints:
                add(expr_to_constraints(e, r), ScopeConstraintKind.WITH,
                    site.owner, site=site.id)
        return out

    def _cones(self) -> None:
        tree = self.tree
        cons = self._constraints()
        uf = _Find()
        for n in tree.nodes:
            uf.find(n.id)
        for c in cons:
            members = c.nodes or [c.owner]
            for m in members[1:]:
                uf.union(members[0], m)
        groups: Dict[int, List[int]] = {}
        for n in tree.nodes:
            groups.setdefault(uf.find(n.id), []).append(n.id)
        for members in groups.values():
            mset = set(members)
            cs = [c for c in cons if set(c.nodes or [c.owner]) <= mset]
            trivial = len(members) == 1 and all(
                c.kind == ScopeConstraintKind.TYPE and c.owner == members[0]
                for c in cs)
            if trivial:
                continue
            tree.cones.append(ScScopeProblem(
                id=len(tree.cones), nodes=sorted(members),
                vars=self._vars(sorted(members), cs), constraints=cs))

    def _vars(self, members: List[int], cs) -> List[ScScopeVar]:
        read = set()
        for c in cs:
            read.update(self._slots(c.constraint))
        out: List[ScScopeVar] = []
        for nid in members:
            node = self.tree.nodes[nid]
            for i, leaf in enumerate(self._lay[nid].own):
                slot = node.base + i
                if not leaf.rand and slot not in read:
                    continue
                width = getattr(leaf.datatype, "bits", 32)
                if width is None or width <= 0:
                    width = 32
                out.append(ScScopeVar(
                    name=(node.path + "." if node.path else "") + leaf.name,
                    node=nid, slot=slot, width=width,
                    signed=bool(getattr(leaf.datatype, "signed", False)),
                    rand=leaf.rand))
        return out


def build_tree(layouts: Layouts, root: str, qname: str) -> ScActionTree:
    """The :class:`ScActionTree` of action *qname*, exported as *root*."""
    return TreeBuilder(layouts).build(root, qname)


__all__ = ["Layouts", "TypeLayout", "TreeBuilder", "build_tree"]
