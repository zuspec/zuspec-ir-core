from __future__ import annotations
import dataclasses as dc
from typing import Optional

from .base import Base
from .expr import Expr, Keyword

# Phase2 container and comprehension nodes
@dc.dataclass(kw_only=True)
class ExprList(Expr):
    elts: list[Expr] = dc.field(default_factory=list)

@dc.dataclass(kw_only=True)
class ExprTuple(Expr):
    elts: list[Expr] = dc.field(default_factory=list)

@dc.dataclass(kw_only=True)
class ExprDict(Expr):
    keys: list[Optional[Expr]] = dc.field(default_factory=list)
    values: list[Expr] = dc.field(default_factory=list)

@dc.dataclass(kw_only=True)
class ExprSet(Expr):
    elts: list[Expr] = dc.field(default_factory=list)

@dc.dataclass(kw_only=True)
class Comprehension(Base):
    target: Expr = dc.field()
    iter: Expr = dc.field()
    ifs: list[Expr] = dc.field(default_factory=list)
    is_async: bool = dc.field(default=False)

@dc.dataclass(kw_only=True)
class ExprListComp(Expr):
    elt: Expr = dc.field()
    generators: list[Comprehension] = dc.field(default_factory=list)

@dc.dataclass(kw_only=True)
class ExprDictComp(Expr):
    key: Expr = dc.field()
    value: Expr = dc.field()
    generators: list[Comprehension] = dc.field(default_factory=list)

@dc.dataclass(kw_only=True)
class ExprSetComp(Expr):
    elt: Expr = dc.field()
    generators: list[Comprehension] = dc.field(default_factory=list)

@dc.dataclass(kw_only=True)
class ExprGeneratorExp(Expr):
    elt: Expr = dc.field()
    generators: list[Comprehension] = dc.field(default_factory=list)

@dc.dataclass(kw_only=True)
class ExprIfExp(Expr):
    test: Expr = dc.field()
    body: Expr = dc.field()
    orelse: Expr = dc.field()

@dc.dataclass(kw_only=True)
class ExprLambda(Expr):
    """Lambda expression captured for synthesis.

    ``arg_names`` holds the positional parameter names as plain strings.
    ``body`` holds the fully converted IR expression for the lambda body,
    allowing downstream synthesis passes to inspect and lower it.
    """
    arg_names: list = dc.field(default_factory=list)
    body: Expr = dc.field()

@dc.dataclass(kw_only=True)
class ExprNamedExpr(Expr):
    target: Expr = dc.field()
    value: Expr = dc.field()

# Phase4: Formatted strings
@dc.dataclass(kw_only=True)
class ExprJoinedStr(Expr):
    values: list[Expr] = dc.field(default_factory=list)

@dc.dataclass(kw_only=True)
class ExprFormattedValue(Expr):
    value: Expr = dc.field()
    conversion: int = dc.field(default=-1)
    format_spec: Optional[Expr] = dc.field(default=None)
