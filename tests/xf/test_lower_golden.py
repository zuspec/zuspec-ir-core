"""Phase-6 golden-IR regression: lock the Layer-1 lowering of representative
PSS so lowering changes are caught without compiling."""
import pytest

from zuspec.ir.core.xf import PSSToScenarioPass, dump_module

pytest.importorskip("zuspec.fe.pss")
from zuspec.fe.pss import Parser  # noqa: E402
from zuspec.fe.pss.ast_to_ir import AstToIrTranslator  # noqa: E402


def _lower(pss):
    p = Parser()
    p.parses([("g.pss", pss)])
    ctx = AstToIrTranslator().translate(p.link(), annotations=p.annotations)
    assert not ctx.errors, ctx.errors
    return PSSToScenarioPass().lower(ctx)


CONSTRAINED = """
component pss_top {
    action write_reg {
        rand bit[32] addr;
        rand bit[32] data;
        constraint addr_aligned { (addr & 3) == 0; }
        constraint addr_range   { addr in [0x1000..0x1FFF]; }
        constraint data_range   { data in [0x00..0xFF]; }
        exec body { message(NONE, "write_reg: addr=0x%x data=0x%x", addr, data); }
    }
    action prog { activity { repeat (3) { do write_reg; } } }
}
"""

CONSTRAINED_GOLD = """\
module
  root pss_top : pss_top
  exports: prog
  coroutine write_reg <pss_top::write_reg> {
    solve_problem vars=[addr:u32[0], data:u32[1]] n_constraints=3
    exec(body, 1 stmts)
  }
  coroutine prog <pss_top::prog> {
    loop(repeat) {
      invoke write_reg
    }
  }"""

PARALLEL = """
component pss_top {
    action chan_a { exec body { message(NONE, "channel A"); } }
    action chan_b { exec body { message(NONE, "channel B"); } }
    action both { activity { parallel { do chan_a; do chan_b; } } }
}
"""

PARALLEL_GOLD = """\
module
  root pss_top : pss_top
  exports: both
  coroutine chan_a <pss_top::chan_a> {
    exec(body, 1 stmts)
  }
  coroutine chan_b <pss_top::chan_b> {
    exec(body, 1 stmts)
  }
  coroutine both <pss_top::both> {
    par(join=ALL) {
      invoke chan_a
      invoke chan_b
    }
  }"""


def test_constrained_repeat_golden():
    assert dump_module(_lower(CONSTRAINED)) == CONSTRAINED_GOLD


def test_parallel_golden():
    assert dump_module(_lower(PARALLEL)) == PARALLEL_GOLD
