"""IR nodes for async pipeline methods.

These nodes are produced by :class:`AsyncPipelineFrontendPass` and consumed
by downstream synthesis passes.  They do not affect ``rt`` execution.
"""

from __future__ import annotations

import dataclasses as dc
from typing import Any, List, Optional


@dc.dataclass
class IrPipeline:
    """Root IR node for one ``@zdc.pipeline`` async method."""
    method_name: str
    clock_lambda: Any            # AST node of the clock lambda, or None
    reset_lambda: Any            # AST node of the reset lambda, or None
    stages: List["IrStage"] = dc.field(default_factory=list)
    clock_field: Optional[str] = None         # clock port name (legacy clock= form)
    reset_field: Optional[str] = None         # reset port name
    clock_domain_field: Optional[str] = None  # ClockDomain field name (new form)
    ingress_ops: List["IrIngressOp"] = dc.field(default_factory=list)
    egress_ops: List["IrEgressOp"] = dc.field(default_factory=list)


@dc.dataclass
class IrStage:
    """One ``async with zdc.pipeline.stage() as NAME:`` block."""
    name: str                    # identifier from the ``as NAME`` clause
    cycles: int = 1              # from ``stage(cycles=N)``, default 1
    body: List[Any] = dc.field(default_factory=list)         # IR nodes for body stmts
    hazard_ops: List["IrHazardOp"] = dc.field(default_factory=list)


@dc.dataclass
class IrHazardOp:
    """Any hazard operation: reserve, block, write, release, or acquire."""
    op: str                      # "reserve" | "block" | "write" | "release" | "acquire"
    resource_expr: Any           # AST expression for ``resource[addr]``
    mode: str = "write"          # "read" or "write"
    value_expr: Any = None       # only for ``op == "write"``
    result_var: Optional[str] = None   # variable receiving the result (for block/acquire)
    result_width: int = 32             # bit-width of result_var


@dc.dataclass
class IrIngressOp:
    """``tok = await self.PORT.get()`` — pipeline ingress via :class:`InPort`.

    Produced when the frontend pass detects ``await self.FIELD.get()`` where
    ``FIELD`` is an :class:`~zuspec.dataclasses.method_port.InPort`.

    Attributes:
        port_name:  Field name of the ``InPort`` on the component class.
        result_var: Local variable name that receives the token value.
        stage_name: Name of the enclosing stage (or empty if before first stage).
        width:      Bit-width of the ingress datum (for RTL port sizing).
    """
    port_name: str
    result_var: Optional[str] = None
    stage_name: str = ""
    width: int = 32


@dc.dataclass
class IrEgressOp:
    """``await self.PORT.put(val)`` — pipeline egress via :class:`OutPort`.

    Produced when the frontend pass detects ``await self.FIELD.put(expr)``
    where ``FIELD`` is an :class:`~zuspec.dataclasses.method_port.OutPort`.

    Attributes:
        port_name:  Field name of the ``OutPort`` on the component class.
        value_expr: AST expression for the value being emitted.
        stage_name: Name of the enclosing stage.
        width:      Bit-width of the egress datum (for RTL port sizing).
    """
    port_name: str
    value_expr: Any = None
    stage_name: str = ""
    width: int = 32


@dc.dataclass
class IrStall:
    """``await NAME.stall(n)``"""
    stage_var: str
    cycles_expr: Any             # AST expression for *n*


@dc.dataclass
class IrBubble:
    """``await NAME.bubble()``"""
    stage_var: str


@dc.dataclass
class IrInFlightSearch:
    """``zdc.pipeline.find(predicate)``"""
    predicate_expr: Any          # AST expression for the predicate callable
