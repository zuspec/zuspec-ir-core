"""Interface facts (latency, rate, capacity, paths, area) and overlay
provenance: what a block's boundary is known to do, and who says so."""
import pytest

import zuspec.ir.core as ir
from zuspec.ir.core.anchor import assign_anchors
from zuspec.ir.core.results import (FactError, Overlay, OverlayHeader, PROVENANCES,
                                    ResultStore, rank)

B = "q_rtl"


def table():
    u8 = ir.DataTypeInt(bits=8, signed=False)
    comp = ir.DataTypeComponent(name=B, super=None, fields=[
        ir.Field(name="in", datatype=ir.DataTypeGetIF(element_type=u8)),
        ir.Field(name="out", datatype=ir.DataTypePutIF(element_type=u8))])
    return assign_anchors([comp])


def ov(t, provenance, facts):
    o = Overlay(OverlayHeader(engine="probe", provenance=provenance,
                              scope_hashes=dict(t.scope_hashes)))
    for kind, value in facts:
        o.add(kind, B, value)
    return o


@pytest.mark.parametrize("kind,value", [
    ("iface.latency", {"src": "in", "dst": "out", "fixed": 1}),
    ("iface.latency", {"src": "in", "dst": "out", "min": 3, "max": 40}),
    ("iface.latency", {"src": "in", "dst": "out", "min": 3}),
    ("iface.ii", {"port": "in", "n": 1}),
    ("iface.throughput", {"port": "in", "k": 2, "n": 3}),
    ("iface.capacity", {"src": "in", "dst": "out", "n": 4}),
    ("iface.comb_path", {"src": "in", "dst": "out"}),
    ("iface.stall_coupling", {"a": "in", "b": "out"}),
    ("iface.registered", {"port": "out"}),
    ("timing.io_delay_ps", {"port": "in", "ps": 120}),
    ("area.estimate", {"value": 18500, "unit": "um2", "source": "datasheet"}),
    ("area.estimate", {"value": 31.5, "unit": "luts"}),
])
def test_good_values(kind, value):
    ov(table(), "declared", [(kind, value)])


@pytest.mark.parametrize("kind,value", [
    ("iface.latency", {"src": "in", "dst": "out"}),                 # no cycles
    ("iface.latency", {"src": "in", "dst": "out", "fixed": -1}),
    ("iface.latency", {"src": "in", "fixed": 1}),                   # no dst
    ("iface.ii", {"port": "in", "n": 0}),
    ("iface.capacity", {"src": "in", "dst": "out", "n": "4"}),
    ("area.estimate", {"value": 1, "unit": "bananas"}),
])
def test_bad_values(kind, value):
    with pytest.raises(FactError):
        ov(table(), "declared", [(kind, value)])


def test_provenance_default_rank_and_round_trip(tmp_path):
    assert OverlayHeader(engine="xls").provenance == "engine"
    assert sorted(PROVENANCES, key=rank) == ["engine", "generator", "measured", "declared"]
    t = table()
    o = ov(t, "measured", [("iface.ii", {"port": "in", "n": 1})])
    o.save(str(tmp_path / "o.json"))
    assert Overlay.load(str(tmp_path / "o.json")).header.provenance == "measured"
    with pytest.raises(ValueError):
        OverlayHeader(engine="x", provenance="rumour")


def test_conflicts():
    t = table()
    s = ResultStore(t)
    lat = lambda **v: ("iface.latency", {"src": "in", "dst": "out", **v})  # noqa: E731
    s.add(ov(t, "declared", [lat(min=1, max=4), ("iface.ii", {"port": "in", "n": 1}),
                             ("iface.capacity", {"src": "in", "dst": "out", "n": 2})]))
    s.add(ov(t, "measured", [lat(min=1, max=2), ("iface.ii", {"port": "in", "n": 2})]))
    s.add(ov(t, "engine", [lat(fixed=5)]))
    found = s.conflicts()
    got = sorted((c.kind, c.higher.overlay.header.provenance, c.lower.overlay.header.provenance)
                 for c in found)
    # measured 1..2 lies inside declared 1..4 (fine); engine 5 lies outside both;
    # measured ii 2 differs from declared ii 1.
    assert got == [("iface.ii", "measured", "declared"),
                   ("iface.latency", "engine", "declared"),
                   ("iface.latency", "engine", "measured")]
    assert all(c.anchor == B for c in found)
    assert "engine" in str(found[0]) or "measured" in str(found[0])
    # Facts on different port pairs never conflict.
    s2 = ResultStore(t)
    s2.add(ov(t, "declared", [("iface.latency", {"src": "in", "dst": "out", "fixed": 1})]))
    s2.add(ov(t, "measured", [("iface.latency", {"src": "out", "dst": "in", "fixed": 3})]))
    assert s2.conflicts() == []
