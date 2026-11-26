"""ConstraintCollect (Phase 3) — gather an action's rand fields + constraints
into an explicit :class:`~...scenario.ScSolveProblem`.

For iteration 1 the solve group is a single atomic action: its rand fields
become solver variables (with a fixed field↔var-id map), and its named
constraint blocks (carried on ``ScCoroutine.pending_constraints`` from Phase 1)
become structured constraint IR.

The constraint *bodies* arrive as ``Stmt`` nodes (``StmtExpr`` / ``StmtIf`` /
``StmtUnique`` / ``StmtForeach``) whose field references are name-based
(``ExprAttribute(TypeExprRefSelf(), attr)`` or ``ExprRefUnresolved(name)``). This
pass does two things a solver backend needs:

* **converts** each statement into the first-class :class:`~...constraint.Constraint`
  form (``StmtExpr`` -> ``ConstraintExpr`` / ``ConstraintImplies``; ``StmtIf`` ->
  ``ConstraintIfElse``; ``StmtUnique`` -> ``ConstraintUnique``), and
* **resolves** every field reference to ``ExprRefField(index=slot)`` where ``slot``
  is the field's index in the full field list — the form the solver lowering
  (``build_solve_blob``) addresses variables by.

``foreach`` over a fixed-size array is unrolled, one copy of its body per
element; ``solve...before`` is deferred and reported, not silently dropped. A top-level ``soft``/``dist`` (``StmtSoft``/
``StmtDist``) becomes ``ConstraintSoft``/``ConstraintDist``; one under a
condition is refused, by the statement's line. ``default`` and ``default
disable`` are resolved over the whole object first (``defaults.py``).
"""
from __future__ import annotations

import dataclasses as dc
import re
from typing import Dict, List, Optional

from ... import constraint as C
from ... import expr as E
from ... import stmt as S
from ...data_type import DataTypeClass, DataTypeEnum, DataTypeInt, DataTypeList, DataTypeRef
from ...scenario import ScCoroutine, ScSolveArray, ScSolveProblem, ScSolveVar
from ..validate import UnsupportedConstructError
from . import defaults as D
from .layout import (action_fields, chain_renames, domain_expr, is_ref, is_struct, leaf_domain,
                     object_layout, rename_members, resolve, struct_chain, struct_fields,
                     struct_functions)


