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
    from .data_type import Function
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
    """
    name: str = dc.field()
    body: List[ScStmt] = dc.field(default_factory=list)
    params: List[str] = dc.field(default_factory=list)
    frame_locals: List[str] = dc.field(default_factory=list)
    action_type: Optional[str] = dc.field(default=None)
    pending_constraints: List['Function'] = dc.field(default_factory=list)

    def accept(self, v: 'Visitor') -> None:
        v.visitScCoroutine(self)


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

    def accept(self, v: 'Visitor') -> None:
        v.visitScSeq(self)


@dc.dataclass(kw_only=True)
class ScPar(ScStmt):
    """Fork ``branches`` concurrently; join per ``join_spec``
    (ALL / FIRST(n) / NONE / SELECT)."""
    branches: List[ScStmt] = dc.field(default_factory=list)
    join_spec: Optional['JoinSpec'] = dc.field(default=None)

    def accept(self, v: 'Visitor') -> None:
        v.visitScPar(self)


@dc.dataclass(kw_only=True)
class ScSelectBranch(Base):
    """One weighted/guarded branch of a :class:`ScSelect`."""
    guard: Optional['Expr'] = dc.field(default=None)
    weight: Optional['Expr'] = dc.field(default=None)
    body: List[ScStmt] = dc.field(default_factory=list)

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

    def accept(self, v: 'Visitor') -> None:
        v.visitScLoop(self)


@dc.dataclass(kw_only=True)
class ScAtomic(ScStmt):
    """No scheduler yields inside — ``body`` runs to completion without
    suspending."""
    body: List[ScStmt] = dc.field(default_factory=list)

    def accept(self, v: 'Visitor') -> None:
        v.visitScAtomic(self)


@dc.dataclass(kw_only=True)
class ScIf(ScStmt):
    """Conditional on a solved/evaluated expression."""
    cond: 'Expr' = dc.field()
    then_body: List[ScStmt] = dc.field(default_factory=list)
    else_body: List[ScStmt] = dc.field(default_factory=list)

    def accept(self, v: 'Visitor') -> None:
        v.visitScIf(self)


@dc.dataclass(kw_only=True)
class ScMatchCase(Base):
    """One case of a :class:`ScMatch` (``pattern is None`` → default)."""
    pattern: Optional['Expr'] = dc.field(default=None)
    body: List[ScStmt] = dc.field(default_factory=list)

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
    """
    target: str = dc.field()
    inst: Optional[str] = dc.field(default=None)
    inline_constraints: List['Expr'] = dc.field(default_factory=list)

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
    """A declared rand variable in a :class:`ScSolveProblem`."""
    name: str = dc.field()
    var_id: int = dc.field()
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
    """
    vars: List[ScSolveVar] = dc.field(default_factory=list)
    constraints: List['Constraint'] = dc.field(default_factory=list)
    writeback: Dict[str, int] = dc.field(default_factory=dict)
    strategy: SolveStrategy = dc.field(default=SolveStrategy.ELAB_NATIVE)
    seed: Optional['Expr'] = dc.field(default=None)
    members: List[str] = dc.field(default_factory=list)
    inject: List[SolveInject] = dc.field(default_factory=list)
    solve_before: List[Tuple[List['Expr'], List['Expr']]] = dc.field(default_factory=list)

    def accept(self, v: 'Visitor') -> None:
        v.visitScSolveProblem(self)


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
    root: Optional[ScComponentInst] = dc.field(default=None)
    export_actions: List[str] = dc.field(default_factory=list)
    deferred_actions: List[str] = dc.field(default_factory=list)
    imports: List['ScImportDecl'] = dc.field(default_factory=list)
    entries: List[ScHarness] = dc.field(default_factory=list)

    def accept(self, v: 'Visitor') -> None:
        v.visitScenarioModule(self)

    # Convenience -----------------------------------------------------------
    def add_coroutine(self, coro: ScCoroutine) -> ScCoroutine:
        if coro.name in self.coroutines:
            raise ValueError("duplicate coroutine name %r" % coro.name)
        self.coroutines[coro.name] = coro
        return coro


__all__ = [
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
    "ScActionInst",
    "ScComponentInst",
    "HarnessKind",
    "SeedSource",
    "ScRootAction",
    "ScHarness",
    "ScenarioModule",
]
