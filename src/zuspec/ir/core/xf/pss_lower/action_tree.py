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

**Components (P1.5, P1-D4).** Each node runs in a component instance: one
of its candidates, the instances of its action's component type in the
subtree of its parent's instance (9.1.5.1; none is a located error, LRM
Ex 51). With one candidate the instance is static, relative to the parent's.
With more, the node's ``comp`` is a variable of its cone, in a slot after
the action subtrees (``ScActionNode.comp_slot``), constrained to the
candidates (``ScopeConstraintKind.COMP``) and by a traversal's
``comp == X`` (a ``with``).

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
from ...constraint import ConstraintExpr, ConstraintImplies
from ..validate import UnsupportedConstructError
from . import defaults as D
from .constraints import (constraint_sites, default_path, expr_to_constraints,
                          resolve_refs, stmt_to_constraints, type_constraints)
from .layout import domain_expr, leaf_domain, object_layout, subst_names

_LOOPS = (ActivityRepeat, ActivityForeach, ActivityDoWhile, ActivityWhileDo)


def _runs_once(s) -> bool:
    """Does loop *s* surely run its body: a do-while, or a positive constant
    count?"""
    if isinstance(s, ActivityDoWhile):
        return True
    count = getattr(s, "count", None)
    return (isinstance(s, (ActivityRepeat, ActivityReplicate))
            and isinstance(count, E.ExprConstant)
            and isinstance(count.value, int) and count.value > 0)


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
    iters: Tuple[int, ...] = ()   # its labeled-replicate iteration(s)
    consts: Dict[str, int] = dc.field(default_factory=dict)  # their index vars
    indices: Tuple[str, ...] = ()  # the index variables of the loops around it


@dc.dataclass
class _Constraint:
    stmt: ActivityConstraint
    scope: int
    names: Dict[str, str]
    consts: Dict[str, int] = dc.field(default_factory=dict)


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
    #: (id(block), part) -> local scope index, for the scenario pass to mark
    #: the statements that open each block (``ScSeq.scope``, ...)
    scope_of: Dict[Tuple[int, Any], int] = dc.field(default_factory=dict)

    def _site(self, stmt, iters=()) -> Optional[_Site]:
        found = [s for s in self.sites if s.stmt is stmt and s.iters == tuple(iters)]
        return found[0] if len(found) == 1 and found[0].key is not None else None

    def child_base(self, stmt, iters=()) -> Optional[int]:
        """``ScInvoke.child_base`` of traversal *stmt* (in labeled-replicate
        iteration *iters*), or None if it has no single node."""
        site = self._site(stmt, iters)
        if site is None:
            return None
        for d in self.decls:
            if d.key == site.key:
                return d.rel_base
        return None

    def site_index(self, stmt, iters=()) -> Optional[int]:
        """``ScInvoke.site``: the traversal's index among the sites that have
        a node, in walk order -- the order ``TreeBuilder`` numbers them."""
        site = self._site(stmt, iters)
        if site is None:
            return None
        keyed = [s for s in self.sites if s.key is not None]
        return next(i for i, s in enumerate(keyed) if s is site)

    def scope(self, block, part=()) -> Optional[int]:
        """The local scope *block* (with *part*: ``"then"``/``"else"``, a
        replicate iteration) opens."""
        return self.scope_of.get((id(block), part))


