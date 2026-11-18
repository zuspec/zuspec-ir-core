"""Process-block IR (Z8, fw-hdl core-layer2 §7): step and comb blocks,
their domains, reset branches and writes."""
import zuspec.ir.core as ir
from zuspec.ir.core.serializer import IRSerializer
from zuspec.ir.core.deserializer import IRDeserializer


def _c(v):
    return ir.ExprConstant(value=v)


def _f(i):
    return ir.ExprRefField(base=ir.TypeExprRefSelf(), index=i)


def _assign(i, v):
    return ir.StmtAssign(targets=[_f(i)], value=v)


def _counter():
    """A counter: step() with a reset_state() branch, comb() drives a
    wire, and a `fw_step(tick) block that handles reset itself."""
    u8 = ir.DataTypeInt(bits=8, signed=False)
    clk = ir.DomainRef(kind="clock")
    rst = ir.DomainRef(kind="reset", target=["out"])
    step = ir.ProcBlock(
        name="step", kind=ir.ProcKind.STEP, func="step",
        body=[_assign(0, ir.ExprBin(lhs=_f(0), op=ir.BinOp.Add, rhs=_c(1)))],
        clock=clk, reset=rst, reset_body=[_assign(0, _c(0))],
        writes=[["count"]])
    comb = ir.ProcBlock(
        name="comb", kind=ir.ProcKind.COMB, func="comb",
        body=[_assign(1, ir.ExprBin(lhs=_f(0), op=ir.BinOp.Eq, rhs=_c(255)))],
        writes=[["out", "wrap"]])
    tick = ir.ProcBlock(
        name="tick", kind=ir.ProcKind.STEP, func="tick", clock=clk,
        body=[ir.StmtIf(test=ir.ExprInReset(reset=rst),
                        body=[_assign(2, _c(0))], orelse=[_assign(2, _c(1))])],
        writes=[[ir.ExprHierarchicalElem(name="flag", subscript=_c(0))]])
    return ir.DataTypeComponent(
        name="counter", super=None,
        fields=[ir.Field(name="count", datatype=u8),
                ir.Field(name="wrap", datatype=ir.DataTypeInt(bits=1, signed=False)),
                ir.Field(name="flag", datatype=ir.DataTypeInt(bits=1, signed=False))],
        proc_blocks=[step, comb, tick])


def test_defaults():
    c = ir.DataTypeComponent(name="c", super=None, fields=[])
    assert c.proc_blocks == []
    b = ir.ProcBlock(name="comb", kind=ir.ProcKind.COMB)
    assert b.func == "" and b.body == [] and b.reset_body == [] and b.writes == []
    assert b.clock is None and b.reset is None
    assert isinstance(ir.ExprInReset(reset=ir.DomainRef(kind="reset")), ir.Expr)


def test_round_trips():
    ctxt = ir.Context(type_m={"counter": _counter()})
    text = IRSerializer().serialize(ctxt, None)
    d = IRDeserializer()
    d.auto_register(ir)
    back, _ = d.deserialize(text)
    assert back.type_m == ctxt.type_m

    step, comb, tick = back.type_m["counter"].proc_blocks
    assert [b.kind for b in (step, comb, tick)] == [
        ir.ProcKind.STEP, ir.ProcKind.COMB, ir.ProcKind.STEP]
    assert step.clock.kind == "clock" and step.reset.target == ["out"]
    assert step.reset_body[0].value.value == 0 and step.writes == [["count"]]
    assert comb.clock is None and comb.writes == [["out", "wrap"]]
    assert tick.reset is None and tick.reset_body == []
    assert isinstance(tick.body[0].test, ir.ExprInReset)
    assert tick.body[0].test.reset.kind == "reset"
    assert isinstance(tick.writes[0][0], ir.ExprHierarchicalElem)
