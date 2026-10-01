"""Scenario Runtime IR — the ``scenario`` dialect (Layer 1).

This is the *shared waist* of the PSS-lowering hourglass
(``design/pss-lowering-architecture.md``).  PSS execution semantics are lowered
**once** — from the Layer-0 PSS-semantic IR (``DataTypeComponent`` /
``DataTypeClass`` actions + ``activity_ir`` + constraints) — into this
target-neutral, execution-model-concrete dialect, from which the SystemVerilog,
C (``zuspec-be-sw``), and formal backends each render.

The dialect is **execution-model concrete but target-syntax neutral**: it knows
about coroutines, suspension, time, and solve problems, but nothing about
``logic`` vs ``uint32_t`` or ``fork`` vs ``zsp_par_block``.

Node style follows the existing ``zuspec-ir-core`` convention: every node is a
``@dc.dataclass(kw_only=True)`` deriving from :class:`~.base.Base`, with an
``accept`` method dispatching to ``v.visit<Name>``.  Because ``scenario`` nodes
live in the ``zuspec.ir.core`` package, they are picked up automatically by
``profile(__name__)`` in ``__init__`` and by the synthesized :class:`Visitor`.

The central construct is :class:`ScCoroutine` — a suspendable procedure that SV
renders as a task and C lowers to an FSM function via the (separate, shared)
``CoroutineFSMPass``.

Iteration-1 status: the structured-concurrency ops (:class:`ScPar`,
:class:`ScSelect`, :class:`ScLoop`, :class:`ScIf`, :class:`ScMatch`,
:class:`ScWait`, :class:`ScSpawn`, :class:`ScJoin`) and
:class:`ScSolveProblem` are *defined* in their final shape but only partially
produced/consumed; Phases 3–5 of the impl plan fill them in.
"""
from __future__ import annotations

import dataclasses as dc
import enum
from typing import Any, Dict, List, Optional, Tuple, TYPE_CHECKING

from .base import Base

if TYPE_CHECKING:
    from .expr import Expr
    from .stmt import Stmt
    from .constraint import Constraint
    from .data_type import DataType, Function
    from .activity import JoinSpec
    from .visitor import Visitor


# ---------------------------------------------------------------------------
# Base statement
# ---------------------------------------------------------------------------

@dc.dataclass(kw_only=True)
class ScStmt(Base):
    """Base class for statements that appear inside a coroutine body."""

    def accept(self, v: 'Visitor') -> None:
        v.visitScStmt(self)


# ---------------------------------------------------------------------------
# The shared construct
# ---------------------------------------------------------------------------

@dc.dataclass(kw_only=True)
class ScCoroutine(Base):
    """A suspendable procedure — an action ``body`` or a compound ``activity``.

    Attributes:
        name:
            Unique coroutine name within the :class:`ScenarioModule`
            (e.g. ``"write_reg"`` or ``"write_reg__traverse"``).
        body:
            Ordered list of structured :class:`ScStmt` ops.  Everything between
            suspend points (:class:`ScWait`, :class:`ScInvoke` of a blocking
            sub-coroutine, :class:`ScJoin`) is straight-line/structured.
        params:
            Names of incoming parameters (e.g. a solved-problem handle).  Kept
            simple for iteration 1.
        frame_locals:
            Names of persistent locals that must survive a suspend.  Each
            backend places them (SV: automatic var in a task; C: frame struct).
            Populated by ``CoroutineFSMPass``; empty for no-suspend bodies.
        action_type:
            Qualified Layer-0 type name of the originating action, when this
            coroutine lowers an action lifecycle.  ``None`` for synthetic
            coroutines.
        pending_constraints:
            Layer-0 constraint ``Function`` objects gathered for this action but
            not yet lowered to a :class:`ScSolveProblem`.  Carried explicitly so
            no constraint information is silently dropped before Phase 3 wires
            up ``ConstraintCollect``; Phase 3 consumes these and clears the list.
        fields:
            The originating action's object layout (:class:`ScField`), one
            per slot, in slot order. A struct attribute is flattened to its
            scalar leaves, each named by its dotted path (``s.csr.eol``;
            ``pss_lower.layout``). Exec code names an attribute as
            ``self.<name>``; this is what resolves that name to a slot and a
            type.  Empty for synthetic coroutines.
        subtree:
            The layout of the action's whole subtree (P1-D1): its own
            ``fields``, then each child node's slots named by its path
            (``b1.x``, ``bs[1].s.f``, ``#0.val``), slots relative to the
            action's base. A traversed child's attributes are read through
            it (``self.b1.x`` after ``b1;``). Empty without an action tree.

    Activity statements carry the local index of the activity block they
    open (``scope``, ``then_scope``/``else_scope``): an index into the
    action type's scopes, so ``ScActionTree.scopes`` is the node's scope
    base plus it. Entering a block resets the handles traversed in it
    (13.4.8) and, for a branch or loop body, commits its structure (P1-D2).
    ``None`` without an action tree.
    """
    name: str = dc.field()
    body: List[ScStmt] = dc.field(default_factory=list)
    params: List[str] = dc.field(default_factory=list)
    frame_locals: List[str] = dc.field(default_factory=list)
    action_type: Optional[str] = dc.field(default=None)
    pending_constraints: List['Function'] = dc.field(default_factory=list)
    fields: List['ScField'] = dc.field(default_factory=list)
    subtree: List['ScField'] = dc.field(default_factory=list)

    def accept(self, v: 'Visitor') -> None:
        v.visitScCoroutine(self)


