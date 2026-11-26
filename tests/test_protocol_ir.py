"""Protocol IR (Z7, fw-hdl protocol-kit-plan §8.11): APIs, protocol hubs,
converters, bundle protocols, endpoints, API and configuration
connections."""
import dataclasses as dc

import pytest

import zuspec.ir.core as ir
from zuspec.ir.core.serializer import IRSerializer
from zuspec.ir.core.deserializer import IRDeserializer


def _c(v):
    return ir.ExprConstant(value=v)


def _apis():
    put = ir.DataTypeProtocol(name="put_if", methods=[ir.Function(name="put", is_async=True)])
    get = ir.DataTypeProtocol(name="get_if", methods=[ir.Function(name="get", is_async=True)])
    return put, get


def _hub():
    return ir.DataTypeProtocolHub(
        name="vr_proto",
        template_params=[ir.TemplateParam(name="W", kind=ir.TemplateParamKind.VALUE)],
        apis=[ir.ProtocolApi(api="put_if", dir=ir.ProtoDir.REQ),
              ir.ProtocolApi(api="get_if", dir=ir.ProtoDir.RSP)])


def _ref(w=32):
    return ir.ProtocolRef(family="vr_proto", params={"W": _c(w)})


def _bundle():
    """A bundle class of the protocol, and a transaction class that
    implements both APIs."""
    b = ir.DataTypeClass(name="vr_bundle", super=None, fields=[
        ir.Field(name="valid", datatype=ir.DataTypeInt(bits=1, signed=False))],
        bundle_protocol=_ref())
    t = ir.DataTypeClass(name="vr_imp", super=None, fields=[],
                         implements=["put_if", "get_if"])
    return b, t


def _generic_bundle():
    """A generic bundle type whose protocol is written over its parameter."""
    return ir.DataTypeBundle(
        name="vr_if", super=None, fields=[],
        template_params=[ir.TemplateParam(name="W", kind=ir.TemplateParamKind.VALUE)],
        bundle_protocol=ir.ProtocolRef(family="vr_proto", params={"W": ir.ExprRefParam(name="W")}))


def _xtor():
    return ir.DataTypeComponent(
        name="vr_driver", super=None, fields=[],
        converter=ir.ProtocolConverter(from_proto=_ref(), to_proto=_ref(), role="initiator"),
        bundle_protocols=[ir.BundleProtocol(target=["bus"], protocol=_ref(), role="mirror")],
        api_endpoints=[ir.ApiEndpoint(name="req", kind="export", api="put_if"),
                       ir.ApiEndpoint(name="sock", kind="target_socket", protocol=_ref(),
                                      imp=["req"])],
        cfg_consumers=[ir.CfgConsumer(name="cfg", cfg_type=ir.DataTypeRef(ref_name="vr_cfg_t"))])


def _abstractor():
    return ir.DataTypeComponent(
        name="vr_to_ax", super=None, fields=[],
        converter=ir.ProtocolConverter(from_proto=_ref(), to_proto=ir.ProtocolRef(family="ax_proto")))


def _top():
    i = ir.ExprRefLocal(name="i")
    return ir.DataTypeComponent(
        name="top", super=None,
        fields=[ir.Field(name="drv", datatype=ir.DataTypeRef(ref_name="vr_driver")),
                ir.Field(name="seq", datatype=ir.DataTypeRef(ref_name="seq_t"))],
        bundle_protocols=[ir.BundleProtocol(
            target=[ir.ExprHierarchicalElem(name="ch", subscript=i)], protocol=_ref(8), role="bundle")],
        api_endpoints=[ir.ApiEndpoint(name="out", kind="port", api="put_if",
                                      api_params={"W": ir.ExprConstant(value=8)},
                                      api_scope="vr_pkg")],
        api_connections=[ir.ApiConnection(src=["seq", "out"], dst=["drv", "req"]),
                         ir.ApiConnection(kind="connect_up", src=["out"], dst=["seq", "out"])],
        cfg_publishers=[ir.CfgPublisher(name="cfg", cfg_type=ir.DataTypeRef(ref_name="vr_cfg_t"))],
        cfg_connections=[ir.CfgConnection(publisher=["cfg"], consumer=["drv", "cfg"]),
                         ir.CfgConnection(publisher=["cfg"], consumer=["mon", "cfg"], derived=True)],
        cfg_listeners=[ir.CfgListener(sink=["drv", "cfg"], listener=["drv"]),
                       ir.CfgListener(sink=["mon", "cfg"], listener=None)])


