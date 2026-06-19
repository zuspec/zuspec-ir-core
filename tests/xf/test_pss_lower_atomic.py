"""Phase-1 lowering test: PSS atomic action → Layer-1 ``scenario`` IR.

Loads a hello-world PSS source, lowers it with ``PSSToScenarioPass``, and
asserts the expected Layer-1 structure: one ``ScCoroutine`` per atomic action
whose body carries the exec-body statements, with constraints parked on
``pending_constraints`` and *no* ``ScSolveProblem`` yet (those arrive in
Phase 3).  Compound actions are deferred, not dropped.
"""
import pytest

import zuspec.ir.core as ir
from zuspec.ir.core.xf import PSSToScenarioPass, UnsupportedConstructError
from zuspec.ir.core.scenario import ScExecBlock, ScSolveProblem

# The frontend lives in a sibling package; skip cleanly if unavailable.
pss = pytest.importorskip("zuspec.fe.pss")
from zuspec.fe.pss import Parser  # noqa: E402
from zuspec.fe.pss.ast_to_ir import AstToIrTranslator  # noqa: E402


HELLO_WORLD = """
component pss_top {
    action write_reg {
        rand bit[32] addr;
        rand bit[32] data;
        constraint addr_aligned { addr[1:0] == 0; }
        constraint addr_range   { addr in [0x1000..0x1FFF]; }
        exec body {
            message(NONE, "write_reg: addr=0x%x data=0x%x", addr, data);
        }
    }

    action read_reg {
        rand bit[32] addr;
        constraint addr[1:0] == 0;
        exec body {
            message(NONE, "read_reg: addr=0x%x", addr);
        }
    }

    action do_test {
        activity {
            do write_reg;
            do read_reg;
        }
    }
}
"""


@pytest.fixture
def ctx():
    parser = Parser()
    parser.parses([("hello_world.pss", HELLO_WORLD)])
    c = AstToIrTranslator().translate(parser.link(), annotations=parser.annotations)
    assert not c.errors, c.errors
    return c


def _exec_blocks(coro):
    return [b for b in coro.body if isinstance(b, ScExecBlock)]


def test_root_and_coroutine_set(ctx):
    mod = PSSToScenarioPass().lower(ctx)
    assert mod.root is not None
    assert mod.root.type_name == "pss_top"
    # Phase 4: the compound action is lowered too (not deferred).
    assert set(mod.coroutines) == {"write_reg", "read_reg", "do_test"}
    assert mod.deferred_actions == []


def test_atomic_body_present_no_solve(ctx):
    # solve_constraints=False preserves the Phase-1 "park constraints" mode.
    mod = PSSToScenarioPass(solve_constraints=False).lower(ctx)
    wr = mod.coroutines["write_reg"]

    # Exactly one body exec block, carrying the message() statement.
    blocks = _exec_blocks(wr)
    assert [b.kind for b in blocks] == ["body"]
    assert len(blocks[0].stmts) == 1

    # No solve problem emitted in Phase 1.
    assert not any(isinstance(b, ScSolveProblem) for b in wr.body)

    # The originating action type is recorded.
    assert wr.action_type == "pss_top::write_reg"


def test_constraints_carried_not_dropped(ctx):
    mod = PSSToScenarioPass(solve_constraints=False).lower(ctx)
    wr = mod.coroutines["write_reg"]
    # Both named constraint blocks are parked for Phase 3.
    names = sorted(f.name for f in wr.pending_constraints)
    assert names == ["addr_aligned", "addr_range"]


def test_exports_default_to_top_actions(ctx):
    # Auto-export = actions not traversed by another action: only do_test.
    mod = PSSToScenarioPass().lower(ctx)
    assert set(mod.export_actions) == {"do_test"}


def test_explicit_root_and_exports(ctx):
    mod = PSSToScenarioPass(root="pss_top", exports=["write_reg"]).lower(ctx)
    assert mod.export_actions == ["write_reg"]


def test_accepts_plain_dict_type_map(ctx):
    # The pass should accept a raw type map as well as a Context object.
    mod = PSSToScenarioPass().lower(ctx.type_map)
    assert "write_reg" in mod.coroutines
