"""Activity IR nodes for PSS action activities.

These nodes represent the parsed structure of ``async def activity(self)``
methods on compound actions (PSS Section 12).  They are produced by
``ActivityParser`` and stored on the class as ``cls.__activity__``.

Node hierarchy::

    ActivityStmt
    ├── ActivitySequenceBlock   -- sequence { ... } or top-level body
    ├── ActivityParallel        -- parallel [join_spec] { ... }
    ├── ActivitySchedule        -- schedule [join_spec] { ... }
    ├── ActivityAtomic          -- atomic { ... }
    ├── ActivityTraversal       -- self.handle() [with constraints]
    ├── ActivityAnonTraversal   -- await do(Type) [as label] [with constraints]
    ├── ActivitySuper           -- super().activity()
    ├── ActivityRepeat          -- for i in range(N)
    ├── ActivityDoWhile         -- with do_while(cond):
    ├── ActivityWhileDo         -- with while_do(cond):
    ├── ActivityForeach         -- for item in self.collection
    ├── ActivitySelect          -- with select():
    ├── ActivityIfElse          -- if cond: ... else: ...
    ├── ActivityMatch           -- match subject: case ...:
    ├── ActivityReplicate       -- for i in replicate(N)
    ├── ActivityConstraint      -- with constraint(): ...
    └── ActivityBind            -- bind(src, dst)
"""
from __future__ import annotations

import dataclasses as dc
import enum
from typing import Any, Dict, List, Optional, Tuple, TYPE_CHECKING

from .base import Base

if TYPE_CHECKING:
    from .expr import Expr
    from .visitor import Visitor


# ---------------------------------------------------------------------------
# Join specification (used by parallel / schedule)
# ---------------------------------------------------------------------------

class JoinKind(enum.Enum):
    """Join policy for a parallel or schedule block"""
    ALL    = enum.auto()
    NONE   = enum.auto()
    FIRST  = enum.auto()
    SELECT = enum.auto()
    BRANCH = enum.auto()

@dc.dataclass(kw_only=True)
class JoinSpec(Base):
    """Specifies the join policy for a parallel or schedule block.

    Attributes:
        kind:          Join policy (default ``JoinKind.ALL``).
        branch_label:  Target label for ``kind=JoinKind.BRANCH``, from the
                       Python front end, which names one.
        branch_labels: The labels a PSS ``join_branch(L1, L2, ...)`` names,
                       in order (LRM 11.3.4): the join waits for those
                       branches.
        count:         Branch count for ``kind=JoinKind.SELECT`` or ``JoinKind.FIRST``.
    """
    kind: JoinKind = dc.field(default=JoinKind.ALL)
    branch_label: Optional[str] = dc.field(default=None)
    count: Optional['Expr'] = dc.field(default=None)
    branch_labels: List[str] = dc.field(default_factory=list)

    def accept(self, v: 'Visitor') -> None:
        v.visitJoinSpec(self)


# ---------------------------------------------------------------------------
# Base activity statement
# ---------------------------------------------------------------------------

@dc.dataclass(kw_only=True)
class ActivityStmt(Base):
    """Base class for all activity IR nodes.

    Attributes:
        pragmas: Key/value map from ``# zdc: key=value, flag`` comments on the
                 corresponding source line.  Flag tokens (no ``=``) are stored
                 as ``{token: True}``.  Backends query this dict to emit
                 tool-specific directives (e.g. ``(* parallel_case *)`` in SV).
                 Labels (``# zdc: label=my_fsm``) can be used to identify
                 specific IR nodes from outside the parse.
        label:   The statement's PSS label (``L1: sequence {...}``,
                 ``L2: do B;``), or None. A labeled statement is a named
                 sub-activity (LRM 11.8): join-branch lists, scheduling
                 constraints and hierarchical references name it.
    """
    pragmas: Dict[str, Any] = dc.field(default_factory=dict)
    label: Optional[str] = dc.field(default=None)

    def accept(self, v: 'Visitor') -> None:
        v.visitActivityStmt(self)


# ---------------------------------------------------------------------------
# Compound / block nodes
# ---------------------------------------------------------------------------

@dc.dataclass(kw_only=True)
class ActivitySequenceBlock(ActivityStmt):
    """Sequential block — the default enclosure for activity bodies.

    Corresponds to PSS ``sequence { ... }`` or the implicit top-level body.
    """
    stmts: List[ActivityStmt] = dc.field(default_factory=list)

    def accept(self, v: 'Visitor') -> None:
        v.visitActivitySequenceBlock(self)


