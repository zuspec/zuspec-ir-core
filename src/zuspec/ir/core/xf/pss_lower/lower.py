"""PSS → Scenario lowering pass implementation (Phase 1 slice)."""
from __future__ import annotations

import dataclasses as dc
import logging
from typing import Any, Dict, List, Optional, Tuple

from ...data_type import (DataTypeClass, DataTypeComponent, DataTypeFloat, DataTypeString,
                          DataTypeList, DataTypeMap, DataTypeSet, DataTypeEntityRef)
from ...expr_phase2 import ExprList
from ...activity import (
    ActivitySequenceBlock, ActivityAnonTraversal, ActivityTraversal,
    ActivityRepeat, ActivityForeach, ActivityIfElse, ActivityMatch, MatchCase,
    ActivityAtomic, ActivityParallel, ActivitySchedule, ActivitySelect,
    ActivityDoWhile, ActivityReplicate, ActivitySchedulingConstraint, ActivityFieldDecl,
    ActivityConstraint, ActivityBind, JoinKind,
)
from ...fields import FieldKind
from ...scenario import (
    ScCoroutine, ScExecBlock, ScComponentInst, ScenarioModule, ScField,
    ScSeq, ScInvoke, ScLoop, ScIf, ScMatch, ScMatchCase, ScAtomic,
    ScPar, ScSelect, ScSelectBranch, ScImport, ScImportDecl,
)
from ...stmt import Stmt, StmtExpr, StmtAssign, StmtSuper
from ...expr import (Expr, ExprCall, ExprAttribute, ExprConstant, TypeExprRefSelf, TypeExprRefTraversed,
                     ExprRefUnresolved, ExprNull)
from ..validate import ScenarioValidator, UnsupportedConstructError
from .constraints import (LIFECYCLE_FUNCS, collect_solve_problem,
                          is_pending_constraint, type_constraints)
from .layout import (action_fields, object_layout, struct_chain, prefix_self, subst_names,
                     chain_renames, rename_members, is_struct)
from .action_tree import Layouts, build_tree, plan_of_schedule, scheduling_constraints
from .comp_tree import CompLayouts, build_comp_tree
from .flow_checks import FlowChecks
from .icl import IclTable
from .pools import PoolTable

_log = logging.getLogger("zuspec.ir.xf.pss_lower")

_LIFECYCLE_FUNCS = LIFECYCLE_FUNCS
_is_pending_constraint = is_pending_constraint

#: The exec kinds (a function's ``metadata["exec_kind"]``) lowered, per kind
#: of body: an action's by ``_exec_blocks``, a component's by
#: ``build_comp_tree``. A struct's pre_solve/post_solve is refused where the
#: attribute is solved (constraints.py ``_struct_sites``).
LOWERED_EXEC_KINDS = {
    "action": frozenset({"body", "pre_body", "pre_solve", "post_solve"}),
    "component": frozenset({"init_down", "init_up", "run_start", "run_end"}),
}

#: Exec kinds the front end produces that nothing here runs yet: refused,
#: with the block's location, rather than dropped. Any other kind outside
#: its body's row of LOWERED_EXEC_KINDS is refused too.
REFUSED_EXEC_KINDS: Dict[str, str] = {}


def _holds_super(node) -> bool:
    """Does statement *node* hold a ``super;`` below its top level?"""
    for f in dc.fields(node) if dc.is_dataclass(node) else ():
        v = getattr(node, f.name)
        for x in (v if isinstance(v, (list, tuple)) else [v]):
            if isinstance(x, StmtSuper):
                return True
            if isinstance(x, Stmt) or (dc.is_dataclass(x) and
                                       type(x).__name__.startswith("Stmt")):
                if _holds_super(x):
                    return True
    return False


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


def _field_layout(dt: Any, types: Dict[str, Any]) -> List[ScField]:
    """The action's object slots, in order: one per scalar, a struct
    attribute flattened to its leaves (``layout.object_layout``)."""
    return [ScField(name=leaf.name, slot=i, datatype=leaf.datatype, rand=leaf.rand,
                    loc=getattr(leaf.field, "loc", None))
            for i, leaf in enumerate(
                object_layout(action_fields(dt, types), types))]


def _self_path(path) -> Any:
    e = TypeExprRefSelf()
    for name in path:
        e = ExprAttribute(value=e, attr=name)
    return e


