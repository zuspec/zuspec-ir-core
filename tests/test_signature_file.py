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
