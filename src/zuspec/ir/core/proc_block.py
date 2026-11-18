"""Process-block IR (fw-hdl layer 2, core-layer2 §7, "Z8").

A component's clocked and combinational logic, as the class writes it
(spec ``semantics/processes.md``):

- a **step block** (``ProcKind.STEP``: ``step()`` or a `` `fw_step(name)``
  block) lowers to an ``always_ff`` on the rising edge of its ``clock``
  domain. ``reset_body`` is the block's part of ``reset_state()``: the
  reset branch, taken while its ``reset`` domain is asserted. An empty
  ``reset_body`` (or ``reset`` of ``None``) is a block without one;
  its ``body`` then handles reset itself through ``ExprInReset``.
- a **comb block** (``ProcKind.COMB``: ``comb()`` or `` `fw_comb(name)``)
  lowers to an ``always_comb``. It has no domain.

``func`` names the component function the block runs (``"step"``,
``"comb"`` or the macro block's function); ``body`` is that function's
body. ``writes`` are the paths of the signals and component fields the
block writes: what a step block writes is a register, what a comb block
writes is a wire. The front end has checked that each has one writer.
``state`` names the component's fields that are plain state variables
(class properties, not signals) a step block writes: a write to one is
seen at once by the block's later statements, as in the class, and the
register takes the value the block ends with.

The reset's polarity and synchronous/asynchronous style are the reset
domain's params; like the rest of the domain IR they are resolved by a
pass (structural flattening, from the class's domain ports), which fills
the ``*_net`` fields: ``clock_net``/``reset_net`` of a block and ``net``
of an ``ExprInReset`` name the module's net, ``*_active_low`` its
polarity, ``reset_async`` the style. A back end emits from those.
``run()`` is never lowered and has no IR.
"""
from __future__ import annotations
import dataclasses as dc
import enum
from typing import List, Optional

from .base import Base
from .domain import DomainRef, PathElem
from .expr import Expr
from .stmt import Stmt


class ProcKind(enum.Enum):
    """The kind of a process block."""
    STEP = enum.auto()   # clocked: always_ff, writes registers
    COMB = enum.auto()   # combinational: always_comb, writes wires


@dc.dataclass(kw_only=True)
class ProcBlock(Base):
    """One ``step`` or ``comb`` block of a component (see the module)."""
    name: str = dc.field()
    kind: ProcKind = dc.field()
    func: str = dc.field(default="")
    body: List[Stmt] = dc.field(default_factory=list)
    clock: Optional[DomainRef] = dc.field(default=None)
    reset: Optional[DomainRef] = dc.field(default=None)
    reset_body: List[Stmt] = dc.field(default_factory=list)
    writes: List[List[PathElem]] = dc.field(default_factory=list)
    state: List[str] = dc.field(default_factory=list)
    clock_net: str = dc.field(default="")
    reset_net: str = dc.field(default="")
    reset_active_low: bool = dc.field(default=False)
    reset_async: bool = dc.field(default=True)


@dc.dataclass(kw_only=True)
class ExprInReset(Expr):
    """``fw_reset_domain::in_reset(...)`` in a lowered body: true while
    ``reset`` is asserted, whatever the net's polarity."""
    reset: DomainRef = dc.field()
    net: str = dc.field(default="")
    active_low: bool = dc.field(default=False)
