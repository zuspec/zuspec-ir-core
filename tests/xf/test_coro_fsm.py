"""Phase-4 golden test: CoroutineFSMPass block decomposition.

Checks that a coroutine body splits into the expected ``idx`` blocks at suspend
points, that a no-suspend body stays a single block, and that a suspend inside
control flow is rejected (Phase 5).
"""
import pytest

import zuspec.ir.core as ir
from zuspec.ir.core.xf import CoroutineFSMPass, UnsupportedConstructError
from zuspec.ir.core.expr import ExprConstant


def test_no_suspend_single_block():
    coro = ir.ScCoroutine(name="atomic", body=[
        ir.ScExecBlock(kind="body"),
        ir.ScInvoke(target="sub"),     # non-blocking by default
        ir.ScExecBlock(kind="body"),
    ])
    fsm = CoroutineFSMPass().run(coro)
    assert fsm.n_blocks == 1
    assert not fsm.suspends
    assert len(fsm.blocks[0].stmts) == 3
    assert fsm.blocks[0].suspend is None


def test_splits_at_wait():
    coro = ir.ScCoroutine(name="timed", body=[
        ir.ScExecBlock(kind="body"),
        ir.ScWait(time=ExprConstant(value=10)),
        ir.ScExecBlock(kind="body"),
        ir.ScWait(time=ExprConstant(value=5)),
        ir.ScExecBlock(kind="body"),
    ])
    fsm = CoroutineFSMPass().run(coro)
    assert fsm.n_blocks == 3
    assert [b.idx for b in fsm.blocks] == [0, 1, 2]
    # Each non-final block ends at a wait.
    assert isinstance(fsm.blocks[0].suspend, ir.ScWait)
    assert isinstance(fsm.blocks[1].suspend, ir.ScWait)
    assert fsm.blocks[2].suspend is None
    # The exec block before each wait is in that block.
    assert len(fsm.blocks[0].stmts) == 1


def test_par_is_suspend():
    coro = ir.ScCoroutine(name="forker", body=[
        ir.ScPar(branches=[ir.ScInvoke(target="a"), ir.ScInvoke(target="b")]),
        ir.ScExecBlock(),   # continuation after join
    ])
    fsm = CoroutineFSMPass().run(coro)
    assert fsm.n_blocks == 2
    assert isinstance(fsm.blocks[0].suspend, ir.ScPar)
    assert fsm.blocks[1].suspend is None


def test_blocking_invoke_is_suspend():
    coro = ir.ScCoroutine(name="caller", body=[
        ir.ScInvoke(target="blocker"),
        ir.ScExecBlock(),
    ])
    fsm = CoroutineFSMPass(blocking_targets=["blocker"]).run(coro)
    assert fsm.n_blocks == 2
    assert isinstance(fsm.blocks[0].suspend, ir.ScInvoke)


def test_seq_is_flattened():
    coro = ir.ScCoroutine(name="seq", body=[
        ir.ScSeq(body=[ir.ScExecBlock(), ir.ScWait(time=ExprConstant(value=1))]),
        ir.ScExecBlock(),
    ])
    fsm = CoroutineFSMPass().run(coro)
    assert fsm.n_blocks == 2


def test_suspend_inside_loop_rejected():
    coro = ir.ScCoroutine(name="bad", body=[
        ir.ScLoop(kind="repeat", count=ExprConstant(value=3),
                  body=[ir.ScWait(time=ExprConstant(value=1))]),
    ])
    with pytest.raises(UnsupportedConstructError):
        CoroutineFSMPass().run(coro)


def test_loop_index_becomes_frame_local_when_spanning_suspend():
    # A loop with no internal suspend stays opaque, but a following wait splits
    # blocks; the loop index is recorded as a frame local.
    coro = ir.ScCoroutine(name="m", body=[
        ir.ScLoop(kind="repeat", count=ExprConstant(value=2), index_var="i",
                  body=[ir.ScExecBlock()]),
        ir.ScWait(time=ExprConstant(value=1)),
        ir.ScExecBlock(),
    ])
    fsm = CoroutineFSMPass().run(coro)
    assert fsm.n_blocks == 2
    assert "i" in fsm.frame_locals
