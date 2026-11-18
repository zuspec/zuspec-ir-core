"""TypeParamWidthPass: packed type parameters become int width parameters."""
import zuspec.ir.core as ir
from zuspec.ir.core.xf import TypeParamWidthPass, lower_type_params_to_widths

K = ir.TemplateParamKind


def u(bits, signed=False):
    return ir.DataTypeInt(bits=bits, signed=signed)


def T(name="T"):
    return ir.DataTypeRef(ref_name=name)


def tparam(name, default):
    return ir.TemplateParamType(name=name, kind=K.TYPE, default_value=default)


def bits(name):
    return ir.ExprCall(func=ir.ExprRefUnresolved(name="bits"), args=[ir.ExprRefParam(name=name)])


def leaf(default=None, name="leaf"):
    """``leaf #(type T = logic [7:0], int N = $bits(T))``: ports, a field, a
    local, a cast and ``$bits(T)`` in a body."""
    fn = ir.Function(name="step", body=[ir.StmtAssign(
        targets=[ir.ExprRefField(base=ir.TypeExprRefSelf(), index=1)],
        value=ir.ExprCast(target_type=T(), value=ir.ExprBin(
            lhs=ir.ExprRefField(base=ir.TypeExprRefSelf(), index=0), op=ir.BinOp.Add,
            rhs=bits("T"))))])
    fn.metadata = {"locals": [("tmp", T())]}
    return ir.DataTypeComponent(
        name=name, super=None,
        template_params=[
            tparam("T", default if default is not None else u(8)),
            ir.TemplateParamValue(name="N", kind=K.VALUE, value_type=u(32, True),
                                  default_value=bits("T"))],
        fields=[ir.FieldInOut(name="d", datatype=T(), is_out=False),
                ir.FieldInOut(name="q", datatype=T(), is_out=True),
                ir.Field(name="mem", datatype=ir.DataTypeArray(element_type=T(), size=4))],
        sync_processes=[fn])


def ctxt(*types):
    c = ir.Context()
    for t in types:
        c.type_m[t.name] = t
    return c


def is_w(dt, wname, bits_=8, signed=False):
    return isinstance(dt, ir.DataTypeInt) and dt.bits == bits_ and dt.signed == signed \
        and isinstance(dt.width_expr, ir.ExprRefParam) and dt.width_expr.name == wname


def test_simple():
    c = ctxt(leaf())
    rep = lower_type_params_to_widths(c)
    assert [(w.component, w.type_param, w.width_param, w.default.value)
            for w in rep.lowered] == [("leaf", "T", "T_W", 8)]
    assert rep.kept == []
    lf = c.type_m["leaf"]
    tp, n = lf.template_params
    assert isinstance(tp, ir.TemplateParamValue) and tp.name == "T_W"
    assert tp.value_type.bits == 32 and tp.value_type.signed
    # $bits(T) in another parameter's default
    assert isinstance(n.default_value, ir.ExprRefParam) and n.default_value.name == "T_W"
    d, q, mem = lf.fields
    assert is_w(d.datatype, "T_W") and is_w(q.datatype, "T_W")
    assert is_w(mem.datatype.element_type, "T_W") and mem.datatype.size == 4
    fn = lf.sync_processes[0]
    assert is_w(fn.metadata["locals"][0][1], "T_W")
    cast = fn.body[0].value
    assert is_w(cast.target_type, "T_W")
    assert isinstance(cast.value.rhs, ir.ExprRefParam) and cast.value.rhs.name == "T_W"
    assert rep.width_param("leaf", "T") == "T_W"


def test_width_expr_default_and_suffix():
    w = ir.ExprBin(lhs=ir.ExprRefParam(name="N"), op=ir.BinOp.Mult, rhs=ir.ExprConstant(value=2))
    c = ir.DataTypeComponent(name="c", super=None, template_params=[
        ir.TemplateParamValue(name="N", kind=K.VALUE, value_type=u(32, True),
                              default_value=ir.ExprConstant(value=4)),
        tparam("T", ir.DataTypeInt(bits=8, signed=True, width_expr=w))],
        fields=[ir.FieldInOut(name="d", datatype=T(), is_out=False)])
    rep = TypeParamWidthPass(suffix="_WIDTH").run(ctxt(c))
    assert rep.lowered[0].width_param == "T_WIDTH"
    assert rep.lowered[0].default is not w and rep.lowered[0].default.lhs.name == "N"
    assert is_w(c.fields[0].datatype, "T_WIDTH", signed=True)


