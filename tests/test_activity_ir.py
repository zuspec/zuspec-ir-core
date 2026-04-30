"""Phase 3 IR tests: ActivityFill, ActivityChain, ActivityConstraintForall,
ActivitySelect.allow_none."""
import pytest
from zuspec.ir.core.activity import (
    ActivityFill,
    ActivityChain,
    ActivityConstraintForall,
    ActivitySelect,
    SelectBranch,
)
from zuspec.ir.core.expr import ExprConstant


def test_activity_fill_defaults():
    fill = ActivityFill(body=[], max_iters=1000)
    assert fill.max_iters == 1000
    assert fill.body == []


def test_activity_fill_custom_iters():
    fill = ActivityFill(body=[], max_iters=42)
    assert fill.max_iters == 42


def test_activity_chain_defaults():
    chain = ActivityChain(stmts=[])
    assert chain.label is None
    assert chain.stmts == []


def test_activity_chain_with_label():
    chain = ActivityChain(label="my_chain", stmts=[])
    assert chain.label == "my_chain"


def test_activity_constraint_forall():
    forall = ActivityConstraintForall(var_name="v", type_name="my_t")
    assert forall.var_name == "v"
    assert forall.type_name == "my_t"
    assert forall.body_exprs == []


def test_activity_select_allow_none_default():
    """allow_none defaults to False for backward compatibility."""
    sel = ActivitySelect(branches=[])
    assert sel.allow_none is False


def test_activity_select_allow_none_set():
    sel = ActivitySelect(branches=[], allow_none=True)
    assert sel.allow_none is True


def test_activity_fill_in_all():
    """ActivityFill is in __all__."""
    from zuspec.ir.core import activity
    assert "ActivityFill" in activity.__all__


def test_activity_chain_in_all():
    from zuspec.ir.core import activity
    assert "ActivityChain" in activity.__all__


def test_activity_constraint_forall_in_all():
    from zuspec.ir.core import activity
    assert "ActivityConstraintForall" in activity.__all__
