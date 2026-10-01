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

``foreach`` (needs array flattening) and ``solve...before`` are deferred and
reported, not silently dropped. ``soft``/``dist``/``default`` have no IR form
yet: the front end records them on their block (``metadata["untranslated"]``)
and ``collect_solve_problem`` refuses such a block.
"""
from __future__ import annotations

import dataclasses as dc
from typing import Dict, List, Optional

from ... import constraint as C
from ... import expr as E
from ... import stmt as S
from ...data_type import DataTypeClass
from ...scenario import ScCoroutine, ScSolveProblem, ScSolveVar
from ..validate import UnsupportedConstructError
from .layout import (is_struct, object_layout, resolve, struct_fields,
                     struct_functions)


def collect_solve_problem(coro: ScCoroutine, dt: DataTypeClass,
                          types: Optional[Dict[str, object]] = None
                          ) -> Optional[ScSolveProblem]:
    """Build a :class:`ScSolveProblem` for *coro* from action *dt*.

    Returns ``None`` when there is nothing to solve (no rand fields and no
    constraints).  Does not mutate *coro*; the caller decides placement.

    Slots come from the flattened layout (``layout.object_layout``): a rand
    struct attribute is one variable per rand leaf, and the constraints its
    struct type declares are in force on it, with ``self`` meaning the
    attribute (``self.f`` in ``struct S`` is ``self.s.f`` on the action).
    """
    leaves = object_layout(dt.fields, types)
    rand_leaves = [(i, leaf) for i, leaf in enumerate(leaves) if leaf.rand]

    # dotted leaf path -> object slot, for reference resolution.
    slots = {leaf.name: i for i, leaf in enumerate(leaves)}
    resolve = slot_resolver(slots)

    constraints: List[C.Constraint] = []
    for prefix, fn in constraint_sites(coro.pending_constraints, dt, types):
        # A statement the front end could not translate (`soft`, `dist`,
        # `default`) is recorded on its block rather than dropped. Solving the
        # block without it would be a weaker problem than the one written.
        for kind, where in (getattr(fn, "metadata", None) or {}).get("untranslated", ()):
            raise UnsupportedConstructError(
                "%s'%s' constraint in %r is not supported yet"
                % (where, kind, getattr(fn, "name", "?")),
                loc=getattr(fn, "loc", None))
        for st in getattr(fn, "body", []) or []:
            constraints.extend(stmt_to_constraints(st, resolve, fn, prefix))

    if not rand_leaves and not constraints:
        return None

    vars_: List[ScSolveVar] = []
    writeback = {}
    for vid, (slot, leaf) in enumerate(rand_leaves):
        f = leaf.field
        if getattr(f, "domain", None) is not None:
            # Inline field domains (`rand bit[8] x in [0..9]`) are not wired up
            # yet — fail loudly rather than solve an unconstrained var.
            raise UnsupportedConstructError(
                "inline field domain on %r is not supported in iteration 1"
                % leaf.name, loc=getattr(f, "loc", None),
                remedy="express the range as a named constraint for now")
        dtp = leaf.datatype
        width = getattr(dtp, "bits", 32)
        if width is None or width <= 0:
            width = 32
        signed = bool(getattr(dtp, "signed", False))
        vars_.append(ScSolveVar(name=leaf.name, var_id=vid, slot=slot, width=width,
                                signed=signed))
        writeback[leaf.name] = vid

    return ScSolveProblem(vars=vars_, constraints=constraints, writeback=writeback)


#: Function names that are part of an action's lifecycle, not constraints.
LIFECYCLE_FUNCS = ("body", "pre_solve", "post_solve")


def is_pending_constraint(f) -> bool:
    """Does function *f* hold constraints that are in force on its type?

    A non-lifecycle function on an action is assumed to be a named constraint
    block. The exception is a generic constraint (PSS 3.1 §13.1.2), which is a
    template: it is inert until referenced, so collecting it here would put a
    body -- and its unbound parameters -- into the solve problem.
    """
    if getattr(f, "name", None) in LIFECYCLE_FUNCS:
        return False
    return not (getattr(f, "metadata", None) or {}).get("_is_generic_constraint")


def type_constraints(dt) -> list:
    """The constraint blocks of action *dt* (its own functions)."""
    return [f for f in getattr(dt, "functions", []) or [] if is_pending_constraint(f)]


def constraint_sites(pending, dt, types):
    """``(prefix, constraint function)`` for every constraint in force on an
    object of action *dt*: its own (prefix ``()``), then each struct
    attribute's type's, at that attribute's path, recursively.

    A struct's ``pre_solve``/``post_solve`` would run when the attribute is
    solved (LRM 8.3.2); nothing runs them yet, so they are refused rather
    than skipped.
    """
    for fn in pending:
        yield (), fn
    yield from _struct_sites(getattr(dt, "fields", []) or [], types, ())


def _struct_sites(fields, types, prefix):
    for f in fields:
        if not is_struct(f.datatype, types):
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

def slot_resolver(slots: Dict[str, int]):
    """A resolver over one object: ``self`` paths only, by dotted leaf name."""
    def resolve(root: str, path) -> Optional[int]:
        return slots.get(".".join(path)) if root == "self" else None
    return resolve


def stmt_to_constraints(stmt, resolve, fn, prefix=()) -> List[C.Constraint]:
    """Convert one constraint-body statement into resolved structured
    constraints. *prefix* is the path of the struct attribute the
    statement's ``self`` stands for (``()`` for the action itself);
    *resolve* maps a reference (see :func:`resolve_refs`) to a slot."""
    def res(e):
        return resolve_refs(e, resolve, prefix)

    if isinstance(stmt, S.StmtExpr):
        return expr_to_constraints(stmt.expr, resolve, prefix)

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
        items = []
        for v in stmt.vars:
            slot = resolve("self", prefix + (v,))
            if slot is None:
                raise UnsupportedConstructError(
                    "unique names field %r which is not a field of this type" % v,
                    loc=getattr(stmt, "loc", None))
            items.append(E.ExprRefField(base=E.TypeExprRefSelf(), index=slot))
        return [C.ConstraintUnique(items=items)]

    if isinstance(stmt, S.StmtForeach):
        raise UnsupportedConstructError(
            "foreach constraints require array flattening (per-element solver vars "
            "+ the ScSolveProblem.arrays map), which is not yet wired here",
            loc=getattr(stmt, "loc", None))

    raise UnsupportedConstructError(
        "constraint %r holds an unsupported statement %s"
        % (getattr(fn, "name", "?"), type(stmt).__name__),
        loc=getattr(stmt, "loc", None))


def expr_to_constraints(e, resolve, prefix=()) -> List[C.Constraint]:
    """One boolean constraint expression, resolved."""
    # `a -> b` is carried as ExprCall(implies, [cond, body]) by ast2ir.
    if _is_implies_call(e):
        return [C.ConstraintImplies(
            antecedent=resolve_refs(e.args[0], resolve, prefix),
            body=[C.ConstraintExpr(expr=resolve_refs(e.args[1], resolve, prefix))])]
    return [C.ConstraintExpr(expr=resolve_refs(e, resolve, prefix))]


def _is_implies_call(e) -> bool:
    return (isinstance(e, E.ExprCall)
            and isinstance(e.func, E.ExprRefUnresolved)
            and e.func.name == "implies"
            and len(e.args) == 2)


# --------------------------------------------------------------------------- #
# Field-reference resolution: name-based ref -> ExprRefField(index=slot)
# --------------------------------------------------------------------------- #

def ref_path(e):
    """``(root, path)`` of a reference, else None.

    *root* is ``"self"`` (``TypeExprRefSelf``) or ``"traversed"``
    (``TypeExprRefTraversed``: the action a ``with`` block or an initializer
    applies to). An element of a handle array with a constant index is one
    path element, ``"arr[1]"``; any other index makes it not a path.
    """
    path = []
    while True:
        if isinstance(e, E.ExprAttribute):
            path.append(e.attr)
            e = e.value
        elif (isinstance(e, E.ExprSubscript) and isinstance(e.value, E.ExprAttribute)
                and isinstance(e.slice, E.ExprConstant)
                and isinstance(e.slice.value, int)):
            path.append("%s[%d]" % (e.value.attr, e.slice.value))
            e = e.value.value
        else:
            break
    if not path:
        return None
    if isinstance(e, E.TypeExprRefSelf):
        return "self", tuple(reversed(path))
    if isinstance(e, E.TypeExprRefTraversed):
        return "traversed", tuple(reversed(path))
    return None


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
    rp = ref_path(e)
    if rp is not None:
        root, path = rp
        if root == "self":
            path = prefix + path
        slot = resolve(root, path)
        if slot is not None:
            return E.ExprRefField(base=E.TypeExprRefSelf(), index=slot)
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
