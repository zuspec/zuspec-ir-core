"""collect_solve_problem converts Stmt-form constraint bodies into structured,
*resolved* Constraint IR.

The frontend hands constraints in as ``Stmt`` nodes whose field references are
name-based (``ExprAttribute(TypeExprRefSelf(), attr)`` / ``ExprRefUnresolved``).
This pass must turn each statement into the first-class ``Constraint`` form and
resolve every field reference to ``ExprRefField(index=slot)`` (the object slot),
which is what the solver lowering addresses variables by.
"""

import pytest

from zuspec.ir.core import expr as E
from zuspec.ir.core import constraint as C
from zuspec.ir.core import stmt as S
from zuspec.ir.core.data_type import DataTypeClass, DataTypeInt, Function
from zuspec.ir.core.fields import Field, RandKind
from zuspec.ir.core.scenario import ScCoroutine
from zuspec.ir.core.xf.pss_lower.constraints import collect_solve_problem
from zuspec.ir.core.xf.validate import UnsupportedConstructError


# --- builders ------------------------------------------------------------- #

def _field(name, rand=True, bits=8):
    return Field(name=name, datatype=DataTypeInt(bits=bits),
                 rand_kind=RandKind.RAND if rand else None)

def SELF(attr):     return E.ExprAttribute(value=E.TypeExprRefSelf(), attr=attr)
def K(n):           return E.ExprConstant(value=n)
def BIN(l, op, r):  return E.ExprBin(lhs=l, op=op, rhs=r)

def _coro(*stmts):
    fn = Function(name="c", body=list(stmts), is_async=False,
                  metadata={"_is_constraint": True})
    return ScCoroutine(name="A", body=[], pending_constraints=[fn])

def _collect(dt, *stmts):
    return collect_solve_problem(_coro(*stmts), dt)


_DT = DataTypeClass(name="A", super=None,
                    fields=[_field("x"), _field("y"), _field("mode")])


# --- tests ---------------------------------------------------------------- #

def test_expr_constraint_resolves_self_field_ref():
    # constraint: x < 10  -> ConstraintExpr(ExprBin(ExprRefField(0), Lt, 10))
    p = _collect(_DT, S.StmtExpr(expr=BIN(SELF("x"), E.BinOp.Lt, K(10))))
    assert len(p.constraints) == 1
    con = p.constraints[0]
    assert isinstance(con, C.ConstraintExpr)
    assert isinstance(con.expr.lhs, E.ExprRefField)
    assert con.expr.lhs.index == 0          # x is slot 0
    assert con.expr.rhs.value == 10


def test_two_field_ref_resolution():
    # x + y == 12  -> both refs become ExprRefField with their slots.
    p = _collect(_DT, S.StmtExpr(expr=BIN(BIN(SELF("x"), E.BinOp.Add, SELF("y")),
                                          E.BinOp.Eq, K(12))))
    add = p.constraints[0].expr.lhs
    assert (add.lhs.index, add.rhs.index) == (0, 1)   # x=0, y=1


def test_if_constraint_becomes_if_else():
    # if (mode == 1) { x < 10 } else { x > 100 }
    st = S.StmtIf(test=BIN(SELF("mode"), E.BinOp.Eq, K(1)),
                  body=[S.StmtExpr(expr=BIN(SELF("x"), E.BinOp.Lt, K(10)))],
                  orelse=[S.StmtExpr(expr=BIN(SELF("x"), E.BinOp.Gt, K(100)))])
    con = _collect(_DT, st).constraints[0]
    assert isinstance(con, C.ConstraintIfElse)
    assert con.cond.lhs.index == 2                      # mode is slot 2
    assert con.then_body[0].expr.lhs.index == 0         # x
    assert con.else_body[0].expr.lhs.index == 0
    assert con.then_body[0].expr.rhs.value == 10


def test_unique_constraint_resolves_members():
    p = _collect(_DT, S.StmtUnique(vars=["x", "y"]))
    con = p.constraints[0]
    assert isinstance(con, C.ConstraintUnique)
    assert [it.index for it in con.items] == [0, 1]
    assert all(isinstance(it, E.ExprRefField) for it in con.items)


def test_implies_call_becomes_constraint_implies():
    # ast2ir renders `a -> b` as ExprCall(implies, [cond, body]) inside a StmtExpr.
    cond = BIN(SELF("mode"), E.BinOp.Eq, K(1))
    body = BIN(SELF("x"), E.BinOp.Lt, K(10))
    call = E.ExprCall(func=E.ExprRefUnresolved(name="implies"), args=[cond, body])
    con = _collect(_DT, S.StmtExpr(expr=call)).constraints[0]
    assert isinstance(con, C.ConstraintImplies)
    assert con.antecedent.lhs.index == 2                # mode
    assert isinstance(con.body[0], C.ConstraintExpr)
    assert con.body[0].expr.lhs.index == 0              # x


def test_slot_resolution_uses_full_field_index():
    # With a non-rand pad first, x/y live at slots 1/2 (not 0/1) -- the constraint
    # must resolve to the object slot, matching ScSolveVar.slot.
    dt = DataTypeClass(name="B", super=None,
                       fields=[_field("pad", rand=False), _field("x"), _field("y")])
    con = _collect(dt, S.StmtExpr(expr=BIN(SELF("x"), E.BinOp.Lt, SELF("y")))).constraints[0]
    assert (con.expr.lhs.index, con.expr.rhs.index) == (1, 2)


def test_foreach_constraint_deferred():
    st = S.StmtForeach(target=E.ExprRefLocal(name="i"), iter=SELF("arr"),
                       body=[S.StmtExpr(expr=BIN(SELF("x"), E.BinOp.Gt, K(0)))])
    with pytest.raises(UnsupportedConstructError):
        _collect(_DT, st)


def test_unknown_statement_rejected():
    with pytest.raises(UnsupportedConstructError):
        _collect(_DT, S.StmtWhile(test=K(1), body=[], orelse=[]))