def _init_block(dt: Any, types: Dict[str, Any]) -> Optional[ScExecBlock]:
    """The attributes' declared initial values, as the object's first code.

    ``bit[4] g = 3;`` on an action, or on a field of a struct attribute
    (LRM 8.5.3), used to be read as 0 on bc: nothing applied it. A rand
    attribute's initial value is applied too; its solve overwrites it.
    """
    stmts = []
    for leaf in object_layout(action_fields(dt, types), types):
        init = getattr(leaf.field, "initial_value", None)
        if init is not None:
            # A struct field's initializer is written against the struct.
            stmts.append(StmtAssign(targets=[_self_path(leaf.path)],
                                    value=prefix_self(init, leaf.path[:-1])))
        elif isinstance(leaf.datatype, (DataTypeList, DataTypeMap, DataTypeSet)):
            # A list, map or set attribute starts empty at each traversal,
            # also of an object a loop traverses again.
            stmts.append(StmtAssign(targets=[_self_path(leaf.path)], value=ExprList()))
        elif isinstance(leaf.datatype, DataTypeEntityRef):
            # A `ref C` attribute starts null (LRM 7.10): the object starts
            # zeroed, and 0 is an instance, the root.
            stmts.append(StmtAssign(targets=[_self_path(leaf.path)], value=ExprNull()))
    return ScExecBlock(kind="init", stmts=stmts) if stmts else None


def _root_traversed(e: Any, path) -> Any:
    """*e* with the traversed action (``TypeExprRefTraversed``) as the child
    node at *path* of the invoking action."""
    if isinstance(e, TypeExprRefTraversed):
        return _self_path(path)
    if dc.is_dataclass(e) and not isinstance(e, type):
        repl = {}
        for f in dc.fields(e):
            val = getattr(e, f.name)
            if isinstance(val, Expr):
                repl[f.name] = _root_traversed(val, path)
            elif isinstance(val, list) and any(isinstance(x, Expr) for x in val):
                repl[f.name] = [_root_traversed(x, path) if isinstance(x, Expr) else x
                                for x in val]
        return dc.replace(e, **repl) if repl else e
    return e


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