def collect_solve_problem(coro: ScCoroutine, dt: DataTypeClass,
                          types: Optional[Dict[str, object]] = None,
                          handles: Optional[set] = None
                          ) -> Optional[ScSolveProblem]:
    """Build a :class:`ScSolveProblem` for *coro* from action *dt*.

    Returns ``None`` when there is nothing to solve (no rand fields and no
    constraints).  Does not mutate *coro*; the caller decides placement.

    Slots come from the flattened layout (``layout.object_layout``): a rand
    struct attribute is one variable per rand leaf, and the constraints its
    struct type declares are in force on it, with ``self`` meaning the
    attribute (``self.f`` in ``struct S`` is ``self.s.f`` on the action).

    *handles* names the action's sub-action handle fields. A constraint
    that reads through one (``a.val < b.val``) ties nodes of the action
    tree: it belongs to the tree's cone (``action_tree``), which solves it
    with lookahead, so it is left out here.
    """
    leaves = object_layout(action_fields(dt, types), types)
    rand_leaves = [(i, leaf) for i, leaf in enumerate(leaves) if leaf.rand]

    # dotted leaf path -> object slot, for reference resolution.
    slots = {leaf.name: i for i, leaf in enumerate(leaves)}
    resolve = slot_resolver(slots)

    constraints: List[C.Constraint] = []
    defaults: List[D.DefaultStmt] = []
    softs = []
    for seq, (prefix, fn) in enumerate(constraint_sites(coro.pending_constraints, dt, types)):
        for st in getattr(fn, "body", []) or []:
            if D.is_default(st):
                # The action is the highest-level context here; a struct
                # attribute's type is lower the deeper it is (13.1.11 d).
                path = default_path(st, prefix)
                if handles and path[0].partition("[")[0] in handles:
                    continue                    # a child's: the tree's cone
                defaults.append(D.default_stmt(
                    st, (-len(prefix), seq), None, leaves_under(leaves, path),
                    lambda v: resolve_refs(v, resolve, prefix)))
                continue
            if mentions_prev(st):
                # A state's `prev` (13.3) is only known in the action tree,
                # which pins it; the initial object satisfies it vacuously.
                continue
            # One through a sub-action handle, or reading a component
            # attribute, belongs to the action tree's cone, and a node whose
            # type reads `comp.f` is always solved in one.
            for c in stmt_to_constraints(st, resolve, fn, prefix, soft=True):
                if (handles and reads_handle(c, handles)) or reads_comp(c):
                    continue
                if isinstance(c, C.ConstraintSoft):
                    # Shallower in the object, then later, wins (as for
                    # defaults); ranks are numbered once all are seen.
                    softs.append(((-len(prefix), seq, len(softs)), c))
                constraints.append(c)
    for rank, (_, c) in enumerate(sorted(softs, key=lambda kc: kc[0])):
        c.rank = rank
    constraints.extend(c for _, c in D.equalities(defaults))

    if not rand_leaves and not constraints:
        return None

    vars_: List[ScSolveVar] = []
    writeback = {}
    arrays: List[ScSolveArray] = []
    for slot, leaf in rand_leaves:
        # A rand list's elements are solved, as many as it holds (F6.10e).
        dom = list_elem_domain(leaf, types)
        if dom is not None:
            arrays.append(ScSolveArray(name=leaf.name, array_id=len(arrays), slot=slot,
                                       width=dom.width, signed=dom.signed, rand=True))
    rand_leaves = [(s, lf) for s, lf in rand_leaves if s not in {a.slot for a in arrays}]
    for vid, (slot, leaf) in enumerate(rand_leaves):
        f = leaf.field
        if getattr(f, "domain", None) is not None:
            # Inline field domains (`rand bit[8] x in [0..9]`) are not wired up
            # yet — fail loudly rather than solve an unconstrained var.
            raise UnsupportedConstructError(
                "inline field domain on %r is not supported in iteration 1"
                % leaf.name, loc=getattr(f, "loc", None),
                remedy="express the range as a named constraint for now")
        dom = leaf_domain(leaf, types)
        vars_.append(ScSolveVar(name=leaf.name, var_id=vid, slot=slot,
                                width=dom.width, signed=dom.signed))
        writeback[leaf.name] = vid
        held = domain_expr(slot, dom)
        if held is not None:
            constraints.append(C.ConstraintExpr(expr=held))

    # A non-rand field a constraint reads (set in pre_solve, say) is an
    # input: pinned to its value when the action is solved.
    # A list it reads (`x in l`, `l.size()`) is an input array, as long as
    # the list is when the action is solved.
    rand_slots = {slot for slot, _ in rand_leaves} | {a.slot for a in arrays}
    for slot in sorted(read_slots(constraints) - rand_slots):
        if not 0 <= slot < len(leaves):
            continue
        leaf = leaves[slot]
        dom = list_elem_domain(leaf, types)
        if dom is not None:
            arrays.append(ScSolveArray(name=leaf.name, array_id=len(arrays), slot=slot,
                                       width=dom.width, signed=dom.signed))
            continue
        dom = leaf_domain(leaf, types)
        vars_.append(ScSolveVar(name=leaf.name, var_id=len(vars_), slot=slot,
                                width=dom.width, signed=dom.signed, input=True))

    return ScSolveProblem(vars=vars_, constraints=constraints, writeback=writeback,
                          input_arrays=arrays)


def list_elem_domain(leaf, types):
    """The solver domain of the elements of list *leaf*, or None when the
    leaf is not a list. A list a constraint reads is an input array of the
    solve (``x in l``, ``l.size()``), so its elements must be integers."""
    dt = resolve(leaf.datatype, types) if isinstance(leaf.datatype, DataTypeRef) \
        else leaf.datatype
    if not isinstance(dt, DataTypeList):
        return None
    elem = dt.element_type
    if isinstance(elem, DataTypeRef):
        elem = resolve(elem, types)
    if not isinstance(elem, (DataTypeInt, DataTypeEnum)):
        raise UnsupportedConstructError(
            "a constraint reads list %r, whose elements are not integers or "
            "enums" % leaf.name, loc=getattr(leaf.field, "loc", None))
    return leaf_domain(leaf._replace(datatype=elem), types)


def read_slots(cons) -> set:
    """The object slots the constraints *cons* read (``ExprRefField``)."""
    found = set()

    def walk(x):
        if isinstance(x, E.ExprRefField) and isinstance(x.base, E.TypeExprRefSelf):
            found.add(x.index)
            return
        if dc.is_dataclass(x) and not isinstance(x, type):
            for f in dc.fields(x):
                v = getattr(x, f.name)
                for y in (v if isinstance(v, list) else [v]):
                    walk(y)
    for c in cons:
        walk(c)
    return found


def mentions_prev(x) -> bool:
    """Whether *x* reads a state object's ``prev`` (13.3): a constraint that
    does is vacuously satisfied by the initial object."""
    if isinstance(x, E.ExprAttribute) and x.attr == "prev" \
            and isinstance(x.value, E.TypeExprRefSelf):
        return True
    if dc.is_dataclass(x) and not isinstance(x, type):
        for f in dc.fields(x):
            v = getattr(x, f.name)
            if any(mentions_prev(y) for y in (v if isinstance(v, list) else [v])):
                return True
    return False


