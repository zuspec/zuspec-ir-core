"""Phase-3 golden test: ConstraintCollect builds a ScSolveProblem.

Asserts the lowered lifecycle for an atomic action with rand fields + named
constraints: a leading ``ScSolveProblem`` (vars + writeback + constraint DAG),
with ``pending_constraints`` cleared.
"""
import pytest

from zuspec.ir.core.xf import PSSToScenarioPass, UnsupportedConstructError
from zuspec.ir.core.scenario import ScSolveProblem, ScExecBlock

pytest.importorskip("zuspec.fe.pss")
from zuspec.fe.pss import Parser  # noqa: E402
from zuspec.fe.pss.ast_to_ir import AstToIrTranslator  # noqa: E402


PSS = """
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
}
"""


@pytest.fixture
def ctx():
    p = Parser()
    p.parses([("c.pss", PSS)])
    c = AstToIrTranslator().translate(p.link(), annotations=p.annotations)
    assert not c.errors, c.errors
    return c


def test_solve_problem_prepended(ctx):
    mod = PSSToScenarioPass().lower(ctx)
    wr = mod.coroutines["write_reg"]

    # Lifecycle: solve_problem first, then the exec body.
    assert isinstance(wr.body[0], ScSolveProblem)
    assert any(isinstance(b, ScExecBlock) for b in wr.body)
    # pending_constraints consumed.
    assert wr.pending_constraints == []


def test_vars_and_writeback(ctx):
    mod = PSSToScenarioPass().lower(ctx)
    sp = mod.coroutines["write_reg"].body[0]

    names = [(v.name, v.var_id, v.width, v.signed) for v in sp.vars]
    assert names == [("addr", 0, 32, False), ("data", 1, 32, False)]
    assert sp.writeback == {"addr": 0, "data": 1}


def test_constraints_captured(ctx):
    mod = PSSToScenarioPass().lower(ctx)
    sp = mod.coroutines["write_reg"].body[0]
    # Both named constraint blocks captured as Layer-0 Expr DAGs.
    assert len(sp.constraints) == 2
