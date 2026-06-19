"""PSS → Scenario lowering pass implementation (Phase 1 slice)."""
from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional, Tuple

from ...data_type import DataTypeClass, DataTypeComponent
from ...activity import (
    ActivitySequenceBlock, ActivityAnonTraversal, ActivityTraversal,
    ActivityRepeat, ActivityForeach, ActivityIfElse, ActivityMatch, MatchCase,
    ActivityAtomic, ActivityParallel, ActivitySchedule, ActivitySelect,
)
from ...scenario import (
    ScCoroutine, ScExecBlock, ScComponentInst, ScenarioModule,
    ScSeq, ScInvoke, ScLoop, ScIf, ScMatch, ScMatchCase, ScAtomic,
    ScPar, ScSelect, ScSelectBranch, ScImport, ScImportDecl,
)
from ...stmt import StmtExpr
from ...expr import ExprCall, ExprAttribute, TypeExprRefSelf, ExprRefUnresolved
from ..validate import ScenarioValidator, UnsupportedConstructError
from .constraints import collect_solve_problem

_log = logging.getLogger("zuspec.ir.xf.pss_lower")

# Function names that are part of the action lifecycle rather than constraints.
_LIFECYCLE_FUNCS = ("body", "pre_solve", "post_solve")


def _get_type_map(ctx: Any) -> Dict[str, Any]:
    """Return the ``name -> DataType`` mapping from a Layer-0 container.

    Accepts the ``zuspec.ir.core.Context`` (``.type_m``), the
    ``zuspec.fe.pss`` ``AstToIrContext`` (``.type_map``), or a plain dict.
    """
    if isinstance(ctx, dict):
        return ctx
    for attr in ("type_map", "type_m"):
        tm = getattr(ctx, attr, None)
        if isinstance(tm, dict):
            return tm
    raise TypeError(
        "cannot find a type map on %r (expected .type_map / .type_m / dict)"
        % type(ctx).__name__)


def _walk_invokes(stmts):
    """Yield every ScInvoke reachable within a coroutine body."""
    for s in stmts:
        if isinstance(s, ScInvoke):
            yield s
        if isinstance(s, ScSelect):
            for br in s.branches:
                yield from _walk_invokes(br.body)
            continue
        if isinstance(s, ScMatch):
            for case in s.cases:
                yield from _walk_invokes(case.body)
            continue
        for attr in ("body", "then_body", "else_body", "branches"):
            sub = getattr(s, attr, None)
            if isinstance(sub, list):
                yield from _walk_invokes(sub)


def _is_action(dt: Any) -> bool:
    """An action is a polymorphic class that is neither a component nor a
    plain struct, and carries either an exec body or an activity."""
    if not isinstance(dt, DataTypeClass) or isinstance(dt, DataTypeComponent):
        return False
    if dt.activity_ir is not None:
        return True
    return any(getattr(f, "name", None) == "body" for f in dt.functions)