#: Function names that are part of an action's lifecycle, not constraints.
LIFECYCLE_FUNCS = ("body", "pre_body", "pre_solve", "post_solve")


def is_pending_constraint(f) -> bool:
    """Does function *f* hold constraints that are in force on its type?

    A non-lifecycle function on an action is assumed to be a named constraint
    block. The exception is a generic constraint (PSS 3.1 §13.1.2), which is a
    template: it is inert until referenced, so collecting it here would put a
    body -- and its unbound parameters -- into the solve problem.
    """
    if getattr(f, "name", None) in LIFECYCLE_FUNCS:
        return False
    meta = getattr(f, "metadata", None) or {}
    # A function declared in an action (a pssc extension) is called, not
    # solved: ``ScenarioModule.functions`` has it.
    return not (meta.get("_is_generic_constraint") or meta.get("action_function"))


def type_constraints(dt, types=None) -> list:
    """The constraint blocks in force on action *dt*: its own and, given
    *types*, its bases' -- a derived block replacing a base's of the same
    name (LRM 16.1.3), a base's members renamed as the layout renames them."""
    try:
        chain = struct_chain(dt, types) if types is not None else [dt]
    except UnsupportedConstructError:
        chain = [dt]
    out = []
    for t, ren in zip(chain, chain_renames(chain)):
        for f in getattr(t, "functions", []) or []:
            if is_pending_constraint(f):
                name = getattr(f, "name", None)
                out[:] = [g for g in out if not name or getattr(g, "name", None) != name]
                out.append(rename_members(f, *ren))
    return out


def constraint_sites(pending, dt, types):
    """``(prefix, constraint function)`` for every constraint in force on an
    object of action *dt*: its own (prefix ``()``), then each struct
    attribute's type's, at that attribute's path, recursively -- and each
    flow-object reference's or claim's type's, at the reference.

    A struct's ``pre_solve``/``post_solve`` would run when the attribute is
    solved (LRM 8.3.2); nothing runs them yet, so they are refused rather
    than skipped.
    """
    for fn in pending:
        yield (), fn
    yield from _struct_sites(action_fields(dt, types), types, ())


def _struct_sites(fields, types, prefix):
    for f in fields:
        # A flow-object reference or a claim holds its object in the
        # action, so the object's type's constraints are in force on it.
        if not (is_struct(f.datatype, types) or (not prefix and is_ref(f))):
            continue
        path = prefix + (f.name,)
        for fn in struct_functions(f.datatype, types):
            name = getattr(fn, "name", None)
            meta = getattr(fn, "metadata", None) or {}
            if name in ("pre_solve", "post_solve"):
                raise UnsupportedConstructError(
                    "exec %s of struct %r (attribute %r) is not supported yet"
                    % (name, getattr(resolve(f.datatype, types), "name", "?"),
                       ".".join(path)), loc=getattr(fn, "loc", None))
            if meta.get("_is_generic_constraint"):
                continue
            if not meta.get("_is_constraint"):
                raise UnsupportedConstructError(
                    "%r in struct %r (attribute %r) is not supported yet"
                    % (name, getattr(resolve(f.datatype, types), "name", "?"),
                       ".".join(path)), loc=getattr(fn, "loc", None))
            yield path, fn
        yield from _struct_sites(struct_fields(f.datatype, types), types, path)


# --------------------------------------------------------------------------- #
# Stmt -> structured Constraint conversion
# --------------------------------------------------------------------------- #

def default_path(stmt, prefix=()) -> tuple:
    """The ``self`` path a ``default``/``default disable`` names."""
    rp = ref_path(stmt.target)
    if rp is None and isinstance(stmt.target, E.ExprRefUnresolved):
        rp = ("self", (stmt.target.name,))
    if rp is None or rp[0] != "self":
        raise UnsupportedConstructError(
            "'default' target is not an attribute path", loc=getattr(stmt, "loc", None))
    return tuple(prefix) + rp[1]


def leaves_under(leaves, path) -> list:
    """``[(slot, Leaf)]``: the scalar *path* names in *leaves* (one object's
    layout), or every scalar under it if it names an aggregate."""
    name = ".".join(path)
    return [(i, lf) for i, lf in enumerate(leaves)
            if lf.name == name or lf.name.startswith(name + ".")]


