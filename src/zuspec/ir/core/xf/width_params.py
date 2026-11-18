"""TypeParamWidthPass — packed type parameters to integer width parameters.

Some flows cannot take a ``parameter type`` (yosys ``read -formal``,
Verilog-2005 tools). This target-neutral transform rewrites a component's
*qualifying* type parameters into value parameters giving their width, so
a back end emits ``parameter int T_W = 8`` where it emitted
``parameter type T = logic [7:0]``:

- the ``TemplateParamType`` ``T`` becomes a ``TemplateParamValue``
  ``T_W`` (an ``int``) in the same position, its default the default
  type's width (``width_expr``, or ``bits`` as a constant);
- every use of ``T`` in the component (a ``DataTypeRef(T)`` in a port,
  field, local, cast, array element, child or bundle ``inst_params``,
  extern parameter ...) becomes a ``DataTypeInt`` with
  ``width_expr = ExprRefParam(T_W)`` and the default type's signedness
  and state count (``bits`` is the default width, as for any width
  written over parameters);
- ``$bits(T)`` (``ExprCall(ExprRefUnresolved("bits"), [ExprRefParam(T)])``)
  becomes ``ExprRefParam(T_W)``;
- every instantiation of the component that overrides ``T`` (a field's
  ``inst_params``, a ``ModuleInstance``'s ``parameters``, in any
  component of the context) overrides ``T_W`` instead, with the
  overriding type's width (a constant, its ``width_expr``, or the
  enclosing component's own ``U_W`` when the override is its lowered
  type parameter ``U``).

A type parameter *qualifies* when it belongs to a non-extern component and:

- its default is a packed integral vector (``DataTypeInt``: ``bit``,
  ``logic [N-1:0]``, ``int`` ...);
- every override of it, anywhere in the context, is a ``DataTypeInt`` of
  the same signedness, or a qualifying type parameter of the instantiating
  component with the same signedness;
- the component does not read it as a value other than through ``$bits``;
- the width parameter's name is free in the component.

Anything else is left as a type parameter and reported (``kept``, with the
reason), never rewritten: a struct- or enum-typed parameter (lowering it to
a vector would lose its field layout at the uses), a parameter with no
default, one overridden with a struct or with a different signedness. A
caller that needs no type parameters at all checks ``report.kept``.

The pass rewrites the context in place. It works on the structural form
(child ``inst_params``) and on the flat form (``module_instances``,
``generates``); ``module_name`` maps a component name to the module name
its ``ModuleInstance``\\ s use when those differ.
"""
from __future__ import annotations

import copy
import dataclasses as dc
from typing import Any, Callable, Dict, Iterable, List, Optional, Set, Tuple

from ..data_type import (
    DataType, DataTypeArray, DataTypeComponent, DataTypeExtern, DataTypeInt,
    DataTypeRef, DataTypeStruct, TemplateParamKind, TemplateParamType,
    TemplateParamValue,
)
from ..expr import Expr, ExprCall, ExprConstant, ExprRefParam, ExprRefUnresolved
from ..connection import ModuleInstance
from ..fields import Field

__all__ = ["TypeParamWidthPass", "TypeParamWidthReport", "WidthParam",
           "KeptTypeParam", "lower_type_params_to_widths"]


@dc.dataclass
class WidthParam:
    """A type parameter the pass lowered."""
    component: str
    type_param: str
    width_param: str
    default: Expr


@dc.dataclass
class KeptTypeParam:
    """A type parameter the pass left as it is, and why."""
    component: str
    type_param: str
    reason: str


@dc.dataclass
class TypeParamWidthReport:
    lowered: List[WidthParam] = dc.field(default_factory=list)
    kept: List[KeptTypeParam] = dc.field(default_factory=list)

    def width_param(self, component: str, type_param: str) -> Optional[str]:
        for w in self.lowered:
            if w.component == component and w.type_param == type_param:
                return w.width_param
        return None


# -- generic walking ---------------------------------------------------------

_SKIP = frozenset(("py_type", "loc", "provenance", "clock_domain", "reset_domain"))


def _is_ir(o) -> bool:
    return dc.is_dataclass(o) and not isinstance(o, type) \
        and type(o).__module__.startswith("zuspec.ir")


def _walk(o, fn: Callable[[Any], Any], shared: Set[int], seen: Set[int]):
    """``o`` with ``fn`` applied top-down: ``fn(node)`` returns a
    replacement (not entered) or None. Lists and dicts are updated in place,
    tuples rebuilt. Named types (``shared``: the context's own types) are
    not entered."""
    if isinstance(o, list):
        for i, x in enumerate(o):
            o[i] = _walk(x, fn, shared, seen)
        return o
    if isinstance(o, dict):
        for k in list(o.keys()):
            o[k] = _walk(o[k], fn, shared, seen)
        return o
    if isinstance(o, tuple):
        return tuple(_walk(x, fn, shared, seen) for x in o)
    if not _is_ir(o):
        return o
    r = fn(o)
    if r is not None:
        return r
    if id(o) in seen or id(o) in shared:
        return o
    seen.add(id(o))
    for f in dc.fields(o):
        if f.name in _SKIP:
            continue
        v = getattr(o, f.name, None)
        if v is None or isinstance(v, (str, int, float, bool)):
            continue
        nv = _walk(v, fn, shared, seen)
        if nv is not v:
            setattr(o, f.name, nv)
    return o