@dc.dataclass(kw_only=True)
class ActivityParallel(ActivityStmt):
    """Parallel scheduling block — ``parallel [join_spec] { ... }``."""
    stmts: List[ActivityStmt] = dc.field(default_factory=list)
    join_spec: Optional[JoinSpec] = dc.field(default=None)

    def accept(self, v: 'Visitor') -> None:
        v.visitActivityParallel(self)


@dc.dataclass(kw_only=True)
class ActivitySchedule(ActivityStmt):
    """Schedule block — ``schedule [join_spec] { ... }``."""
    stmts: List[ActivityStmt] = dc.field(default_factory=list)
    join_spec: Optional[JoinSpec] = dc.field(default=None)

    def accept(self, v: 'Visitor') -> None:
        v.visitActivitySchedule(self)


@dc.dataclass(kw_only=True)
class ActivityAtomic(ActivityStmt):
    """Atomic block — ``atomic { ... }``."""
    stmts: List[ActivityStmt] = dc.field(default_factory=list)

    def accept(self, v: 'Visitor') -> None:
        v.visitActivityAtomic(self)


# ---------------------------------------------------------------------------
# Traversal nodes
# ---------------------------------------------------------------------------

@dc.dataclass(kw_only=True)
class ActivityTraversal(ActivityStmt):
    """Traversal of a declared action handle field.

    Corresponds to PSS ``handle;`` or ``handle with { ... };``.

    Attributes:
        handle:             Field name on ``self`` (e.g. ``"a1"``).
        index:              Optional subscript expression for array handles.
        inline_constraints: Constraint expressions from the ``with`` body.
        type_qname:         The handle's action type as the front end's
                            linker resolved it, qualified (``"pss_top::B"``).
                            None when the producer resolved nothing (IR
                            built by hand or from Python); a consumer then
                            resolves ``handle`` itself.
        comp_expr:          The component instance a ``with { comp == X; }``
                            steers the traversal to (LRM 13.4.5, Ex 143),
                            taken out of ``inline_constraints``. None when
                            the traversal does not constrain ``comp``. A
                            consumer that cannot place an action in a chosen
                            instance must refuse it, never ignore it.
    """
    handle: str = dc.field()
    index: Optional['Expr'] = dc.field(default=None)
    inline_constraints: List['Expr'] = dc.field(default_factory=list)
    type_qname: Optional[str] = dc.field(default=None)
    comp_expr: Optional['Expr'] = dc.field(default=None)

    def accept(self, v: 'Visitor') -> None:
        v.visitActivityTraversal(self)


@dc.dataclass(kw_only=True)
class ActivityAnonTraversal(ActivityStmt):
    """Anonymous traversal by action type — ``await T()``.

    Corresponds to PSS ``do Type;`` or ``label: do Type with { ... };``.

    Attributes:
        action_type:        Qualified type name string (e.g. ``"WriteAction"``).
        label:              Optional label when assigned (``x = await T()`` or
                            ``with T() as x:``).
        inline_constraints: Constraint expressions from the ``with`` body.
        init_bindings:      Flow bindings from keyword args (``await T(field=label.attr)``).
        type_qname:         ``action_type`` as the front end's linker
                            resolved it, qualified (``do B`` in ``pss_top``
                            -> ``"pss_top::B"``); ``action_type`` stays as
                            written. None when the producer resolved nothing.
        comp_expr:          As on ``ActivityTraversal``: the instance a
                            ``do T with { comp == X; }`` steers to.
    """
    action_type: str = dc.field()
    type_qname: Optional[str] = dc.field(default=None)
    label: Optional[str] = dc.field(default=None)
    inline_constraints: List['Expr'] = dc.field(default_factory=list)
    action_type_cls: Optional[type] = dc.field(default=None)
    comp_expr: Optional['Expr'] = dc.field(default=None)
    init_bindings: List[Tuple[str, str, str]] = dc.field(default_factory=list)
    # Each tuple: (target_field_name, src_label, src_attr)
    # e.g. ("fetch", "fetch", "result") for fetch=fetch.result

    def accept(self, v: 'Visitor') -> None:
        v.visitActivityAnonTraversal(self)


@dc.dataclass(kw_only=True)
class ActivitySuper(ActivityStmt):
    """Traversal of the base-class activity — ``super().activity()``.

    Maps to PSS ``super;`` in an activity block.
    """

    def accept(self, v: 'Visitor') -> None:
        v.visitActivitySuper(self)


# ---------------------------------------------------------------------------
# Loop nodes
# ---------------------------------------------------------------------------