def slot_resolver(slots: Dict[str, int]):
    """A resolver over one object: ``self`` paths only, by dotted leaf name."""
    def resolve(root: str, path) -> Optional[int]:
        return slots.get(".".join(path)) if root == "self" else None

    def leaves(root: str, path):
        if root != "self":
            return None
        name = ".".join(path)
        return [(n[len(name) + 1:], s) for n, s in slots.items()
                if n.startswith(name + ".")]
    resolve.leaves = leaves
    return resolve


def stmt_to_constraints(stmt, resolve, fn, prefix=(), soft=False) -> List[C.Constraint]:
    """:func:`_stmt_to_constraints`, each result carrying *stmt*'s source
    location (an unsat solve names its conflicting constraints by it)."""
    out = _stmt_to_constraints(stmt, resolve, fn, prefix, soft)
    loc = getattr(stmt, "loc", None)
    if loc is not None:
        for c in out:
            if getattr(c, "loc", None) is None:
                c.loc = loc
    return out


def _stmt_to_constraints(stmt, resolve, fn, prefix=(), soft=False) -> List[C.Constraint]:
    """Convert one constraint-body statement into resolved structured
    constraints. *prefix* is the path of the struct attribute the
    statement's ``self`` stands for (``()`` for the action itself);
    *resolve* maps a reference (see :func:`resolve_refs`) to a slot.

    *soft* accepts a top-level ``soft`` statement (a ``ConstraintSoft`` of
    rank 0; the caller ranks it) and a top-level ``dist`` (a
    ``ConstraintDist``). Without it, or under a condition, both are refused:
    the caller cannot rank a soft, and the solver takes a dist only at top
    level."""
    def res(e):
        return resolve_refs(e, resolve, prefix)

    if isinstance(stmt, S.StmtExpr):
        return expr_to_constraints(stmt.expr, resolve, prefix)

    # A top-level default is resolved with the object's others (defaults.py);
    # one that reaches here is under a condition.
    D.check_unconditioned(stmt, fn)

    if isinstance(stmt, S.StmtIf):
        then_body: List[C.Constraint] = []
        for s in (stmt.body or []):
            then_body.extend(stmt_to_constraints(s, resolve, fn, prefix))
        else_body: List[C.Constraint] = []
        for s in (stmt.orelse or []):
            else_body.extend(stmt_to_constraints(s, resolve, fn, prefix))
        return [C.ConstraintIfElse(cond=res(stmt.test),
                                   then_body=then_body, else_body=else_body)]

    if isinstance(stmt, S.StmtUnique):
        if stmt.items:
            items = []
            for x in stmt.items:
                items.extend(_unique_elements(x, resolve, prefix) or [res(x)])
            return [C.ConstraintUnique(items=items)]
        items = []
        for v in stmt.vars:
            slot = resolve("self", prefix + (v,))
            if slot is None:
                raise UnsupportedConstructError(
                    "unique names field %r which is not a field of this type" % v,
                    loc=getattr(stmt, "loc", None))
            items.append(E.ExprRefField(base=E.TypeExprRefSelf(), index=slot))
        return [C.ConstraintUnique(items=items)]

    if soft and isinstance(stmt, S.StmtSoft):
        return [C.ConstraintSoft(expr=res(stmt.expr))]

    if soft and isinstance(stmt, S.StmtDist):
        return [C.ConstraintDist(
            target=res(stmt.target),
            weights=[C.DistWeight(rng=res(w.rng),
                                  weight=None if w.weight is None else res(w.weight),
                                  per_value=w.per_value)
                     for w in stmt.weights])]

    if isinstance(stmt, (S.StmtSoft, S.StmtDist)):
        # Not lowered to the solver yet; solving without it would be a weaker
        # problem than the one written.
        refuse_soft_dist(stmt, fn)

    if isinstance(stmt, S.StmtForeach):
        return foreach_to_constraints(stmt, resolve, fn, prefix)

    raise UnsupportedConstructError(
        "constraint %r holds an unsupported statement %s"
        % (getattr(fn, "name", "?"), type(stmt).__name__),
        loc=getattr(stmt, "loc", None))


def _unique_elements(e, resolve, prefix):
    """The element slots of a fixed-size array of scalars that a ``unique``
    operand names (``unique {arr}``: every element distinct, F6.2c), in index
    order, or None if *e* is not such an array."""
    rp = ref_path(e)
    leaves = getattr(resolve, "leaves", None)
    if rp is None or leaves is None:
        return None
    root, path = rp
    if root == "self":
        path = prefix + path
    if resolve(root, path) is not None:
        return None                              # a scalar
    elems = leaves(root, index_path(path)) or []
    if not elems or not all(n.isdigit() for n, _ in elems):
        return None
    return [E.ExprRefField(base=E.TypeExprRefSelf(), index=s)
            for _, s in sorted(elems, key=lambda ns: int(ns[0]))]


