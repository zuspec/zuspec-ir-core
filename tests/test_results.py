"""Facts, overlays, the ResultStore and the IR bundle (docs/results.md)."""
import time

import pytest

import zuspec.ir.core as ir
from zuspec.ir.core.anchor import assign_anchors, qualify
from zuspec.ir.core.results import (DERIVED, MERGED, ORPHAN, STALE, VALID, FactError,
                                    FactKind, Overlay, OverlayHeader, ResultStore, load_store,
                                    read_bundle, register, write_bundle)

from test_anchor import component

R = "rle_pkg.RunLengthEncoder"


def overlay_for(table, engine="xls"):
    o = Overlay(OverlayHeader(engine=engine, engine_version="0.59.0.1",
                              constraints={"clock_period_ps": 2000},
                              scope_hashes=dict(table.scope_hashes)))
    o.add("sched.stage_count", f"{R}.run", 2)
    o.add("sched.stage", f"{R}.run#s1.e2", 0)
    o.add("sched.stage", f"{R}.run#s2.orelse.s2.e2", 1, MERGED)
    o.add("timing.node_delay_ps", f"{R}.run#s1.e2", 412)
    o.add("rtl.register", qualify(f"{R}.prev_count", "reg", 1),
          {"module": "RunLengthEncoder", "name": "p1_prev_count", "width": 2, "stage": 1},
          DERIVED)
    o.add("xls.node_name", f"{R}.run#s1.e2", "add.17")
    return o


# -- kinds -------------------------------------------------------------------

@pytest.mark.parametrize("kind,value", [
    ("sched.stage", -1), ("sched.stage", "1"), ("sched.ii", {"value": 1}),
    ("rtl.signal", {"module": "m"}), ("iface.channel_flop", "latch"),
    ("timing.critical_path", {"stage": 0, "anchors": ["a"], "delay_ps": 3, "bogus": 1}),
])
def test_core_kinds_reject_bad_values(kind, value):
    o = Overlay(OverlayHeader(engine="t"))
    with pytest.raises(FactError):
        o.add(kind, "A", value)


def test_core_kinds_accept_good_values():
    o = Overlay(OverlayHeader(engine="t"))
    o.add("sched.ii", "A", {"value": 1, "reliable": False})
    o.add("timing.critical_path", "A", {"stage": 1, "anchors": ["A#s1.e1"], "delay_ps": 840})
    o.add("timing.critical_path", "B", {"stage": 1, "anchors": [], "delay_ps": 0, "slack_ps": -5})
    sig = ir.BlockSignature(name="b")
    o.add("iface.block", "A", ir.signature_to_json(sig))
    assert len(o) == 4


def test_unknown_core_kind_rejected_engine_extras_pass():
    o = Overlay(OverlayHeader(engine="t"))
    with pytest.raises(FactError, match="unknown core"):
        o.add("sched.bogus", "A", 1)
    with pytest.raises(FactError, match="namespace"):
        o.add("plain", "A", 1)
    with pytest.raises(FactError, match="quality"):
        o.add("xls.node_name", "A", "x", "approximately")
    o.add("xls.node_name", "A", {"anything": ["goes"]})


def test_registering_a_kind(monkeypatch):
    from zuspec.ir.core.results import kinds
    kinds.get("sched.stage")                                  # load the core kinds
    monkeypatch.setattr(kinds, "_registry", dict(kinds._registry))
    register([FactKind("area.estimate", lambda v: isinstance(v, (int, float)) and v >= 0)])
    o = Overlay(OverlayHeader(engine="t"))
    o.add("area.estimate", "A", 12.5)
    with pytest.raises(FactError):
        o.add("area.estimate", "A", -1)


# -- overlays ------------------------------------------------------------------

def test_overlay_round_trip(tmp_path):
    t = assign_anchors(component())
    o = overlay_for(t)
    p = tmp_path / o.filename
    o.save(str(p))
    back = Overlay.load(str(p))
    assert back.to_json() == o.to_json()
    assert back.header.constraints_hash == o.header.constraints_hash
    assert back.column("sched.stage")["quality"] == ["exact", "merged"]
    assert o.filename.startswith("xls-")


def test_overlay_rejects_bad_files():
    with pytest.raises(ValueError):
        Overlay.from_json({"format": "zuspec.anchor-table", "version": 1})
    o = overlay_for(assign_anchors(component())).to_json()
    o["facts"]["sched.stage"]["value"].pop()
    with pytest.raises(ValueError, match="different lengths"):
        Overlay.from_json(o)