@dc.dataclass(kw_only=True)
class ActivityRepeat(ActivityStmt):
    """Count-based repeat — ``for i in range(N)``.

    Maps to PSS ``repeat (N) { ... }`` or ``repeat (i : N) { ... }``.

    Attributes:
        count:     Expression for the repeat count.
        index_var: Loop variable name (optional).
        body:      Body statements.
    """
    count: 'Expr' = dc.field()
    index_var: Optional[str] = dc.field(default=None)
    body: List[ActivityStmt] = dc.field(default_factory=list)

    def accept(self, v: 'Visitor') -> None:
        v.visitActivityRepeat(self)


@dc.dataclass(kw_only=True)
class ActivityDoWhile(ActivityStmt):
    """Do-while loop — ``with do_while(cond):``.

    Body executes first, then condition is checked.
    Maps to PSS ``repeat { ... } while (cond);``.
    """
    condition: 'Expr' = dc.field()
    body: List[ActivityStmt] = dc.field(default_factory=list)

    def accept(self, v: 'Visitor') -> None:
        v.visitActivityDoWhile(self)


@dc.dataclass(kw_only=True)
class ActivityWhileDo(ActivityStmt):
    """While-do loop — ``with while_do(cond):``.

    Condition checked first, then body executes.
    Not present in PSS LRM but useful for Python-native patterns.
    """
    condition: 'Expr' = dc.field()
    body: List[ActivityStmt] = dc.field(default_factory=list)

    def accept(self, v: 'Visitor') -> None:
        v.visitActivityWhileDo(self)


@dc.dataclass(kw_only=True)
class ActivityForeach(ActivityStmt):
    """Foreach iteration — ``for item in self.collection``.

    Maps to PSS ``foreach (item : collection) { ... }``.

    Attributes:
        iterator:   Loop variable name.
        collection: Expression for the collection (attribute reference).
        index_var:  Index variable name when ``enumerate()`` is used.
        body:       Body statements.
    """
    iterator: str = dc.field()
    collection: 'Expr' = dc.field()
    index_var: Optional[str] = dc.field(default=None)
    body: List[ActivityStmt] = dc.field(default_factory=list)

    def accept(self, v: 'Visitor') -> None:
        v.visitActivityForeach(self)


@dc.dataclass(kw_only=True)
class ActivityReplicate(ActivityStmt):
    """Replicate construct — ``for i in replicate(N)``.

    Unlike ``repeat``, ``replicate`` expands copies into the enclosing
    scheduling scope rather than introducing a sequential loop.
    Maps to PSS ``replicate (N) [label[]:] { ... }``.
    """
    count: 'Expr' = dc.field()
    index_var: Optional[str] = dc.field(default=None)
    label: Optional[str] = dc.field(default=None)
    body: List[ActivityStmt] = dc.field(default_factory=list)

    def accept(self, v: 'Visitor') -> None:
        v.visitActivityReplicate(self)


# ---------------------------------------------------------------------------
# Selection / branching nodes
# ---------------------------------------------------------------------------

@dc.dataclass(kw_only=True)
class SelectBranch(Base):
    """One branch of a ``select`` statement.

    Attributes:
        guard:  Optional boolean guard expression.
        weight: Optional integer weight expression.
        body:   Body statements for this branch.
    """
    guard: Optional['Expr'] = dc.field(default=None)
    weight: Optional['Expr'] = dc.field(default=None)
    body: List[ActivityStmt] = dc.field(default_factory=list)

    def accept(self, v: 'Visitor') -> None:
        v.visitSelectBranch(self)


@dc.dataclass(kw_only=True)
class ActivitySelect(ActivityStmt):
    """Select statement — ``with select(): with branch(): ...``.

    Maps to PSS ``select { ... }``.
    """
    branches: List[SelectBranch] = dc.field(default_factory=list)
    allow_none: bool = dc.field(default=False)  # True when a ``constraint { label.allow_none == true; }`` is present

    def accept(self, v: 'Visitor') -> None:
        v.visitActivitySelect(self)


# ---------------------------------------------------------------------------
# Conditional nodes
# ---------------------------------------------------------------------------

@dc.dataclass(kw_only=True)
class ActivityIfElse(ActivityStmt):
    """If-else statement — standard Python ``if/elif/else``.

    Maps to PSS ``if (cond) { ... } else { ... }``.
    """
    condition: 'Expr' = dc.field()
    if_body: List[ActivityStmt] = dc.field(default_factory=list)
    else_body: List[ActivityStmt] = dc.field(default_factory=list)

    def accept(self, v: 'Visitor') -> None:
        v.visitActivityIfElse(self)