class Layouts:
    """Every action type's :class:`TypeLayout`, computed once."""

    def __init__(self, types: Dict[str, Any]):
        self.types = types
        self._done: Dict[str, TypeLayout] = {}
        self._open: List[str] = []
        #: why a type has no layout (``try_get`` returned None)
        self.errors: Dict[str, UnsupportedConstructError] = {}

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
        except UnsupportedConstructError as e:
            self.errors[qname] = e
            return None

    # -- building -----------------------------------------------------------

    def subtree(self, qname: str) -> List[Tuple[str, int, Any]]:
        """Every slot of *qname*'s subtree: ``(path name, slot relative to
        its base, layout.Leaf)``. The type's own leaves, then each child's
        subtree under its key (``b1.x``, ``#0.s.f``)."""
        lay = self.get(qname)
        out = [(leaf.name, i, leaf) for i, leaf in enumerate(lay.own)]
        for d in lay.decls:
            for name, slot, leaf in self.subtree(d.type_qname):
                out.append(("%s.%s" % (d.key, name), d.rel_base + slot, leaf))
        return out

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
        #: the index variables of the (unrolled-at-run-time) loops being walked
        self.loop_vars: List[str] = []

    def _scope(self, kind: ScopeKind, parent: int, block=None, part=None) -> int:
        self.lay.scopes.append((kind, parent))
        idx = len(self.lay.scopes) - 1
        if block is not None:
            self.lay.scope_of[(id(block), part)] = idx
        return idx

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

    def walk(self, stmts, prefix: str, chain, scope: int, iters=(), consts=None) -> None:
        chain = chain + [{}]
        for s in stmts or []:
            self.stmt(s, prefix, chain, scope, iters, consts or {})

    def stmt(self, s, prefix, chain, scope, iters=(), consts=None) -> None:
        consts = consts or {}
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
                # `l1: do A;` names its action `l1` in the block (11.8):
                # `l1.val` in a constraint or a `with` after it is that node.
                chain[-1][s.label] = key
            else:
                n = self.anon.get(prefix, 0)
                self.anon[prefix] = n + 1
                key = self._key("%s#%d" % (prefix, n))
            lay.decls.append(_Decl(key, tq, scope=scope))
            lay.sites.append(_Site(s, key, scope, self._names(chain), tuple(iters),
                                   dict(consts), tuple(self.loop_vars)))
            return
        if isinstance(s, ActivityTraversal):
            base = self._names(chain).get(s.handle)
            key = base
            if base is not None and s.index is not None:
                idx = s.index
                key = ("%s[%d]" % (base, idx.value)
                       if isinstance(idx, E.ExprConstant) and isinstance(idx.value, int)
                       else None)
            if key is not None and (s.inline_constraints or s.comp_expr is not None) \
                    and any(o.key == key and o.scope == scope and o.iters == tuple(iters)
                            for o in lay.sites):
                # O-P1-3: which traversal's values would a constraint over
                # the handle see? Refused rather than guessed.
                raise UnsupportedConstructError(
                    "handle %r is traversed again in the same activity scope, "
                    "with an inline constraint; traverse it in a block of its "
                    "own (13.4.8)" % s.handle, loc=_loc(s))
            lay.sites.append(_Site(s, key, scope, self._names(chain), tuple(iters),
                                   dict(consts), tuple(self.loop_vars)))
            return
        if isinstance(s, ActivityConstraint):
            lay.constraints.append(_Constraint(s, scope, self._names(chain), dict(consts)))
            return
        if isinstance(s, ActivityReplicate) and s.label is not None:
            count = s.count
            if not (isinstance(count, E.ExprConstant) and isinstance(count.value, int)):
                raise UnsupportedConstructError(
                    "replicate with an iteration label (%s[]) needs a constant "
                    "count: each iteration's actions are nodes of the action "
                    "tree" % s.label, loc=_loc(s))
            for i in range(count.value):
                it = tuple(iters) + (i,)
                sub = self._scope(ScopeKind.REPLICATE_ITER, scope, s, it)
                ic = dict(consts, **({s.index_var: i} if s.index_var else {}))
                self.walk(s.body, "%s%s[%d]." % (prefix, s.label, i), chain, sub, it, ic)
            return
        if isinstance(s, ActivityReplicate) or isinstance(s, _LOOPS):
            kind = (ScopeKind.LOOP_BODY_CERTAIN if _runs_once(s)
                    else ScopeKind.LOOP_BODY)
            iv = getattr(s, "index_var", None)
            self.loop_vars.append(iv)
            try:
                self.walk(s.body, prefix, chain, self._scope(kind, scope, s, iters),
                          iters, consts)
            finally:
                self.loop_vars.pop()
            return
        kinds = {ActivitySequenceBlock: ScopeKind.SEQUENCE,
                 ActivityParallel: ScopeKind.PARALLEL,
                 ActivitySchedule: ScopeKind.SCHEDULE,
                 ActivityAtomic: ScopeKind.ATOMIC}
        if type(s) in kinds:
            self.walk(s.stmts, prefix, chain,
                      self._scope(kinds[type(s)], scope, s, iters), iters, consts)
            return
        if isinstance(s, ActivitySelect):
            for br in s.branches:
                self.walk(br.body, prefix, chain,
                          self._scope(ScopeKind.SELECT_BRANCH, scope, br, iters), iters, consts)
            return
        if isinstance(s, ActivityIfElse):
            self.walk(s.if_body, prefix, chain,
                      self._scope(ScopeKind.IF_THEN, scope, s, ("then",) + tuple(iters)),
                      iters, consts)
            self.walk(s.else_body, prefix, chain,
                      self._scope(ScopeKind.IF_ELSE, scope, s, ("else",) + tuple(iters)),
                      iters, consts)
            return
        if isinstance(s, ActivityMatch):
            for case in s.cases:
                self.walk(case.body, prefix, chain,
                          self._scope(ScopeKind.MATCH_CASE, scope, case, iters), iters, consts)
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


