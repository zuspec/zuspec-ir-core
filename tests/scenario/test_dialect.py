"""Phase-1 dialect tests for the Layer-1 ``scenario`` ops.

Constructs every node, exercises the synthesized visitor traversal, and
round-trips a small hand-built coroutine tree.
"""
import zuspec.ir.core as ir
from zuspec.ir.core.visitor import Visitor
from zuspec.ir.core.expr import ExprConstant


# ---------------------------------------------------------------------------
# Construction
# ---------------------------------------------------------------------------

def test_construct_every_node():
    """Each scenario node constructs with its documented defaults."""
    nodes = [
        ir.ScStmt(),
        ir.ScCoroutine(name="c"),
        ir.ScExecBlock(),
        ir.ScSeq(),
        ir.ScPar(),
        ir.ScSelectBranch(),
        ir.ScSelect(),
        ir.ScLoop(),
        ir.ScAtomic(),
        ir.ScIf(cond=ExprConstant(value=1)),
        ir.ScMatchCase(),
        ir.ScMatch(subject=ExprConstant(value=0)),
        ir.ScInvoke(target="t"),
        ir.ScSpawn(target="t"),
        ir.ScJoin(),
        ir.ScWait(time=ExprConstant(value=10)),
        ir.ScSolveVar(name="addr", var_id=0),
        ir.ScSolveProblem(),
        ir.ScActionInst(name="a", type_name="A"),
        ir.ScComponentInst(name="top", type_name="Top"),
        ir.ScenarioModule(),
    ]
    # All derive from Base and report a (possibly None) loc.
    for n in nodes:
        assert isinstance(n, ir.Base)
        assert n.getLoc() is None


def test_exec_block_defaults():
    blk = ir.ScExecBlock()
    assert blk.kind == "body"
    assert blk.stmts == []


def test_scloop_defaults():
    loop = ir.ScLoop()
    assert loop.kind == "repeat"
    assert loop.count is None and loop.cond is None


def test_module_add_coroutine_and_dup():
    m = ir.ScenarioModule()
    c = ir.ScCoroutine(name="x")
    assert m.add_coroutine(c) is c
    assert m.coroutines["x"] is c
    try:
        m.add_coroutine(ir.ScCoroutine(name="x"))
        assert False, "expected duplicate-name ValueError"
    except ValueError:
        pass


def test_all_exports():
    for name in (
        "ScCoroutine", "ScExecBlock", "ScSeq", "ScPar", "ScSelect", "ScLoop",
        "ScAtomic", "ScIf", "ScMatch", "ScInvoke", "ScSpawn", "ScJoin",
        "ScWait", "ScSolveProblem", "ScSolveVar", "ScActionInst",
        "ScComponentInst", "ScenarioModule",
    ):
        assert name in ir.scenario.__all__
        assert hasattr(ir, name)


# ---------------------------------------------------------------------------
# Visitor traversal / round-trip
# ---------------------------------------------------------------------------

class _Collector(Visitor):
    """Records the type-name of each scenario node it visits.

    The base :class:`Visitor` already defines every ``visitSc*`` default (with
    correct child traversal), so this subclass just overrides the handful it
    cares about.  ``__new__`` is overridden to bypass the profile-synthesis
    path, which is unnecessary here.
    """

    def __new__(cls, *args, **kwargs):
        return object.__new__(cls)

    def __init__(self):
        self.seen = []

    def visitScCoroutine(self, o):
        self.seen.append("ScCoroutine")
        super().visitScCoroutine(o)

    def visitScSeq(self, o):
        self.seen.append("ScSeq")
        super().visitScSeq(o)

    def visitScExecBlock(self, o):
        self.seen.append("ScExecBlock")

    def visitScInvoke(self, o):
        self.seen.append("ScInvoke")

    def visitScPar(self, o):
        self.seen.append("ScPar")
        super().visitScPar(o)


def _new_collector():
    return _Collector()


def test_visitor_traverses_nested_tree():
    """A hand-built coroutine tree is fully traversed in order."""
    coro = ir.ScCoroutine(name="top", body=[
        ir.ScSeq(body=[
            ir.ScExecBlock(kind="body"),
            ir.ScPar(branches=[
                ir.ScInvoke(target="a"),
                ir.ScInvoke(target="b"),
            ]),
        ]),
    ])
    v = _new_collector()
    coro.accept(v)
    assert v.seen == [
        "ScCoroutine", "ScSeq", "ScExecBlock", "ScPar", "ScInvoke", "ScInvoke",
    ]


def test_module_visit_visits_all_coroutines():
    m = ir.ScenarioModule()
    m.add_coroutine(ir.ScCoroutine(name="a", body=[ir.ScExecBlock()]))
    m.add_coroutine(ir.ScCoroutine(name="b", body=[ir.ScExecBlock()]))
    v = _new_collector()
    m.accept(v)
    assert v.seen.count("ScCoroutine") == 2
    assert v.seen.count("ScExecBlock") == 2