def test_overridden_instance():
    """A child's override, by a type and by the parent's own lowered type
    parameter; and a flat ``ModuleInstance`` override."""
    mid = ir.DataTypeComponent(
        name="mid", super=None, template_params=[tparam("U", u(4))],
        fields=[ir.FieldInOut(name="a", datatype=T("U"), is_out=False),
                ir.Field(name="x", datatype=T("leaf"), inst_params={"T": T("U")})])
    top = ir.DataTypeComponent(
        name="top", super=None,
        fields=[ir.Field(name="m", datatype=T("mid"), inst_params={"U": u(16)}),
                ir.Field(name="y", datatype=ir.DataTypeArray(element_type=T("leaf"), size=2),
                         inst_params={"N": ir.ExprConstant(value=3), "T": u(12)})],
        module_instances=[ir.ModuleInstance(module="leaf", name="z",
                                            parameters={"T": u(5)})])
    c = ctxt(leaf(), mid, top)
    rep = lower_type_params_to_widths(c)
    assert sorted((w.component, w.width_param) for w in rep.lowered) == [
        ("leaf", "T_W"), ("mid", "U_W")]
    assert rep.kept == []
    m, y = top.fields
    assert list(m.inst_params) == ["U_W"] and m.inst_params["U_W"].value == 16
    assert list(y.inst_params) == ["N", "T_W"] and y.inst_params["T_W"].value == 12
    assert list(top.module_instances[0].parameters) == ["T_W"]
    assert top.module_instances[0].parameters["T_W"].value == 5
    x = mid.fields[1]
    assert isinstance(x.inst_params["T_W"], ir.ExprRefParam) and x.inst_params["T_W"].name == "U_W"
    assert is_w(mid.fields[0].datatype, "U_W", bits_=4)


def test_bits_in_child_override_and_bundle():
    """``$bits(T)`` passed to a child's value parameter, and ``T`` passed to
    a bundle's type parameter (bundles are flattened, not emitted)."""
    p = ir.DataTypeComponent(
        name="p", super=None, template_params=[tparam("T", u(8))],
        fields=[ir.Field(name="c", datatype=T("other"), inst_params={"W": bits("T")}),
                ir.Field(name="b", datatype=T("rv"), role=ir.BundleRole.MIRROR,
                         inst_params={"T": T()})])
    other = ir.DataTypeComponent(name="other", super=None, template_params=[
        ir.TemplateParamValue(name="W", kind=K.VALUE, value_type=u(32, True),
                              default_value=ir.ExprConstant(value=1))])
    rv = ir.DataTypeBundle(name="rv", super=None, fields=[
        ir.FieldInOut(name="data", datatype=T(), is_out=True)])
    c = ctxt(p, other, rv)
    rep = lower_type_params_to_widths(c)
    assert [w.component for w in rep.lowered] == ["p"]
    assert p.fields[0].inst_params["W"].name == "T_W"
    assert is_w(p.fields[1].inst_params["T"], "T_W")
    assert isinstance(rv.fields[0].datatype, ir.DataTypeRef)   # the bundle is untouched


def test_non_qualifying():
    """Struct default, struct override, signedness, bare value use, no
    default, a taken name and an extern: all kept and reported."""
    s = ir.DataTypeStruct(name="pkt_t", super=None, fields=[
        ir.Field(name="a", datatype=u(4)), ir.Field(name="b", datatype=u(4))])
    struct_def = leaf(T("pkt_t"), "sd")
    over = leaf(name="ov")
    sign = leaf(name="sg")
    bare = leaf(name="bv")
    bare.template_params.append(ir.TemplateParamValue(
        name="M", kind=K.VALUE, value_type=u(32, True), default_value=ir.ExprRefParam(name="T")))
    nodef = ir.DataTypeComponent(name="nd", super=None,
                                 template_params=[ir.TemplateParamType(name="T", kind=K.TYPE)])
    taken = leaf(name="tk")
    taken.fields.append(ir.Field(name="T_W", datatype=u(1)))
    ext = ir.DataTypeExtern(name="ex", super=None, extern_name="ex_rtl",
                            template_params=[tparam("T", u(8))])
    top = ir.DataTypeComponent(name="top", super=None, fields=[
        ir.Field(name="o", datatype=T("ov"), inst_params={"T": T("pkt_t")}),
        ir.Field(name="g", datatype=T("sg"), inst_params={"T": u(8, True)})])
    good = leaf()
    c = ctxt(s, struct_def, over, sign, bare, nodef, taken, ext, top, good)
    before = struct_def.fields[0].datatype
    rep = lower_type_params_to_widths(c)
    assert [w.component for w in rep.lowered] == ["leaf"]
    kept = {k.component: k.reason for k in rep.kept}
    assert set(kept) == {"sd", "ov", "sg", "bv", "nd", "tk", "ex"}
    assert "'pkt_t' is not a packed integral vector" in kept["sd"]
    assert "override in 'top'" in kept["ov"] and "pkt_t" in kept["ov"]
    assert "signedness" in kept["sg"]
    assert "read as a value" in kept["bv"]
    assert "no default" in kept["nd"]
    assert "taken" in kept["tk"]
    assert "extern" in kept["ex"]
    # Kept parameters are untouched, and so are their overrides.
    assert struct_def.fields[0].datatype is before
    assert isinstance(struct_def.template_params[0], ir.TemplateParamType)
    assert list(top.fields[0].inst_params) == ["T"]


def test_components_filter():
    a, b = leaf(name="a"), leaf(name="b")
    rep = lower_type_params_to_widths(ctxt(a, b), components=["b"])
    assert [w.component for w in rep.lowered] == ["b"]
    assert isinstance(a.template_params[0], ir.TemplateParamType)