def foreach_to_constraints(stmt, resolve, fn, prefix=()) -> List[C.Constraint]:
    """``foreach (v : a[i]) { body }`` over a fixed-size array, unrolled: the
    body once per element, ``i`` the element's index and ``v`` the element
    (``a[k]``), so each copy names scalar slots and the solver needs no
    array support (F6.2a)."""
    loc = getattr(stmt, "loc", None)
    rp = ref_path(stmt.iter)
    leaves = getattr(resolve, "leaves", None)
    count = None
    if rp is not None and leaves is not None:
        root, path = rp
        if root == "self":
            path = prefix + path
        idx = {n.split(".", 1)[0] for n, _ in (leaves(root, index_path(path)) or [])}
        if idx and all(i.isdigit() for i in idx):
            count = max(int(i) for i in idx) + 1
    tgt = stmt.target.name if isinstance(stmt.target, E.ExprRefLocal) else None
    ix = stmt.index_var.name if isinstance(stmt.index_var, E.ExprRefLocal) else None
    if count is None and rp is not None:
        # A list is one slot, as long as it is when solved: the foreach is
        # kept for the solver, which loops over the list's elements (F6.10e).
        root, path = rp
        slot = resolve(root, prefix + path if root == "self" else path)
        if slot is not None:
            ix = ix or "__foreach_ix%d" % id(stmt)
            binds = {}
            if tgt is not None and tgt != ix:
                binds[tgt] = E.ExprSubscript(value=stmt.iter, slice=E.ExprRefLocal(name=ix))
            body = []
            for s in bind_locals(stmt.body or [], binds):
                body.extend(stmt_to_constraints(s, resolve, fn, prefix))
            return [C.ConstraintForeach(array=E.ExprRefField(base=E.TypeExprRefSelf(), index=slot),
                                        index_var=ix, body=body)]
    if count is None:
        raise UnsupportedConstructError(
            "constraint foreach in %r iterates over something other than a "
            "fixed-size array field or list" % getattr(fn, "name", "?"), loc=loc)
    out: List[C.Constraint] = []
    for k in range(count):
        binds = {}
        if tgt is not None and tgt != ix:
            binds[tgt] = E.ExprSubscript(value=stmt.iter, slice=E.ExprConstant(value=k))
        if ix is not None:
            binds[ix] = E.ExprConstant(value=k)
        for s in prune_ifs(bind_locals(stmt.body or [], binds)):
            out.extend(stmt_to_constraints(s, resolve, fn, prefix))
    return out


_FOLD = {
    E.BinOp.Add: lambda a, b: a + b, E.BinOp.Sub: lambda a, b: a - b,
    E.BinOp.Mult: lambda a, b: a * b,
    E.BinOp.Div: lambda a, b: int(a / b) if b else None,
    E.BinOp.Mod: lambda a, b: a % b if b else None,
    E.BinOp.Eq: lambda a, b: int(a == b), E.BinOp.NotEq: lambda a, b: int(a != b),
    E.BinOp.Lt: lambda a, b: int(a < b), E.BinOp.LtE: lambda a, b: int(a <= b),
    E.BinOp.Gt: lambda a, b: int(a > b), E.BinOp.GtE: lambda a, b: int(a >= b),
    E.BinOp.And: lambda a, b: int(bool(a and b)), E.BinOp.Or: lambda a, b: int(bool(a or b)),
}


def const_int(e) -> Optional[int]:
    """*e*'s value if it is built from integer constants alone (an unrolled
    foreach index and arithmetic or comparisons on it), else None."""
    if isinstance(e, E.ExprConstant):
        return e.value if isinstance(e.value, int) and not isinstance(e.value, bool) else None
    if isinstance(e, E.ExprBin) and e.op in _FOLD:
        a, b = const_int(e.lhs), const_int(e.rhs)
        return None if a is None or b is None else _FOLD[e.op](a, b)
    if isinstance(e, E.ExprUnary) and e.op in (E.UnaryOp.USub, E.UnaryOp.Not):
        a = const_int(e.operand)
        return None if a is None else (-a if e.op is E.UnaryOp.USub else int(not a))
    return None


def prune_ifs(stmts) -> list:
    """*stmts* with each ``if`` whose condition is a constant (once a
    foreach index is fixed) replaced by the branch it takes: ``if (i > 0)
    a[i] > a[i-1];`` must not name ``a[-1]`` in the first copy."""
    out = []
    for s in stmts:
        if isinstance(s, S.StmtIf):
            v = const_int(s.test)
            if v is not None:
                out.extend(prune_ifs((s.body if v else s.orelse) or []))
                continue
            s = dc.replace(s, body=prune_ifs(s.body or []), orelse=prune_ifs(s.orelse or []))
        out.append(s)
    return out


