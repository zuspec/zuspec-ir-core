"""T3-1: IR unit tests for PSS-path coverage nodes."""
import pytest
from zuspec.ir.core.coverage import PssCoverGroup, PssCoverPoint, PssCoverCross


def test_pss_coverpoint_construction():
    pp = PssCoverPoint(name="x", target_expr=None)
    assert pp.name == "x"
    assert pp.target_expr is None
    assert pp.ignore_bin_exprs == []


def test_pss_covercross_construction():
    pc = PssCoverCross(name="x_y", coverpoint_names=["x", "y"])
    assert pc.name == "x_y"
    assert pc.coverpoint_names == ["x", "y"]


def test_pss_covergroup_construction():
    pp = PssCoverPoint(name="x", target_expr=None)
    pg = PssCoverGroup(instance_name="cg", coverpoints=[pp], crosses=[])
    assert pg.instance_name == "cg"
    assert len(pg.coverpoints) == 1
    assert pg.coverpoints[0] is pp
