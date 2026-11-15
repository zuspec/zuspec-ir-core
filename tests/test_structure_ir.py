"""Structural IR: bundle types and roles, the extern pin map, and the flat
structural form (module instances and net assigns)."""
import zuspec.ir.core as ir


def _rv_bundle():
    return ir.DataTypeBundle(name="fw_rv_if#(bit[7:0])", super=None, fields=[
        ir.FieldInOut(name="valid", datatype=ir.DataTypeInt(bits=1, signed=False), is_out=True),
        ir.FieldInOut(name="ready", datatype=ir.DataTypeInt(bits=1, signed=False), is_out=False),
        ir.FieldInOut(name="data", datatype=ir.DataTypeInt(bits=8, signed=False), is_out=True),
    ])


def test_bundle_field_role():
    b = _rv_bundle()
    f = ir.Field(name="in", datatype=ir.DataTypeRef(ref_name=b.name), role=ir.BundleRole.MIRROR)
    assert f.role is ir.BundleRole.MIRROR
    assert isinstance(b, ir.DataTypeStruct)
    assert ir.Field(name="x", datatype=ir.DataTypeInt()).role is None


def test_extern_pin_map():
    ext = ir.DataTypeExtern(
        name="rv_fifo#(bit[7:0])", super=None, extern_name="fw_rv_fifo",
        extern_params={"WIDTH": ir.ExprConstant(value=8), "DEPTH": ir.ExprConstant(value=2)},
        pins=[ir.ExternPin(name="clk", path=["clk"]),
              ir.ExternPin(name="in_vld", path=["in", "valid"]),
              ir.ExternPin(name="cfg", tie=ir.ExprConstant(value=0, width=4)),
              ir.ExternPin(name="dbg", open=True)])
    assert list(ext.extern_params) == ["WIDTH", "DEPTH"]
    assert [p.name for p in ext.pins] == ["clk", "in_vld", "cfg", "dbg"]
    assert ext.pins[1].path == ["in", "valid"]
    assert ext.pins[3].open and ext.pins[3].path is None


def test_flat_structural_form():
    c = ir.DataTypeComponent(name="top", super=None)
    c.module_instances.append(ir.ModuleInstance(
        module="fw_rv_fifo", name="u_0",
        parameters={"WIDTH": ir.ExprConstant(value=8)},
        connections=[ir.PortConnection(port="clk", signal="clk"),
                     ir.PortConnection(port="cfg", signal=ir.ExprConstant(value=0, width=4)),
                     ir.PortConnection(port="dbg", signal=None)]))
    c.net_assigns.append(ir.NetAssign(lhs="out_valid", rhs="in_valid"))
    assert c.net_assigns[0].rhs == "in_valid"
    assert c.module_instances[0].connections[2].signal is None


def test_structural_ir_round_trips_through_yaml():
    """Enums keep their type, and an object reached twice is written twice
    (only a cycle is cut), so structural IR survives serialization."""
    from zuspec.ir.core.serializer import IRSerializer
    from zuspec.ir.core.deserializer import IRDeserializer
    b = _rv_bundle()
    shared = ir.DataTypeInt(bits=1, signed=False)
    top = ir.DataTypeComponent(name="top", super=None, fields=[
        ir.Field(name="in", datatype=ir.DataTypeRef(ref_name=b.name), role=ir.BundleRole.MIRROR),
        ir.Field(name="out", datatype=ir.DataTypeRef(ref_name=b.name), role=ir.BundleRole.INITIATOR),
        ir.FieldInOut(name="a", datatype=shared, is_out=False, direction=ir.SignalDirection.INPUT),
        ir.FieldInOut(name="b", datatype=shared, is_out=True, direction=ir.SignalDirection.OUTPUT),
    ], bind_map=[ir.Bind(
        lhs=ir.ExprHierarchical(elements=[ir.ExprHierarchicalElem(name="in")]),
        rhs=ir.ExprHierarchical(elements=[ir.ExprHierarchicalElem(name="out")]))])
    ctxt = ir.Context(type_m={b.name: b, "top": top})
    text = IRSerializer().serialize(ctxt, None)
    d = IRDeserializer()
    d.auto_register(ir)
    back, _ = d.deserialize(text)
    assert back.type_m == ctxt.type_m
    assert back.type_m["top"].fields[0].role is ir.BundleRole.MIRROR
    assert back.type_m["top"].fields[3].datatype == shared


def _hier(*els):
    return ir.ExprHierarchical(elements=[
        ir.ExprHierarchicalElem(name=n, subscript=s) for n, s in els])