def coro_key(module: ScenarioModule, name: str) -> Optional[str]:
    """The coroutine of *module* that *name* names: its key (``T``,
    ``sub_c::S``), its action's qualified name (``pss_top::T``), or a simple
    name only one lowered action has. None if it names none; an ambiguous
    simple name is an error."""
    if name in module.coroutines:
        return name
    by_type = [k for k, c in module.coroutines.items() if c.action_type == name]
    if by_type:
        return by_type[0]
    simple = [k for k in module.coroutines if k.rsplit("::", 1)[-1] == name]
    if len(simple) > 1:
        raise UnsupportedConstructError(
            "action name %r is ambiguous: %s; qualify it"
            % (name, ", ".join(sorted(simple))))
    return simple[0] if simple else None


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
        exports: Optional explicit list of actions to export, each a name
                 :func:`coro_key` accepts.  When ``None``, the model's
                 ``export`` declarations, else every root action nothing
                 traverses.
        lookahead: Test only. False switches value lookahead off
                 (:attr:`~...scenario.ScActionTree.lookahead`), to show that a
                 lookahead test fails without it (P1.6).
    """

    def __init__(self, root: Optional[str] = None,
                 exports: Optional[List[str]] = None,
                 solve_constraints: bool = True,
                 lookahead: bool = True):
        self.root = root
        self.lookahead = lookahead
        self.exports = exports
        # When True (Phase 3+), ConstraintCollect runs and the lifecycle gains a
        # leading ScSolveProblem; when False the Phase-1 behavior is preserved
        # (constraints parked on pending_constraints, no ScSolveProblem).
        self.solve_constraints = solve_constraints
        self.validator = ScenarioValidator()
        #: labeled-replicate iterations being lowered (action tree site keys)
        self._iters: Tuple[int, ...] = ()

    # ------------------------------------------------------------------
    def lower(self, ctx: Any) -> ScenarioModule:
        type_map = _get_type_map(ctx)
        module = ScenarioModule(types=type_map)

        # --- imports: assign stable ids; build the lookup + module decls ---
        self._imports = {}
        for fn_id, f in enumerate(getattr(ctx, "import_functions", []) or []):
            blocking = bool(getattr(f, "is_target", False)) and f.returns is None
            arg_types, string_at, struct_at = [], [], {}
            args = getattr(f, "args", None)
            for i, a in enumerate(getattr(args, "args", []) if args is not None else []):
                ann = getattr(a, "annotation", None)
                if isinstance(ann, DataTypeString):
                    string_at.append(i)
                elif is_struct(ann, type_map):
                    struct_at[i] = ann
                # A float crosses as its binary64 bits (F6.6f2), float32 too.
                arg_types.append((64, True) if isinstance(ann, DataTypeFloat)
                                 else (getattr(ann, "bits", 32) or 32,
                                       bool(getattr(ann, "signed", False))))
            ret_type = None
            if f.returns is not None:
                if isinstance(f.returns, DataTypeString):
                    string_at.append("return")
                elif is_struct(f.returns, type_map):
                    struct_at["return"] = f.returns
                if isinstance(f.returns, DataTypeFloat):
                    # binary64 bits; the third item says a float comes back
                    ret_type = (64, True, "float")
                elif isinstance(f.returns, DataTypeString):
                    # the host answers with text, made a heap string (F6.3b2)
                    ret_type = (64, False, "string")
                else:
                    ret_type = (getattr(f.returns, "bits", 32) or 32,
                                bool(getattr(f.returns, "signed", False)))
            self._imports[f.name] = {
                "fn_id": fn_id, "blocking": blocking,
                "arg_types": arg_types, "ret_type": ret_type}
            module.imports.append(ScImportDecl(
                name=f.name, fn_id=fn_id, blocking=blocking,
                arg_types=arg_types, ret_type=ret_type, string_at=string_at,
                struct_at=struct_at))

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

        # The component tree (P1.5): every instance under the root, one
        # object, constructed before the root action runs.
        self._type_map = type_map
        self._comps = CompLayouts(type_map)
        if isinstance(type_map.get(root), DataTypeComponent):
            module.comp_tree = build_comp_tree(self._comps, root)
            placed = {i.type_qname for i in module.comp_tree.instances}
        else:
            placed = {root}

        # The actions of the root and of every component type the tree
        # instantiates (or one of its bases): each can run.
        def runs(owner: str) -> bool:
            if owner == root:
                return True
            if not isinstance(type_map.get(owner), DataTypeComponent):
                return False
            # A specialization runs its own copies of its template's actions
            # (`w_c_base_c::A`), never the template's (`w_c::A`), whose base
            # may be its own type parameter.
            return any(self._comps.is_a(t, owner, templates=False) for t in placed)
        owned = [(q, dt) for (q, dt) in actions
                 if q.rsplit("::", 1)[0] == root]
        # A component with no actions can still be entered at its functions
        # (a fixture of test functions, pssc P1.3).
        if not owned and module.comp_tree is None:
            raise ValueError("root component %r owns no actions" % root)
        owned += [(q, dt) for (q, dt) in actions
                  if q.rsplit("::", 1)[0] != root and runs(q.rsplit("::", 1)[0])]
        self._check_exec_kinds(placed, owned)

        # TraversalResolve: a traversal names its target by qualified action
        # type. A coroutine is keyed by its action's name relative to the
        # root component: the root's actions by simple name (``T``), any
        # other component's qualified (``sub_c::S``) -- unique, and a model
        # of the root's actions alone keeps its names (O5).
        if module.comp_tree is not None:
            self._prune_selects(root)
        self._layouts = Layouts(type_map)
        self._coro_of: Dict[str, str] = {}
        for qname, dt in owned:
            owner, _, simple = qname.rpartition("::")
            self._coro_of[qname] = simple if owner == root else qname
        self._root = root

        # --- callable functions: package scope, then every component's ---
        module.functions = self._collect_functions(ctx, type_map)

        # --- LifecycleNormalize ---
        for qname, dt in owned:
            self.validator.check_action(qname, dt)
            self._cur_action = dt
            self._cur_qname = qname
            self._cur_layout = self._layouts.try_get(qname)
            self._iters = ()
            if dt.activity_ir is not None:
                coro = self._lower_compound(qname, dt)   # ScheduleNormalize
            else:
                coro = self._lower_atomic(qname, dt)
            if self.solve_constraints:
                # ConstraintCollect: rand fields + named constraints → a leading
                # ScSolveProblem; clears pending_constraints so nothing is left
                # dangling (the lifecycle becomes solve → body/activity).
                # A constraint through a sub-action handle belongs to the
                # action tree's cone (P1.2), which solves it with lookahead.
                handles = (set(self._cur_layout.fields)
                           if self._cur_layout is not None else None)
                problem = collect_solve_problem(coro, dt, type_map, handles)
                if problem is not None:
                    idx = 0
                    if (coro.body and isinstance(coro.body[0], ScExecBlock)
                            and coro.body[0].kind == "pre_solve"):
                        idx = 1
                    coro.body.insert(idx, problem)
                    coro.pending_constraints = []
            coro.fields = _field_layout(dt, type_map)
            if self._cur_layout is not None:
                coro.subtree = [
                    ScField(name=name, slot=slot, datatype=leaf.datatype, rand=leaf.rand,
                            loc=getattr(leaf.field, "loc", None))
                    for name, slot, leaf in self._layouts.subtree(qname)]
            init = _init_block(dt, type_map)
            if init is not None:
                coro.body.insert(0, init)
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
            module.export_actions = [coro_key(module, x) or x for x in self.exports]
        elif declared:
            exports = []
            for qname in declared:
                if qname not in self._coro_of:
                    raise UnsupportedConstructError(
                        "exported action %r is not an action of a component "
                        "of the tree under %r" % (qname, root))
                exports.append(self._coro_of[qname])
            module.export_actions = exports
            params = getattr(ctx, "export_params", None) or {}
            module.export_params = {self._coro_of[q]: list(n)
                                    for q, n in params.items() if q in self._coro_of}
        else:
            module.export_actions = [n for n in self._auto_exports(module)
                                     if "::" not in n]

        # The action tree of each export (P1.2): its nodes, and the cones of
        # constraints that tie them.
        for name in module.export_actions:
            coro = module.coroutines.get(name)
            if coro is not None and coro.action_type is not None:
                module.trees[name] = build_tree(
                    self._layouts, name, coro.action_type,
                    comps=self._comps if module.comp_tree is not None else None,
                    root_comp=root)
                module.trees[name].lookahead = self.lookahead
                # an export the user named that cannot run is refused here
                probs = module.trees[name].flow_problems
                if probs and (self.exports is not None or declared):
                    raise probs[0].error()

        return module

    def _check_exec_kinds(self, placed, owned) -> None:
        """Refuse, at its location, every exec block of a component in the
        tree (or a base) or of an action that can run whose kind is not in
        its body's row of LOWERED_EXEC_KINDS: nothing would run it."""
        typed = []
        for q in sorted(placed):
            if isinstance(self._type_map.get(q), DataTypeComponent):
                typed += [("component", q, t) for t in self._comps.chain(q)]
        typed += [("action", q, dt) for q, dt in owned]
        seen = set()
        for body, qname, dt in typed:
            if id(dt) in seen:
                continue
            seen.add(id(dt))
            for fn in getattr(dt, "functions", []) or []:
                kind = (getattr(fn, "metadata", None) or {}).get("exec_kind")
                if kind is None or kind in LOWERED_EXEC_KINDS[body]:
                    continue
                why = REFUSED_EXEC_KINDS.get(
                    kind, "is not supported in a %s" % body)
                raise UnsupportedConstructError(
                    "`exec %s` of %s %r %s" % (
                        kind, body, getattr(dt, "name", qname), why),
                    loc=getattr(fn, "loc", None))

    @staticmethod
    def _collect_functions(ctx: Any, type_map: Dict[str, Any]) -> Dict[str, Any]:
        """Native functions exec code may call (see ``ScenarioModule.functions``).

        Constraint blocks and exec blocks are also ``Function``s on a component;
        they are not callable and are left out.
        """
        out: Dict[str, Any] = dict(getattr(ctx, "functions", {}) or {})
        for qname, dt in type_map.items():
            if not isinstance(dt, DataTypeComponent):
                # An action's own functions (a pssc extension), inherited
                # ones included: ``<action>::f``, called with the action's
                # object as ``self``. One declared with no body is left out.
                if "::" in qname and _is_action(dt):
                    for f in getattr(dt, "functions", []) or []:
                        meta = getattr(f, "metadata", None) or {}
                        if meta.get("action_function") and not meta.get("abstract"):
                            out.setdefault(f"{qname}::{f.name}", f)
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
        pre_block, post_block, body_ops, pending = self._exec_blocks(dt)
        return ScCoroutine(
            name=self._coro_of[qname],
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
        # Named constraint blocks (e.g. addr_aligned), the bases' too.
        # Carried until Phase 3 folds them into a ScSolveProblem.
        pending = type_constraints(dt, self._type_map)
        body = self._exec_stmts(dt, "body")
        pre_body = self._exec_stmts(dt, "pre_body")
        if pre_body is not None:
            # exec pre_body runs after allocation, just before exec body
            # (LRM 22.1.1); its locals share body's scope
            body = pre_body + (body or [])
        if body is not None:
            # Split the exec body at blocking-import calls (each becomes a
            # ScImport suspend point between straight-line ScExecBlocks).
            body_ops = self._lower_exec_stmts(body, "body")
        pre = self._exec_stmts(dt, "pre_solve")
        if pre is not None:
            pre_block = ScExecBlock(kind="pre_solve", stmts=pre)
        post = self._exec_stmts(dt, "post_solve")
        if post is not None:
            post_block = ScExecBlock(kind="post_solve", stmts=post)
        return pre_block, post_block, body_ops, pending

    def _exec_stmts(self, dt, kind: str, _ren=None) -> Optional[List]:
        """The statements exec block *kind* of action *dt* runs (LRM 17.1):
        its own block, else the nearest base's; each top-level ``super;`` in
        it replaced by the base's block, expanded the same way. None if no
        type in the chain declares one. A base's statements read a field
        a derived type shadows, and ``super.f``, by the slot's name in *dt*
        (``layout.chain_renames``)."""
        if _ren is None:
            try:
                chain = struct_chain(dt, self._type_map)
            except UnsupportedConstructError:
                chain = [dt]
            _ren = {id(t): r for t, r in zip(chain, chain_renames(chain))}
        seen: List[Any] = []
        while dt is not None:
            if any(t is dt for t in seen):
                raise UnsupportedConstructError(
                    "action %r inherits from itself" % getattr(dt, "name", "?"),
                    loc=getattr(dt, "loc", None))
            seen.append(dt)
            sup = getattr(dt, "super", None)
            base = (self._type_map.get(getattr(sup, "ref_name", None))
                    if sup is not None else None)
            fn = next((f for f in getattr(dt, "functions", []) or []
                       if getattr(f, "name", None) == kind), None)
            if fn is None:
                dt = base
                continue
            out: List = []
            for st in fn.body:
                if isinstance(st, StmtSuper):
                    if base is not None:
                        out.extend(self._exec_stmts(base, kind, _ren) or [])
                else:
                    if _holds_super(st):
                        raise UnsupportedConstructError(
                            "`super;` inside a statement of exec %s: only a "
                            "top-level `super;` is supported" % kind,
                            loc=getattr(st, "loc", None))
                    out.append(rename_members(st, *_ren.get(id(dt), ({}, {}))))
            return out
        return None

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
            imp = self._as_blocking_import(s, kind)
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

    def _as_blocking_import(self, stmt, kind: str):
        """Return a ScImport if *stmt* is a statement-level call to a blocking
        (target) import of the exec *kind*'s body, else None."""
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
                        args=list(e.args), kind=kind).copy_loc(stmt)

    # ------------------------------------------------------------------
    # ScheduleNormalize — compound activities → structured scenario ops
    # ------------------------------------------------------------------
    def _lower_compound(self, qname: str, dt: DataTypeClass) -> ScCoroutine:
        # A compound action has the same lifecycle as an atomic one, with its
        # activity in place of the exec body (LRM 13.4.12): its own pre_solve
        # runs before its children's, which solve when they are traversed.
        pre_block, post_block, _, pending = self._exec_blocks(dt)
        body = self._lifecycle(pre_block, post_block,
                               self._lower_activity(dt.activity_ir))
        return ScCoroutine(
            name=self._coro_of[qname], body=body, action_type=qname,
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
                    "traversal of %s runs %s, an action of component %r, which "
                    "is not instantiated under %r (9.1.5.1)"
                    % (what, type_qname, type_qname.rsplit("::", 1)[0], self._root),
                    loc=s.getLoc())
            if getattr(self._type_map.get(type_qname), "is_abstract", False):
                raise UnsupportedConstructError(
                    "traversal of %s: %r is an abstract action, which may not be "
                    "traversed directly (10.2.3)" % (what, type_qname),
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
            "traversal of %s does not name a lowered action" % what,
            loc=s.getLoc())

    @staticmethod
    def _check_no_replicate_branches(s, kind: str) -> None:
        """`replicate` directly in a parallel/schedule expands into that many
        BRANCHES (LRM 11.5.1); a loop would be one branch."""
        for st in s.stmts:
            if isinstance(st, ActivityReplicate):
                raise UnsupportedConstructError(
                    "replicate directly inside %s expands into branches; "
                    "that is P1" % kind, loc=st.getLoc())

    def _join_branches(self, s) -> List[int]:
        """For ``join_branch(L1, ...)``: the indices, among the branches
        :meth:`_lower_stmts` keeps, of the top-level branches those labels
        name (LRM 13.3.6 a.1). A label names a labelled statement, or a
        traversed handle."""
        js = s.join_spec
        if js is None or js.kind != JoinKind.BRANCH:
            return []
        labels = list(js.branch_labels) or ([js.branch_label] if js.branch_label else [])
        names = {}
        i = 0
        for st in s.stmts:
            if isinstance(st, (ActivityFieldDecl, ActivitySchedulingConstraint)) or (
                    isinstance(st, (ActivityConstraint, ActivityBind))
                    and self._cur_layout is not None):
                continue
            for n in (getattr(st, "label", None), getattr(st, "handle", None)):
                if n:
                    names.setdefault(n, i)
            i += 1
        out = []
        for lb in labels:
            if lb not in names:
                raise UnsupportedConstructError(
                    "join_branch label %r is not a top-level branch of this "
                    "block (LRM 13.3.6 a.1)" % lb, loc=s.getLoc())
            if names[lb] not in out:
                out.append(names[lb])
        return out

    def _schedule_plan(self, s):
        """The members of ``schedule`` *s* and their plan (M8b2, xf/plan):
        members that exchange buffers or state run in a legal order the seed
        picks, and members that lock the same resource type take turns.
        Scheduling constraints over its members (LRM 16.2) add order
        (``sequence``) or need them to start together (``parallel``), as
        stream partners do (M8b4). Refused: a join policy over members that
        interact."""
        from ..plan import CycleError, ParallelError
        act = getattr(self._cur_action, "activity_ir", None)
        cons = scheduling_constraints([act] if act is not None else [])
        try:
            members, plan, used = plan_of_schedule(s, cons, self._type_map)
        except CycleError as e:
            raise UnsupportedConstructError(
                "schedule members %s exchange buffers in a cycle, or a sequence "
                "constraint orders them against their flow: no legal order "
                "(LRM 13.3.5, 16.2 e)" % ", ".join(str(i + 1) for i in e.cycle),
                loc=s.getLoc())
        except ParallelError as e:
            raise UnsupportedConstructError(
                ("schedule members %s cannot run in parallel: they exchange flow "
                 "objects, lock a resource or are sequenced (LRM 16.2 e)" if e.tied else
                 "schedule members %s must start together, but one of them may "
                 "wait for another member it exchanges flow objects or a "
                 "resource with; not supported yet")
                % ", ".join(str(i + 1) for i in e.members), loc=s.getLoc())
        self._sched_used = getattr(self, "_sched_used", set()) | used
        kind = s.join_spec.kind if s.join_spec is not None else None
        if plan.interacts and kind not in (None, JoinKind.ALL):
            raise UnsupportedConstructError(
                "schedule with a %s join over members that exchange flow "
                "objects or lock a resource is not supported yet" % kind.name, loc=s.getLoc())
        return members, plan

    def _lower_planned(self, s, members, plan) -> ScPar:
        """``schedule`` *s* as planned: each group of members that interact
        is one branch, a sequence in one of its legal orders, chosen by the
        seed (a ``select`` when there are several); the rest run alone."""
        lowered = [self._lower_stmts([m]) for m in members]
        branches = []
        for group, orders in zip(plan.groups, plan.orders):
            if len(group) == 1:
                branches.append(ScSeq(body=lowered[group[0]]))
                continue
            seqs = [[x for i in order for x in lowered[i]] for order in orders]
            if len(seqs) == 1:
                branches.append(ScSeq(body=seqs[0]))
            else:
                branches.append(ScSelect(branches=[ScSelectBranch(body=q)
                                                   for q in seqs]))
        return ScPar(branches=branches, join_spec=s.join_spec,
                     scope=self._scope(s)).copy_loc(s)

    def _no_tree(self, what: str, loc=None):
        """Refuse *what*, which needs the current action's tree: with the
        reason it has none, when building it failed."""
        err = self._layouts.errors.get(getattr(self, "_cur_qname", None))
        if err is not None:
            raise err
        raise UnsupportedConstructError(
            "%s cannot be lowered without the action tree, and this action "
            "has none" % what, loc=loc)

    def _child_base(self, s) -> Optional[int]:
        lay = getattr(self, "_cur_layout", None)
        return lay.child_base(s, self._iters) if lay is not None else None

    def _site(self, s) -> Optional[int]:
        lay = getattr(self, "_cur_layout", None)
        return lay.site_index(s, self._iters) if lay is not None else None

    def _unroll_replicate(self, s) -> ScSeq:
        """``replicate (N) R[]: S``: each iteration runs its own nodes of the
        action tree (``R[i].…``), at their own bases, so the iterations are
        unrolled -- N is a constant (O4) -- each a block with its index
        variable fixed."""
        self._layouts.get(self._cur_qname, loc=s.getLoc())  # O4 refusal
        if self._cur_layout is None:
            self._no_tree(
                "replicate with an iteration label (%s[])" % s.label, loc=s.getLoc())
        saved = self._iters
        iterations = []
        try:
            for i in range(s.count.value):
                self._iters = saved + (i,)
                body = self._lower_stmts(s.body)
                if s.index_var:
                    body = subst_names(body, {s.index_var: i})
                iterations.append(ScSeq(body=body, scope=self._scope(s)).copy_loc(s))
        finally:
            self._iters = saved
        return ScSeq(body=iterations).copy_loc(s)

    def _traversal_init(self, s, target: str) -> List:
        """``ScInvoke.init`` of traversal *s*: the child's initial values,
        then its initializers in order (11.3.1 b i-ii), rooted at the
        child's node. Empty when *s* has no initializers."""
        inits = getattr(s, "initializers", None) or []
        if not inits:
            return []
        key = self._cur_layout._site(s, self._iters).key
        path = tuple(key.split("."))
        child = next((q for q, n in self._coro_of.items() if n == target), None)
        out = []
        blk = _init_block(self._type_map.get(child), self._type_map) if child else None
        for st in (blk.stmts if blk is not None else []):
            out.append(prefix_self(st, path))
        for lhs, rhs in inits:
            out.append(StmtAssign(targets=[_root_traversed(lhs, path)],
                                  value=_root_traversed(rhs, path)))
        return out

    def _prune_selects(self, root: str) -> None:
        """Mark the select branches that can never run (M8b5a): the action
        tree gives them no nodes and they are never chosen. A select left
        with none, and no ``select[0..1]``-style ``allow_none``, is refused."""
        checks = FlowChecks(IclTable(PoolTable(self._comps, root)))
        selects = {}
        for s, br, why in checks.dead_branches().values():
            br.infeasible = why
            selects[id(s)] = s
        for s in selects.values():
            if not getattr(s, "allow_none", False) \
                    and all(b.infeasible is not None for b in s.branches):
                raise UnsupportedConstructError(
                    "no branch of this select can ever run: %s" % "; ".join(
                        "branch %d: %s" % (i + 1, b.infeasible)
                        for i, b in enumerate(s.branches)),
                    loc=getattr(s, "loc", None))

    def _scope(self, block, part=None) -> Optional[int]:
        """The action tree's local scope *block* opens (``ScSeq.scope``...)."""
        lay = getattr(self, "_cur_layout", None)
        if lay is None:
            return None
        return lay.scope(block, tuple(part or ()) + tuple(self._iters))

    def _lower_activity(self, act) -> List:
        """Lower the top-level activity into a coroutine body (a list of ops)."""
        if act is None:
            return []
        # The activity's top-level block is the ACTIVITY scope (local 0); the
        # node's entry, not a statement, opens it.
        self._sched_used = set()
        out = self._lower_stmts(act.stmts if isinstance(act, ActivitySequenceBlock)
                                else [act])
        for c in scheduling_constraints([act]):
            if id(c) not in self._sched_used:
                raise UnsupportedConstructError(
                    "scheduling constraint relates statements that are not members "
                    "of one schedule; v0 supports only that (LRM 16.2)",
                    loc=c.getLoc())
        return out

    def _lower_stmts(self, stmts) -> List:
        """A block's statements as ops. A declaration is not a statement: a
        handle needs nothing until P1.2 gives it a node (a traversal names
        its type itself), and a data field is refused."""
        out = []
        for s in stmts:
            if isinstance(s, ActivityFieldDecl):
                self._check_field_decl(s)
                continue
            if isinstance(s, ActivitySchedulingConstraint):
                continue  # shapes its schedule (_schedule_plan)
            if isinstance(s, (ActivityConstraint, ActivityBind)) \
                    and self._cur_layout is not None:
                # In force while its scope is (13.1.9 b.3): the action tree
                # holds it, tagged with that scope, and the cone solves it.
                # A `bind` is an equality there (11.9, D-B13).
                continue
            out.append(self._lower_activity_stmt(s))
        return out

    @staticmethod
    def _check_field_decl(s) -> None:
        if s.type_qname is None:
            raise UnsupportedConstructError(
                "data field %r declared in an activity block is not "
                "supported yet: traversing it randomizes a value with no action "
                "(11.3.1), which the action tree has no node for" % s.field.name,
                loc=s.getLoc())

    def _refuse_unlowered_traversal_parts(self, s):
        """Refuse what a traversal carries and this pass does not lower yet.

        Each of these used to be ignored, and the traversal ran as though
        it had not been written: ``h[i]`` ran the element TYPE with no
        element, ``comp == X`` ran the action in whatever instance, and
        Python-front-end flow bindings were not bound.
        """
        if getattr(s, "index", None) is not None and self._child_base(s) is None:
            err = self._layouts.errors.get(self._cur_qname)
            if err is not None:
                raise err               # why the action has no tree
            # A constant index names a node of the action tree (P1.2); a
            # computed one would choose among them at run time.
            raise UnsupportedConstructError(
                "traversal of an element of the handle array %r with a "
                "computed index is not supported yet" % s.handle, loc=s.getLoc())
        if getattr(s, "comp_expr", None) is not None and self._site(s) is None:
            # The action tree chooses the instance (P1-D4).
            self._no_tree(
                "a traversal constrained with `comp == ...`", loc=s.getLoc())
        if getattr(s, "initializers", None) and self._site(s) is None:
            self._no_tree("traversal initializers ({.x = ...})", loc=s.getLoc())
        if getattr(s, "init_bindings", None):
            raise UnsupportedConstructError(
                "flow bindings on a traversal (%s) are not lowered by this "
                "pass" % ", ".join(b[0] for b in s.init_bindings),
                loc=s.getLoc())

    def _lower_activity_stmt(self, s):
        if isinstance(s, ActivitySequenceBlock):
            return ScSeq(body=self._lower_stmts(s.stmts),
                         scope=self._scope(s)).copy_loc(s)

        if isinstance(s, (ActivityAnonTraversal, ActivityTraversal)):
            self._refuse_unlowered_traversal_parts(s)

        if isinstance(s, ActivityAnonTraversal):
            if s.inline_constraints and self._site(s) is None:
                self._no_tree("inline traversal constraints (`do %s with {...}`)"
                              % s.action_type, loc=s.getLoc())
            target = self._traversal_target(s, s.type_qname, s.action_type)
            return ScInvoke(target=target, inst=s.label,
                            inline_constraints=list(s.inline_constraints or []),
                            child_base=self._child_base(s),
                            site=self._site(s),
                            init=self._traversal_init(s, target)).copy_loc(s)

        if isinstance(s, ActivityTraversal):
            if s.inline_constraints and self._site(s) is None:
                self._no_tree("inline traversal constraints on %r" % s.handle,
                              loc=s.getLoc())
            written = None
            if s.type_qname is None:
                # Hand-built IR: the handle's declared type, as written.
                for f in action_fields(self._cur_action, self._type_map):
                    if f.name == s.handle:
                        dt = getattr(f, "datatype", None)
                        dt = getattr(dt, "element_type", None) or dt
                        written = getattr(dt, "ref_name", None)
                        break
            target = self._traversal_target(s, s.type_qname, written,
                                            what="handle %r" % s.handle)
            return ScInvoke(target=target, inst=s.handle,
                            inline_constraints=list(s.inline_constraints or []),
                            child_base=self._child_base(s),
                            site=self._site(s),
                            init=self._traversal_init(s, target)).copy_loc(s)

        if isinstance(s, ActivityDoWhile):
            # `repeat {...} while (c);`: the body runs before the first test.
            return ScLoop(kind="dowhile", cond=s.condition,
                          body=self._lower_stmts(s.body),
                          scope=self._scope(s)).copy_loc(s)

        if isinstance(s, ActivityReplicate):
            # In a sequential scope, `replicate (N) S` is N copies of S in
            # sequence: a counted loop, while nothing names the per-iteration
            # instances. (In parallel/schedule it is refused below.)
            if s.label is not None:
                return self._unroll_replicate(s)
            return ScLoop(kind="repeat", count=s.count, index_var=s.index_var,
                          body=self._lower_stmts(s.body),
                          scope=self._scope(s)).copy_loc(s)

        if isinstance(s, ActivityRepeat):
            return ScLoop(kind="repeat", count=s.count, index_var=s.index_var,
                          body=self._lower_stmts(s.body),
                          scope=self._scope(s)).copy_loc(s)

        if isinstance(s, ActivityForeach):
            return ScLoop(kind="foreach", iter_var=s.iterator,
                          collection=s.collection, index_var=s.index_var,
                          body=self._lower_stmts(s.body),
                          scope=self._scope(s)).copy_loc(s)

        if isinstance(s, ActivityIfElse):
            return ScIf(cond=s.condition,
                        then_body=self._lower_stmts(s.if_body),
                        else_body=self._lower_stmts(s.else_body),
                        then_scope=self._scope(s, ("then",)),
                        else_scope=self._scope(s, ("else",))).copy_loc(s)

        if isinstance(s, ActivityMatch):
            cases = [ScMatchCase(pattern=c.pattern,
                                 body=self._lower_stmts(c.body),
                                 scope=self._scope(c))
                     for c in s.cases]
            return ScMatch(subject=s.subject, cases=cases).copy_loc(s)

        if isinstance(s, ActivityAtomic):
            return ScAtomic(body=self._lower_stmts(s.stmts),
                            scope=self._scope(s)).copy_loc(s)

        if isinstance(s, ActivityParallel):
            self._check_no_replicate_branches(s, "parallel")
            return ScPar(branches=self._lower_stmts(s.stmts),
                         join_spec=s.join_spec, join_branches=self._join_branches(s),
                         scope=self._scope(s)).copy_loc(s)

        if isinstance(s, ActivitySchedule):
            # With members that do not interact, running them all in
            # parallel is one legal schedule (LRM 11.3.5); members that
            # interact run as planned (xf/plan).
            self._check_no_replicate_branches(s, "schedule")
            members, plan = self._schedule_plan(s)
            if plan.interacts:
                return self._lower_planned(s, members, plan)
            return ScPar(branches=self._lower_stmts(s.stmts),
                         join_spec=s.join_spec, join_branches=self._join_branches(s),
                         scope=self._scope(s)).copy_loc(s)

        if isinstance(s, ActivitySelect):
            # A branch that can never run keeps its place (weights, trace
            # indices) behind a false guard.
            branches = [ScSelectBranch(guard=b.guard, weight=b.weight,
                                       body=self._lower_stmts(b.body),
                                       scope=self._scope(b))
                        if b.infeasible is None else
                        ScSelectBranch(guard=ExprConstant(value=False), weight=b.weight,
                                       body=[], scope=self._scope(b))
                        for b in s.branches]
            return ScSelect(branches=branches,
                            allow_none=getattr(s, "allow_none", False)).copy_loc(s)

        raise UnsupportedConstructError(
            "unsupported activity construct %s" % type(s).__name__,
            loc=getattr(s, "loc", None))