@dc.dataclass(kw_only=True)
class ScField(Base):
    """One slot of an action object: its storage slot and Layer-0 type.

    ``name`` is the scalar's dotted path (``x``, or ``s.f`` for a field of a
    struct attribute). ``slot`` is its index in the flattened object -- the
    same slot a :class:`ScSolveVar` writes back to and ``ExprRefField(index=)``
    addresses.
    """
    name: str = dc.field()
    slot: int = dc.field()
    datatype: Optional['DataType'] = dc.field(default=None)
    rand: bool = dc.field(default=False)


# ---------------------------------------------------------------------------
# Leaf body op — wraps Layer-0 exec statements
# ---------------------------------------------------------------------------

@dc.dataclass(kw_only=True)
class ScExecBlock(ScStmt):
    """Straight-line Layer-0 statements (an ``exec body`` / ``pre_solve`` /
    ``post_solve`` block) embedded verbatim in a coroutine body.

    The statements are unmodified Layer-0 :class:`~.stmt.Stmt` nodes; each
    backend lowers them with its existing statement/expression generators.

    Attributes:
        kind:  ``"body"`` | ``"pre_solve"`` | ``"post_solve"``.
        stmts: The Layer-0 statements.
    """
    kind: str = dc.field(default="body")
    stmts: List['Stmt'] = dc.field(default_factory=list)

    def accept(self, v: 'Visitor') -> None:
        v.visitScExecBlock(self)


# ---------------------------------------------------------------------------
# Structured-concurrency ops (§5.2 of the architecture doc)
# ---------------------------------------------------------------------------

@dc.dataclass(kw_only=True)
class ScSeq(ScStmt):
    """Ordered region — execute ``body`` statements in sequence."""
    body: List[ScStmt] = dc.field(default_factory=list)
    scope: Optional[int] = dc.field(default=None)

    def accept(self, v: 'Visitor') -> None:
        v.visitScSeq(self)


@dc.dataclass(kw_only=True)
class ScPar(ScStmt):
    """Fork ``branches`` concurrently; join per ``join_spec``
    (ALL / FIRST(n) / NONE / SELECT)."""
    branches: List[ScStmt] = dc.field(default_factory=list)
    join_spec: Optional['JoinSpec'] = dc.field(default=None)
    scope: Optional[int] = dc.field(default=None)

    def accept(self, v: 'Visitor') -> None:
        v.visitScPar(self)


@dc.dataclass(kw_only=True)
class ScSelectBranch(Base):
    """One weighted/guarded branch of a :class:`ScSelect`."""
    guard: Optional['Expr'] = dc.field(default=None)
    weight: Optional['Expr'] = dc.field(default=None)
    body: List[ScStmt] = dc.field(default_factory=list)
    scope: Optional[int] = dc.field(default=None)

    def accept(self, v: 'Visitor') -> None:
        v.visitScSelectBranch(self)


@dc.dataclass(kw_only=True)
class ScSelect(ScStmt):
    """Weighted single choice among ``branches``."""
    branches: List[ScSelectBranch] = dc.field(default_factory=list)
    allow_none: bool = dc.field(default=False)

    def accept(self, v: 'Visitor') -> None:
        v.visitScSelect(self)


@dc.dataclass(kw_only=True)
class ScLoop(ScStmt):
    """Counted / foreach / do-while loop.

    Exactly one of ``count`` (repeat/foreach) or ``cond`` (do-while/while-do)
    drives iteration; ``kind`` disambiguates.
    """
    kind: str = dc.field(default="repeat")  # repeat | foreach | dowhile | whiledo
    count: Optional['Expr'] = dc.field(default=None)
    cond: Optional['Expr'] = dc.field(default=None)
    index_var: Optional[str] = dc.field(default=None)
    iter_var: Optional[str] = dc.field(default=None)
    collection: Optional['Expr'] = dc.field(default=None)
    body: List[ScStmt] = dc.field(default_factory=list)
    scope: Optional[int] = dc.field(default=None)

    def accept(self, v: 'Visitor') -> None:
        v.visitScLoop(self)