@dc.dataclass(kw_only=True)
class MatchCase(Base):
    """One case of a ``match`` statement.

    ``pattern`` is ``None`` for the ``default`` case.
    """
    pattern: 'Optional[Expr]' = dc.field(default=None)
    body: List[ActivityStmt] = dc.field(default_factory=list)

    def accept(self, v: 'Visitor') -> None:
        v.visitMatchCase(self)


@dc.dataclass(kw_only=True)
class ActivityMatch(ActivityStmt):
    """Match statement — Python ``match/case``.

    Maps to PSS ``match (subject) { ... }``.
    """
    subject: 'Expr' = dc.field()
    cases: List[MatchCase] = dc.field(default_factory=list)

    def accept(self, v: 'Visitor') -> None:
        v.visitActivityMatch(self)


# ---------------------------------------------------------------------------
# Miscellaneous nodes
# ---------------------------------------------------------------------------

@dc.dataclass(kw_only=True)
class ActivityConstraint(ActivityStmt):
    """Scheduling constraint block — ``with constraint(): ...``.

    Constraints inside constrain relationships between sub-action fields.
    """
    constraints: List['Expr'] = dc.field(default_factory=list)

    def accept(self, v: 'Visitor') -> None:
        v.visitActivityConstraint(self)


@dc.dataclass(kw_only=True)
class ActivitySchedulingConstraint(ActivityStmt):
    """``constraint parallel {a, b};`` / ``constraint sequence {a, b};``
    (LRM 13.2): the named sub-activities run in parallel, or in the
    order given, within an enclosing ``schedule``.

    Attributes:
        is_parallel: True for ``parallel``, False for ``sequence``.
        targets:     References to the constrained statements (labels or
                     handles), as ``ExprAttribute`` chains from ``self``.
    """
    is_parallel: bool = dc.field()
    targets: List['Expr'] = dc.field(default_factory=list)

    def accept(self, v: 'Visitor') -> None:
        v.visitActivitySchedulingConstraint(self)


@dc.dataclass(kw_only=True)
class ActivityBind(ActivityStmt):
    """Explicit flow-object binding — ``bind(src, dst)``.

    Maps to PSS ``bind src dst;`` inside an activity.
    """
    src: 'Expr' = dc.field()
    dst: 'Expr' = dc.field()

    def accept(self, v: 'Visitor') -> None:
        v.visitActivityBind(self)


__all__ = [
    "JoinSpec",
    "ActivityStmt",
    "ActivitySequenceBlock",
    "ActivityParallel",
    "ActivitySchedule",
    "ActivityAtomic",
    "ActivityTraversal",
    "ActivityAnonTraversal",
    "ActivitySuper",
    "ActivityRepeat",
    "ActivityDoWhile",
    "ActivityWhileDo",
    "ActivityForeach",
    "ActivityReplicate",
    "SelectBranch",
    "ActivitySelect",
    "ActivityIfElse",
    "MatchCase",
    "ActivityMatch",
    "ActivityConstraint",
    "ActivityBind",
    "ActivitySchedulingConstraint",
    "ActivityFill",
    "ActivityChain",
    "ActivityConstraintForall",
]


# ---------------------------------------------------------------------------
# Phase 3 additions
# ---------------------------------------------------------------------------

@dc.dataclass(kw_only=True)
class ActivityFill(ActivityStmt):
    """Bounded coverage-fill loop -- PSS section 12.9.

    Repeats body until all required bins in the coverage model are hit,
    or max_iters is reached (whichever comes first).
    """
    body: List[ActivityStmt] = dc.field(default_factory=list)
    max_iters: int = dc.field(default=1000)

    def accept(self, v: 'Visitor') -> None:
        v.visitActivityFill(self)


@dc.dataclass(kw_only=True)
class ActivityChain(ActivityStmt):
    """Chain statement -- PSS section 12.2.2.

    Sequential actions connected by automatic flow-object forwarding:
    the output of each action is passed as the input of the next.
    """
    label: Optional[str] = dc.field(default=None)
    stmts: List[ActivityStmt] = dc.field(default_factory=list)

    def accept(self, v: 'Visitor') -> None:
        v.visitActivityChain(self)


@dc.dataclass(kw_only=True)
class ActivityConstraintForall(ActivityStmt):
    """Forall constraint -- PSS section 12.7.

    ``constraint forall (var : Type) { body_exprs }``
    """
    var_name: str = dc.field()
    type_name: str = dc.field()
    body_exprs: List['Expr'] = dc.field(default_factory=list)

    def accept(self, v: 'Visitor') -> None:
        v.visitActivityConstraintForall(self)