def _visit(o, fn: Callable[[Any], None], shared: Set[int]):
    """Calls ``fn`` on every IR node under ``o`` (not entering ``shared``)."""
    def f(n):
        fn(n)
        return None
    _walk(o, f, shared, set())


def _is_bits(e, name: str) -> bool:
    if not (isinstance(e, ExprCall) and isinstance(e.func, ExprRefUnresolved)
            and e.func.name == "bits" and len(e.args) == 1):
        return False
    a = e.args[0]
    return (isinstance(a, ExprRefParam) and a.name == name) \
        or (isinstance(a, DataTypeRef) and a.ref_name == name)


def _target_of(dt) -> Optional[str]:
    while isinstance(dt, DataTypeArray):
        dt = dt.element_type
    return dt.ref_name if isinstance(dt, DataTypeRef) else None


class TypeParamWidthPass:
    """Lower qualifying type parameters to width parameters (module doc)."""

    def __init__(self, suffix: str = "_W",
                 module_name: Optional[Callable[[str], str]] = None):
        self.suffix = suffix
        self.module_name = module_name or (lambda n: n)

    # ------------------------------------------------------------------
    def run(self, ctxt_or_type_m, components: Optional[Iterable[str]] = None
            ) -> TypeParamWidthReport:
        type_m: Dict[str, DataType] = getattr(ctxt_or_type_m, "type_m", ctxt_or_type_m)
        shared = {id(t) for t in type_m.values()}
        only = set(components) if components is not None else None
        report = TypeParamWidthReport()
        kept: Dict[Tuple[str, str], str] = {}

        # Candidates: type parameters of generated components.
        cand: Dict[str, Dict[str, TemplateParamType]] = {}
        for name, t in type_m.items():
            if not isinstance(t, DataTypeComponent) or not t.template_params:
                continue
            if only is not None and name not in only:
                continue
            for tp in t.template_params:
                if not isinstance(tp, TemplateParamType):
                    continue
                why = self._why_not(t, tp)
                if why is None and isinstance(t, DataTypeExtern):
                    why = "extern component: its module is not generated"
                if why is None:
                    why = self._bare_use(t, tp.name, shared)
                if why is None:
                    cand.setdefault(name, {})[tp.name] = tp
                else:
                    kept[(name, tp.name)] = why

        # Overrides: (owner, target, param, value, setter).
        bindings = self._bindings(type_m, shared)

        changed = True
        while changed:
            changed = False
            for owner, target, p, v, _ in bindings:
                tp = cand.get(target, {}).get(p)
                if tp is None:
                    continue
                why = self._binding_why_not(v, tp, cand.get(owner, {}))
                if why is not None:
                    del cand[target][p]
                    kept[(target, p)] = f"override in '{owner}': {why}"
                    changed = True

        # Rewrite the overrides first (in the instantiating component's
        # terms), then each lowered component's parameters and uses.
        for owner, target, p, v, put in bindings:
            if p in cand.get(target, {}):
                put(p, self._wname(p), self._width_of(v, cand.get(owner, {})))
        for name, params in cand.items():
            if not params:
                continue
            comp = type_m[name]
            for i, tp in enumerate(comp.template_params):
                if isinstance(tp, TemplateParamType) and tp.name in params:
                    d = self._width_of(tp.default_value, params)
                    comp.template_params[i] = TemplateParamValue(
                        name=self._wname(tp.name), kind=TemplateParamKind.VALUE,
                        value_type=DataTypeInt(bits=32, signed=True), default_value=d)
                    report.lowered.append(WidthParam(name, tp.name, self._wname(tp.name), d))
            self._rewrite_uses(comp, params, shared)
        for (c, p), why in kept.items():
            report.kept.append(KeptTypeParam(c, p, why))
        return report

    # ------------------------------------------------------------------
    def _wname(self, p: str) -> str:
        return p + self.suffix

    def _why_not(self, comp: DataTypeComponent, tp: TemplateParamType) -> Optional[str]:
        d = tp.default_value
        if d is None:
            return "no default type"
        if not isinstance(d, DataTypeInt):
            kind = type(d).__name__
            if isinstance(d, DataTypeRef):
                kind = f"'{d.ref_name}'"
            return f"default {kind} is not a packed integral vector"
        w = self._wname(tp.name)
        names = {p.name for p in comp.template_params} | {f.name for f in comp.fields}
        if w in names:
            return f"name '{w}' is taken"
        return None

    def _bare_use(self, comp, name: str, shared) -> Optional[str]:
        """A reason if ``comp`` reads type parameter ``name`` as a value
        other than through ``$bits``."""
        bad: List[bool] = []

        def fn(n):
            if _is_bits(n, name):
                return n          # stop: the argument is the allowed use
            if isinstance(n, ExprRefParam) and n.name == name:
                bad.append(True)
            return None
        for tp in comp.template_params:
            if tp.name != name:
                _walk(tp, fn, shared, set())
        for f in dc.fields(comp):
            if f.name in _SKIP or f.name in ("template_params", "name", "super"):
                continue
            _walk(getattr(comp, f.name), fn, shared, set())
        return "read as a value other than through $bits" if bad else None

    def _bindings(self, type_m, shared):
        out = []
        by_mod = {self.module_name(n): n for n, c in type_m.items()
                  if isinstance(c, DataTypeComponent)}
        for owner, t in type_m.items():
            if not isinstance(t, DataTypeStruct):
                continue
            for f in getattr(t, "fields", []) or []:
                tgt = _target_of(f.datatype)
                if tgt is None or not f.inst_params:
                    continue
                for p, v in list(f.inst_params.items()):
                    out.append((owner, tgt, p, v, self._dict_put(f.inst_params)))
            if not isinstance(t, DataTypeComponent):
                continue
            mis: List[ModuleInstance] = []

            def col(n):
                if isinstance(n, ModuleInstance):
                    mis.append(n)
            for attr in ("module_instances", "generates"):
                _visit(getattr(t, attr, []), col, shared)
            for mi in mis:
                tgt = by_mod.get(mi.module)
                if tgt is None:
                    continue
                for p, v in list(mi.parameters.items()):
                    out.append((owner, tgt, p, v, self._dict_put(mi.parameters)))
        return out

    @staticmethod
    def _dict_put(d: Dict[str, Any]):
        def put(old, new, value):
            items = [(new, value) if k == old else (k, v) for k, v in d.items()]
            d.clear()
            d.update(items)
        return put

    def _binding_why_not(self, v, tp: TemplateParamType, owner_cand) -> Optional[str]:
        signed = tp.default_value.signed
        if isinstance(v, DataTypeInt):
            if v.signed != signed:
                return "signedness differs from the default"
            return None
        if isinstance(v, DataTypeRef) and v.ref_name in owner_cand:
            if owner_cand[v.ref_name].default_value.signed != signed:
                return "signedness differs from the default"
            return None
        if isinstance(v, DataTypeRef):
            return f"type '{v.ref_name}' is not a packed integral vector"
        return f"{type(v).__name__} is not a packed integral vector"

    def _width_of(self, dt, params) -> Expr:
        """The width of ``dt`` (a ``DataTypeInt``, or a ``DataTypeRef`` to
        one of ``params``) as an expression."""
        if isinstance(dt, DataTypeRef) and dt.ref_name in params:
            return ExprRefParam(name=self._wname(dt.ref_name))
        we = getattr(dt, "width_expr", None)
        if we is not None:
            return self._expr_subst(copy.deepcopy(we), params)
        bits = dt.bits if dt.bits != -1 else 1
        return ExprConstant(value=bits)

    def _expr_subst(self, e, params):
        return _walk(e, self._repl(params), set(), set())

    def _repl(self, params):
        def fn(n):
            if isinstance(n, DataTypeRef) and n.ref_name in params:
                d = params[n.ref_name].default_value
                return DataTypeInt(bits=d.bits, signed=d.signed, four_state=d.four_state,
                                   width_expr=ExprRefParam(name=self._wname(n.ref_name)))
            for p in params:
                if _is_bits(n, p):
                    return ExprRefParam(name=self._wname(p))
            return None
        return fn

    def _rewrite_uses(self, comp: DataTypeComponent, params, shared):
        fn = self._repl(params)
        seen: Set[int] = set()
        for f in dc.fields(comp):
            if f.name in _SKIP or f.name in ("name", "super"):
                continue
            v = getattr(comp, f.name)
            if v is None or isinstance(v, (str, int, float, bool)):
                continue
            nv = _walk(v, fn, shared, seen)
            if nv is not v:
                setattr(comp, f.name, nv)


def lower_type_params_to_widths(ctxt_or_type_m, components: Optional[Iterable[str]] = None,
                                *, suffix: str = "_W",
                                module_name: Optional[Callable[[str], str]] = None
                                ) -> TypeParamWidthReport:
    """Run :class:`TypeParamWidthPass` over a context (or its ``type_m``)."""
    return TypeParamWidthPass(suffix=suffix, module_name=module_name).run(
        ctxt_or_type_m, components)