@dc.dataclass(kw_only=True)
class ScAtomic(ScStmt):
    """No scheduler yields inside — ``body`` runs to completion without
    suspending."""
    body: List[ScStmt] = dc.field(default_factory=list)
    scope: Optional[int] = dc.field(default=None)

    def accept(self, v: 'Visitor') -> None:
        v.visitScAtomic(self)


@dc.dataclass(kw_only=True)
class ScIf(ScStmt):
    """Conditional on a solved/evaluated expression."""
    cond: 'Expr' = dc.field()
    then_body: List[ScStmt] = dc.field(default_factory=list)
    else_body: List[ScStmt] = dc.field(default_factory=list)
    then_scope: Optional[int] = dc.field(default=None)
    else_scope: Optional[int] = dc.field(default=None)

    def accept(self, v: 'Visitor') -> None:
        v.visitScIf(self)


@dc.dataclass(kw_only=True)
class ScMatchCase(Base):
    """One case of a :class:`ScMatch` (``pattern is None`` → default)."""
    pattern: Optional['Expr'] = dc.field(default=None)
    body: List[ScStmt] = dc.field(default_factory=list)
    scope: Optional[int] = dc.field(default=None)

    def accept(self, v: 'Visitor') -> None:
        v.visitScMatchCase(self)


@dc.dataclass(kw_only=True)
class ScMatch(ScStmt):
    """Multi-way branch on ``subject``."""
    subject: 'Expr' = dc.field()
    cases: List[ScMatchCase] = dc.field(default_factory=list)

    def accept(self, v: 'Visitor') -> None:
        v.visitScMatch(self)


@dc.dataclass(kw_only=True)
class ScInvoke(ScStmt):
    """Run a sub-action coroutine to completion (a possible suspend point).

    Attributes:
        target:
            Name of the callee :class:`ScCoroutine`.
        inst:
            Optional sub-action instance name (the traversal handle/label).
        inline_constraints:
            Constraint expressions from a ``with { ... }`` body on the
            traversal, carried until Phase 3 folds them into the callee's
            :class:`ScSolveProblem`.
        child_base:
            Where the traversed action's object starts, in slots from the
            invoking action's own base (P1-D1). It is static: an action
            type's subtree has the same layout wherever it is instantiated
            (:class:`ScActionTree`). ``None`` when the site has no single
            node (a handle-array element with a computed index, an iteration
            of a labeled ``replicate``).
        site:
            The traversal's index among the invoking type's traversal sites
            (those with a node), in walk order: the node's first site in
            ``ScActionTree.sites`` plus it is this traversal there. ``None``
            when ``child_base`` is.
        init:
            A traversal with initializers (LRM 11.3.1 b i-ii): the traversed
            action's attribute initial values, then its handle declaration's
            initializers, then the traversal's -- assignments rooted at the
            child (``self.b1.x``) that the INVOKING action runs, on the
            child's slots, before the child starts. The child then skips its
            own initial values. Empty for a traversal with no initializers.
    """
    target: str = dc.field()
    inst: Optional[str] = dc.field(default=None)
    inline_constraints: List['Expr'] = dc.field(default_factory=list)
    child_base: Optional[int] = dc.field(default=None)
    site: Optional[int] = dc.field(default=None)
    init: List['Stmt'] = dc.field(default_factory=list)

    def accept(self, v: 'Visitor') -> None:
        v.visitScInvoke(self)


@dc.dataclass(kw_only=True)
class ScSpawn(ScStmt):
    """Fork a coroutine without an immediate join (feeds a :class:`ScPar`)."""
    target: str = dc.field()
    inst: Optional[str] = dc.field(default=None)

    def accept(self, v: 'Visitor') -> None:
        v.visitScSpawn(self)


@dc.dataclass(kw_only=True)
class ScJoin(ScStmt):
    """Block until a :class:`ScPar`'s join condition is met."""
    par_label: Optional[str] = dc.field(default=None)

    def accept(self, v: 'Visitor') -> None:
        v.visitScJoin(self)


@dc.dataclass(kw_only=True)
class ScWait(ScStmt):
    """Advance time by ``time`` (a suspend point)."""
    time: 'Expr' = dc.field()

    def accept(self, v: 'Visitor') -> None:
        v.visitScWait(self)


@dc.dataclass(kw_only=True)
class ScImport(ScStmt):
    """Call a PSS ``import`` foreign function (the DUT/testbench API).

    A ``target`` import (void, time-consuming) is **blocking** — a suspend point
    that the host (SV) services as a task. A ``solve`` import (value-returning)
    is non-blocking — the host runs it synchronously.

    Attributes:
        fn:        Import function name.
        fn_id:     Stable integer id (host/C agree on it).
        blocking:  True for a ``target`` import (SV task); False for ``solve``.
        args:      Layer-0 argument expressions.
        ret_var:   Local/field receiving the return value (solve imports), else
                   ``None``.
    """
    fn: str = dc.field()
    fn_id: int = dc.field()
    blocking: bool = dc.field(default=True)
    args: List['Expr'] = dc.field(default_factory=list)
    ret_var: Optional[str] = dc.field(default=None)

    def accept(self, v: 'Visitor') -> None:
        v.visitScImport(self)


