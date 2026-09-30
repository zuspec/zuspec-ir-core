"""PSS → Scenario lowering pass implementation (Phase 1 slice)."""
from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional, Tuple

from ...data_type import DataTypeClass, DataTypeComponent
from ...activity import (
    ActivitySequenceBlock, ActivityAnonTraversal, ActivityTraversal,
    ActivityRepeat, ActivityForeach, ActivityIfElse, ActivityMatch, MatchCase,
    ActivityAtomic, ActivityParallel, ActivitySchedule, ActivitySelect,
    ActivityDoWhile, ActivityReplicate, ActivitySchedulingConstraint,
)
from ...fields import FieldKind
from ...scenario import (
    ScCoroutine, ScExecBlock, ScComponentInst, ScenarioModule, ScField,
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


def _is_pending_constraint(f) -> bool:
    """Does function *f* hold constraints that are in force on its type?

    A non-lifecycle function on an action is assumed to be a named constraint
    block. The exception is a generic constraint (PSS 3.1 §13.1.2), which is a
    template: it is inert until referenced, so collecting it here would put a
    body -- and its unbound parameters -- into the solve problem.
    """
    if getattr(f, "name", None) in _LIFECYCLE_FUNCS:
        return False
    return not (getattr(f, "metadata", None) or {}).get("_is_generic_constraint")


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


def _field_layout(dt: Any) -> List[ScField]:
    """The action's attributes in object-slot order (the full field list)."""
    return [ScField(name=f.name, slot=i, datatype=getattr(f, "datatype", None),
                    rand=getattr(f, "rand_kind", None) is not None)
            for i, f in enumerate(getattr(dt, "fields", []) or [])]


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


def _walk_activity(stmts):
    """Every activity statement under *stmts*, depth first."""
    for s in stmts or []:
        yield s
        for attr in ("stmts", "body", "if_body", "else_body"):
            sub = getattr(s, attr, None)
            if isinstance(sub, list):
                yield from _walk_activity(sub)
        for br in getattr(s, "branches", None) or []:
            yield from _walk_activity(getattr(br, "body", None))
        for case in getattr(s, "cases", None) or []:
            yield from _walk_activity(getattr(case, "body", None))


def _is_action(dt: Any) -> bool:
    """An action is a polymorphic class that is neither a component nor a
    plain struct.

    A bodiless action is an action too: it lowers to a coroutine that does
    nothing but solve. Leaving it out left ``do Z`` naming no coroutine,
    which bc then ran as coroutine 0 -- a different action.
    """
    return (isinstance(dt, DataTypeClass) and not isinstance(dt, DataTypeComponent)
            and getattr(dt, "flow_kind", None) is None
            and not getattr(dt, "is_abstract", False))


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
        # `export T(...);` in the model (LRM 20.10), qualified by the front
        # end. It decides the root and the exports when the caller does not.
        declared = list(getattr(ctx, "export_actions", None) or [])
        root = (self.root
                or (declared[0].rsplit("::", 1)[0] if declared else None)
                or self._resolve_root(type_map, actions))
        if root is None:
            raise ValueError("no root component with actions found; "
                             "pass root=... explicitly")
        module.root = ScComponentInst(
            name=root.rsplit("::", 1)[-1], type_name=root)

        owned = [(q, dt) for (q, dt) in actions
                 if q.rsplit("::", 1)[0] == root]
        if not owned:
            raise ValueError("root component %r owns no actions" % root)

        # TraversalResolve: a traversal names its target by qualified action
        # type; coroutines are keyed by simple name (design O5: qualified
        # keys come with non-root components, P1).
        self._type_map = type_map
        self._coro_of: Dict[str, str] = {}
        for qname, dt in owned:
            simple = qname.rsplit("::", 1)[-1]
            if simple in self._coro_of.values():
                raise UnsupportedConstructError(
                    "two lowered actions are both named %r; coroutines are "
                    "keyed by simple name until P1" % simple, loc=dt.getLoc())
            self._coro_of[qname] = simple

        # --- callable functions: package scope, then every component's ---
        module.functions = self._collect_functions(ctx, type_map)

        # --- LifecycleNormalize ---
        for qname, dt in owned:
            self.validator.check_action(qname, dt)
            self._cur_action = dt
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
            coro.fields = _field_layout(dt)
            module.add_coroutine(coro)

        # Every traversal must name a coroutine of this module. A backend
        # given an unknown name has nothing correct to run (bc used to run
        # coroutine 0), so this is checked here, where the location is.
        for coro in module.coroutines.values():
            for inv in _walk_invokes(coro.body):
                if inv.target not in module.coroutines:
                    raise UnsupportedConstructError(
                        "traversal target %r does not name a lowered action"
                        % inv.target, loc=inv.getLoc())

        # --- export selection ---
        if self.exports is not None:
            module.export_actions = list(self.exports)
        elif declared:
            exports = []
            for qname in declared:
                if qname not in self._coro_of:
                    raise UnsupportedConstructError(
                        "exported action %r is not an action of the root "
                        "component %r; only the root's actions are lowered "
                        "until P1" % (qname, root))
                exports.append(self._coro_of[qname])
            module.export_actions = exports
        else:
            module.export_actions = self._auto_exports(module)

        return module

    @staticmethod
    def _collect_functions(ctx: Any, type_map: Dict[str, Any]) -> Dict[str, Any]:
        """Native functions exec code may call (see ``ScenarioModule.functions``).

        Constraint blocks and exec blocks are also ``Function``s on a component;
        they are not callable and are left out.
        """
        out: Dict[str, Any] = dict(getattr(ctx, "functions", {}) or {})
        for qname, dt in type_map.items():
            if not isinstance(dt, DataTypeComponent):
                continue
            for f in getattr(dt, "functions", []) or []:
                meta = getattr(f, "metadata", None) or {}
                if (f.name in _LIFECYCLE_FUNCS or meta.get("_is_constraint")
                        or meta.get("_is_generic_constraint")):
                    continue
                out.setdefault(f"{qname}::{f.name}", f)
        return out

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
        pre_block, post_block, body_ops, pending = self._exec_blocks(dt)
        return ScCoroutine(
            name=simple,
            body=self._lifecycle(pre_block, post_block, body_ops),
            action_type=qname,
            pending_constraints=pending,
        ).copy_loc(dt)

    def _exec_blocks(self, dt: DataTypeClass):
        """An action's exec blocks and in-force constraints:
        ``(pre_solve, post_solve, body ops, pending constraints)``."""
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
            elif _is_pending_constraint(f):
                # A named constraint block (e.g. addr_aligned).  Carried until
                # Phase 3 folds it into a ScSolveProblem.
                pending.append(f)
        return pre_block, post_block, body_ops, pending

    @staticmethod
    def _lifecycle(pre_block, post_block, main: List) -> List:
        """Lifecycle order: pre_solve → [solve] → post_solve → *main* (the
        exec body, or the activity). The ScSolveProblem is inserted between
        pre_solve and post_solve by ConstraintCollect in lower()."""
        seq: List = []
        if pre_block is not None:
            seq.append(pre_block)
        if post_block is not None:
            seq.append(post_block)
        seq.extend(main)
        return seq

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
        # A compound action has the same lifecycle as an atomic one, with its
        # activity in place of the exec body (LRM 13.4.12): its own pre_solve
        # runs before its children's, which solve when they are traversed.
        pre_block, post_block, _, pending = self._exec_blocks(dt)
        body = self._lifecycle(pre_block, post_block,
                               self._lower_activity(dt.activity_ir))
        return ScCoroutine(
            name=simple, body=body, action_type=qname,
            pending_constraints=pending,
        ).copy_loc(dt)

    def _traversal_target(self, s, type_qname: Optional[str],
                          written: Optional[str], what: Optional[str] = None) -> str:
        """The coroutine a traversal of *s* runs.

        *type_qname* is the action type as the front end's linker resolved
        it, and decides. Without one (IR built by hand or from Python),
        *written* must name an owned action exactly: its qualified name, or
        its coroutine's simple name. Anything else is refused -- never
        guessed.
        """
        what = what or "action %r" % written
        if type_qname is not None:
            coro = self._coro_of.get(type_qname)
            if coro is not None:
                return coro
            if _is_action(self._type_map.get(type_qname)):
                raise UnsupportedConstructError(
                    "traversal of %s runs %s, an action of component %r; only "
                    "the root component's actions are lowered until P1"
                    % (what, type_qname, type_qname.rsplit("::", 1)[0]),
                    loc=s.getLoc())
            raise UnsupportedConstructError(
                "traversal of %s: %r is not an action" % (what, type_qname),
                loc=s.getLoc())
        if written is not None:
            if written in self._coro_of:
                return self._coro_of[written]
            if written in self._coro_of.values():
                return written
        raise UnsupportedConstructError(
            "traversal of %s does not name an action of the root component"
            % what, loc=s.getLoc())

    @staticmethod
    def _check_no_replicate_branches(s, kind: str) -> None:
        """`replicate` directly in a parallel/schedule expands into that many
        BRANCHES (LRM 11.5.1); a loop would be one branch."""
        for st in s.stmts:
            if isinstance(st, ActivityReplicate):
                raise UnsupportedConstructError(
                    "replicate directly inside %s expands into branches; "
                    "that is P1" % kind, loc=st.getLoc())

    def _check_schedule_members(self, s) -> None:
        """Refuse a ``schedule`` whose members interact (design D4): a
        scheduling constraint, or a member that -- directly or through the
        activity of a compound action -- has a flow-object reference or a
        resource claim."""
        for st in _walk_activity(s.stmts):
            if isinstance(st, ActivitySchedulingConstraint):
                raise UnsupportedConstructError(
                    "schedule with a scheduling constraint needs the planner "
                    "(P3/P4, design D4)", loc=st.getLoc())
        seen = set()
        for st in _walk_activity(s.stmts):
            qname = getattr(st, "type_qname", None)
            if isinstance(st, (ActivityTraversal, ActivityAnonTraversal)) \
                    and qname is not None:
                why = self._interaction(qname, seen)
                if why is not None:
                    raise UnsupportedConstructError(
                        "schedule with interacting members (%s) needs the "
                        "planner (P3/P4, design D4)" % why, loc=st.getLoc())

    def _interaction(self, qname: str, seen: set) -> Optional[str]:
        """Why action *qname* interacts with others, or None."""
        if qname in seen:
            return None
        seen.add(qname)
        dt = self._type_map.get(qname)
        chain = dt
        while chain is not None:
            for f in getattr(chain, "fields", []) or []:
                if f.kind in (FieldKind.Input, FieldKind.Output):
                    return "%s has flow-object reference %r" % (qname, f.name)
                if f.kind in (FieldKind.Lock, FieldKind.Share):
                    return "%s claims resource %r" % (qname, f.name)
            sup = getattr(chain, "super", None)
            chain = self._type_map.get(getattr(sup, "ref_name", None))
        act = getattr(dt, "activity_ir", None)
        for st in _walk_activity(act.stmts if act is not None else []):
            sub = getattr(st, "type_qname", None)
            if sub is not None:
                why = self._interaction(sub, seen)
                if why is not None:
                    return why
        return None

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
            target = self._traversal_target(s, s.type_qname, s.action_type)
            return ScInvoke(target=target, inst=s.label).copy_loc(s)

        if isinstance(s, ActivityTraversal):
            if s.inline_constraints:
                raise UnsupportedConstructError(
                    "inline traversal constraints on %r are a later phase"
                    % s.handle, loc=s.getLoc())
            written = None
            if s.type_qname is None:
                # Hand-built IR: the handle's declared type, as written.
                for f in getattr(self._cur_action, "fields", []) or []:
                    if f.name == s.handle:
                        written = getattr(getattr(f, "datatype", None),
                                          "ref_name", None)
                        break
            target = self._traversal_target(s, s.type_qname, written,
                                            what="handle %r" % s.handle)
            return ScInvoke(target=target, inst=s.handle).copy_loc(s)

        if isinstance(s, ActivityDoWhile):
            # `repeat {...} while (c);`: the body runs before the first test.
            return ScLoop(kind="dowhile", cond=s.condition,
                          body=self._lower_stmts(s.body)).copy_loc(s)

        if isinstance(s, ActivityReplicate):
            # In a sequential scope, `replicate (N) S` is N copies of S in
            # sequence: a counted loop, while nothing names the per-iteration
            # instances. (In parallel/schedule it is refused below.)
            if s.label is not None:
                raise UnsupportedConstructError(
                    "replicate with an iteration label (%s[]) names each "
                    "iteration's instances; that is P1" % s.label,
                    loc=s.getLoc())
            return ScLoop(kind="repeat", count=s.count, index_var=s.index_var,
                          body=self._lower_stmts(s.body)).copy_loc(s)

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
            self._check_no_replicate_branches(s, "parallel")
            return ScPar(branches=self._lower_stmts(s.stmts),
                         join_spec=s.join_spec).copy_loc(s)

        if isinstance(s, ActivitySchedule):
            # With members that do not interact, running them all in
            # parallel is one legal schedule (LRM 11.3.5). Members that
            # interact need the planner (P3/P4), so they are refused (D4).
            self._check_no_replicate_branches(s, "schedule")
            self._check_schedule_members(s)
            return ScPar(branches=self._lower_stmts(s.stmts),
                         join_spec=s.join_spec).copy_loc(s)

        if isinstance(s, ActivitySelect):
            branches = [ScSelectBranch(guard=b.guard, weight=b.weight,
                                       body=self._lower_stmts(b.body))
                        for b in s.branches]
            return ScSelect(branches=branches,
                            allow_none=getattr(s, "allow_none", False)).copy_loc(s)

        raise UnsupportedConstructError(
            "unsupported activity construct %s" % type(s).__name__,
            loc=getattr(s, "loc", None))
