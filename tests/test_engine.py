"""The engine interface (zuspec.ir.core.engine), with a fake in-process engine.

The fake is also the worked example of writing an adapter: build RTL, map the
engine's results onto the anchors, return them as an overlay.
"""
import os
from typing import Optional, Sequence

import pytest

import zuspec.ir.core as ir
from zuspec.ir.core.anchor import AnchorTable, assign_anchors
from zuspec.ir.core.engine import (BuildResult, Constraints, Engine, Reason, Verdict,
                                   engines, get_engine, register_engine)
from zuspec.ir.core.results import Overlay, OverlayHeader, ResultStore, VALID

from test_anchor import component

R = "rle_pkg.RunLengthEncoder"


class FakeEngine:
    """Puts every statement of every process in stage 0 and writes an empty module."""
    name = "fake"

    def version(self) -> Optional[str]:
        return "1.0"

    def accepts(self, roots: Sequence[ir.Base], top: str) -> Verdict:
        comp = next((r for r in roots if getattr(r, "name", None) == top), None)
        if not isinstance(comp, ir.DataTypeComponent):
            return Verdict(False, [Reason(f"{top}: only components are supported")])
        return Verdict(True)

    def build(self, roots, top, anchors: AnchorTable, constraints: Constraints,
              workdir: str) -> BuildResult:
        module = constraints.module_name or top.replace("::", "__")
        rtl = os.path.join(workdir, f"{module}.sv")
        with open(rtl, "w") as f:
            f.write(f"module {module}; endmodule\n")
        o = Overlay(OverlayHeader(engine=self.name, engine_version=self.version(),
                                  constraints=constraints.to_json(),
                                  scope_hashes=dict(anchors.scope_hashes),
                                  artifacts={"verilog": rtl}))
        for a, e in anchors.entries.items():
            if e.kind == "stmt":
                o.add("sched.stage", a, 0)
        return BuildResult(rtl=[rtl], top_module=module, overlay=o)


def test_fake_engine_satisfies_the_protocol():
    assert isinstance(FakeEngine(), Engine)


def test_registry_and_build(tmp_path):
    register_engine("fake", FakeEngine)
    assert "fake" in engines()
    eng = get_engine("fake")
    comp = component()
    t = assign_anchors(comp)
    assert eng.accepts([comp], comp.name).ok
    res = eng.build([comp], comp.name, t, Constraints(pipeline_stages=2), str(tmp_path))
    assert os.path.exists(res.rtl[0]) and res.top_module == "rle_pkg__RunLengthEncoder"
    assert res.overlay.header.constraints == {"pipeline_stages": 2}
    s = ResultStore(t)
    s.add(res.overlay)
    views = s.facts(comp.proc_processes[0].body[0], kind="sched.stage")
    assert [(v.fact.value, v.status) for v in views] == [(0, VALID)]


def test_rejections_carry_reasons():
    fn = ir.Function(name="f")
    v = FakeEngine().accepts([fn], "f")
    assert not v.ok and "only components" in v.reasons[0].message


def test_unknown_engine():
    with pytest.raises(KeyError, match="no synthesis engine"):
        get_engine("no-such-engine")


def test_constraints_json_drops_unset_and_merges_extra():
    c = Constraints(clock_period_ps=1000, extra={"delay_model": "unit"})
    assert c.to_json() == {"clock_period_ps": 1000, "delay_model": "unit"}