def bind_locals(x, binds):
    """*x* (an expression, statement or list of them) with each local
    ``ExprRefLocal`` named in *binds* replaced by its expression."""
    if isinstance(x, E.ExprRefLocal) and x.name in binds:
        return binds[x.name]
    if isinstance(x, list):
        return [bind_locals(y, binds) for y in x]
    if dc.is_dataclass(x) and not isinstance(x, (type, DataTypeClass)):
        repl = {}
        for f in dc.fields(x):
            if not f.init:
                continue
            val = getattr(x, f.name)
            if isinstance(val, (E.Expr, S.Stmt, list)):
                new = bind_locals(val, binds)
                if new is not val:
                    repl[f.name] = new
        x = dc.replace(x, **repl) if repl else x
        if isinstance(x, E.ExprSubscript) and not isinstance(x.slice, E.ExprConstant):
            v = const_int(x.slice)
            if v is not None:
                x = dc.replace(x, slice=E.ExprConstant(value=v))
    return x


def refuse_soft_dist(stmt, fn) -> None:
    """Refuse a ``soft`` or ``dist`` statement, by its line."""
    kind = "soft" if isinstance(stmt, S.StmtSoft) else "dist"
    loc = getattr(stmt, "loc", None)
    where = "line %d: " % loc.line if loc is not None and loc.line else ""
    raise UnsupportedConstructError(
        "%s'%s' constraint in %r is not supported yet"
        % (where, kind, getattr(fn, "name", "?")),
        loc=loc or getattr(fn, "loc", None))


def expr_to_constraints(e, resolve, prefix=(), soft=False) -> List[C.Constraint]:
    """One boolean constraint expression, resolved. *soft* accepts a
    ``soft e`` (``ExprCall(soft, [e])`` from a ``with`` or an activity
    constraint) as a ``ConstraintSoft`` of rank 0, for the caller to rank,
    and a ``dist`` call as a ``ConstraintDist``; without it both are refused."""
    # `soft e` in a `with` or an activity constraint: ExprCall(soft, [e]).
    if _is_call(e, "soft", 1):
        if not soft:
            loc = getattr(e, "loc", None) or getattr(e.args[0], "loc", None)
            raise UnsupportedConstructError(
                "'soft' constraint here is not supported yet", loc=loc)
        return [C.ConstraintSoft(expr=resolve_refs(e.args[0], resolve, prefix))]
    # `dist x in [...]` there: ExprCall(dist, [x, item...]), an item a bare
    # range (weight 1) or ExprCall(':=' / ':/', [range, weight]).
    if (isinstance(e, E.ExprCall) and isinstance(e.func, E.ExprRefUnresolved)
            and e.func.name == "dist" and len(e.args) >= 2):
        if not soft:
            loc = getattr(e, "loc", None) or getattr(e.args[0], "loc", None)
            raise UnsupportedConstructError(
                "'dist' constraint here is not supported yet", loc=loc)
        res = lambda x: resolve_refs(x, resolve, prefix)
        weights = []
        for it in e.args[1:]:
            if _is_call(it, ":=", 2) or _is_call(it, ":/", 2):
                weights.append(C.DistWeight(rng=res(it.args[0]), weight=res(it.args[1]),
                                            per_value=it.func.name == ":/"))
            else:
                weights.append(C.DistWeight(rng=res(it), weight=None, per_value=False))
        return [C.ConstraintDist(target=res(e.args[0]), weights=weights)]
    # `unique {a, b}` there: ExprCall(unique, [a, b]).
    if (isinstance(e, E.ExprCall) and isinstance(e.func, E.ExprRefUnresolved)
            and e.func.name == "unique" and len(e.args) >= 2):
        return [C.ConstraintUnique(
            items=[resolve_refs(x, resolve, prefix) for x in e.args])]
    # `a -> b` is carried as ExprCall(implies, [cond, body]) by ast2ir.
    if _is_implies_call(e):
        return [C.ConstraintImplies(
            antecedent=resolve_refs(e.args[0], resolve, prefix),
            body=[C.ConstraintExpr(expr=resolve_refs(e.args[1], resolve, prefix))])]
    return [C.ConstraintExpr(expr=resolve_refs(e, resolve, prefix))]


def _is_implies_call(e) -> bool:
    return _is_call(e, "implies", 2)


def _is_call(e, name, nargs) -> bool:
    return (isinstance(e, E.ExprCall)
            and isinstance(e.func, E.ExprRefUnresolved)
            and e.func.name == name
            and len(e.args) == nargs)


# --------------------------------------------------------------------------- #
# Field-reference resolution: name-based ref -> ExprRefField(index=slot)
# --------------------------------------------------------------------------- #