def test_defaults():
    c = ir.DataTypeComponent(name="c", super=None, fields=[])
    assert c.converter is None and c.implements == [] and c.bundle_protocol is None
    assert ir.DataTypeBundle(name="b", super=None, fields=[]).bundle_protocol is None
    assert (c.bundle_protocols, c.api_endpoints, c.api_connections,
            c.cfg_publishers, c.cfg_consumers, c.cfg_connections, c.cfg_listeners) == ([],) * 7
    assert ir.CfgListener().listener == []
    h = ir.DataTypeProtocolHub(name="p")
    assert h.apis == [] and h.template_params == []
    e = ir.ApiEndpoint(name="e", kind="port")
    assert e.api is None and e.protocol is None and e.imp == []
    assert e.api_params == {} and e.api_scope is None
    assert ir.ApiConnection().kind == "connect"
    assert ir.ProtocolConverter(from_proto=_ref(), to_proto=_ref()).role == ""
    assert isinstance(_ref(), ir.Expr) and isinstance(_hub(), ir.DataType)


def test_every_shape_round_trips():
    put, get = _apis()
    b, t = _bundle()
    ctxt = ir.Context(type_m={
        "put_if": put, "get_if": get, "vr_proto": _hub(), "vr_bundle": b, "vr_imp": t,
        "vr_driver": _xtor(), "vr_to_ax": _abstractor(), "top": _top(),
        "vr_if": _generic_bundle()})
    text = IRSerializer().serialize(ctxt, None)
    d = IRDeserializer()
    d.auto_register(ir)
    back, _ = d.deserialize(text)
    assert back.type_m == ctxt.type_m

    m = back.type_m
    h = m["vr_proto"]
    assert isinstance(h, ir.DataTypeProtocolHub)
    assert [a.dir for a in h.apis] == [ir.ProtoDir.REQ, ir.ProtoDir.RSP]
    assert h.template_params[0].name == "W"
    assert m["vr_bundle"].bundle_protocol.params["W"].value == 32
    assert m["vr_imp"].implements == ["put_if", "get_if"]
    assert m["vr_if"].bundle_protocol.params["W"] == ir.ExprRefParam(name="W")
    x = m["vr_driver"]
    assert x.converter.from_proto == x.converter.to_proto and x.converter.role == "initiator"
    assert x.api_endpoints[1].protocol.family == "vr_proto" and x.api_endpoints[1].imp == ["req"]
    assert m["vr_to_ax"].converter.to_proto.family == "ax_proto"
    top = m["top"]
    assert isinstance(top.bundle_protocols[0].target[0], ir.ExprHierarchicalElem)
    assert [c.kind for c in top.api_connections] == ["connect", "connect_up"]
    assert [c.derived for c in top.cfg_connections] == [False, True]
    assert [x.listener for x in top.cfg_listeners] == [["drv"], None]


def test_hub_has_no_cfg_or_timing():
    """Configuration belongs to endpoints and timing to channels
    (fw-hdl semantics/protocols.md), so a hub carries neither."""
    names = {f.name for f in dc.fields(ir.DataTypeProtocolHub)}
    assert not names & {"cfg_type", "functions", "xfer_time", "mismatch"}
    with pytest.raises(TypeError):
        ir.DataTypeProtocolHub(name="p", cfg_type=None)
