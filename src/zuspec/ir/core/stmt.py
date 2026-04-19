from __future__ import annotations
import dataclasses as dc
from typing import Any, Dict, Optional, List
from .base import Base
from .expr import Expr, AugOp

@dc.dataclass(kw_only=True)
class Alias(Base):
    name: str = dc.field()
    asname: Optional[str] = dc.field(default=None)

@dc.dataclass(kw_only=True)
class Arg(Base):
    arg: str = dc.field()
    annotation: Optional[Expr] = dc.field(default=None)

@dc.dataclass(kw_only=True)
class Arguments(Base):
    posonlyargs: List[Arg] = dc.field(default_factory=list)
    args: List[Arg] = dc.field(default_factory=list)
    vararg: Optional[Arg] = dc.field(default=None)
    kwonlyargs: List[Arg] = dc.field(default_factory=list)
    kw_defaults: List[Optional[Expr]] = dc.field(default_factory=list)
    kwarg: Optional[Arg] = dc.field(default=None)
    defaults: List[Expr] = dc.field(default_factory=list)

@dc.dataclass(kw_only=True)
class Stmt(Base):
    comment: Optional[str] = dc.field(default=None)

@dc.dataclass(kw_only=True)
class StmtExpr(Stmt):
    expr: Expr = dc.field()

@dc.dataclass(kw_only=True)
class StmtAssign(Stmt):
    targets: List[Expr] = dc.field(default_factory=list)
    value: Expr = dc.field()
    pragmas: Dict[str, Any] = dc.field(default_factory=dict)

@dc.dataclass(kw_only=True)
class StmtAnnAssign(Stmt):
    target: Expr = dc.field()
    annotation: Expr = dc.field()
    value: Optional[Expr] = dc.field(default=None)
    ir_type: Optional[Any] = dc.field(default=None)  # DataType for typed locals (e.g. DataTypeAction)

@dc.dataclass(kw_only=True)
class StmtAugAssign(Stmt):
    target: Expr = dc.field()
    op: AugOp = dc.field()
    value: Expr = dc.field()

@dc.dataclass(kw_only=True)
class StmtReturn(Stmt):
    value: Optional[Expr] = dc.field(default=None)

@dc.dataclass(kw_only=True)
class StmtIf(Stmt):
    test: Expr = dc.field()
    body: List[Stmt] = dc.field(default_factory=list)
    orelse: List[Stmt] = dc.field(default_factory=list)
    pragmas: Dict[str, Any] = dc.field(default_factory=dict)

@dc.dataclass(kw_only=True)
class StmtFor(Stmt):
    target: Expr = dc.field()
    iter: Expr = dc.field()
    body: List[Stmt] = dc.field(default_factory=list)
    orelse: List[Stmt] = dc.field(default_factory=list)

@dc.dataclass(kw_only=True)
class StmtWhile(Stmt):
    test: Expr = dc.field()
    body: List[Stmt] = dc.field(default_factory=list)
    orelse: List[Stmt] = dc.field(default_factory=list)

@dc.dataclass(kw_only=True)
class StmtBreak(Stmt):
    pass

@dc.dataclass(kw_only=True)
class StmtContinue(Stmt):
    pass

@dc.dataclass(kw_only=True)
class StmtPass(Stmt):
    pass

@dc.dataclass(kw_only=True)
class StmtRaise(Stmt):
    exc: Optional[Expr] = dc.field(default=None)
    cause: Optional[Expr] = dc.field(default=None)

@dc.dataclass(kw_only=True)
class StmtAssert(Stmt):
    test: Expr = dc.field()
    msg: Optional[Expr] = dc.field(default=None)

@dc.dataclass(kw_only=True)
class StmtAssume(Stmt):
    """Represents an assumption (formal verification)"""
    test: Expr = dc.field()
    msg: Optional[Expr] = dc.field(default=None)

@dc.dataclass(kw_only=True)
class StmtCover(Stmt):
    """Represents a coverage goal (formal verification)"""
    test: Expr = dc.field()
    msg: Optional[Expr] = dc.field(default=None)

@dc.dataclass(kw_only=True)
class StmtUnique(Stmt):
    """Represents a uniqueness constraint: all listed variables must have distinct values."""
    vars: List[str] = dc.field(default_factory=list)

# Phase3: With/Try/Except + Phase2 module-level nodes
@dc.dataclass(kw_only=True)
class WithItem(Base):
    context_expr: Expr = dc.field()
    optional_vars: Optional[Expr] = dc.field(default=None)

@dc.dataclass(kw_only=True)
class StmtWith(Stmt):
    items: List[WithItem] = dc.field(default_factory=list)
    body: List[Stmt] = dc.field(default_factory=list)

@dc.dataclass(kw_only=True)
class StmtExceptHandler(Base):
    type: Optional[Expr] = dc.field(default=None)
    name: Optional[str] = dc.field(default=None)
    body: List[Stmt] = dc.field(default_factory=list)