def reads_handle(c, handles) -> bool:
    """Does constraint *c* read through one of the action handles *handles*
    (``self.b1.x``, ``self.bs[1].x``)?"""
    found = []

    def walk(x):
        if found:
            return
        rp = ref_path(x) if isinstance(x, (E.ExprAttribute, E.ExprSubscript)) else None
        if rp is not None and rp[0] == "self" and rp[1][0].partition("[")[0] in handles:
            found.append(x)
            return
        if dc.is_dataclass(x) and not isinstance(x, type):
            for f in dc.fields(x):
                v = getattr(x, f.name)
                for y in (v if isinstance(v, list) else [v]):
                    walk(y)
    walk(c)
    return bool(found)


def reads_comp(c) -> bool:
    """Does constraint *c* read a component attribute (``comp.f``)?"""
    found = []

    def walk(x):
        if found:
            return
        rp = ref_path(x) if isinstance(x, (E.ExprAttribute, E.ExprSubscript)) else None
        if rp is not None and len(rp[1]) > 1 and rp[1][0] == "comp":
            found.append(x)
            return
        if dc.is_dataclass(x) and not isinstance(x, type):
            for f in dc.fields(x):
                v = getattr(x, f.name)
                for y in (v if isinstance(v, list) else [v]):
                    walk(y)
    walk(c)
    return bool(found)


def ref_path(e):
    """``(root, path)`` of a reference, else None.

    *root* is ``"self"`` (``TypeExprRefSelf``) or ``"traversed"``
    (``TypeExprRefTraversed``: the action a ``with`` block or an initializer
    applies to). An element of an array with constant indices is one path
    element, ``"arr[1]"`` (``"m[1][2]"``); any other index makes it not a
    path. A data array's leaves are named by index instead (``("arr", "1")``):
    :func:`index_path` gives that form.
    """
    path = []
    while True:
        if isinstance(e, E.ExprAttribute):
            path.append(e.attr)
            e = e.value
            continue
        idx = []
        v = e
        while (isinstance(v, E.ExprSubscript) and isinstance(v.slice, E.ExprConstant)
                and isinstance(v.slice.value, int)):
            idx.insert(0, v.slice.value)
            v = v.value
        if not idx or not isinstance(v, E.ExprAttribute):
            break
        path.append(v.attr + "".join("[%d]" % i for i in idx))
        e = v.value
    if not path:
        return None
    if isinstance(e, E.TypeExprRefSelf):
        return "self", tuple(reversed(path))
    if isinstance(e, E.TypeExprRefTraversed):
        return "traversed", tuple(reversed(path))
    return None


_INDEXED = re.compile(r"^([^\[]+)((?:\[\d+\])+)$")


def index_path(path):
    """*path* with each ``"a[1][2]"`` element split into ``"a", "1", "2"``:
    the path of a data array's leaf (F6.1a; ``layout.value_leaves``)."""
    out = []
    for p in path:
        m = _INDEXED.match(p)
        if m is None:
            out.append(p)
        else:
            out.append(m.group(1))
            out.extend(re.findall(r"\d+", m.group(2)))
    return tuple(out)


def _aggregate_compare(e, resolve, prefix):
    """``a == b`` (``a != b``) on two struct attributes: each scalar of
    ``a`` equal to the same scalar of ``b`` (any one of them differing), or
    None if *e* is not such a comparison. *resolve* lays out aggregates
    through its ``leaves(root, path)``: ``[(name under path, slot)]``."""
    if not (isinstance(e, E.ExprBin) and e.op in (E.BinOp.Eq, E.BinOp.NotEq)):
        return None
    leaves = getattr(resolve, "leaves", None)
    lp, rp = ref_path(e.lhs), ref_path(e.rhs)
    if leaves is None or lp is None or rp is None:
        return None
    sides = []
    for root, path in (lp, rp):
        if root == "self":
            path = prefix + path
        if resolve(root, path) is not None:
            return None                          # a scalar
        sides.append(leaves(root, path) or [])
    if not sides[0] or not sides[1]:
        return None
    lhs, rhs = dict(sides[0]), dict(sides[1])
    if sorted(lhs) != sorted(rhs):
        raise UnsupportedConstructError(
            "'%s' compares two aggregates of different types (%s vs %s)"
            % ("==" if e.op is E.BinOp.Eq else "!=", ".".join(lp[1]), ".".join(rp[1])),
            loc=getattr(e, "loc", None))

    def ref(slot):
        return E.ExprRefField(base=E.TypeExprRefSelf(), index=slot)
    terms = [E.ExprBin(lhs=ref(lhs[n]), op=e.op, rhs=ref(rhs[n])) for n in sorted(lhs)]
    if len(terms) == 1:
        return terms[0]
    return E.ExprBool(op=E.BoolOp.And if e.op is E.BinOp.Eq else E.BoolOp.Or,
                      values=terms)


