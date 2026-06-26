"""Tests for the structured constraint IR (plan tasks A1/A2, decision D3).

Covers construction, visitor dispatch, and serializer round-trip for the
ConstraintBlock / Constraint hierarchy.
"""
import enum
import yaml

import zuspec.ir.core as ir
from zuspec.ir.core.serializer import IRSerializer
from zuspec.ir.core.deserializer import IRDeserializer


class _Layer(enum.Enum):
    ACTIVITY = "ACTIVITY"


def _self_attr(name):
    return ir.ExprAttribute(value=ir.TypeExprRefSelf(), attr=name)


def _c(v):
    return ir.ExprConstant(value=v)


def _eq(name, v):
    return ir.ExprBin(lhs=_self_attr(name), op=ir.BinOp.Eq, rhs=_c(v))


def _make_block():
    """A block exercising every constraint item form."""
    return ir.ConstraintBlock(name="c_main", items=[
        ir.ConstraintExpr(expr=_eq("addr", 0)),
        ir.ConstraintImplies(
            antecedent=_eq("mode", 1),
            body=[ir.ConstraintExpr(expr=ir.ExprBin(
                lhs=_self_attr("addr"), op=ir.BinOp.Gt, rhs=_c(0)))]),
        ir.ConstraintIfElse(
            cond=_eq("kind", 2),
            then_body=[ir.ConstraintExpr(expr=_eq("size", 4))],
            else_body=[ir.ConstraintExpr(expr=_eq("size", 8))]),
        ir.ConstraintForeach(
            array=_self_attr("data"), index_var="i",
            body=[ir.ConstraintExpr(expr=ir.ExprIn(
                value=ir.ExprSubscript(value=_self_attr("data"),
                                       slice=ir.ExprRefLocal(name="i")),
                container=ir.ExprRangeList(ranges=[
                    ir.ExprRange(lower=_c(0), upper=_c(255))])))]),
        ir.ConstraintUnique(items=[_self_attr("a"), _self_attr("b")]),
        ir.ConstraintSoft(expr=_eq("len", 1)),
        ir.ConstraintDist(target=_self_attr("opcode"), weights=[
            ir.DistWeight(rng=_c(0), weight=_c(10)),
            ir.DistWeight(rng=ir.ExprRange(lower=_c(1), upper=_c(3)),
                          weight=_c(5), per_value=True),
        ]),
        ir.ConstraintSolveBefore(before=[_self_attr("mode")],
                                 after=[_self_attr("addr")]),
    ])


def test_construct():
    blk = _make_block()
    assert blk.name == "c_main"
    assert len(blk.items) == 8
    assert isinstance(blk.items[0], ir.ConstraintExpr)
    assert isinstance(blk.items[6], ir.ConstraintDist)
    assert blk.items[6].weights[1].per_value is True


def test_accept_dispatches():
    blk = _make_block()

    class V:
        hit = None
        def visitConstraintBlock(self, o):
            V.hit = (o.name, len(o.items))

    blk.accept(V())
    assert V.hit == ("c_main", 8)


def test_visit_default_recurses():
    # Wrap constants directly so the default walk reaches them (binary-op
    # children would stop at the no-op visitExprBin handler below).
    blk = ir.ConstraintBlock(name="c", items=[
        ir.ConstraintExpr(expr=_c(1)),
        ir.ConstraintExpr(expr=_c(2)),
    ])
    seen = []

    class Walk:
        def visitConstraintBlock(self, o):
            o.visitDefault(self)
        def visitConstraintExpr(self, o):
            o.visitDefault(self)
        def visitExprConstant(self, o):
            seen.append(o.value)
        def __getattr__(self, _k):
            return lambda o: None

    blk.accept(Walk())
    assert seen == [1, 2]


def test_serializer_roundtrip():
    blk = _make_block()

    text = IRSerializer().serialize(blk, _Layer.ACTIVITY)
    data = yaml.safe_load(text)
    assert data["_type"] == "ConstraintBlock"
    assert len(data["items"]) == 8

    deser = IRDeserializer()
    deser.auto_register(ir)
    obj, _layer = deser.deserialize(text)
    assert type(obj).__name__ == "ConstraintBlock"
    assert obj.name == "c_main"
    assert len(obj.items) == 8
    # spot-check deep structure survived the round-trip
    assert isinstance(obj.items[1], ir.ConstraintImplies)
    assert isinstance(obj.items[3], ir.ConstraintForeach)
    assert obj.items[3].index_var == "i"
    assert isinstance(obj.items[6], ir.ConstraintDist)
    assert obj.items[6].weights[1].per_value is True
    assert isinstance(obj.items[7], ir.ConstraintSolveBefore)