def test_overlay_ids_differ_by_constraints():
    a = OverlayHeader(engine="xls", constraints={"stages": 2}, created="t0")
    b = OverlayHeader(engine="xls", constraints={"stages": 3}, created="t0")
    assert a.overlay_id != b.overlay_id


# -- the store -------------------------------------------------------------------

def test_store_lookup_by_anchor_and_node():
    comp = component()
    t = assign_anchors(comp)
    s = ResultStore(t)
    s.add(overlay_for(t))
    add_node = comp.proc_processes[0].body[0].value
    kinds = sorted(v.fact.kind for v in s.facts(add_node))
    assert kinds == ["sched.stage", "timing.node_delay_ps", "xls.node_name"]
    assert [v.fact.value for v in s.facts(add_node, kind="sched.stage")] == [0]
    assert all(v.status == VALID for v in s.facts(add_node))
    # Derived facts come with their base anchor, or alone when asked for.
    assert [v.fact.kind for v in s.facts(f"{R}.prev_count")] == ["rtl.register"]
    assert s.facts(f"{R}.prev_count", derived=False) == []
    assert len(s.facts(qualify(f"{R}.prev_count", "reg", 1))) == 1


def test_store_marks_stale_and_orphan_after_edit():
    t_old = assign_anchors(component())
    o = overlay_for(t_old)
    edited = component()
    run = edited.proc_processes[0]
    run.body[1].orelse.pop()                         # `run` changed; its last stmt is gone
    s = ResultStore(assign_anchors(edited))
    s.add(o)
    assert s.facts(f"{R}.run#s1.e2")[0].status == STALE
    gone = Overlay(OverlayHeader(engine="xls", scope_hashes=o.header.scope_hashes))
    gone.add("sched.stage", f"{R}.run#s2.orelse.s2.e2", 1)
    s.add(gone)
    views = s.facts(f"{R}.run#s2.orelse.s2.e2", overlay=gone)
    assert views[0].status == ORPHAN
    # prev_count did not change: its facts are still valid.
    assert s.facts(f"{R}.prev_count", overlay=o)[0].status == VALID


def test_two_overlays_side_by_side():
    t = assign_anchors(component())
    s = ResultStore(t)
    a = s.add(overlay_for(t, "xls"))
    b = Overlay(OverlayHeader(engine="native", scope_hashes=dict(t.scope_hashes)))
    b.add("sched.stage", f"{R}.run#s1.e2", 1)
    s.add(b)
    by_engine = {v.overlay.header.engine: v.fact.value
                 for v in s.facts(f"{R}.run#s1.e2", kind="sched.stage")}
    assert by_engine == {"xls": 0, "native": 1}
    assert [v.fact.value for v in s.facts(f"{R}.run#s1.e2", kind="sched.stage", overlay=a)] == [0]


# -- the bundle --------------------------------------------------------------------

def test_bundle_round_trip(tmp_path):
    t = assign_anchors(component())
    o = overlay_for(t)
    write_bundle(str(tmp_path / "zir"), t, [o])
    anchors, overlays, manifest = read_bundle(str(tmp_path / "zir"))
    assert anchors.to_json() == t.to_json()
    assert [x.to_json() for x in overlays] == [o.to_json()]
    assert manifest["model"] is None
    store = load_store(str(tmp_path / "zir"))
    assert store.facts(f"{R}.run#s1.e2", kind="sched.stage")[0].status == VALID


def test_bundle_keeps_earlier_overlays(tmp_path):
    t = assign_anchors(component())
    write_bundle(str(tmp_path / "zir"), t, [overlay_for(t, "xls")])
    write_bundle(str(tmp_path / "zir"), t, [overlay_for(t, "native")], model_yaml="x: 1\n")
    _, overlays, m = read_bundle(str(tmp_path / "zir"))
    assert sorted(o.header.engine for o in overlays) == ["native", "xls"]
    assert m["model"] == "model.yaml"


def test_overlay_scale(tmp_path):
    """100k facts write and load in well under a second (guards the columnar form)."""
    o = Overlay(OverlayHeader(engine="t"))
    for i in range(100_000):
        o.add("sched.stage", f"T.f#s{i}.e1", i % 7)
    p = tmp_path / "big.json"
    t0 = time.perf_counter()
    o.save(str(p))
    back = Overlay.load(str(p))
    assert len(back) == 100_000
    assert time.perf_counter() - t0 < 2.0
