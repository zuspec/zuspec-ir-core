"""ConstraintCollect (Phase 3) — gather an action's rand fields + constraints
into an explicit :class:`~...scenario.ScSolveProblem`.

For iteration 1 the solve group is a single atomic action: its rand fields
become solver variables (with a fixed field↔var-id map), and its named
constraint blocks (carried on ``ScCoroutine.pending_constraints`` from Phase 1)
become the constraint expression DAG.  The DAG stays as Layer-0 :class:`Expr`
nodes; each backend translates it (C → ``expr_*`` builder calls; SV → native /
DPI).  Inline traversal constraints and stream-joint merges are later phases.
"""
from __future__ import annotations

from typing import List, Optional

from ...data_type import DataTypeClass
from ...fields import RandKind
from ...scenario import ScCoroutine, ScSolveProblem, ScSolveVar
from ..validate import UnsupportedConstructError


def collect_solve_problem(coro: ScCoroutine,
                          dt: DataTypeClass) -> Optional[ScSolveProblem]:
    """Build a :class:`ScSolveProblem` for *coro* from action *dt*.

    Returns ``None`` when there is nothing to solve (no rand fields and no
    constraints).  Does not mutate *coro*; the caller decides placement.
    """
    rand_fields = [f for f in dt.fields if getattr(f, "rand_kind", None) is not None]

    constraints: List = []
    for fn in coro.pending_constraints:
        for st in getattr(fn, "body", []) or []:
            expr = getattr(st, "expr", None)
            if expr is None:
                raise UnsupportedConstructError(
                    "constraint %r holds a non-expression statement %s"
                    % (getattr(fn, "name", "?"), type(st).__name__),
                    loc=getattr(st, "loc", None))
            constraints.append(expr)

    if not rand_fields and not constraints:
        return None

    vars_: List[ScSolveVar] = []
    writeback = {}
    for vid, f in enumerate(rand_fields):
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
        vars_.append(ScSolveVar(name=f.name, var_id=vid, width=width,
                                signed=signed))
        writeback[f.name] = vid

    return ScSolveProblem(vars=vars_, constraints=constraints, writeback=writeback)
