"""IR unit tests for the coverage model (pure data: expressions, no callables)."""
import dataclasses
import pickle

import zuspec.ir.core as ir
from zuspec.ir.core.coverage import (
    BinDef, BinKind, CovergroupDef, CovergroupParam, CoverpointDef,
    CrossBinDef, CrossDef,
)


def _self_ref(name):
    return ir.ExprAttribute(value=ir.TypeExprRefSelf(), attr=name)


def _cg():
    lo = ir.ExprConstant(value=0)
    hi = ir.ExprConstant(value=3)
    cp_x = CoverpointDef(
        name="x", target=_self_ref("x"),
        iff=_self_ref("en"),
        bins=[BinDef(name="low", values=[ir.ExprRange(lower=lo, upper=hi)]),
              BinDef(name="big", values=[ir.ExprConstant(value=15)],
                     kind=BinKind.ILLEGAL),
              BinDef(name="each", values=[ir.ExprRange(lower=lo, upper=hi)],
                     array=True, array_size=ir.ExprConstant(value=4))])
    cp_y = CoverpointDef(name="y", target=_self_ref("y"))
    cx = CrossDef(name="x_y", coverpoints=["x", "y"],
                  bins=[CrossBinDef(name="skip", kind=BinKind.IGNORE)])
    return CovergroupDef(name="cg", instance_name="cg", parent="pkg::A",
                         coverpoints=[cp_x, cp_y], crosses=[cx])


def test_inline_covergroup_shape():
    cg = _cg()
    assert cg.inline
    assert [cp.name for cp in cg.coverpoints] == ["x", "y"]
    assert cg.crosses[0].coverpoints == ["x", "y"]
    assert cg.options.at_least == 1 and cg.type_options.weight == 1


def test_auto_bins_follow_declared_bins():
    cg = _cg()
    assert not cg.coverpoints[0].auto_bins
    assert cg.coverpoints[1].auto_bins
    # an ignore-only cross still has automatic bins
    assert cg.crosses[0].auto_bins
    assert CoverpointDef(
        name="z", bins=[BinDef(name="i", kind=BinKind.IGNORE)]).auto_bins


def test_covergroup_type_with_params():
    cg = CovergroupDef(name="cg_t", params=[CovergroupParam(name="a")],
                       coverpoints=[CoverpointDef(
                           name="a", target=ir.ExprRefParam(name="a", index=0))])
    assert not cg.inline
    assert cg.params[0].name == "a"


def test_model_is_pure_data():
    """No field anywhere in the model holds a callable; the model pickles."""
    def walk(obj):
        assert not callable(obj) or isinstance(obj, type), obj
        if dataclasses.is_dataclass(obj):
            for f in dataclasses.fields(obj):
                walk(getattr(obj, f.name))
        elif isinstance(obj, (list, tuple)):
            for o in obj:
                walk(o)
    cg = _cg()
    walk(cg)
    assert pickle.loads(pickle.dumps(cg)) == cg