@dc.dataclass(kw_only=True)
class ScImportDecl(Base):
    """Module-level declaration of an import (drives the SV shim + marshalling).

    Attributes:
        name:     Import function name.
        fn_id:    Stable integer id.
        blocking: True for ``target`` (SV task), False for ``solve`` (function).
        arg_types: ``[(width, signed)]`` per argument (scalar v1).
        ret_type:  ``(width, signed)`` for the return value, or ``None`` (void).
    """
    name: str = dc.field()
    fn_id: int = dc.field()
    blocking: bool = dc.field(default=True)
    arg_types: List = dc.field(default_factory=list)
    ret_type: Optional[tuple] = dc.field(default=None)

    def accept(self, v: 'Visitor') -> None:
        v.visitScImportDecl(self)


# ---------------------------------------------------------------------------
# Solve problem (§5.3) — defined in final shape, produced from Phase 3
# ---------------------------------------------------------------------------

@dc.dataclass(kw_only=True)
class ScSolveVar(Base):
    """A declared rand variable in a :class:`ScSolveProblem`.

    ``var_id`` is the variable's index in the solver problem (rand fields only).
    ``slot`` is the variable's index in the action's *full* field list -- i.e. its
    object storage slot, the index a constraint's ``ExprRefField`` and procedural
    ``LD_FIELD``/``ST_FIELD`` use. The two differ when non-rand fields are
    interleaved among the rand fields; ``slot == -1`` means "unset -> equals
    ``var_id``" (the rand-only-object case)."""
    name: str = dc.field()
    var_id: int = dc.field()
    slot: int = dc.field(default=-1)
    width: int = dc.field(default=32)
    signed: bool = dc.field(default=False)
    domain: Optional['Expr'] = dc.field(default=None)

    def accept(self, v: 'Visitor') -> None:
        v.visitScSolveVar(self)


class SolveStrategy(enum.Enum):
    """How/when a :class:`ScSolveProblem` is solved. Set by the frontend
    solve-group analysis; each backend realizes it differently."""
    ELAB_NATIVE    = "elab_native"     # SV randomize() at elaboration
    INJECT_NATIVE  = "inject_native"   # pin inputs, disable dynamic blocks, randomize()
    JOINT_CHAIN    = "joint_chain"     # multi-action back-prop solve (SV: DPI; C: one problem)
    SOLVER_RUNTIME = "solver_runtime"  # dv-solve invoked at run time
    SOLVER_GENTIME = "solver_gentime"  # dv-solve invoked at generation; bake constants


@dc.dataclass(kw_only=True)
class SolveInject(Base):
    """A pinned input value injected before a solve (``INJECT_NATIVE`` /
    ``JOINT_CHAIN``): ``field`` is set to ``value`` prior to solving."""
    field: str = dc.field()
    value: 'Expr' = dc.field()

    def accept(self, v: 'Visitor') -> None:
        v.visitSolveInject(self)


@dc.dataclass(kw_only=True)
class ScSolveProblem(ScStmt):
    """An explicit, scoped constraint problem — the ``dv-solve`` ``SolveProblem``
    in IR form. Also referred to as a *solve group*.

    Attributes:
        vars:        Declared rand vars (with fixed field<->var-id map).
        constraints: Structured constraint IR (Layer-0 :class:`~.constraint.Constraint`
                     items — ``ConstraintExpr``/``ConstraintImplies``/``ConstraintIfElse``/
                     ``ConstraintForeach``/``ConstraintUnique``/``ConstraintSoft``/
                     ``ConstraintDist``). A plain boolean constraint is a
                     ``ConstraintExpr`` wrapping the boolean :class:`~.expr.Expr`.
        writeback:   ``field_name -> var_id`` map: which fields receive which
                     solved values.
        strategy:    When/how the group is solved (see :class:`SolveStrategy`).
        seed:        Randomness source (an :class:`~.expr.Expr`); backends may
                     derive per-problem seeds from a base (e.g. ``seed + index``).
        members:     Participating action/instance scopes. More than one entry
                     marks a joint flow-chain solve.
        inject:      Pinned input values applied before solving.
        solve_before: ``solve <before> before <after>`` ordering pairs
                     (distribution only; lifted from ``ConstraintSolveBefore``).
        arrays:      ``array_base_slot -> [element_slot, ...]`` map. A rand array
                     is flattened into one ``ScSolveVar`` per element (each with its
                     own object slot); this records, for every array field slot, the
                     ordered element slots so ``foreach``/subscript lowering can
                     resolve ``arr[i]`` to the right element var.
    """
    vars: List[ScSolveVar] = dc.field(default_factory=list)
    constraints: List['Constraint'] = dc.field(default_factory=list)
    writeback: Dict[str, int] = dc.field(default_factory=dict)
    arrays: Dict[int, List[int]] = dc.field(default_factory=dict)
    strategy: SolveStrategy = dc.field(default=SolveStrategy.ELAB_NATIVE)
    seed: Optional['Expr'] = dc.field(default=None)
    members: List[str] = dc.field(default_factory=list)
    inject: List[SolveInject] = dc.field(default_factory=list)
    solve_before: List[Tuple[List['Expr'], List['Expr']]] = dc.field(default_factory=list)

    def accept(self, v: 'Visitor') -> None:
        v.visitScSolveProblem(self)


