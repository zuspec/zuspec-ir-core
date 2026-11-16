"""Domain IR (Z6, fw-hdl core-domains-plan §6.2): kinds, domain objects,
associations, drivers, waivers and extern domain pins."""
import zuspec.ir.core as ir
from zuspec.ir.core.serializer import IRSerializer
from zuspec.ir.core.deserializer import IRDeserializer


def _c(v, width=None):
    return ir.ExprConstant(value=v) if width is None else ir.ExprConstant(value=v, width=width)


def _kinds():
    S = ir.DomainSeverity
    clock = ir.DataTypeDomainKind(
        name="fw_clock_kind",
        derivations={"root": ir.DomainDerivDecl(),
                     "derived": ir.DomainDerivDecl(sources=["from"])},
        severities={"same": S.NONE, "related": S.INFO, "unrelated": S.ERROR},
        port_names={"": "clk", "wr": "wr_clk"})
    reset = ir.DataTypeDomainKind(
        name="fw_reset_kind",
        # synced(.from(por), .clock(div2)): por is a source, the clock is not
        derivations={"root": ir.DomainDerivDecl(),
                     "synced": ir.DomainDerivDecl(sources=["from"])},
        severities={"same": S.NONE, "related": S.WARNING, "unrelated": S.ERROR},
        port_names={ir.port_name_key("", {"active_low": _c(1, 1)}): "rst_n"},
        pin_default={"active_low": _c(0, 1)}, polarity="active_low")
    macro = ir.DataTypeDomainKind(name="macro_kind", physical=False,
                                  severities={"unrelated": S.INFO})
    return clock, reset, macro


def _top():
    """A component with every shape: a root and a derived clock, a synced
    reset naming a clock of another kind, default, named and interface
    associations (one indexed, in a generate loop), a driver, a waiver, and
    a resolution reached through an interface."""
    i = ir.ExprRefLocal(name="i")
    clk = ir.DomainRef(decl="clk")
    div2 = ir.DomainRef(decl="div2")
    por = ir.DomainRef(kind="fw_reset_kind")             # get(this)
    top = ir.DataTypeComponent(name="top", super=None, fields=[
        ir.FieldInOut(name="clk_i", datatype=ir.DataTypeInt(bits=1, signed=False), is_out=False),
        ir.Field(name="u0", datatype=ir.DataTypeRef(ref_name="fifo")),
        ir.Field(name="h", datatype=ir.DataTypeDomain(kind="fw_clock_kind")),
    ])
    top.domains = [
        ir.DomainDecl(name="clk", kind="fw_clock_kind",
                      params={"period_ps": _c(10000)}),
        ir.DomainDecl(name="div2", kind="fw_clock_kind", deriv="derived", sources=[clk],
                      args={"from": clk, "div": _c(2)},
                      params={"from": clk, "div": _c(2)}),
        ir.DomainDecl(name="rst", kind="fw_reset_kind", deriv="synced", sources=[por],
                      args={"from": por, "clock": div2}),
    ]
    top.domain_assocs = [
        ir.DomainAssoc(kind="fw_clock_kind", domain=clk),                       # set(this, clk)
        ir.DomainAssoc(target=["u0"], kind="fw_clock_kind", assoc="wr", domain=div2),
        ir.DomainAssoc(target=["u0", "in"], kind="fw_reset_kind",
                       domain=ir.DomainRef(decl="rst")),
    ]
    top.domain_drivers = [
        ir.DomainDriver(domain=clk, signal=["clk_i"]),
        ir.DomainDriver(domain=ir.DomainRef(decl="rst"), signal=["rst_ni"],
                        attr={"active_low": _c(1, 1)}),
    ]
    top.domain_waivers = [
        ir.DomainWaiver(kind="fw_clock_kind", a=["u0", "out"], b=["u1", "in"],
                        reason="gray-coded pointer")]
    top.domain_port_names = [ir.DomainPortName(kind="fw_clock_kind", assoc="wr", port="wck")]
    top.domain_provides = [ir.DomainProvides(
        kind="fw_clock_kind", assoc="out", deriv="derived",
        sources=[ir.DomainRef(kind="fw_clock_kind")], params={"div": _c(2)})]
    top.generates = [ir.GenerateFor(
        var="i", start=_c(0),
        cond=ir.ExprCompare(left=i, ops=[ir.CmpOp.Lt], comparators=[ir.ExprRefParam(name="n")]),
        label="g_1",
        items=[ir.DomainAssoc(target=[ir.ExprHierarchicalElem(name="ch", subscript=i)],
                              kind="fw_clock_kind",
                              domain=ir.DomainRef(kind="fw_clock_kind", target=["in"]))])]
    return top


def _ext():
    return ir.DataTypeExtern(name="fifo", super=None, extern_name="async_fifo", pins=[
        ir.ExternPin(name="wclk", domain_kind="fw_clock_kind",
                     domain=ir.DomainRef(kind="fw_clock_kind", assoc="wr")),
        ir.ExternPin(name="rclk", domain_kind="fw_clock_kind"),         # the default
        ir.ExternPin(name="rst_n", domain_kind="fw_reset_kind",
                     domain_attr={"active_low": _c(1, 1), "style": _c("async")}),
        ir.ExternPin(name="clk_o", domain_kind="fw_clock_kind", output=True,
                     domain=ir.DomainRef(decl="gen")),
        ir.ExternPin(name="d", path=["in", "data"]),
    ])