def _select(e, resolve, prefix):
    """``arr[i]`` of a fixed-size array of scalars by a non-constant index
    (a loop index a ``with`` reads, F8.4a): a choice among the elements'
    slots, ``i == 0 ? arr[0] : i == 1 ? arr[1] : ... arr[n-1]``. None when
    the base is not such an array (a list resolves to one slot instead).
    An index past the end reads the last element."""
    rp = ref_path(e.value)
    if rp is None:
        return None
    root, path = rp
    if root == "self":
        path = prefix + path
    if resolve(root, path) is not None:
        return None
    elems = []
    while True:
        p = path[:-1] + ("%s[%d]" % (path[-1], len(elems)),)
        slot = resolve(root, p)
        if slot is None and index_path(p) != p:
            slot = resolve(root, index_path(p))
        if slot is None:
            break
        elems.append(slot)
    if not elems:
        return None
    idx = resolve_refs(e.slice, resolve, prefix)

    def ref(slot):
        return E.ExprRefField(base=E.TypeExprRefSelf(), index=slot)
    out = ref(elems[-1])
    for k in reversed(range(len(elems) - 1)):
        out = E.ExprIfExp(test=E.ExprBin(lhs=idx, op=E.BinOp.Eq,
                                         rhs=E.ExprConstant(value=k)),
                          body=ref(elems[k]), orelse=out)
    return out


def resolve_refs(e, resolve, prefix=()):
    """Rewrite ``e``, replacing each resolvable reference with an ``ExprRefField``.

    The frontend renders a field ``x`` as ``ExprAttribute(TypeExprRefSelf(), 'x')``
    and a field of a struct attribute as ``self.s.f`` (or, for ``unique``
    members, ``ExprRefUnresolved('x')``); the solver lowering addresses
    variables by their object slot via ``ExprRefField(index=slot)``.
    ``resolve(root, path)`` gives the slot (see :func:`ref_path`), or None.
    *prefix* is prepended to every ``self`` path (a struct type's constraint,
    applied to one attribute). A reference that does not resolve is left in
    place, for the backend to report. This walks the expression tree
    structurally (over dataclass fields), so it reaches every nested reference.
    """
    agg = _aggregate_compare(e, resolve, prefix)
    if agg is not None:
        return agg
    if (isinstance(e, E.ExprCall) and not e.args and isinstance(e.func, E.ExprAttribute)
            and e.func.attr == "size"):
        # `l.size()`: the list resolves to its slot, read as an input array.
        base = resolve_refs(e.func.value, resolve, prefix)
        if isinstance(base, E.ExprRefField):
            return dc.replace(e, func=E.ExprAttribute(value=base, attr="size"))
        return e
    if isinstance(e, E.ExprSubscript) and not isinstance(e.slice, E.ExprConstant):
        sel = _select(e, resolve, prefix)
        if sel is not None:
            return sel
    rp = ref_path(e)
    if rp is not None:
        root, path = rp
        if root == "self":
            path = prefix + path
        slot = resolve(root, path)
        if slot is None and index_path(path) != path:
            slot = resolve(root, index_path(path))
        if slot is not None:
            return E.ExprRefField(base=E.TypeExprRefSelf(), index=slot)
        if isinstance(e, E.ExprSubscript):
            # `l[1]` of a list, one slot: an element the solver reads.
            base = resolve_refs(e.value, resolve, prefix)
            if isinstance(base, E.ExprRefField):
                return dc.replace(e, value=base)
        if prefix and root == "self":
            # Not a scalar (a struct-valued path, or a name the layout does
            # not hold): keep it, rooted at the attribute, for the backend to
            # report. Left as `self.x` it would name the action's own `x`.
            out = E.TypeExprRefSelf()
            for p in path:
                out = E.ExprAttribute(value=out, attr=p)
            return out
        return e
    if isinstance(e, E.ExprRefUnresolved):
        slot = resolve("self", prefix + (e.name,))
        if slot is not None:
            return E.ExprRefField(base=E.TypeExprRefSelf(), index=slot)

    if dc.is_dataclass(e) and not isinstance(e, type):
        repl = {}
        for f in dc.fields(e):
            val = getattr(e, f.name)
            if isinstance(val, E.Expr):
                repl[f.name] = resolve_refs(val, resolve, prefix)
            elif isinstance(val, list) and any(isinstance(x, E.Expr) for x in val):
                repl[f.name] = [resolve_refs(x, resolve, prefix) if isinstance(x, E.Expr)
                                else x for x in val]
        return dc.replace(e, **repl) if repl else e
    return e
