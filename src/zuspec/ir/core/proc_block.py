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

A **task block** (``ProcKind.TASK``: ``run()`` and the code it calls) is
simulation-only and never lowered to RTL; its ``clock``/``reset`` stay
``None``. Its ``body`` and ``args`` are the task's code, for a back end
that generates the class side of a lowered transactor (its bridge): a
crossing call as in a step body, a blocking call (a crossing's
``put``/``get``, a task called through an API port) in ``ExprAwait``, and
a call through an API port or export as a call on the endpoint, named by
its path from the component (``ExprHierarchical``; SV's
``i = p.get_if(); i.m(...)`` is ``p.m(...)``). A front end that cannot map
a task's code leaves ``body`` empty.
``reads`` and ``writes`` are the paths of the signals it touches (a front
end fills ``reads`` for task blocks; it is optional for the others). Each
such signal is a **crossing port** (spec ``semantics/processes.md``,
"Crossings", C1–C8): task code reads it at an edge as it was before the
edge and writes it with register timing just after, and a step block of
the same component that also writes it gives its default (C5). Nothing
declares a crossing port: a pass derives one :class:`XingPort` per signal
from a component's blocks (``zuspec.synth.passes.domains.xing``).
"""
from __future__ import annotations
import dataclasses as dc
import enum
from typing import Dict, List, Optional

from .base import Base
from .data_type import DataType, SyncXingDir
from .domain import DomainRef, PathElem
from .expr import Expr
from .stmt import Arg, Stmt


class ProcKind(enum.Enum):
    """The kind of a process block."""
    STEP = enum.auto()   # clocked: always_ff, writes registers
    COMB = enum.auto()   # combinational: always_comb, writes wires
    TASK = enum.auto()   # task code (run()): simulation-only, crossing ports


@dc.dataclass(kw_only=True)
class ProcBlock(Base):
    """One ``step``, ``comb`` or task block of a component (see the
    module)."""
    name: str = dc.field()
    kind: ProcKind = dc.field()
    func: str = dc.field(default="")
    body: List[Stmt] = dc.field(default_factory=list)
    clock: Optional[DomainRef] = dc.field(default=None)
    reset: Optional[DomainRef] = dc.field(default=None)
    reset_body: List[Stmt] = dc.field(default_factory=list)
    writes: List[List[PathElem]] = dc.field(default_factory=list)
    reads: List[List[PathElem]] = dc.field(default_factory=list)
    state: List[str] = dc.field(default_factory=list)
    clock_net: str = dc.field(default="")
    reset_net: str = dc.field(default="")
    reset_active_low: bool = dc.field(default=False)
    reset_async: bool = dc.field(default=True)
    # The task-to-sync crossing methods the block calls (with the component
    # functions and tasks it calls), in order of first use.
    xing_uses: List["SyncXingUse"] = dc.field(default_factory=list)
    # A task block's formal arguments (``stmt.Arg``: name, ``datatype``,
    # ``direction``), in order; empty for ``run()`` and the other kinds.
    args: List["Arg"] = dc.field(default_factory=list)


@dc.dataclass(kw_only=True)
class SyncXingUse(Base):
    """A call of a task-to-sync crossing's method (``DataTypeSyncXing``)
    by a block: ``path`` names the crossing from the component (``[x]`` for
    its own, ``[child, x]`` for a child's), ``method`` the method
    (``put``, ``valid``, ``take``, ...)."""
    path: List[PathElem] = dc.field(default_factory=list)
    method: str = dc.field(default="")


@dc.dataclass(kw_only=True)
class SyncXingRecord(Base):
    """A lowered task-to-sync crossing of a component, as the ports that
    replaced it (``DataTypeComponent.sync_xings``), recorded by the
    crossing lowering pass so that a back end can build the task side
    (an SV interface, a cocotb bridge) from the IR alone.

    ``path`` names the crossing from the component (``[x]`` for its own,
    ``[child, x]`` for one lifted from a child, as ``SyncXingUse``);
    ``direction``, ``element_type`` and ``depth`` are the crossing's
    (``DataTypeSyncXing``); ``ports`` maps each port role (``valid``,
    ``ready``, ``data``, ``wait``) to the component's port name."""
    path: List[PathElem] = dc.field(default_factory=list)
    direction: SyncXingDir = dc.field(default=SyncXingDir.TO_SYNC)
    element_type: Optional[DataType] = dc.field(default=None)
    depth: int = dc.field(default=1)
    ports: Dict[str, str] = dc.field(default_factory=dict)


@dc.dataclass(kw_only=True)
class ExprInReset(Expr):
    """``fw_reset_domain::in_reset(...)`` in a lowered body: true while
    ``reset`` is asserted, whatever the net's polarity."""
    reset: DomainRef = dc.field()
    net: str = dc.field(default="")
    active_low: bool = dc.field(default=False)


@dc.dataclass(kw_only=True)
class XingPort(Base):
    """A crossing port, derived by a pass (see the module): a signal of a
    component that its task code reads or writes. ``path`` is from the
    component; ``default`` names the component's step block that also
    writes it ("" for none: the port holds); ``clock``/``reset`` are the
    domains of the signal's interface, ``None`` where unresolved."""
    path: List[PathElem] = dc.field(default_factory=list)
    read: bool = dc.field(default=False)
    written: bool = dc.field(default=False)
    default: str = dc.field(default="")
    clock: Optional[DomainRef] = dc.field(default=None)
    reset: Optional[DomainRef] = dc.field(default=None)
