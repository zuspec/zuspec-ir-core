"""Structured constraint IR (plan task A1 / decision D3).

Constraints were previously carried as ``Function`` nodes flagged with
``metadata['_is_constraint']`` whose bodies were ``StmtExpr``/``StmtIf``/
``StmtForeach``/``StmtUnique``. That representation forces every backend to
re-discover constraint structure and re-render it as text. This module gives a
first-class, backend-neutral constraint model that a code generator can emit
directly and that lowering passes (forwarding, solve-groups) can transform.

A type's constraints are a list of :class:`ConstraintBlock`, each a named block
holding an ordered list of :class:`Constraint` items. Boolean relations live in
the :mod:`zuspec.ir.core.expr` tree (``ExprBin``, ``ExprIn``, ...); the nodes
here capture the constraint *forms* SV expresses as statements or that carry
solver intent (``soft``/``dist``/``solve...before``).
"""
from __future__ import annotations

import dataclasses as dc
from typing import List, Optional

from .base import Base
from .expr import Expr


@dc.dataclass(kw_only=True)
class Constraint(Base):
    """Base class for a single constraint item within a block."""
    pass


@dc.dataclass(kw_only=True)
class ConstraintExpr(Constraint):
    """A boolean expression constraint: ``expr;``."""
    expr: Expr = dc.field()


@dc.dataclass(kw_only=True)
class ConstraintImplies(Constraint):
    """Implication block: ``antecedent -> { body }``.

    A single-expression implication may also be modeled directly in the expr
    tree; this node is for the block form with one or more body constraints.
    """
    antecedent: Expr = dc.field()
    body: List[Constraint] = dc.field(default_factory=list)


@dc.dataclass(kw_only=True)
class ConstraintIfElse(Constraint):
    """``if (cond) { then_body } [ else { else_body } ]``."""
    cond: Expr = dc.field()
    then_body: List[Constraint] = dc.field(default_factory=list)
    else_body: List[Constraint] = dc.field(default_factory=list)


@dc.dataclass(kw_only=True)
class ConstraintForeach(Constraint):
    """Iterative constraint: ``foreach (array[index_var]) { body }``."""
    array: Expr = dc.field()
    index_var: str = dc.field()
    body: List[Constraint] = dc.field(default_factory=list)


@dc.dataclass(kw_only=True)
class ConstraintUnique(Constraint):
    """``unique { items };`` — all listed members take distinct values."""
    items: List[Expr] = dc.field(default_factory=list)


@dc.dataclass(kw_only=True)
class ConstraintSoft(Constraint):
    """``soft expr;`` — a soft constraint that yields to harder ones."""
    expr: Expr = dc.field()


@dc.dataclass(kw_only=True)
class DistWeight(Base):
    """One weighted entry in a ``dist`` set.

    ``rng`` is a value or range expression (``ExprConstant``/``ExprRange``).
    ``weight`` is the weight expression (``None`` => default weight 1).
    ``per_value`` selects the ``:/`` operator (split weight across the range)
    versus ``:=`` (same weight for each value in the range).
    """
    rng: Expr = dc.field()
    weight: Optional[Expr] = dc.field(default=None)
    per_value: bool = dc.field(default=False)


@dc.dataclass(kw_only=True)
class ConstraintDist(Constraint):
    """``target dist { w1, w2, ... };`` — weighted value distribution."""
    target: Expr = dc.field()
    weights: List[DistWeight] = dc.field(default_factory=list)


@dc.dataclass(kw_only=True)
class ConstraintSolveBefore(Constraint):
    """``solve <before> before <after>;`` — solver ordering (affects
    distribution only, not the solution set)."""
    before: List[Expr] = dc.field(default_factory=list)
    after: List[Expr] = dc.field(default_factory=list)


@dc.dataclass(kw_only=True)
class ConstraintBlock(Base):
    """A named constraint block: ``constraint name { items }``.

    ``is_dynamic`` marks SV ``constraint`` blocks that may be toggled at runtime
    via ``constraint_mode()`` (used by the solve-group / select machinery).
    """
    name: str = dc.field()
    items: List[Constraint] = dc.field(default_factory=list)
    is_dynamic: bool = dc.field(default=False)