def test_defaults():
    k = ir.DataTypeDomainKind(name="k")
    assert k.physical and k.derivations == {} and k.severities == {}
    d = ir.DomainDecl(name="d", kind="k")
    assert d.deriv == "root" and d.sources == [] and d.params == {}
    r = ir.DomainRef(kind="k")
    assert r.decl is None and r.assoc == "" and r.target == []
    assert isinstance(r, ir.Expr)
    c = ir.DataTypeComponent(name="c", super=None)
    assert (c.domains, c.domain_assocs, c.domain_drivers, c.domain_waivers) == ([], [], [], [])
    p = ir.ExternPin(name="clk", path=["clk"])
    assert p.domain is None and p.domain_kind is None and p.domain_attr == {} and not p.output


def test_kinds_are_instances():
    clock, reset, macro = _kinds()
    assert all(isinstance(k, ir.DataType) for k in (clock, reset, macro))
    assert clock.derivations["derived"].sources == ["from"]
    assert reset.severities["related"] is ir.DomainSeverity.WARNING
    assert not macro.physical


def test_every_shape_round_trips():
    clock, reset, macro = _kinds()
    top, ext = _top(), _ext()
    ctxt = ir.Context(type_m={"fw_clock_kind": clock, "fw_reset_kind": reset,
                              "macro_kind": macro, "fifo": ext, "top": top})
    text = IRSerializer().serialize(ctxt, None)
    d = IRDeserializer()
    d.auto_register(ir)
    back, _ = d.deserialize(text)
    assert back.type_m == ctxt.type_m

    b = back.type_m
    assert b["fw_clock_kind"].severities["unrelated"] is ir.DomainSeverity.ERROR
    assert b["fw_clock_kind"].port_names[""] == "clk"
    assert isinstance(b["fw_clock_kind"].derivations["derived"], ir.DomainDerivDecl)
    assert b["macro_kind"].physical is False
    t = b["top"]
    assert isinstance(t.fields[2].datatype, ir.DataTypeDomain)
    assert t.fields[2].datatype.kind == "fw_clock_kind"
    assert [x.name for x in t.domains] == ["clk", "div2", "rst"]
    assert t.domains[1].sources[0].decl == "clk" and t.domains[1].args["div"].value == 2
    rst = t.domains[2]
    assert rst.sources[0].kind == "fw_reset_kind" and rst.sources[0].decl is None
    assert isinstance(rst.args["clock"], ir.DomainRef) and rst.args["clock"].decl == "div2"
    assert t.domain_assocs[0].target == [] and t.domain_assocs[0].assoc == ""
    assert t.domain_assocs[1].assoc == "wr" and t.domain_assocs[2].target == ["u0", "in"]
    assert t.domain_drivers[1].attr["active_low"].value == 1
    assert t.domain_waivers[0].reason == "gray-coded pointer"
    assert t.domain_port_names[0].port == "wck"
    assert t.domain_provides[0].sources[0].kind == "fw_clock_kind"
    assert t.domain_provides[0].params["div"].value == 2
    assert b["fw_reset_kind"].port_names["/active_low=1"] == "rst_n"
    assert b["fw_reset_kind"].pin_default["active_low"].value == 0
    assert b["fw_reset_kind"].polarity == "active_low"
    ga = t.generates[0].items[0]
    assert isinstance(ga, ir.DomainAssoc)
    assert ga.target[0].name == "ch" and ga.target[0].subscript.name == "i"
    assert ga.domain.target == ["in"]
    pins = b["fifo"].pins
    assert pins[0].domain.assoc == "wr" and pins[1].domain is None
    assert pins[2].domain_attr["style"].value == "async"
    assert pins[3].output and pins[3].domain.decl == "gen"
    assert pins[4].domain_kind is None and pins[4].path == ["in", "data"]


def test_source_domain_objects_not_serialized():
    """``clock_domain``/``reset_domain`` hold a front end's own objects:
    they are not written, and the component reads back without them."""
    class Src:
        name = "sys"
    top = _top()
    top.clock_domain = Src()
    ctxt = ir.Context(type_m={"top": top})
    text = IRSerializer().serialize(ctxt, None)
    assert "clock_domain" not in text and "reset_domain" not in text
    d = IRDeserializer()
    d.auto_register(ir)
    back, _ = d.deserialize(text)
    t = back.type_m["top"]
    assert t.clock_domain is None and [x.name for x in t.domains] == ["clk", "div2", "rst"]


def _anchors(ctxt):
    t = ir.assign_anchors(ctxt)
    return list(t)


def test_anchors_of_domain_shapes():
    clock, reset, _ = _kinds()
    ctxt = ir.Context(type_m={"fw_clock_kind": clock, "fw_reset_kind": reset,
                              "fifo": _ext(), "top": _top()})
    ir.assign_anchors(ctxt)
    t = ctxt.type_m["top"]
    # Domain objects are members, in the component's name space.
    assert [d.anchor for d in t.domains] == ["top.clk", "top.div2", "top.rst"]
    # Associations, drivers and waivers are statements of the component,
    # not declarations: a named association claims no member anchor.
    assert all(a.anchor is None for a in t.domain_assocs)
    assert "top.wr" not in [e for e in _anchors(ctxt)]
