"""Name-keyed external entries in the LoweringRegistry (register_external /
lookup), and plugin load failures surfacing at lookup."""
import pytest

from zuspec.ir.core import (LoweringRegistry, PluginLoadError, RecognitionError)
from zuspec.ir.core import registry as R


def _reg():
    r = LoweringRegistry()
    r.register_external("fw_arbiter", {"N": int, "POLICY": ("RR",), "T": str}, "xls",
                        lowering="arb", origin="test")
    return r


def test_lookup_returns_the_entry_and_checked_params():
    hit = _reg().lookup("fw_arbiter", {"N": 4, "POLICY": "RR", "T": "bit[7:0]"}, "xls")
    assert hit.lowering == "arb" and hit.params == {"N": 4, "POLICY": "RR", "T": "bit[7:0]"}


def test_lookup_misses_on_another_name_or_target():
    r = _reg()
    assert r.lookup("fw_other", {}, "xls") is None
    assert r.lookup("fw_arbiter", {"N": 4}, "native") is None


@pytest.mark.parametrize("params, msg", [
    ({"N": 4, "POLICY": "PRIO"}, "POLICY='PRIO' has no xls lowering (supported: RR)"),
    ({"N": "4"}, "N='4' is not a int"),
    ({"N": True}, "N=True is not a int"),
    ({"M": 1}, "parameter 'M' is not one its xls lowering takes"),
])
def test_lookup_rejects_params_the_entry_does_not_take(params, msg):
    with pytest.raises(RecognitionError, match=msg.replace("(", r"\(").replace(")", r"\)")):
        _reg().lookup("fw_arbiter", params, "xls")


def test_register_twice_is_an_error_unless_replaced():
    r = _reg()
    with pytest.raises(ValueError, match="already has a xls lowering .from test."):
        r.register_external("fw_arbiter", {}, "xls", lowering="b")
    r.register_external("fw_arbiter", {}, "xls", lowering="b", replace=True)
    assert r.lookup("fw_arbiter", {}, "xls").lowering == "b"


def test_unknown_target_is_rejected():
    with pytest.raises(ValueError, match="Unknown target"):
        LoweringRegistry().register_external("x", {}, "verilog", lowering=None)


def test_externals_lists_by_target():
    r = _reg()
    r.register_external("fw_reg_component", {}, "native", lowering="n")
    assert [e.name for e in r.externals("native")] == ["fw_reg_component"]
    assert [(e.name, e.target) for e in r.externals()] == [("fw_arbiter", "xls"),
                                                         ("fw_reg_component", "native")]


def test_a_failed_plugin_is_reported_by_lookup(monkeypatch):
    class EP:
        name = "broken"

        def load(self):
            raise ImportError("no module named broken_pkg")

    import importlib.metadata as meta
    monkeypatch.setattr(meta, "entry_points", lambda **kw: [EP()])
    r = _reg()
    R._load_plugins(r)                     # does not raise
    assert r.get_sv_model("anything") is None
    with pytest.raises(PluginLoadError, match="broken: ImportError: no module named broken_pkg"):
        r.lookup("fw_arbiter", {}, "xls")