# ---------------------------------------------------------------------------
# The action tree of an activation, and its solve cones (P1.2)
# ---------------------------------------------------------------------------

class ScopeKind(enum.Enum):
    """What an :class:`ScActivityScope` is."""
    ACTIVITY      = "activity"       # a compound action's activity
    SEQUENCE      = "sequence"
    PARALLEL      = "parallel"
    SCHEDULE      = "schedule"
    ATOMIC        = "atomic"
    SELECT_BRANCH = "select_branch"
    IF_THEN       = "if_then"
    IF_ELSE       = "if_else"
    MATCH_CASE    = "match_case"
    LOOP_BODY     = "loop_body"      # repeat / foreach / while / replicate
    LOOP_BODY_CERTAIN = "loop_body_certain"  # a body that surely runs: a
                                             # positive constant count, do-while
    REPLICATE_ITER = "replicate_iter"  # one iteration of a labeled replicate


#: Entering a scope of one of these kinds commits the structure of the nodes
#: it holds (design §5.4); the others commit with their enclosing scope. A loop
#: body that surely runs commits with its loop: its first iteration's
#: traversals are lookahead for what precedes the loop (LRM Ex 180).
COMMITTING_SCOPES = frozenset({
    ScopeKind.SELECT_BRANCH, ScopeKind.IF_THEN, ScopeKind.IF_ELSE,
    ScopeKind.MATCH_CASE, ScopeKind.LOOP_BODY})


@dc.dataclass(kw_only=True)
class ScActivityScope(Base):
    """One activity block of one node's activity.

    Attributes:
        id:     Index in :attr:`ScActionTree.scopes`.
        kind:   What the block is.
        parent: The enclosing scope; for a node's ACTIVITY scope, the scope
                holding the node's first traversal (None for the root's).
        node:   The node whose activity holds it.
    """
    id: int = dc.field()
    kind: ScopeKind = dc.field()
    parent: Optional[int] = dc.field(default=None)
    node: int = dc.field()

    def accept(self, v: 'Visitor') -> None:
        v.visitScActivityScope(self)


@dc.dataclass(kw_only=True)
class ScActionNode(Base):
    """One action of an activation: the root, a handle, an anonymous
    traversal site, or an iteration's instance of one (P1-D1).

    Attributes:
        id:         Index in :attr:`ScActionTree.nodes`; the root is 0.
        path:       Its name from the root, dotted: ``""`` for the root,
                    ``s1.a``, ``arr[1]``, ``#2`` (the third anonymous
                    traversal of its parent's activity, when unlabeled),
                    ``R[0].#0``.
        type_qname: Its action type.
        base:       Its first slot in the activation's object.
        size:       Its own slots (``layout.object_layout`` of its type);
                    its children follow, in :attr:`children` order.
        parent:     The node whose attribute or activity declares it.
        decl_scope: The scope whose every entry leaves it uninitialized
                    (13.4.8): the block declaring it, or for a handle
                    declared in an action body, its parent's ACTIVITY scope.
        children:   Child node ids.
        comp:       The component instances it may run in (P1.5, LRM
                    13.4.5): ids of instances of its action's component
                    type, as offsets from its parent's instance (the root's,
                    from the root component's), in pre-order. One
                    candidate: it runs there. More: the solve chooses.
        comp_slot:  With more than one candidate, the slot of the
                    activation's object holding the instance chosen (an
                    absolute :attr:`ScCompInstance.id`): a variable of the
                    node's cone. It follows the action subtrees.
    """
    id: int = dc.field()
    path: str = dc.field(default="")
    type_qname: str = dc.field()
    base: int = dc.field(default=0)
    size: int = dc.field(default=0)
    parent: Optional[int] = dc.field(default=None)
    decl_scope: Optional[int] = dc.field(default=None)
    children: List[int] = dc.field(default_factory=list)
    comp: List[int] = dc.field(default_factory=lambda: [0])
    comp_slot: Optional[int] = dc.field(default=None)

    def accept(self, v: 'Visitor') -> None:
        v.visitScActionNode(self)


