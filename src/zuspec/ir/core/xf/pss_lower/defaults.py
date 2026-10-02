"""Default value constraints (LRM 13.1.11), resolved over one object.

``default x == v;`` holds as ``x == v`` unless a default or a ``default
disable`` of higher precedence reaches the same attribute (13.1.11 d):

1. one from a higher-level containing context wins over one from a lower
   level (an action's ``default s.a == 2`` over struct ``S``'s own
   ``default a == 0``; a parent action's over its child's);
2. one from a derived type wins over one from its base;
3. within one type, the later one wins.

So which default holds on an attribute is known only once every context that
reaches it is known: for an action's own problem, its type and its struct
attributes' types; for the action tree, every node above it as well. Both
callers lay their statements out as :class:`DefaultStmt` with a *rank* that
orders them by these rules, and :func:`winners` picks one per scalar. A
winning default is then an ordinary equality, owned by whoever wrote it; a
winning ``default disable`` leaves nothing.

A default on an aggregate applies to each rand scalar it holds (e); a
``default disable`` on one disables them all. Flow-object and resource
attributes (h, i) do not reach here yet (bc has no flow objects).
"""
from __future__ import annotations

import dataclasses as dc
from typing import Any, Callable, Dict, List, Optional, Tuple

from ... import constraint as C
from ... import expr as E
from ... import stmt as S
from ..validate import UnsupportedConstructError

DEFAULT_STMTS = (S.StmtDefault, S.StmtDefaultDisable)


@dc.dataclass
class DefaultStmt:
    """One ``default`` or ``default disable``, resolved to the scalars it
    reaches."""
    rank: Tuple            # larger wins (13.1.11 d)
    slots: List[int]       # the rand scalars it applies to
    value: Optional[Any]   # the constant, slot-resolved; None: `default disable`
    owner: Any             # who wrote it (an action-tree node id; None per type)
    loc: Any = None


def is_default(stmt) -> bool:
    return isinstance(stmt, DEFAULT_STMTS)


def check_unconditioned(stmt, fn) -> None:
    """13.1.11 f: a default may not be conditioned on a non-constant
    expression. One under an ``if`` or an implication is refused."""
    if is_default(stmt):
        raise UnsupportedConstructError(
            "a 'default' constraint under a condition in %r is not supported "
            "(LRM 13.1.11 f)" % getattr(fn, "name", "?"), loc=getattr(stmt, "loc", None))


def _what(stmt) -> str:
    return "default disable" if isinstance(stmt, S.StmtDefaultDisable) else "default"


def _reads_a_field(e) -> bool:
    found = []

    def walk(x):
        if found:
            return
        if isinstance(x, (E.ExprRefField, E.TypeExprRefSelf, E.TypeExprRefTraversed)):
            found.append(x)
            return
        if dc.is_dataclass(x) and not isinstance(x, type):
            for f in dc.fields(x):
                v = getattr(x, f.name)
                for y in (v if isinstance(v, list) else [v]):
                    walk(y)
    walk(e)
    return bool(found)


def default_stmt(stmt, rank, owner, leaves: Optional[list], value_of: Callable
                 ) -> DefaultStmt:
    """The :class:`DefaultStmt` for IR statement *stmt*.

    *leaves* is ``[(slot, layout.Leaf)]``, every scalar the target names (one
    for a scalar, each of an aggregate's), or None if it names none.
    *value_of* resolves the default's value expression.
    """
    loc = getattr(stmt, "loc", None)
    what = _what(stmt)
    if not leaves:
        raise UnsupportedConstructError(
            "'%s' names no attribute of this object" % what, loc=loc)
    rand = [(slot, leaf) for slot, leaf in leaves if leaf.rand]
    if not rand:
        # 13.1.11 c: the attribute shall be rand.
        raise UnsupportedConstructError(
            "'%s' on a non-rand attribute (LRM 13.1.11 c)" % what, loc=loc)
    value = None
    if isinstance(stmt, S.StmtDefault):
        if len(leaves) != 1:
            raise UnsupportedConstructError(
                "'default' on an aggregate attribute with a value is not "
                "supported yet", loc=loc)
        value = value_of(stmt.value)
        if _reads_a_field(value):
            raise UnsupportedConstructError(
                "a 'default' value must be a constant expression (LRM 13.1.11)",
                loc=loc)
    return DefaultStmt(rank=rank, slots=[s for s, _ in rand], value=value,
                       owner=owner, loc=loc)


def winners(stmts: List[DefaultStmt]) -> Dict[int, DefaultStmt]:
    """The statement in force on each scalar: the highest rank that reaches
    it, whether a default or a ``default disable``."""
    out: Dict[int, DefaultStmt] = {}
    for d in stmts:
        for slot in d.slots:
            cur = out.get(slot)
            if cur is None or d.rank > cur.rank:
                out[slot] = d
    return out


def equalities(stmts: List[DefaultStmt]) -> List[Tuple[DefaultStmt, C.Constraint]]:
    """``(statement, slot == value)`` for each scalar a default holds on,
    in slot order."""
    out = []
    for slot, d in sorted(winners(stmts).items()):
        if d.value is None:
            continue
        out.append((d, C.ConstraintExpr(expr=E.ExprBin(
            lhs=E.ExprRefField(base=E.TypeExprRefSelf(), index=slot),
            op=E.BinOp.Eq, rhs=d.value))))
    return out


__all__ = ["DefaultStmt", "is_default", "check_unconditioned", "default_stmt",
           "winners", "equalities", "DEFAULT_STMTS"]
