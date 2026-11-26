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


def test_task_block_and_xing_port_round_trip():
    """A task block (run()) with reads and writes, and a derived crossing
    port with its default step block (fw-hdl-spec processes.md, C5)."""
    clk = ir.DomainRef(kind="clock", target=["out"])
    run = ir.ProcBlock(name="run", kind=ir.ProcKind.TASK, func="run",
                       reads=[["out", "ready"]],
                       writes=[["out", "valid"], ["out", "data"]])
    dflt = ir.ProcBlock(name="dflt", kind=ir.ProcKind.STEP, func="dflt",
                        clock=clk, reads=[["out", "ready"]],
                        writes=[["out", "valid"]])
    comp = ir.DataTypeComponent(name="drv", super=None, fields=[],
                                proc_blocks=[run, dflt])
    ctxt = ir.Context(type_m={"drv": comp})
    text = IRSerializer().serialize(ctxt, None)
    d = IRDeserializer()
    d.auto_register(ir)
    back, _ = d.deserialize(text)
    assert back.type_m == ctxt.type_m
    run_b = back.type_m["drv"].proc_blocks[0]
    assert run_b.kind == ir.ProcKind.TASK and run_b.clock is None
    assert run_b.reads == [["out", "ready"]]

    p = ir.XingPort(path=["out", "valid"], read=False, written=True,
                    default="dflt", clock=clk)
    assert p.reset is None
    text = IRSerializer().serialize(p, None)
    back_p, _ = d.deserialize(text)
    assert back_p == p


def test_task_code_and_xing_record_round_trip():
    """A task block's code and arguments, and a component's lowered
    crossings (fw-hdl xtor-shell-plan.md T1/T2)."""
    i32 = ir.DataTypeInt(bits=32, signed=True)
    put = ir.StmtExpr(expr=ir.ExprAwait(value=ir.ExprCall(
        func=ir.ExprAttribute(value=_f(0), attr="put"), args=[ir.ExprRefLocal(name="x")])))
    xfer = ir.StmtExpr(expr=ir.ExprAwait(value=ir.ExprCall(
        func=ir.ExprAttribute(value=ir.ExprHierarchical(
            elements=[ir.ExprHierarchicalElem(name="p")]), attr="xfer"),
        args=[ir.ExprRefLocal(name="y"), ir.ExprRefLocal(name="x")])))
    task = ir.ProcBlock(name="xfer", kind=ir.ProcKind.TASK, func="xfer", body=[put, xfer],
                        args=[ir.Arg(arg="y", datatype=i32, direction=ir.SignalDirection.OUTPUT),
                              ir.Arg(arg="x", datatype=i32, direction=ir.SignalDirection.INPUT)])
    rec = ir.SyncXingRecord(path=["u", "reqs"], direction=ir.SyncXingDir.FROM_SYNC,
                            element_type=i32, depth=1,
                            ports={"valid": "u_reqs_valid", "ready": "u_reqs_ready",
                                   "data": "u_reqs_data", "wait": "u_reqs_wait"})
    comp = ir.DataTypeComponent(name="x", super=None, fields=[], proc_blocks=[task],
                                sync_xings=[rec])
    ctxt = ir.Context(type_m={"x": comp})
    text = IRSerializer().serialize(ctxt, None)
    d = IRDeserializer()
    d.auto_register(ir)
    back, _ = d.deserialize(text)
    assert back.type_m == ctxt.type_m
    b = back.type_m["x"]
    assert b.sync_xings[0].direction is ir.SyncXingDir.FROM_SYNC
    assert b.sync_xings[0].ports["wait"] == "u_reqs_wait"
    assert b.proc_blocks[0].args[0].direction is ir.SignalDirection.OUTPUT