def test_parameterized_structure_round_trips():
    """Z5 shapes (core-layer1.md §6.3, §6.6): a width over a template
    parameter, an array of bundles and a port array, a child declared in a
    generate if (both arms), a subscript below a child (its bundle array),
    and the flat form's net declared in a generate block."""
    from zuspec.ir.core.serializer import IRSerializer
    from zuspec.ir.core.deserializer import IRDeserializer
    n = ir.ExprRefParam(name="n")
    i = ir.ExprRefLocal(name="i")
    w = ir.DataTypeInt(bits=8, signed=False, width_expr=ir.ExprRefParam(name="W"))
    b = ir.DataTypeBundle(name="rv", super=None, fields=[
        ir.FieldInOut(name="valid", datatype=ir.DataTypeInt(bits=1, signed=False), is_out=True),
        ir.FieldInOut(name="data", datatype=ir.DataTypeRef(ref_name="T"), is_out=True)],
        template_params=[ir.TemplateParamType(name="T", kind=ir.TemplateParamKind.TYPE)])
    top = ir.DataTypeComponent(name="top", super=None, fields=[
        ir.Field(name="in", role=ir.BundleRole.MIRROR, inst_params={"T": w},
                 datatype=ir.DataTypeArray(element_type=ir.DataTypeRef(ref_name="rv"), size=2,
                                           size_expr=n)),
        ir.FieldInOut(name="fire", is_out=True,
                      datatype=ir.DataTypeArray(element_type=ir.DataTypeInt(bits=1, signed=False),
                                                size=2, size_expr=n)),
        ir.Field(name="x", datatype=ir.DataTypeRef(ref_name="lanes"),
                 inst_params={"n": n}),
    ], template_params=[
        ir.TemplateParamValue(name="W", kind=ir.TemplateParamKind.VALUE,
                              value_type=ir.DataTypeInt(bits=32, signed=True),
                              default_value=ir.ExprConstant(value=8)),
        ir.TemplateParamValue(name="n", kind=ir.TemplateParamKind.VALUE,
                              value_type=ir.DataTypeInt(bits=32, signed=True),
                              default_value=ir.ExprConstant(value=2))])
    top.generates = [
        ir.GenerateFor(var="i", start=ir.ExprConstant(value=0),
                       cond=ir.ExprCompare(left=i, ops=[ir.CmpOp.Lt], comparators=[n]),
                       label="g_1",
                       items=[ir.Bind(lhs=_hier(("x", None), ("in", i)), rhs=_hier(("in", i)))]),
        ir.GenerateIf(cond=ir.ExprRefParam(name="use_fifo"), label="g_2",
                      items=[ir.Field(name="f", datatype=ir.DataTypeRef(ref_name="fifo"))],
                      orelse=[ir.Field(name="w", datatype=ir.DataTypeRef(ref_name="wire"))]),
    ]
    flat = ir.DataTypeComponent(name="flat", super=None, flat_structural=True)
    flat.generates = [ir.GenerateIf(cond=ir.ExprRefParam(name="use_fifo"), label="g_1", items=[
        ir.Field(name="f_in_data", datatype=w),
        ir.ModuleInstance(module="fifo", name="f",
                          connections=[ir.PortConnection(port="in_data", signal="f_in_data")]),
        ir.NetAssign(lhs="f_in_data", rhs="in_data")])]
    ctxt = ir.Context(type_m={"rv": b, "top": top, "flat": flat})
    text = IRSerializer().serialize(ctxt, None)
    d = IRDeserializer()
    d.auto_register(ir)
    back, _ = d.deserialize(text)
    assert back.type_m == ctxt.type_m
    t = back.type_m["top"]
    assert t.fields[0].datatype.size_expr.name == "n" and t.fields[0].role is ir.BundleRole.MIRROR
    assert t.fields[0].inst_params["T"].width_expr.name == "W"
    bind = t.generates[0].items[0]
    assert bind.lhs.elements[1].name == "in" and bind.lhs.elements[1].subscript.name == "i"
    gif = t.generates[1]
    assert [f.name for f in gif.items] == ["f"] and [f.name for f in gif.orelse] == ["w"]
    fl = back.type_m["flat"].generates[0].items
    assert isinstance(fl[0], ir.Field) and fl[0].datatype.width_expr.name == "W"


def test_value_types_round_trip():
    """Packed struct and enum port types (core-layer1.md §6.3): a struct
    declared by the IR, an enum declared outside it (``extern_name``),
    2/4-state on integers, and an open extern pin's type."""
    from zuspec.ir.core.serializer import IRSerializer
    from zuspec.ir.core.deserializer import IRDeserializer
    assert ir.DataTypeInt(bits=8).four_state is None          # unstated by default
    assert ir.DataTypeInt(bits=8, four_state=False) != ir.DataTypeInt(bits=8, four_state=True)
    mode = ir.DataTypeEnum(name="prm_pkg::mode_e", items={"SLOW": 0, "FAST": 1}, width=2,
                           base_type=ir.DataTypeInt(bits=2, signed=False, four_state=True),
                           extern_name="prm_pkg::mode_e")
    pair = ir.DataTypeStruct(name="p::pair_t", super=None, packed=True, fields=[
        ir.Field(name="a", datatype=ir.DataTypeInt(bits=4, signed=False, four_state=False)),
        ir.Field(name="m", datatype=ir.DataTypeRef(ref_name="prm_pkg::mode_e"))])
    ext = ir.DataTypeExtern(name="x", super=None, extern_name="xmod", pins=[
        ir.ExternPin(name="dbg", open=True,
                     datatype=ir.DataTypeInt(bits=8, signed=False,
                                             width_expr=ir.ExprRefParam(name="W")))])
    top = ir.DataTypeComponent(name="top", super=None, fields=[
        ir.FieldInOut(name="p", datatype=ir.DataTypeRef(ref_name="p::pair_t"), is_out=False)])
    ctxt = ir.Context(type_m={"prm_pkg::mode_e": mode, "p::pair_t": pair, "x": ext, "top": top})
    text = IRSerializer().serialize(ctxt, None)
    d = IRDeserializer()
    d.auto_register(ir)
    back, _ = d.deserialize(text)
    assert back.type_m == ctxt.type_m
    b = back.type_m
    assert b["p::pair_t"].packed and b["p::pair_t"].extern_name is None
    assert b["p::pair_t"].fields[0].datatype.four_state is False
    assert b["prm_pkg::mode_e"].extern_name == "prm_pkg::mode_e"
    assert b["prm_pkg::mode_e"].base_type.four_state is True
    assert b["x"].pins[0].datatype.width_expr.name == "W"
