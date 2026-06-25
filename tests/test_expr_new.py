"""Tests for the ExprNew class-model IR node (non-synthesizable SW path).

ExprNew represents `obj = new(args)` object allocation. It is the join point
between the type system, the constructor, and the SW backend's GC heap.
"""
import enum
import yaml

import zuspec.ir.core as ir
from zuspec.ir.core import ExprNew, ExprConstant
from zuspec.ir.core.serializer import IRSerializer
from zuspec.ir.core.deserializer import IRDeserializer


class _Layer(enum.Enum):
    ACTIVITY = "ACTIVITY"


def _make_new(*values):
    return ExprNew(
        datatype=ir.DataTypeClass(name="derived", super=None),
        args=[ExprConstant(value=v) for v in values],
    )


def test_construct():
    n = _make_new(1, 2)
    assert n.datatype.name == "derived"
    assert [a.value for a in n.args] == [1, 2]


def test_accept_dispatches_to_visit_expr_new():
    n = _make_new(7)

    class V:
        hit = None
        def visitExprNew(self, o):
            V.hit = (o.datatype.name, len(o.args))

    n.accept(V())
    assert V.hit == ("derived", 1)


def test_visit_default_recurses_into_args():
    n = _make_new(7, 8)
    seen = []

    class Walk:
        def visitExprNew(self, o):
            o.visitDefault(self)
        def visitDataTypeClass(self, o):
            pass
        def visitExprConstant(self, o):
            seen.append(o.value)
        def __getattr__(self, _k):
            return lambda o: None

    n.accept(Walk())
    assert seen == [7, 8]


def test_serializer_roundtrip():
    node = _make_new(7, 8)

    text = IRSerializer().serialize(node, _Layer.ACTIVITY)
    data = yaml.safe_load(text)
    assert data["_type"] == "ExprNew"
    assert len(data["args"]) == 2

    deser = IRDeserializer()
    deser.auto_register(ir)
    obj, _layer = deser.deserialize(text)
    assert type(obj).__name__ == "ExprNew"
    assert obj.datatype.name == "derived"
    assert [a.value for a in obj.args] == [7, 8]
