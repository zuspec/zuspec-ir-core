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

``foreach`` (needs array flattening) and ``soft``/``dist``/``solve...before`` (not
yet surfaced cleanly by the parser) are deferred and reported, not silently
dropped.
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


def collect_solve_problem(coro: ScCoroutine,
                          dt: DataTypeClass) -> Optional[ScSolveProblem]:
    """Build a :class:`ScSolveProblem` for *coro* from action *dt*.

    Returns ``None`` when there is nothing to solve (no rand fields and no
    constraints).  Does not mutate *coro*; the caller decides placement.
    """
    # Keep each rand field's index in the *full* field list -- that is its object
    # storage slot (what constraints' ExprRefField / procedural LD_FIELD address).
    rand_fields = [(i, f) for i, f in enumerate(dt.fields)
                   if getattr(f, "rand_kind", None) is not None]

    # field name -> object slot (full-field index), for reference resolution.
    slots = {f.name: i for i, f in enumerate(dt.fields)}

    constraints: List[C.Constraint] = []
    for fn in coro.pending_constraints:
        for st in getattr(fn, "body", []) or []:
            constraints.extend(_stmt_to_constraints(st, slots, fn))

    if not rand_fields and not constraints:
        return None

    vars_: List[ScSolveVar] = []
    writeback = {}
    for vid, (slot, f) in enumerate(rand_fields):
        if getattr(f, "domain", None) is not None:
            # Inline field domains (`rand bit[8] x in [0..9]`) are not wired up
            # yet — fail loudly rather than solve an unconstrained var.
            raise UnsupportedConstructError(
                "inline field domain on %r is not supported in iteration 1"
                % f.name, loc=getattr(f, "loc", None),
                remedy="express the range as a named constraint for now")
        dtp = f.datatype
        width = getattr(dtp, "bits", 32)
        if width is None or width <= 0:
            width = 32
        signed = bool(getattr(dtp, "signed", False))
        vars_.append(ScSolveVar(name=f.name, var_id=vid, slot=slot, width=width,
                                signed=signed))
        writeback[f.name] = vid

    return ScSolveProblem(vars=vars_, constraints=constraints, writeback=writeback)


# --------------------------------------------------------------------------- #
# Stmt -> structured Constraint conversion
# --------------------------------------------------------------------------- #

def _stmt_to_constraints(stmt, slots: Dict[str, int], fn) -> List[C.Constraint]:
    """Convert one constraint-body statement into resolved structured constraints."""
    if isinstance(stmt, S.StmtExpr):
        e = stmt.expr
        # `a -> b` is carried as ExprCall(implies, [cond, body]) by ast2ir.
        if _is_implies_call(e):
            return [C.ConstraintImplies(
                antecedent=_resolve_refs(e.args[0], slots),
                body=[C.ConstraintExpr(expr=_resolve_refs(e.args[1], slots))])]
        return [C.ConstraintExpr(expr=_resolve_refs(e, slots))]

    if isinstance(stmt, S.StmtIf):
        then_body: List[C.Constraint] = []
        for s in (stmt.body or []):
            then_body.extend(_stmt_to_constraints(s, slots, fn))
        else_body: List[C.Constraint] = []
        for s in (stmt.orelse or []):
            else_body.extend(_stmt_to_constraints(s, slots, fn))
        return [C.ConstraintIfElse(cond=_resolve_refs(stmt.test, slots),
                                   then_body=then_body, else_body=else_body)]

    if isinstance(stmt, S.StmtUnique):
        items = []
        for v in stmt.vars:
            if v not in slots:
                raise UnsupportedConstructError(
                    "unique names field %r which is not a field of this type" % v,
                    loc=getattr(stmt, "loc", None))
            items.append(E.ExprRefField(base=E.TypeExprRefSelf(), index=slots[v]))
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


def _is_implies_call(e) -> bool:
    return (isinstance(e, E.ExprCall)
            and isinstance(e.func, E.ExprRefUnresolved)
            and e.func.name == "implies"
            and len(e.args) == 2)


# --------------------------------------------------------------------------- #
# Field-reference resolution: name-based ref -> ExprRefField(index=slot)
# --------------------------------------------------------------------------- #

def _resolve_refs(e, slots: Dict[str, int]):
    """Rewrite ``e``, replacing each self-field reference with an ``ExprRefField``.

    The frontend renders a field ``x`` as ``ExprAttribute(TypeExprRefSelf(), 'x')``
    (or, for ``unique`` members, ``ExprRefUnresolved('x')``); the solver lowering
    addresses variables by their object slot via ``ExprRefField(index=slot)``. This
    walks the expression tree structurally (over dataclass fields), so it reaches
    every nested reference.
    """
    if (isinstance(e, E.ExprAttribute)
            and isinstance(e.value, E.TypeExprRefSelf)
            and e.attr in slots):
        return E.ExprRefField(base=E.TypeExprRefSelf(), index=slots[e.attr])
    if isinstance(e, E.ExprRefUnresolved) and e.name in slots:
        return E.ExprRefField(base=E.TypeExprRefSelf(), index=slots[e.name])

    if dc.is_dataclass(e) and not isinstance(e, type):
        repl = {}
        for f in dc.fields(e):
            val = getattr(e, f.name)
            if isinstance(val, E.Expr):
                repl[f.name] = _resolve_refs(val, slots)
            elif isinstance(val, list) and any(isinstance(x, E.Expr) for x in val):
                repl[f.name] = [_resolve_refs(x, slots) if isinstance(x, E.Expr)
                                else x for x in val]
        return dc.replace(e, **repl) if repl else e
    return e
