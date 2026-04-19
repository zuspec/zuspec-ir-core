"""Pipeline IR data classes for the old sync @zdc.stage / @zdc.pipeline API.

These types are consumed by
:class:`~zuspec.synth.passes.pipeline_frontend.PipelineFrontendPass` and
populated by :meth:`~zuspec.dataclasses.data_model_factory.DataModelFactory._build_pipeline_irs`.
"""
from __future__ import annotations

import dataclasses as dc
from typing import Any, List, Optional


@dc.dataclass
class StallDecl:
    """Stall declaration extracted from a ``zdc.stage.stall(self, COND)`` call."""
    cond_ast: Any = None  # AST expression node for the stall condition


@dc.dataclass
class CancelDecl:
    """Cancel declaration extracted from a ``zdc.stage.cancel(self, COND)`` call."""
    cond_ast: Any = None  # AST expression node for the cancel condition


@dc.dataclass
class FlushDecl:
    """Flush declaration extracted from ``zdc.stage.flush(self.STAGE, COND)``."""
    target_stage: str = ""
    cond_ast: Any = None  # AST expression node for the flush condition (may be None)


@dc.dataclass
class QueryNode:
    """Query on a stage — e.g. ``zdc.stage.ready(self.STAGE)``."""
    kind: str          # "ready" (extensible)
    stage_name: str = ""


@dc.dataclass
class StageCallNode:
    """One call to a stage method in the pipeline orchestrator body.

    Example::

        (x, y) = self.EX(a, b)  →  StageCallNode("EX", ["a", "b"], ["x", "y"])
        self.WB(result)          →  StageCallNode("WB", ["result"], [])
    """
    stage_name: str
    arg_names: List[str] = dc.field(default_factory=list)
    return_names: List[str] = dc.field(default_factory=list)
    cycles: int = 1


@dc.dataclass
class StageMethodIR:
    """IR for a single ``@zdc.stage``-decorated method."""
    name: str
    no_forward: bool = False
    cycles: int = 1
    stall_decls: List[StallDecl] = dc.field(default_factory=list)
    cancel_decls: List[CancelDecl] = dc.field(default_factory=list)
    flush_decls: List[FlushDecl] = dc.field(default_factory=list)
    body_ast: Any = None  # AST FunctionDef node (for the whole stage method)


@dc.dataclass
class SyncMethodIR:
    """IR for a ``@zdc.sync``-decorated method that interacts with pipeline stages."""
    name: str
    flush_decls: List[FlushDecl] = dc.field(default_factory=list)
    query_nodes: List[QueryNode] = dc.field(default_factory=list)
    clock: Any = None   # clock field name or lambda (for clock domain resolution)
    reset: Any = None   # reset field name or lambda


@dc.dataclass
class PipelineRootIR:
    """IR for the ``@zdc.pipeline``-decorated orchestrator method.

    ``stage_calls`` is an ordered list of :class:`StageCallNode` reflecting the
    data-flow sequence defined by the pipeline body (e.g.::

        (x,) = self.S1()   →  StageCallNode("S1", [], ["x"])
        self.S2(x)         →  StageCallNode("S2", ["x"], [])
    """
    stage_calls: List[StageCallNode] = dc.field(default_factory=list)
    forward: Any = None   # bool | list[_LegacyForwardingDecl] | None
    clock: Any = None     # clock field name or lambda
    reset: Any = None     # reset field name or lambda