@dc.dataclass(kw_only=True)
class StmtTry(Stmt):
    body: List[Stmt] = dc.field(default_factory=list)
    handlers: List[StmtExceptHandler] = dc.field(default_factory=list)
    orelse: List[Stmt] = dc.field(default_factory=list)
    finalbody: List[Stmt] = dc.field(default_factory=list)

@dc.dataclass(kw_only=True)
class TypeIgnore(Base):
    lineno: int = dc.field()
    tag: str = dc.field()

@dc.dataclass(kw_only=True)
class Module(Base):
    body: List[Stmt] = dc.field(default_factory=list)
    type_ignores: List[TypeIgnore] = dc.field(default_factory=list)

# Phase4: Pattern Matching
@dc.dataclass(kw_only=True)
class StmtMatch(Stmt):
    subject: Expr = dc.field()
    cases: List['StmtMatchCase'] = dc.field(default_factory=list)
    pragmas: Dict[str, Any] = dc.field(default_factory=dict)

@dc.dataclass(kw_only=True)
class StmtMatchCase(Base):
    pattern: 'Pattern' = dc.field()
    guard: Optional[Expr] = dc.field(default=None)
    body: List[Stmt] = dc.field(default_factory=list)

@dc.dataclass(kw_only=True)
class Pattern(Base):
    pass

@dc.dataclass(kw_only=True)
class PatternValue(Pattern):
    value: Expr = dc.field()

@dc.dataclass(kw_only=True)
class PatternAs(Pattern):
    pattern: Optional[Pattern] = dc.field(default=None)
    name: Optional[str] = dc.field(default=None)

@dc.dataclass(kw_only=True)
class PatternOr(Pattern):
    patterns: List[Pattern] = dc.field(default_factory=list)

@dc.dataclass(kw_only=True)
class PatternSequence(Pattern):
    patterns: List[Pattern] = dc.field(default_factory=list)


# ============================================================================
# PSS-Specific Statements (Phase 2)
# ============================================================================

@dc.dataclass(kw_only=True)
class StmtRepeat(Stmt):
    """PSS repeat statement with count: repeat (count) { body }
    
    Examples:
        repeat (10) { ... }
        repeat (i : 10) { array[i] = i; }
    """
    count: Expr = dc.field()
    iterator: Optional[Expr] = dc.field(default=None)  # Optional loop variable
    body: List[Stmt] = dc.field(default_factory=list)


@dc.dataclass(kw_only=True)
class StmtRepeatWhile(Stmt):
    """PSS repeat-while statement: repeat while (condition) { body }
    
    Similar to do-while, but PSS-specific syntax.
    
    Example:
        repeat while (!done) { process(); }
    """
    condition: Expr = dc.field()
    body: List[Stmt] = dc.field(default_factory=list)


@dc.dataclass(kw_only=True)
class StmtForeach(Stmt):
    """PSS foreach statement: foreach (item : collection) { body }
    
    Iterates over elements in a collection with optional index.
    
    Examples:
        foreach (item : array) { process(item); }
        foreach (item[idx] : array) { data[idx] = item; }
    """
    target: Expr = dc.field()  # Iterator variable
    iter: Expr = dc.field()    # Collection to iterate
    body: List[Stmt] = dc.field(default_factory=list)
    index_var: Optional[Expr] = dc.field(default=None)  # Optional index variable


@dc.dataclass(kw_only=True)
class StmtYield(Stmt):
    """PSS yield statement for activity scheduling
    
    Suspends execution and returns control to scheduler.
    
    Example:
        yield;
    """
    value: Optional[Expr] = dc.field(default=None)


@dc.dataclass(kw_only=True)
class StmtRandomize(Stmt):
    """PSS randomize statement with inline constraints
    
    Randomizes an object with optional inline constraints.
    
    Examples:
        randomize(obj);
        randomize(obj) with { x > 10; y < 20; }
    """
    target: Optional[Expr] = dc.field(default=None)
    # Note: constraints will need constraint IR (future phase)
    constraints: List[Stmt] = dc.field(default_factory=list)


# =============================================================================
# Interface-Protocol Statement Nodes (IfProtocol, Completion, Queue, spawn, select)
# =============================================================================

@dc.dataclass(kw_only=True)
class SpawnStmt(Stmt):
    """Represents ``zdc.spawn(self._handler(req, done))``."""
    coro_call: Expr = dc.field()


@dc.dataclass(kw_only=True)
class SelectStmt(Stmt):
    """Represents ``(item, tag) = await zdc.select(...)``."""
    queues: List[Any] = dc.field(default_factory=list)  # List[Tuple[Expr, Any]]
    result_var: str = dc.field(default="")
    tag_var: str = dc.field(default="")


@dc.dataclass(kw_only=True)
class CompletionSetStmt(Stmt):
    """Represents ``done.set(value)``."""
    completion_expr: Expr = dc.field()
    value_expr: Expr = dc.field()


@dc.dataclass(kw_only=True)
class QueuePutStmt(Stmt):
    """Represents ``await queue.put(item)``."""
    queue_expr: Expr = dc.field()
    value_expr: Expr = dc.field()

