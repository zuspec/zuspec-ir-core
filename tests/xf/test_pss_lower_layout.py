"""The flattened object layout (P1-D1): a struct attribute is one slot per
scalar leaf, base struct's fields first, named by its dotted path, and the
solve problem uses the same slots.

A rand struct attribute used to be one 32-bit solver variable in one slot.
"""
import pytest

from zuspec.ir.core import expr as E
from zuspec.ir.core import stmt as S
from zuspec.ir.core.data_type import (DataTypeClass, DataTypeInt, DataTypeRef,
                                      DataTypeStruct)
from zuspec.ir.core.data_type import Function
from zuspec.ir.core.fields import Field, RandKind
from zuspec.ir.core.scenario import ScCoroutine
from zuspec.ir.core.xf.pss_lower import layout
from zuspec.ir.core.xf.pss_lower.constraints import collect_solve_problem
from zuspec.ir.core.xf.validate import UnsupportedConstructError


def _f(name, dt=None, rand=False, init=None):
    return Field(name=name, datatype=dt or DataTypeInt(bits=4, signed=False),
                 rand_kind=RandKind.RAND if rand else None, initial_value=init)


def _self(*path):
    e = E.TypeExprRefSelf()
    for p in path:
        e = E.ExprAttribute(value=e, attr=p)
    return e


def _lt(a, b):
    return S.StmtExpr(expr=E.ExprBin(lhs=a, op=E.BinOp.Lt, rhs=b))


BASE = DataTypeStruct(name="base_s", super=None, fields=[_f("b", rand=True)],
                      functions=[Function(name="c0", body=[_lt(_self("b"), E.ExprConstant(value=5))],
                                          metadata={"_is_constraint": True})])
DERIVED = DataTypeStruct(name="s_t", super=DataTypeRef(ref_name="base_s"),
                         fields=[_f("f", rand=True), _f("g")])
TYPES = {"base_s": BASE, "s_t": DERIVED}


def test_a_struct_attribute_is_its_leaves_base_first():
    leaves = layout.object_layout(
        [_f("x"), _f("s", DataTypeRef(ref_name="s_t"), rand=True), _f("y")], TYPES)
    assert [(l.name, l.rand) for l in leaves] == [
        ("x", False), ("s.b", True), ("s.f", True), ("s.g", False), ("y", False)]


def test_a_non_rand_struct_attribute_has_no_rand_leaves():
    leaves = layout.object_layout([_f("t", DERIVED)], TYPES)
    assert [l.rand for l in leaves] == [False, False, False]


def test_a_struct_containing_itself_is_refused():
    loop = DataTypeStruct(name="loop_s", super=None, fields=[])
    loop.fields.append(_f("inner", DataTypeRef(ref_name="loop_s")))
    with pytest.raises(UnsupportedConstructError, match="contains itself"):
        layout.object_layout([_f("l", loop)], {"loop_s": loop})


def test_the_solve_problem_uses_leaf_slots_and_the_types_constraints():
    dt = DataTypeClass(name="A", super=None, fields=[
        _f("pad"), _f("s", DERIVED, rand=True), _f("x", rand=True)])
    act_c = Function(name="c", body=[_lt(_self("s", "f"), _self("x"))])
    p = collect_solve_problem(ScCoroutine(name="A", pending_constraints=[act_c]),
                              dt, TYPES)
    assert [(v.name, v.slot) for v in p.vars] == [("s.b", 1), ("s.f", 2), ("x", 4)]
    # the action's `s.f < x`, then the base struct's `b < 5` as `s.b < 5`
    def ref(e):
        return e.index if isinstance(e, E.ExprRefField) else e.value
    refs = [(ref(c.expr.lhs), ref(c.expr.rhs)) for c in p.constraints]
    assert refs == [(2, 4), (1, 5)]


def test_prefix_self_roots_a_struct_expression_at_its_attribute():
    e = layout.prefix_self(E.ExprBin(lhs=_self("a"), op=E.BinOp.Add,
                                     rhs=E.ExprConstant(value=1)), ("s", "csr"))
    assert e.lhs == _self("s", "csr", "a")
