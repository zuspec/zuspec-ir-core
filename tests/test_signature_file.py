"""SIG-1 (fw-hdl xls-phase2.md): the JSON file form of BlockSignature."""
import json

import pytest

import zuspec.ir.core as ir


def _sig():
    return ir.BlockSignature(
        name="crc32_stream", engine="xls", sources=["crc32_stream.sv"],
        clock="clk", reset="rst", reset_active_low=False, latency=1,
        initiation_interval=1,
        channels=[ir.ChannelSignature(name="in", direction="in",
                                      payload=ir.DataTypeInt(bits=9, signed=False),
                                      ports={"data": "in", "valid": "in_vld", "ready": "in_rdy"},
                                      payload_sv_type="bit[8:0]"),
                  ir.ChannelSignature(name="out", direction="out",
                                      payload=ir.DataTypeInt(bits=32, signed=False),
                                      ports={"data": "out", "valid": "out_vld",
                                             "ready": "out_rdy"})])


def test_round_trip(tmp_path):
    sig = _sig()
    p = tmp_path / "crc.sig.json"
    ir.save_signature(sig, str(p))
    back = ir.load_signature(str(p))
    assert ir.signature_to_json(back) == ir.signature_to_json(sig)
    assert back.channel("in").payload.bits == 9 and back.channel("in").payload_sv_type == "bit[8:0]"
    assert back.sources == ["crc32_stream.sv"] and back.latency == 1
    assert json.loads(p.read_text())["format"] == "zuspec.block-signature"


def test_rejects_other_files_and_versions():
    d = ir.signature_to_json(_sig())
    with pytest.raises(ValueError, match="not a block signature"):
        ir.signature_from_json({**d, "format": "something"})
    with pytest.raises(ValueError, match="version"):
        ir.signature_from_json({**d, "version": 99})


def test_version_1_files_still_read():
    d = ir.signature_to_json(_sig())
    v1 = {**d, "version": 1,
          "channels": [{k: v for k, v in c.items() if k not in ("stage", "flop_kind")}
                       for c in d["channels"]]}
    back = ir.signature_from_json(v1)
    assert back.channel("in").stage is None and back.channel("out").flop_kind is None


def test_channel_stage_flop_and_struct_payload_round_trip():
    sig = _sig()
    sig.channels[0].stage, sig.channels[0].flop_kind = 0, "flop"
    sig.channels[1].stage, sig.channels[1].flop_kind = 2, "skid"
    sig.channels.append(ir.ChannelSignature(
        name="rec", direction="out", stage=1,
        payload=ir.DataTypeStruct(name="rec_t", super=None, fields=[
            ir.Field(name="symbol", datatype=ir.DataTypeInt(bits=32)),
            ir.Field(name="count", datatype=ir.DataTypeInt(bits=2)),
            ir.Field(name="mem", datatype=ir.DataTypeArray(
                size=4, element_type=ir.DataTypeInt(bits=8)))])))
    d = ir.signature_to_json(sig)
    assert d["version"] == 3
    back = ir.signature_from_json(d)
    assert ir.signature_to_json(back) == d
    assert back.channel("out").stage == 2 and back.channel("out").flop_kind == "skid"
    rec = back.channel("rec").payload
    assert [f.name for f in rec.fields] == ["symbol", "count", "mem"]
    assert rec.fields[2].datatype.size == 4


def _fn_sig():
    """A pipelined XLS function block, as gcd's codegen describes it."""
    u8 = ir.DataTypeInt(bits=8, signed=False)
    return ir.BlockSignature(
        name="gcd_pkg__gcd_binary8", kind="function", engine="xls",
        clock="clk", reset="rst", latency=2,
        data_ports=[ir.DataPortSignature(name="a", direction="in", type=u8, sv_type="logic [7:0]"),
                    ir.DataPortSignature(name="b", direction="in", type=u8),
                    ir.DataPortSignature(name="out", direction="out", type=u8)],
        valid_ports={"input_valid": "input_valid", "output_valid": "output_valid"})


def test_function_block_round_trip(tmp_path):
    sig = _fn_sig()
    p = tmp_path / "gcd.sig.json"
    ir.save_signature(sig, str(p))
    d = json.loads(p.read_text())
    assert d["version"] == 3 and d["kind"] == "function"
    back = ir.load_signature(str(p))
    assert ir.signature_to_json(back) == d
    assert [p.name for p in back.data_ports] == ["a", "b", "out"]
    assert back.data_port("a").sv_type == "logic [7:0]" and back.data_port("out").type.bits == 8
    assert back.valid_ports == {"input_valid": "input_valid", "output_valid": "output_valid"}
    assert back.channels == []


def test_version_2_files_read_as_procs():
    d = ir.signature_to_json(_sig())
    v2 = {k: v for k, v in d.items() if k not in ("kind", "data_ports", "valid_ports")}
    v2["version"] = 2
    back = ir.signature_from_json(v2)
    assert back.kind == "proc" and back.data_ports == [] and back.valid_ports == {}
    assert [c.name for c in back.channels] == ["in", "out"]


def test_rejects_unknown_kind():
    with pytest.raises(ValueError, match="kind"):
        ir.signature_from_json({**ir.signature_to_json(_sig()), "kind": "module"})
