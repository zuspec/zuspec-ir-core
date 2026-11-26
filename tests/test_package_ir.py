"""Package IR (fw-hdl protocol-kit-followups §18, Q19/Q20): packages with
imports, constants and functions in source order, class-scope typedefs,
member-type references and package-member references."""
import zuspec.ir.core as ir
from zuspec.ir.core.serializer import IRSerializer
from zuspec.ir.core.deserializer import IRDeserializer


def _c(v):
    return ir.ExprConstant(value=v)


def _prm_t():
    return ir.DataTypeStruct(name="defs::prm_t", super=None, packed=True, fields=[
        ir.Field(name="ADDR_W", datatype=ir.DataTypeInt(bits=32, signed=False)),
        ir.Field(name="ID_W", datatype=ir.DataTypeInt(bits=32, signed=False))])


def _defs():
    w1 = ir.Function(
        name="w1",
        args=ir.Arguments(args=[ir.Arg(arg="w", datatype=ir.DataTypeInt(bits=32, signed=False))]),
        returns=ir.DataTypeInt(bits=32, signed=False))
    return ir.Package(name="defs", items=[
        ir.PackageType(name="defs::prm_t"),
        ir.PackageConst(name="DEF", datatype=ir.DataTypeRef(ref_name="defs::prm_t"),
                        value=ir.ExprStructLiteral(fields=[
                            ir.ExprStructField(name="ADDR_W", value=_c(32)),
                            ir.ExprStructField(name="ID_W", value=_c(4))])),
        w1,
    ])


def _types_tmpl():
    """``class types #(prm_t P = DEF); typedef struct packed {
    logic [w1(P.ID_W)-1:0] id; } aw_t; endclass``"""
    width = ir.ExprCall(func=ir.ExprRefPackage(package="defs", name="w1"),
                        args=[ir.ExprAttribute(value=ir.ExprRefParam(name="P"), attr="ID_W")])
    aw_t = ir.DataTypeStruct(super=None, packed=True, fields=[
        ir.Field(name="id", datatype=ir.DataTypeInt(bits=4, signed=False, four_state=True,
                                                     width_expr=width))])
    body = ir.DataTypeClass(name="types", super=None,
                            typedefs=[ir.TypeDef(name="aw_t", datatype=aw_t)])
    return ir.DataTypeParameterized(name="types", body=body, template_params=[
        ir.TemplateParamValue(name="P", kind=ir.TemplateParamKind.VALUE,
                              value_type=ir.DataTypeRef(ref_name="defs::prm_t"),
                              default_value=ir.ExprRefPackage(package="defs", name="DEF"))])


def _api(tmpl):
    """``interface class api_if #(prm_t P = DEF); typedef types #(P)::aw_t aw_t;``"""
    spec = ir.DataTypeSpecialized(name="types", base_template=tmpl, template_args=[
        ir.TemplateArgValue(param_name="P", value_expr=ir.ExprRefParam(name="P"))])
    aw = ir.TypeDef(name="aw_t", datatype=ir.DataTypeMemberRef(base=spec, member="aw_t"))
    return ir.DataTypeProtocol(name="api_if", typedefs=[aw], methods=[
        ir.Function(name="put", is_async=True, args=ir.Arguments(args=[
            ir.Arg(arg="aw", datatype=ir.DataTypeRef(ref_name="aw_t"))]))])


def test_package_accessors():
    p = _defs()
    assert p.types == ["defs::prm_t"]
    assert [c.name for c in p.constants] == ["DEF"]
    assert [f.name for f in p.functions] == ["w1"]
    assert p.imports == []
    q = ir.Package(name="core", items=[ir.PackageImport(package="defs"),
                                        ir.PackageImport(package="x", name="y")])
    assert [(i.package, i.name) for i in q.imports] == [("defs", None), ("x", "y")]


def test_round_trip():
    tmpl = _types_tmpl()
    ctxt = ir.Context(
        type_m={"defs::prm_t": _prm_t(), "types": tmpl, "api_if": _api(tmpl)},
        packages={"defs": _defs(),
                  "core": ir.Package(name="core", items=[
                      ir.PackageImport(package="defs"),
                      ir.PackageType(name="core::types"),
                      ir.PackageType(name="core::api_if")])})
    text = IRSerializer().serialize(ctxt, None)
    d = IRDeserializer()
    d.auto_register(ir)
    back, _ = d.deserialize(text)
    assert back.type_m == ctxt.type_m
    assert back.packages == ctxt.packages
    assert back.packages["defs"].constants[0].value.fields[1].value == _c(4)
    td = back.type_m["types"].body.typedefs[0]
    assert td.name == "aw_t"
    w = td.datatype.fields[0].datatype.width_expr
    assert w.func == ir.ExprRefPackage(package="defs", name="w1")
    aw = back.type_m["api_if"].typedefs[0].datatype
    assert isinstance(aw, ir.DataTypeMemberRef) and aw.member == "aw_t"
    assert aw.base.template_args[0].value_expr == ir.ExprRefParam(name="P")