class PSSToScenarioPass:
    """Lower a Layer-0 PSS-semantic ``Context`` to a Layer-1
    :class:`~...scenario.ScenarioModule`.

    Args:
        root:    Qualified name of the root component to lower.  When ``None``,
                 auto-detected as the (non-library) component that owns actions.
        exports: Optional explicit list of action simple-names to export.  When
                 ``None``, every lowered atomic action is exported.
    """

    def __init__(self, root: Optional[str] = None,
                 exports: Optional[List[str]] = None,
                 solve_constraints: bool = True):
        self.root = root
        self.exports = exports
        # When True (Phase 3+), ConstraintCollect runs and the lifecycle gains a
        # leading ScSolveProblem; when False the Phase-1 behavior is preserved
        # (constraints parked on pending_constraints, no ScSolveProblem).
        self.solve_constraints = solve_constraints
        self.validator = ScenarioValidator()

    # ------------------------------------------------------------------
    def lower(self, ctx: Any) -> ScenarioModule:
        type_map = _get_type_map(ctx)
        module = ScenarioModule()

        # --- imports: assign stable ids; build the lookup + module decls ---
        self._imports = {}
        for fn_id, f in enumerate(getattr(ctx, "import_functions", []) or []):
            blocking = bool(getattr(f, "is_target", False)) and f.returns is None
            arg_types = []
            args = getattr(f, "args", None)
            for a in (getattr(args, "args", []) if args is not None else []):
                ann = getattr(a, "annotation", None)
                arg_types.append((getattr(ann, "bits", 32) or 32,
                                  bool(getattr(ann, "signed", False))))
            ret_type = None
            if f.returns is not None:
                ret_type = (getattr(f.returns, "bits", 32) or 32,
                            bool(getattr(f.returns, "signed", False)))
            self._imports[f.name] = {
                "fn_id": fn_id, "blocking": blocking,
                "arg_types": arg_types, "ret_type": ret_type}
            module.imports.append(ScImportDecl(
                name=f.name, fn_id=fn_id, blocking=blocking,
                arg_types=arg_types, ret_type=ret_type))

        # --- TraversalResolve (partial): gather actions, grouped by owner ---
        actions = self._collect_actions(type_map)
        root = self.root or self._resolve_root(type_map, actions)
        if root is None:
            raise ValueError("no root component with actions found; "
                             "pass root=... explicitly")
        module.root = ScComponentInst(
            name=root.rsplit("::", 1)[-1], type_name=root)

        owned = [(q, dt) for (q, dt) in actions
                 if q.rsplit("::", 1)[0] == root]
        if not owned:
            raise ValueError("root component %r owns no actions" % root)

        # --- LifecycleNormalize ---
        for qname, dt in owned:
            self.validator.check_action(qname, dt)
            if dt.activity_ir is not None:
                coro = self._lower_compound(qname, dt)   # ScheduleNormalize
            else:
                coro = self._lower_atomic(qname, dt)
            if self.solve_constraints:
                # ConstraintCollect: rand fields + named constraints → a leading
                # ScSolveProblem; clears pending_constraints so nothing is left
                # dangling (the lifecycle becomes solve → body/activity).
                problem = collect_solve_problem(coro, dt)
                if problem is not None:
                    idx = 0
                    if (coro.body and isinstance(coro.body[0], ScExecBlock)
                            and coro.body[0].kind == "pre_solve"):
                        idx = 1
                    coro.body.insert(idx, problem)
                    coro.pending_constraints = []
            module.add_coroutine(coro)

        # --- export selection ---
        if self.exports is not None:
            module.export_actions = list(self.exports)
        else:
            module.export_actions = self._auto_exports(module)

        return module

    @staticmethod
    def _auto_exports(module: ScenarioModule) -> List[str]:
        """Exports = actions not traversed by any other action's activity."""
        invoked = set()
        for coro in module.coroutines.values():
            for inv in _walk_invokes(coro.body):
                invoked.add(inv.target)
        return [n for n in module.coroutines if n not in invoked]

    # ------------------------------------------------------------------
    def _collect_actions(self, type_map: Dict[str, Any]
                         ) -> List[Tuple[str, Any]]:
        out: List[Tuple[str, Any]] = []
        for qname, dt in type_map.items():
            if "::" not in qname:
                # Skip bare-name aliases; we key on qualified names so each
                # action is collected exactly once.
                continue
            if _is_action(dt):
                out.append((qname, dt))
        return out

    def _resolve_root(self, type_map: Dict[str, Any],
                      actions: List[Tuple[str, Any]]) -> Optional[str]:
        comp_keys = {q for q, dt in type_map.items()
                     if isinstance(dt, DataTypeComponent)}
        # Owners that are actually components.
        owners: Dict[str, int] = {}
        for qname, _ in actions:
            owner = qname.rsplit("::", 1)[0]
            if owner in comp_keys:
                owners[owner] = owners.get(owner, 0) + 1
        if not owners:
            return None
        # Prefer a user (non-library) component: heuristic is "no _pkg in the
        # owner path".  Among ties, the one owning the most actions.
        user = {o: n for o, n in owners.items() if "_pkg" not in o}
        pool = user or owners
        return max(pool.items(), key=lambda kv: kv[1])[0]

    def _lower_atomic(self, qname: str, dt: DataTypeClass) -> ScCoroutine:
        """LifecycleNormalize for an atomic action: exec body → coroutine.

        pre_solve / post_solve exec blocks (when present) are emitted around the
        body; named constraint functions are carried on
        ``pending_constraints`` for Phase 3.
        """
        simple = qname.rsplit("::", 1)[-1]
        body_ops: List = []
        pre_block: Optional[ScExecBlock] = None
        post_block: Optional[ScExecBlock] = None
        pending = []
        for f in dt.functions:
            fname = getattr(f, "name", None)
            if fname == "body":
                # Split the exec body at blocking-import calls (each becomes a
                # ScImport suspend point between straight-line ScExecBlocks).
                body_ops = self._lower_exec_stmts(f.body, "body")
            elif fname == "pre_solve":
                pre_block = ScExecBlock(kind="pre_solve", stmts=list(f.body))
            elif fname == "post_solve":
                post_block = ScExecBlock(kind="post_solve", stmts=list(f.body))
            else:
                # A named constraint block (e.g. addr_aligned).  Carried until
                # Phase 3 folds it into a ScSolveProblem.
                pending.append(f)

        # Lifecycle order (pre_solve → [solve] → post_solve → body); the
        # ScSolveProblem is inserted between pre_solve and post_solve by
        # ConstraintCollect in lower().
        seq: List = []
        if pre_block is not None:
            seq.append(pre_block)
        if post_block is not None:
            seq.append(post_block)
        seq.extend(body_ops)

        return ScCoroutine(
            name=simple,
            body=seq,
            action_type=qname,
            pending_constraints=pending,
        ).copy_loc(dt)

    # ------------------------------------------------------------------
    # Exec-body lowering with import splitting
    # ------------------------------------------------------------------
    def _lower_exec_stmts(self, stmts, kind: str) -> List:
        out: List = []
        cur: List = []
        for s in stmts:
            imp = self._as_blocking_import(s)
            if imp is not None:
                if cur:
                    out.append(ScExecBlock(kind=kind, stmts=cur))
                    cur = []
                out.append(imp)
            else:
                cur.append(s)
        if cur or not out:
            out.append(ScExecBlock(kind=kind, stmts=cur))
        return out

    def _as_blocking_import(self, stmt):
        """Return a ScImport if *stmt* is a statement-level call to a blocking
        (target) import, else None."""
        imports = getattr(self, "_imports", {})
        if not isinstance(stmt, StmtExpr):
            return None
        e = stmt.expr
        if not isinstance(e, ExprCall):
            return None
        f = e.func
        if isinstance(f, ExprAttribute) and isinstance(f.value, TypeExprRefSelf):
            name = f.attr
        elif isinstance(f, ExprRefUnresolved):
            name = f.name
        else:
            return None
        info = imports.get(name)
        if info is None or not info["blocking"]:
            return None
        return ScImport(fn=name, fn_id=info["fn_id"], blocking=True,
                        args=list(e.args)).copy_loc(stmt)

    # ------------------------------------------------------------------
    # ScheduleNormalize — compound activities → structured scenario ops
    # ------------------------------------------------------------------
    def _lower_compound(self, qname: str, dt: DataTypeClass) -> ScCoroutine:
        simple = qname.rsplit("::", 1)[-1]
        # pending constraints (named constraint blocks on a compound action)
        pending = [f for f in dt.functions
                   if getattr(f, "name", None) not in _LIFECYCLE_FUNCS]
        body = self._lower_activity(dt.activity_ir)
        return ScCoroutine(
            name=simple, body=body, action_type=qname,
            pending_constraints=pending,
        ).copy_loc(dt)

    def _lower_activity(self, act) -> List:
        """Lower the top-level activity into a coroutine body (a list of ops)."""
        if act is None:
            return []
        if isinstance(act, ActivitySequenceBlock):
            return [self._lower_activity_stmt(s) for s in act.stmts]
        return [self._lower_activity_stmt(act)]

    def _lower_stmts(self, stmts) -> List:
        return [self._lower_activity_stmt(s) for s in stmts]

    def _lower_activity_stmt(self, s):
        if isinstance(s, ActivitySequenceBlock):
            return ScSeq(body=self._lower_stmts(s.stmts)).copy_loc(s)

        if isinstance(s, ActivityAnonTraversal):
            if s.inline_constraints:
                raise UnsupportedConstructError(
                    "inline traversal constraints (`do %s with {...}`) are a "
                    "later phase" % s.action_type, loc=s.getLoc(),
                    remedy="use a named constraint on the action for now")
            return ScInvoke(target=s.action_type, inst=s.label).copy_loc(s)

        if isinstance(s, ActivityTraversal):
            # Resolve a handle field to its action type.
            if s.inline_constraints:
                raise UnsupportedConstructError(
                    "inline traversal constraints on %r are a later phase"
                    % s.handle, loc=s.getLoc())
            return ScInvoke(target=s.handle, inst=s.handle).copy_loc(s)

        if isinstance(s, ActivityRepeat):
            return ScLoop(kind="repeat", count=s.count, index_var=s.index_var,
                          body=self._lower_stmts(s.body)).copy_loc(s)

        if isinstance(s, ActivityForeach):
            return ScLoop(kind="foreach", iter_var=s.iterator,
                          collection=s.collection, index_var=s.index_var,
                          body=self._lower_stmts(s.body)).copy_loc(s)

        if isinstance(s, ActivityIfElse):
            return ScIf(cond=s.condition,
                        then_body=self._lower_stmts(s.if_body),
                        else_body=self._lower_stmts(s.else_body)).copy_loc(s)

        if isinstance(s, ActivityMatch):
            cases = [ScMatchCase(pattern=c.pattern,
                                 body=self._lower_stmts(c.body))
                     for c in s.cases]
            return ScMatch(subject=s.subject, cases=cases).copy_loc(s)

        if isinstance(s, ActivityAtomic):
            return ScAtomic(body=self._lower_stmts(s.stmts)).copy_loc(s)

        if isinstance(s, ActivityParallel):
            return ScPar(branches=self._lower_stmts(s.stmts),
                         join_spec=s.join_spec).copy_loc(s)

        if isinstance(s, ActivitySchedule):
            # Iteration-1 approximation: `schedule` ≈ `parallel` (ALL join).
            _log.info("approximating `schedule` as `parallel`+ALL "
                      "(iteration-1 limitation)")
            return ScPar(branches=self._lower_stmts(s.stmts),
                         join_spec=None).copy_loc(s)

        if isinstance(s, ActivitySelect):
            branches = [ScSelectBranch(guard=b.guard, weight=b.weight,
                                       body=self._lower_stmts(b.body))
                        for b in s.branches]
            return ScSelect(branches=branches,
                            allow_none=getattr(s, "allow_none", False)).copy_loc(s)

        raise UnsupportedConstructError(
            "unsupported activity construct %s" % type(s).__name__,
            loc=getattr(s, "loc", None))