@dc.dataclass(kw_only=True)
class ScTraversalSite(Base):
    """One traversal statement, in one node's activity.

    Attributes:
        id:     Index in :attr:`ScActionTree.sites`.
        owner:  The node whose activity holds the statement.
        target: The node it traverses.
        scope:  The scope the statement is in.
    """
    id: int = dc.field()
    owner: int = dc.field()
    target: int = dc.field()
    scope: int = dc.field()

    def accept(self, v: 'Visitor') -> None:
        v.visitScTraversalSite(self)


class ScopeConstraintKind(enum.Enum):
    """Where a constraint of an :class:`ScScopeProblem` comes from, which
    decides when it is in force."""
    TYPE     = "type"      # a constraint of a node's type: while its nodes exist
    ACTIVITY = "activity"  # an activity `constraint`: in its scope (13.1.9)
    WITH     = "with"      # an inline `with`: at its traversal only (13.1.4)
    COMP     = "comp"      # a node's component is one of its candidates
                           # (13.4.5, P1-D4): while the node exists


@dc.dataclass(kw_only=True)
class ScScopeVar(Base):
    """A variable of an :class:`ScScopeProblem`: one slot of one node.

    A non-rand slot a constraint reads is a variable too, pinned to its value
    in the object when the problem is solved.
    """
    name: str = dc.field()
    node: int = dc.field()
    slot: int = dc.field()
    width: int = dc.field(default=32)
    signed: bool = dc.field(default=False)
    rand: bool = dc.field(default=True)

    def accept(self, v: 'Visitor') -> None:
        v.visitScScopeVar(self)


@dc.dataclass(kw_only=True)
class ScScopeConstraint(Base):
    """One constraint of an :class:`ScScopeProblem`, with when it holds.

    Attributes:
        constraint: The constraint; its references are ``ExprRefField``
                    slots of the activation's object. A reference that did
                    not resolve is left as written, for the backend to refuse.
        nodes:      The nodes it reads.
        kind:       Where it comes from.
        owner:      The node whose type, activity or traversal declares it.
        scope:      ACTIVITY: the scope it is declared in.
        site:       WITH: the traversal it belongs to.
    """
    constraint: 'Constraint' = dc.field()
    nodes: List[int] = dc.field(default_factory=list)
    kind: ScopeConstraintKind = dc.field(default=ScopeConstraintKind.TYPE)
    owner: int = dc.field(default=0)
    scope: Optional[int] = dc.field(default=None)
    site: Optional[int] = dc.field(default=None)

    def accept(self, v: 'Visitor') -> None:
        v.visitScScopeConstraint(self)


@dc.dataclass(kw_only=True)
class ScScopeProblem(Base):
    """A connected cone of an activation (P1-D2, LRM 13.4.9, 13.4.10).

    The nodes whose values are tied by constraints -- a parent's constraint
    over its sub-actions' attributes, a `with`, an activity constraint -- and
    every such constraint. Each traversal of a node here solves the whole
    problem, with the values already committed pinned and only the
    constraints in force enabled, and commits only the traversed node's
    values: so a value is chosen with lookahead over the constraints the rest
    of the activity will impose. A node no constraint ties to another keeps
    its own :class:`ScSolveProblem` (P1-D3) and is in no cone.
    """
    id: int = dc.field()
    nodes: List[int] = dc.field(default_factory=list)
    vars: List[ScScopeVar] = dc.field(default_factory=list)
    constraints: List[ScScopeConstraint] = dc.field(default_factory=list)

    def accept(self, v: 'Visitor') -> None:
        v.visitScScopeProblem(self)


@dc.dataclass(kw_only=True)
class ScActionTree(Base):
    """The static action tree of one exported action (P1-D1).

    Every handle and anonymous traversal site, recursively through compound
    types, laid out in ONE object of :attr:`size` slots: node ``n`` owns
    ``n.base .. n.base + n.size``. An action type's subtree has the same
    layout wherever it is instantiated, which is what makes
    :attr:`ScInvoke.child_base` static.
    """
    root: str = dc.field()
    type_qname: str = dc.field()
    size: int = dc.field(default=0)
    nodes: List[ScActionNode] = dc.field(default_factory=list)
    scopes: List[ScActivityScope] = dc.field(default_factory=list)
    sites: List[ScTraversalSite] = dc.field(default_factory=list)
    cones: List[ScScopeProblem] = dc.field(default_factory=list)

    def accept(self, v: 'Visitor') -> None:
        v.visitScActionTree(self)

    def node_at(self, path: str) -> ScActionNode:
        for n in self.nodes:
            if n.path == path:
                return n
        raise KeyError(path)

    def cone_of(self, node: int) -> Optional[ScScopeProblem]:
        for c in self.cones:
            if node in c.nodes:
                return c
        return None


# ---------------------------------------------------------------------------
# Instances & module registry
# ---------------------------------------------------------------------------