def _owner(qname: str) -> str:
    """The component type an action is declared in."""
    return qname.rsplit("::", 1)[0]


class TreeBuilder:
    """Builds the :class:`ScActionTree` of one exported action."""

    def __init__(self, layouts: Layouts, comps=None, root_comp: Optional[str] = None):
        self.layouts = layouts
        #: ``comp_tree.CompLayouts``; None: every node runs in instance 0
        self.comps = comps
        self.root_comp = root_comp

    def build(self, root: str, qname: str) -> ScActionTree:
        self.tree = ScActionTree(root=root, type_qname=qname)
        self._lay: Dict[int, TypeLayout] = {}
        self._scope_base: Dict[int, int] = {}
        self._child: Dict[Tuple[int, str], int] = {}
        self._site_src: Dict[int, _Site] = {}
        dt = self.layouts.types.get(qname)
        lay = self.layouts.get(qname, loc=_loc(dt))
        self._node(lay, 0, "", None, None, None, _loc(dt))
        self.tree.size = lay.size
        # A node choosing among instances holds its choice in a slot of its own.
        self._aux: Dict[int, int] = {}
        #: nodes solved in a cone even with nothing tying them to another
        self._needs_cone: set = set()
        #: (node, key) -> the slot past the subtrees holding a value its
        #: constraints read: ``comp.<path>`` (key: the path), or a loop's
        #: index (key: ``"$" + name``)
        self._aux_reads: Dict[Tuple[int, Any], int] = {}
        #: node -> the variables those reads add to its cone
        self._aux_vars: Dict[int, List[ScScopeVar]] = {}
        #: (node, constraint) tying a read to the node's choice of instance
        self._comp_ties: List[Tuple[int, Any]] = []
        for n in self.tree.nodes:
            if len(n.comp) > 1:
                n.comp_slot = self.tree.size
                self._aux[n.comp_slot] = n.id
                self.tree.size += 1
        self._cones()
        return self.tree

    def _candidates(self, qname: str, parent: Optional[int], loc) -> List[int]:
        if self.comps is None:
            return [0]
        ctx = (_owner(self.tree.nodes[parent].type_qname) if parent is not None
               else self.root_comp)
        cands = self.comps.candidates(ctx, _owner(qname))
        if not cands:
            raise UnsupportedConstructError(
                "action %r runs in component %r, which is not instantiated in "
                "the subtree of %r, its context (9.1.5.1)"
                % (qname, _owner(qname), ctx), loc=loc)
        return cands

    # -- nodes, scopes, sites -------------------------------------------------

    def _node(self, lay: TypeLayout, base: int, path: str, parent: Optional[int],
              decl_scope: Optional[int], activity_parent: Optional[int], loc=None) -> int:
        tree = self.tree
        nid = len(tree.nodes)
        cands = self._candidates(lay.qname, parent, loc)
        tree.nodes.append(ScActionNode(id=nid, path=path, type_qname=lay.qname,
                                       base=base, size=len(lay.own), parent=parent,
                                       decl_scope=decl_scope, comp=cands))
        self._lay[nid] = lay
        sbase = len(tree.scopes)
        self._scope_base[nid] = sbase
        for i, (kind, lparent) in enumerate(lay.scopes):
            tree.scopes.append(ScActivityScope(
                id=sbase + i, kind=kind, node=nid,
                parent=(sbase + lparent) if lparent is not None else activity_parent))
        # A site's scope, for the ACTIVITY scope of the child it first reaches.
        first_site: Dict[str, int] = {}
        first_loc: Dict[str, Any] = {}
        for st in lay.sites:
            if st.key is not None:
                first_site.setdefault(st.key, sbase + st.scope)
                first_loc.setdefault(st.key, _loc(st.stmt))
        for d in lay.decls:
            # A handle declared in the action body is reset on entry to the
            # activity; one declared in a block, on entry to that block.
            dscope = (sbase + d.scope) if lay.scopes else None
            cid = self._node(self.layouts.get(d.type_qname), base + d.rel_base,
                             (path + "." if path else "") + d.key, nid, dscope,
                             first_site.get(d.key), first_loc.get(d.key, loc))
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

    def _resolver(self, nid: int, names: Dict[str, str], target: Optional[int] = None,
                  indices: Tuple[str, ...] = ()):
        def where(root):
            if root == "traversed":
                return (target, dict(self._lay[target].fields)) if target is not None \
                    else (None, None)
            return nid, names

        def resolve(root, path):
            # A loop's index variable shadows an attribute of the same name.
            if (root == "self" and len(path) == 1 and path[0] in indices
                    and target is not None):
                return self._loop_var(target, nid, path[0])
            n, nm = where(root)
            return None if n is None else self._lookup(n, path, nm)

        def leaves(root, path):
            # The scalars of a struct attribute (not of a child action: its
            # own layout's leaves, wherever the path leads).
            n, nm = where(root)
            if n is None:
                return None
            found = self._leaves_under(n, path, nm)
            owners = {self._owner_of(s) for _, s, _ in found}
            if len(owners) != 1 or any(not name for name, _, _ in found):
                return None
            owner = owners.pop()
            lay = self._lay[owner]
            base = self.tree.nodes[owner].base
            if any(lay.own[s - base].name.count(".") == 0 for _, s, _ in found):
                return None                     # a child action, not a struct
            return [(name, s) for name, s, _ in found]
        resolve.leaves = leaves
        return resolve

    def _lookup(self, nid: int, path, names: Dict[str, str]) -> Optional[int]:
        """The absolute slot *path* names from node *nid*, or None."""
        if len(path) > 1 and path[0] == "comp":
            return self._comp_attr(nid, tuple(path[1:]))
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

    def _leaves_under(self, nid: int, path, names: Dict[str, str]) -> list:
        """``[(name, slot, layout.Leaf)]``: the scalar *path* names from node
        *nid*, or each scalar under it if it names an aggregate (a struct
        attribute, or a child action). *name* is relative to *path*: ``""``
        for the scalar itself, ``"f"`` for ``path.f``."""
        node = self.tree.nodes[nid]
        lay = self._lay[nid]
        name = ".".join(path)
        own = [("" if lf.name == name else lf.name[len(name) + 1:], node.base + i, lf)
               for i, lf in enumerate(lay.own)
               if lf.name == name or lf.name.startswith(name + ".")]
        if own:
            return own
        base, br, idx = path[0].partition("[")
        key = names.get(base)
        child = self._child.get((nid, key + br + idx)) if key is not None else None
        if child is None:
            return []
        if len(path) > 1:
            return self._leaves_under(child, path[1:], dict(self._lay[child].fields))
        return self._subtree_leaves(child)

    def _subtree_leaves(self, nid: int) -> list:
        """``[(name, slot, layout.Leaf)]`` of node *nid* and its children."""
        base = self.tree.nodes[nid].base
        lay = self._lay[nid]
        out = [(lf.name, base + i, lf) for i, lf in enumerate(lay.own)]
        for d in lay.decls:
            out.extend(("%s.%s" % (d.key, n), s, lf) for n, s, lf in
                       self._subtree_leaves(self._child[(nid, d.key)]))
        return out

    def _depth(self, nid: int) -> int:
        d = 0
        while self.tree.nodes[nid].parent is not None:
            nid = self.tree.nodes[nid].parent
            d += 1
        return d

    def _owner_of(self, slot: int) -> Optional[int]:
        if slot in self._aux:
            return self._aux[slot]
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

    # -- components -----------------------------------------------------------

    def _instances(self, nid: int) -> List[int]:
        """Every component instance node *nid* may run in, by id."""
        node = self.tree.nodes[nid]
        parents = self._instances(node.parent) if node.parent is not None else [0]
        return sorted({p + c for p in parents for c in node.comp})

    def _loop_var(self, target: int, owner: int, name: str) -> int:
        """The slot of loop index *name*, read by a ``with`` of a traversal
        of *target* in *owner*'s activity (G6): a variable of *target*,
        pinned when it is solved to the loop's counter in *owner*'s frame."""
        key = (target, "$" + name)
        if key not in self._aux_reads:
            node = self.tree.nodes[target]
            slot = self._new_aux(target)
            self._aux_vars.setdefault(target, []).append(ScScopeVar(
                name="%s$%s" % ((node.path + ".") if node.path else "", name),
                node=target, slot=slot, width=32, signed=False, rand=True,
                loop_local=name, loop_node=owner))
            self._aux_reads[key] = slot
        return self._aux_reads[key]

    def _new_aux(self, nid: int) -> int:
        slot = self.tree.size
        self.tree.size += 1
        self._aux[slot] = nid
        return slot

    def _comp_attr(self, nid: int, apath: Tuple[str, ...]) -> Optional[int]:
        """The slot that holds ``comp.<apath>`` for a constraint of node
        *nid* (G4). A component attribute is fixed once the tree is
        constructed, so each instance the node may run in gives an input,
        pinned from the component object when the cone is solved. With one
        instance the read IS that input; with several, it is a variable tied
        to the input of the instance the node's ``comp`` chooses."""
        key = (nid, apath)
        if key in self._aux_reads:
            return self._aux_reads[key]
        node = self.tree.nodes[nid]
        name = ".".join(apath)
        if self.comps is None:
            return None             # no component tree: left for the backend
        root = self.comps.get(self.root_comp)
        prefix = (node.path + "." if node.path else "") + "comp." + name
        insts = self._instances(nid)
        inputs: List[ScScopeVar] = []
        for k in insts:
            if k == 0:
                base, tq = 0, self.root_comp
            else:
                sub = next(s for s in root.subs.values() if s.inst == k)
                base, tq = sub.slot, sub.type_qname
            lay = self.comps.get(tq)
            off = lay.slot_of(name)
            if off is None:
                raise UnsupportedConstructError(
                    "component %r has no attribute %r (read as comp.%s in %r)"
                    % (tq, name, name, node.type_qname), loc=None)
            dom = leaf_domain(lay.slots[off][1], self.layouts.types)
            inputs.append(ScScopeVar(
                name=prefix if len(insts) == 1 else "%s@%d" % (prefix, k),
                node=nid, slot=self._new_aux(nid), width=dom.width,
                signed=dom.signed, rand=False, comp_read=base + off))
        self._aux_vars.setdefault(nid, []).extend(inputs)
        self._needs_cone.add(nid)
        if len(inputs) == 1:
            val = inputs[0].slot
        else:
            val = self._new_aux(nid)
            wide = max(inputs, key=lambda v: (v.width, v.signed))
            self._aux_vars[nid].append(ScScopeVar(
                name=prefix, node=nid, slot=val, width=wide.width,
                signed=wide.signed, rand=True))
            comp = self._comp_expr(*self._comp_of(nid))
            for k, v in zip(insts, inputs):
                self._comp_ties.append((nid, ConstraintImplies(
                    antecedent=E.ExprBin(lhs=comp, op=E.BinOp.Eq,
                                         rhs=E.ExprConstant(value=k)),
                    body=[ConstraintExpr(expr=E.ExprBin(
                        lhs=E.ExprRefField(base=E.TypeExprRefSelf(), index=val),
                        op=E.BinOp.Eq,
                        rhs=E.ExprRefField(base=E.TypeExprRefSelf(), index=v.slot)))])))
        self._aux_reads[key] = val
        return val

    def _comp_of(self, nid: int) -> Tuple[Optional[int], int]:
        """Node *nid*'s instance as ``(slot, offset)``: the value of the slot
        holding the nearest choice on its path (None: no choice, so the
        root component's instance 0), plus a static offset."""
        node = self.tree.nodes[nid]
        if node.comp_slot is not None:
            return node.comp_slot, 0
        slot, off = (self._comp_of(node.parent) if node.parent is not None
                     else (None, 0))
        return slot, off + node.comp[0]

    @staticmethod
    def _comp_expr(slot: Optional[int], off: int) -> Any:
        if slot is None:
            return E.ExprConstant(value=off)
        ref = E.ExprRefField(base=E.TypeExprRefSelf(), index=slot)
        if off == 0:
            return ref
        return E.ExprBin(lhs=ref, op=E.BinOp.Add, rhs=E.ExprConstant(value=off))

    def _comp_choice(self, nid: int) -> Any:
        """A choice node's instance is one of its candidates."""
        node = self.tree.nodes[nid]
        pslot, poff = (self._comp_of(node.parent) if node.parent is not None
                       else (None, 0))
        me = E.ExprRefField(base=E.TypeExprRefSelf(), index=node.comp_slot)
        return E.ExprBool(op=E.BoolOp.Or, values=[
            E.ExprBin(lhs=me, op=E.BinOp.Eq, rhs=self._comp_expr(pslot, poff + c))
            for c in node.comp])

    def _comp_path(self, e, owner: int, target: int, loc) -> Tuple[Optional[int], int, str]:
        """``(slot, offset, type)`` of the instance component reference *e*
        names, in a ``with`` of a traversal of *target* from *owner*:
        ``this.comp`` (*owner*'s), ``comp`` (*target*'s), and sub-instance
        paths below them (``this.comp.sub1``, ``comp.ch[1]``)."""
        if isinstance(e, E.ExprAttribute) and e.attr == "comp" and isinstance(
                e.value, (E.TypeExprRefSelf, E.TypeExprRefTraversed)):
            nid = owner if isinstance(e.value, E.TypeExprRefSelf) else target
            slot, off = self._comp_of(nid)
            return slot, off, _owner(self.tree.nodes[nid].type_qname)
        if isinstance(e, E.ExprRefUnresolved) and e.name == "comp":
            slot, off = self._comp_of(target)
            return slot, off, _owner(self.tree.nodes[target].type_qname)
        key = None
        base = None
        if isinstance(e, E.ExprAttribute):
            base, key = e.value, e.attr
        elif (isinstance(e, E.ExprSubscript) and isinstance(e.value, E.ExprAttribute)
              and isinstance(e.slice, E.ExprConstant) and isinstance(e.slice.value, int)):
            base, key = e.value.value, "%s[%d]" % (e.value.attr, e.slice.value)
        if base is not None:
            slot, off, tq = self._comp_path(base, owner, target, loc)
            sub = self.comps.get(tq).subs.get(key)
            if sub is not None:
                return slot, off + sub.inst, sub.type_qname
        raise UnsupportedConstructError(
            "`comp == ...` names something that is not a component instance "
            "this action can reach (this.comp, comp, or a sub-instance path "
            "below one)", loc=loc)

    def _comp_with(self, site) -> Optional[Any]:
        """A traversal's ``comp == X``, as a constraint over instances; None
        when it holds whatever is chosen."""
        stmt = self._site_src[site.id].stmt
        x = getattr(stmt, "comp_expr", None)
        if x is None:
            return None
        if self.comps is None:
            raise UnsupportedConstructError(
                "`comp == ...` needs the component tree", loc=_loc(stmt))
        lhs = self._comp_of(site.target)
        rslot, roff, _ = self._comp_path(x, site.owner, site.target, _loc(stmt))
        if lhs[0] is None and rslot is None:
            if lhs[1] != roff:
                raise UnsupportedConstructError(
                    "`comp == ...` can never hold: the action's only instance "
                    "is not the one named", loc=_loc(stmt))
            return None
        return E.ExprBin(lhs=self._comp_expr(*lhs), op=E.BinOp.Eq,
                         rhs=self._comp_expr(rslot, roff))

    # -- constraints and cones ----------------------------------------------

    def _constraints(self) -> List[ScScopeConstraint]:
        tree = self.tree
        out: List[ScScopeConstraint] = []

        def add(cs, kind, owner, scope=None, site=None):
            for c in cs:
                nodes = sorted({self._owner_of(s) for s in self._slots(c)} - {None})
                out.append(ScScopeConstraint(constraint=c, nodes=nodes, kind=kind,
                                             owner=owner, scope=scope, site=site))

        defaults: List[D.DefaultStmt] = []
        seq = 0
        for node in tree.nodes:
            lay = self._lay[node.id]
            sbase = self._scope_base[node.id]
            resolve = self._resolver(node.id, dict(lay.fields))
            depth = self._depth(node.id)
            for prefix, fn in constraint_sites(type_constraints(lay.dt), lay.dt,
                                               self.layouts.types):
                for kind, where in (getattr(fn, "metadata", None) or {}).get(
                        "untranslated", ()):
                    raise UnsupportedConstructError(
                        "%s'%s' constraint in %r is not supported yet"
                        % (where, kind, getattr(fn, "name", "?")), loc=_loc(fn))
                for st in getattr(fn, "body", []) or []:
                    if D.is_default(st):
                        # Higher in the tree, then shallower in the node's
                        # own structs, then later, wins (13.1.11 d).
                        seq += 1
                        path = default_path(st, prefix)
                        defaults.append(D.default_stmt(
                            st, (-depth, -len(prefix), seq), node.id,
                            [(s, lf) for _, s, lf in
                             self._leaves_under(node.id, path, dict(lay.fields))],
                            lambda v, r=resolve, p=prefix: resolve_refs(v, r, p)))
                        continue
                    add(stmt_to_constraints(st, resolve, fn, prefix),
                        ScopeConstraintKind.TYPE, node.id)
            # A rand leaf holds only its type's values (an enum's members),
            # wherever it is solved.
            for i, leaf in enumerate(lay.own):
                held = leaf.rand and domain_expr(
                    node.base + i, leaf_domain(leaf, self.layouts.types))
                if held:
                    add([ConstraintExpr(expr=held)], ScopeConstraintKind.TYPE, node.id)
            for ac in lay.constraints:
                r = self._resolver(node.id, ac.names)
                for e in ac.stmt.constraints:
                    add(expr_to_constraints(subst_names(e, ac.consts), r),
                        ScopeConstraintKind.ACTIVITY, node.id, scope=sbase + ac.scope)
        # A default, or a disable, that another node wrote changes what this
        # node's own problem would solve: the node is solved in a cone.
        for d in defaults:
            for s in d.slots:
                if self._owner_of(s) != d.owner:
                    self._needs_cone.add(self._owner_of(s))
        for d, c in D.equalities(defaults):
            add([c], ScopeConstraintKind.TYPE, d.owner)
        for node in tree.nodes:
            if node.comp_slot is not None:
                add([ConstraintExpr(expr=self._comp_choice(node.id))],
                    ScopeConstraintKind.COMP, node.id)
        for site in tree.sites:
            ce = self._comp_with(site)
            if ce is not None:
                add([ConstraintExpr(expr=ce)], ScopeConstraintKind.WITH,
                    site.owner, site=site.id)
            stmt = self._site_src[site.id]
            if not getattr(stmt.stmt, "inline_constraints", None):
                continue
            r = self._resolver(site.owner, stmt.names, target=site.target,
                               indices=tuple(v for v in stmt.indices if v))
            for e in stmt.stmt.inline_constraints:
                add(expr_to_constraints(subst_names(e, stmt.consts), r),
                    ScopeConstraintKind.WITH, site.owner, site=site.id)
        for nid, tie in self._comp_ties:
            add([tie], ScopeConstraintKind.COMP, nid)
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
                for c in cs) and members[0] not in self._needs_cone
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
                dom = leaf_domain(leaf, self.layouts.types)
                out.append(ScScopeVar(
                    name=(node.path + "." if node.path else "") + leaf.name,
                    node=nid, slot=slot, width=dom.width, signed=dom.signed,
                    rand=leaf.rand))
            if node.comp_slot is not None:
                out.append(ScScopeVar(
                    name=(node.path + "." if node.path else "") + "comp",
                    node=nid, slot=node.comp_slot, width=32, rand=True))
            out.extend(v for v in self._aux_vars.get(nid, ()) if v.slot in read)
        return out


def build_tree(layouts: Layouts, root: str, qname: str, comps=None,
               root_comp: Optional[str] = None) -> ScActionTree:
    """The :class:`ScActionTree` of action *qname*, exported as *root*.

    *comps* (``comp_tree.CompLayouts``) and *root_comp*, the root component
    type, place each node in its component instances; without them every
    node runs in the root's."""
    return TreeBuilder(layouts, comps, root_comp).build(root, qname)


__all__ = ["Layouts", "TypeLayout", "TreeBuilder", "build_tree"]