@dc.dataclass(kw_only=True)
class ScActionInst(Base):
    """A declared sub-action instance within a compound action."""
    name: str = dc.field()
    type_name: str = dc.field()

    def accept(self, v: 'Visitor') -> None:
        v.visitScActionInst(self)


@dc.dataclass(kw_only=True)
class ScCompInstance(Base):
    """One instance of the elaborated component tree (P1.5).

    Attributes:
        id:         Pre-order index; the root component is 0. The instances
                    of a component type's subtree are numbered the same way
                    wherever it is instantiated, so an instance is its
                    parent's id plus a static offset.
        path:       Its name from the root, dotted: ``""``, ``a.sub``,
                    ``ch[2]``.
        type_qname: Its component type.
        base:       Its first slot in the component object.
        size:       The slots of its subtree.
        count:      The instances of its subtree, itself included.
        parent:     Its parent instance; None for the root.
    """
    id: int = dc.field()
    path: str = dc.field(default="")
    type_qname: str = dc.field()
    base: int = dc.field(default=0)
    size: int = dc.field(default=0)
    count: int = dc.field(default=1)
    parent: Optional[int] = dc.field(default=None)

    def accept(self, v: 'Visitor') -> None:
        v.visitScCompInstance(self)


@dc.dataclass(kw_only=True)
class ScCompInit(Base):
    """One block of component-tree construction, run in one instance:
    ``self`` is that instance. ``kind`` is ``"init"`` (the attributes'
    declared initial values), ``"init_down"`` or ``"init_up"``."""
    instance: int = dc.field()
    kind: str = dc.field()
    stmts: List['Stmt'] = dc.field(default_factory=list)

    def accept(self, v: 'Visitor') -> None:
        v.visitScCompInit(self)


@dc.dataclass(kw_only=True)
class ScComponentTree(Base):
    """The elaborated component tree (P1.5, LRM 9.1.4): ONE flattened
    component object, each instance a slot range (P1-D1).

    Attributes:
        root:       The root component type.
        size:       Slots of the component object.
        instances:  Every instance, in pre-order.
        fields:     Every slot, named by its path from the root
                    (``a.sub.k``, ``ch[2].id``).
        init:       Construction, in order (9.1.4.1 d, 20.1.3): every
                    instance's initial values, then ``init_down`` top-down,
                    then ``init_up`` bottom-up (Example 281's order).
    """
    root: str = dc.field()
    size: int = dc.field(default=0)
    instances: List[ScCompInstance] = dc.field(default_factory=list)
    fields: List[ScField] = dc.field(default_factory=list)
    init: List[ScCompInit] = dc.field(default_factory=list)

    def accept(self, v: 'Visitor') -> None:
        v.visitScComponentTree(self)


@dc.dataclass(kw_only=True)
class ScComponentInst(Base):
    """A component instance in the elaborated component tree."""
    name: str = dc.field()
    type_name: str = dc.field()
    children: List['ScComponentInst'] = dc.field(default_factory=list)

    def accept(self, v: 'Visitor') -> None:
        v.visitScComponentInst(self)


# ---------------------------------------------------------------------------
# Harness / entry points (§3)
# ---------------------------------------------------------------------------

class HarnessKind(enum.Enum):
    """The flavor of a runnable entry (drives the per-backend expansion)."""
    STANDALONE = "standalone"   # self-driving: owns seed + run loop + finish
    EXPORT_API = "export_api"   # callable entry; no own seed/finish (caller drives)
    DPI_FACADE = "dpi_facade"   # SV shim over a C standalone entry
    BRIDGE     = "bridge"       # SV<->C bridge dispatch entry


class SeedSource(enum.Enum):
    """Where a harness obtains its random seed / verbosity value."""
    FIXED    = "fixed"     # a constant (see ScHarness.seed_value)
    PLUSARG  = "plusarg"   # SV ``$value$plusargs`` (default in seed_value)
    ARGV     = "argv"      # command-line argument
    TIME     = "time"      # wall-clock time
    EXTERNAL = "external"  # supplied by the caller/environment


@dc.dataclass(kw_only=True)
class ScRootAction(Base):
    """One root action run by a harness, with its lifecycle flavor."""
    coroutine: str = dc.field()              # ScCoroutine name to run
    has_activity: bool = dc.field(default=True)  # activity() vs body()
    order: int = dc.field(default=0)         # run order among roots

    def accept(self, v: 'Visitor') -> None:
        v.visitScRootAction(self)


@dc.dataclass(kw_only=True)
class ScHarness(Base):
    """Backend-neutral description of one runnable entry point (§3).

    Carries only the *environment* that differs between a testbench module, a C
    ``main``, and a DPI facade; the per-root lifecycle itself is an ordinary
    scenario subtree. Each backend owns the expansion of this node.

    Attributes:
        name:             Entry name (generated module / function).
        kind:             Entry flavor (see :class:`HarnessKind`).
        comp_tree:        Component tree to construct, or ``None`` to use
                          :attr:`ScenarioModule.root`.
        roots:            Root actions to run, in ``order``.
        seed_source:      Where the seed comes from.
        seed_value:       Constant seed (``FIXED``) or default (``PLUSARG``).
        verbosity_source: Optional verbosity control source.
        timeout:          Watchdog timeout (an :class:`~.expr.Expr`, ns);
                          ``None`` disables it.
        import_binding:   Import-interface driver type name, if the entry must
                          bind one; otherwise ``None``.
        finish_on_complete: Emit ``$finish`` / ``return`` at the end.
    """
    name: str = dc.field()
    kind: HarnessKind = dc.field(default=HarnessKind.STANDALONE)
    comp_tree: Optional[ScComponentInst] = dc.field(default=None)
    roots: List[ScRootAction] = dc.field(default_factory=list)
    seed_source: SeedSource = dc.field(default=SeedSource.PLUSARG)
    seed_value: Optional['Expr'] = dc.field(default=None)
    verbosity_source: Optional[SeedSource] = dc.field(default=None)
    timeout: Optional['Expr'] = dc.field(default=None)
    import_binding: Optional[str] = dc.field(default=None)
    finish_on_complete: bool = dc.field(default=True)

    def accept(self, v: 'Visitor') -> None:
        v.visitScHarness(self)


@dc.dataclass(kw_only=True)
class ScenarioModule(Base):
    """Top-level container for a lowered scenario (Layer-1 analog of
    :class:`~.context.Context`).

    Attributes:
        coroutines:
            ``name -> ScCoroutine`` for every lowered coroutine.
        root:
            The root :class:`ScComponentInst`, when elaborated.
        export_actions:
            Names of actions exported as runnable entry points.
        deferred_actions:
            Layer-0 qualified names of actions recognized but not yet lowered in
            the current phase (e.g. compound activities before Phase 4).  Kept
            so callers can see — not silently ignore — what was skipped.
        entries:
            Runnable entry points (:class:`ScHarness`), expanded per backend.
    """
    coroutines: Dict[str, ScCoroutine] = dc.field(default_factory=dict)
    #: Native PSS functions exec code may call, by the name a call site uses:
    #: package/global functions by bare and qualified name, component functions
    #: as ``<component>::<name>``. Layer-0 ``Function``s, bodies unlowered.
    functions: Dict[str, 'Function'] = dc.field(default_factory=dict)
    root: Optional[ScComponentInst] = dc.field(default=None)
    export_actions: List[str] = dc.field(default_factory=list)
    deferred_actions: List[str] = dc.field(default_factory=list)
    imports: List['ScImportDecl'] = dc.field(default_factory=list)
    entries: List[ScHarness] = dc.field(default_factory=list)
    #: The Layer-0 types by name (qualified and bare), so a backend can lay
    #: out a struct a local is declared with (``pss_lower.layout``): its base
    #: is a by-name reference.
    types: Dict[str, 'DataType'] = dc.field(default_factory=dict)
    #: The action tree of each exported action, by coroutine name.
    trees: Dict[str, ScActionTree] = dc.field(default_factory=dict)
    #: The elaborated component tree under the root component (P1.5).
    comp_tree: Optional[ScComponentTree] = dc.field(default=None)

    def accept(self, v: 'Visitor') -> None:
        v.visitScenarioModule(self)

    # Convenience -----------------------------------------------------------
    def add_coroutine(self, coro: ScCoroutine) -> ScCoroutine:
        if coro.name in self.coroutines:
            raise ValueError("duplicate coroutine name %r" % coro.name)
        self.coroutines[coro.name] = coro
        return coro


__all__ = [
    "ScCompInstance",
    "ScCompInit",
    "ScComponentTree",
    "ScStmt",
    "ScCoroutine",
    "ScExecBlock",
    "ScSeq",
    "ScPar",
    "ScSelectBranch",
    "ScSelect",
    "ScLoop",
    "ScAtomic",
    "ScIf",
    "ScMatchCase",
    "ScMatch",
    "ScInvoke",
    "ScSpawn",
    "ScJoin",
    "ScWait",
    "ScImport",
    "ScImportDecl",
    "ScSolveVar",
    "SolveStrategy",
    "SolveInject",
    "ScSolveProblem",
    "ScopeKind",
    "COMMITTING_SCOPES",
    "ScActivityScope",
    "ScActionNode",
    "ScTraversalSite",
    "ScopeConstraintKind",
    "ScScopeVar",
    "ScScopeConstraint",
    "ScScopeProblem",
    "ScActionTree",
    "ScActionInst",
    "ScComponentInst",
    "HarnessKind",
    "SeedSource",
    "ScRootAction",
    "ScHarness",
    "ScenarioModule",
]
