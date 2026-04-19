"""AST visitor skeleton that extracts :class:`IrPipeline` from an
``@zdc.pipeline`` async method.

This module is a skeleton — it can parse the basic pipeline structure but
does not yet handle all body-statement patterns.  Extend as synthesis needs
grow.
"""

from __future__ import annotations

import ast
from typing import List, Optional, Tuple

from .pipeline_async import (
    IrBubble,
    IrEgressOp,
    IrHazardOp,
    IrInFlightSearch,
    IrIngressOp,
    IrPipeline,
    IrStage,
    IrStall,
)

_PIPELINE_DECORATOR_NAMES = {"pipeline"}
_STAGE_CALL = "stage"                  # zdc.pipeline.stage()
_HAZARD_OPS = {"reserve", "block", "write", "release", "acquire"}
_NON_AWAIT_HAZARD_OPS = {"write", "release"}

# Width table for zdc type annotations
_ZDC_WIDTHS = {
    "bit": 1, "u1": 1, "u2": 2, "u3": 3, "u4": 4, "u5": 5, "u6": 6, "u7": 7,
    "u8": 8, "u16": 16, "u32": 32, "u64": 64, "u128": 128,
    "i8": 8, "i16": 16, "i32": 32, "i64": 64,
}


def _width_from_ast_ann(ann: ast.expr, default: int = 32) -> int:
    """Return bit-width for a zdc type annotation AST node (e.g. ``zdc.u32`` → 32)."""
    attr = None
    if isinstance(ann, ast.Attribute):
        attr = ann.attr
    elif isinstance(ann, ast.Name):
        attr = ann.id
    if attr:
        if attr in _ZDC_WIDTHS:
            return _ZDC_WIDTHS[attr]
        if attr.startswith(("u", "i")):
            try:
                return int(attr[1:])
            except ValueError:
                pass
    return default


class AsyncPipelineFrontendPass(ast.NodeVisitor):
    """Extract :class:`IrPipeline` from an async pipeline method's AST.

    Usage::

        import ast, inspect, textwrap
        src = textwrap.dedent(inspect.getsource(MyComp.run))
        tree = ast.parse(src)
        pass_ = AsyncPipelineFrontendPass()
        pass_.visit(tree)
        ir = pass_.result   # IrPipeline or None
    """

    def __init__(self) -> None:
        self.result: Optional[IrPipeline] = None

    # ------------------------------------------------------------------
    # Public entry
    # ------------------------------------------------------------------

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
        if self._has_pipeline_decorator(node):
            self.result = self._extract_pipeline(node)
        self.generic_visit(node)

    # ------------------------------------------------------------------
    # Pipeline extraction
    # ------------------------------------------------------------------

    def _has_pipeline_decorator(self, node: ast.AsyncFunctionDef) -> bool:
        for dec in node.decorator_list:
            name = self._decorator_name(dec)
            if name in _PIPELINE_DECORATOR_NAMES:
                return True
        return False

    def _decorator_name(self, node: ast.expr) -> str:
        if isinstance(node, ast.Name):
            return node.id
        if isinstance(node, ast.Attribute):
            return node.attr
        if isinstance(node, ast.Call):
            return self._decorator_name(node.func)
        return ""

    def _extract_pipeline(self, node: ast.AsyncFunctionDef) -> IrPipeline:
        clock_field, reset_field, clock_domain_field = self._extract_clock_reset(node)
        stages: List[IrStage] = []
        ingress_ops: List[IrIngressOp] = []
        egress_ops: List[IrEgressOp] = []
        current_stage_name = ""
        for stmt in node.body:
            # Check for ingress/egress before stage extraction
            ingress = self._try_extract_ingress(stmt, current_stage_name)
            if ingress is not None:
                ingress_ops.append(ingress)
                continue
            egress = self._try_extract_egress(stmt, current_stage_name)
            if egress is not None:
                egress_ops.append(egress)
                continue
            stage = self._try_extract_stage(stmt)
            if stage is not None:
                current_stage_name = stage.name
                stages.append(stage)
        return IrPipeline(
            method_name=node.name,
            clock_lambda=None,
            reset_lambda=None,
            stages=stages,
            clock_field=clock_field,
            reset_field=reset_field,
            clock_domain_field=clock_domain_field,
            ingress_ops=ingress_ops,
            egress_ops=egress_ops,
        )

    def _extract_clock_reset(
        self, node: ast.AsyncFunctionDef
    ) -> Tuple[Optional[str], Optional[str], Optional[str]]:
        """Extract clock=, reset=, clock_domain= from the @zdc.pipeline decorator.

        Returns:
            Tuple of (clock_field, reset_field, clock_domain_field).
        """
        for dec in node.decorator_list:
            if isinstance(dec, ast.Call):
                clk = rst = cd = None
                for kw in dec.keywords:
                    val = kw.value
                    # Extract simple lambda body attribute names: lambda s: s.FIELD
                    field_name = self._lambda_attr_name(val)
                    if kw.arg == "clock":
                        clk = field_name or (str(val.value) if isinstance(val, ast.Constant) else None)
                    elif kw.arg == "reset":
                        rst = field_name or (str(val.value) if isinstance(val, ast.Constant) else None)
                    elif kw.arg == "clock_domain":
                        cd = field_name
                if clk is not None or rst is not None or cd is not None:
                    return clk, rst, cd
        return None, None, None

    def _lambda_attr_name(self, node: ast.expr) -> Optional[str]:
        """If *node* is ``lambda s: s.FIELD``, return ``"FIELD"``, else ``None``."""
        if not isinstance(node, ast.Lambda):
            return None
        body = node.body
        if isinstance(body, ast.Attribute) and isinstance(body.value, ast.Name):
            return body.attr
        return None

    # ------------------------------------------------------------------
    # Stage extraction
    # ------------------------------------------------------------------

    def _try_extract_stage(self, node: ast.stmt) -> Optional[IrStage]:
        """Return an :class:`IrStage` if *node* is an ``async with stage()``."""
        if not isinstance(node, ast.AsyncWith):
            return None
        if not node.items:
            return None
        item = node.items[0]
        if not self._is_stage_call(item.context_expr):
            return None
        name = self._extract_stage_name(item)
        cycles = self._extract_cycles_kwarg(item.context_expr)
        hazard_ops: List[IrHazardOp] = []
        body_nodes: List[object] = []
        for stmt in node.body:
            # Check for ingress/egress ops inside stage body
            ingress = self._try_extract_ingress(stmt, stage_name=name)
            if ingress is not None:
                body_nodes.append(ingress)
                continue
            egress = self._try_extract_egress(stmt, stage_name=name)
            if egress is not None:
                body_nodes.append(egress)
                continue
            op = self._try_extract_hazard_op(stmt, stage_name=name)
            if op is not None:
                if isinstance(op, IrHazardOp):
                    hazard_ops.append(op)
                body_nodes.append(op)
            else:
                body_nodes.append(stmt)
        return IrStage(name=name, cycles=cycles, body=body_nodes, hazard_ops=hazard_ops)

    def _is_stage_call(self, node: ast.expr) -> bool:
        """True if *node* looks like ``zdc.pipeline.stage(...)`` or ``pipeline.stage(...)``."""
        if not isinstance(node, ast.Call):
            return False
        fn = node.func
        if isinstance(fn, ast.Attribute):
            return fn.attr == _STAGE_CALL
        return False

    def _extract_stage_name(self, item: ast.withitem) -> str:
        """Return the ``as NAME`` identifier, or ``""`` if absent."""
        if item.optional_vars is not None and isinstance(item.optional_vars, ast.Name):
            return item.optional_vars.id
        return ""

    def _extract_cycles_kwarg(self, call: ast.Call) -> int:
        """Return the ``cycles=N`` keyword argument value, defaulting to 1."""
        for kw in call.keywords:
            if kw.arg == "cycles" and isinstance(kw.value, ast.Constant):
                return int(kw.value.value)
        return 1

    # ------------------------------------------------------------------
    # Ingress / egress extraction (InPort.get / OutPort.put)
    # ------------------------------------------------------------------

    def _try_extract_ingress(
        self, node: ast.stmt, stage_name: str
    ) -> Optional[IrIngressOp]:
        """Return :class:`IrIngressOp` if *node* is ``tok = await self.PORT.get()``.

        Matches:
        - ``await self.PORT.get()``
        - ``name = await self.PORT.get()``
        - ``name: type = await self.PORT.get()``
        """
        call, result_target = self._unwrap_await_expr(node)
        if call is None:
            return None
        fn = call.func
        if not isinstance(fn, ast.Attribute) or fn.attr != "get":
            return None
        # fn.value must be ``self.PORT`` — Attribute(value=Name("self"), attr=PORT)
        port_name = self._self_attr_name(fn.value)
        if port_name is None:
            return None
        result_var, width = result_target
        return IrIngressOp(
            port_name=port_name,
            result_var=result_var,
            stage_name=stage_name,
            width=width if width != 32 else 32,
        )

    def _try_extract_egress(
        self, node: ast.stmt, stage_name: str
    ) -> Optional[IrEgressOp]:
        """Return :class:`IrEgressOp` if *node* is ``await self.PORT.put(expr)``.

        Matches:
        - ``await self.PORT.put(expr)``
        """
        call, _ = self._unwrap_await_expr(node)
        if call is None:
            return None
        fn = call.func
        if not isinstance(fn, ast.Attribute) or fn.attr != "put":
            return None
        port_name = self._self_attr_name(fn.value)
        if port_name is None:
            return None
        value_expr = call.args[0] if call.args else None
        return IrEgressOp(
            port_name=port_name,
            value_expr=value_expr,
            stage_name=stage_name,
        )

    def _self_attr_name(self, node: ast.expr) -> Optional[str]:
        """If *node* is ``self.FIELD``, return ``"FIELD"``, else ``None``."""
        if (isinstance(node, ast.Attribute)
                and isinstance(node.value, ast.Name)
                and node.value.id == "self"):
            return node.attr
        return None

    # ------------------------------------------------------------------
    # Hazard operation extraction
    # ------------------------------------------------------------------

    def _try_extract_hazard_op(
        self, node: ast.stmt, stage_name: str = ""
    ) -> Optional[object]:
        """Return :class:`IrHazardOp`, :class:`IrBubble`, or :class:`IrStall` if matched."""

        # --- await-based expressions ---
        call, result_target = self._unwrap_await_expr(node)
        if call is not None:
            fn = call.func
            if isinstance(fn, ast.Attribute):
                op_name = fn.attr
                if op_name in _HAZARD_OPS:
                    resource_expr = call.args[0] if call.args else None
                    mode = "write"
                    for kw in call.keywords:
                        if kw.arg == "mode" and isinstance(kw.value, ast.Constant):
                            mode = str(kw.value.value)
                    value_expr = (
                        call.args[1] if op_name == "write" and len(call.args) > 1 else None
                    )
                    # Preserve result variable for block/acquire
                    result_var, result_width = result_target
                    return IrHazardOp(
                        op=op_name,
                        resource_expr=resource_expr,
                        mode=mode,
                        value_expr=value_expr,
                        result_var=result_var,
                        result_width=result_width,
                    )
                # await STAGE_VAR.bubble()
                if op_name == "bubble":
                    return IrBubble(stage_var=stage_name)
                # await STAGE_VAR.stall(n)
                if op_name == "stall":
                    cycles_expr = call.args[0] if call.args else None
                    return IrStall(stage_var=stage_name, cycles_expr=cycles_expr)

        # --- non-await call expressions (write, release) ---
        non_await_call = self._unwrap_non_await_expr(node)
        if non_await_call is not None:
            fn = non_await_call.func
            if isinstance(fn, ast.Attribute) and fn.attr in _NON_AWAIT_HAZARD_OPS:
                op_name = fn.attr
                resource_expr = non_await_call.args[0] if non_await_call.args else None
                value_expr = (
                    non_await_call.args[1]
                    if op_name == "write" and len(non_await_call.args) > 1
                    else None
                )
                return IrHazardOp(
                    op=op_name,
                    resource_expr=resource_expr,
                    mode="write",
                    value_expr=value_expr,
                )

        return None

    def _unwrap_await_expr(
        self, node: ast.stmt
    ) -> Tuple[Optional[ast.Call], Tuple[Optional[str], int]]:
        """Return ``(Call, (result_var, result_width))`` for await expressions.

        Handles:
        - ``await <call>``                    (Expr)
        - ``name = await <call>``             (Assign)
        - ``name: type = await <call>``       (AnnAssign)
        """
        no_result: Tuple[Optional[str], int] = (None, 32)

        if isinstance(node, ast.Expr) and isinstance(node.value, ast.Await):
            inner = node.value.value
            if isinstance(inner, ast.Call):
                return inner, no_result

        if isinstance(node, ast.Assign) and isinstance(node.value, ast.Await):
            inner = node.value.value
            if isinstance(inner, ast.Call):
                var = None
                if node.targets and isinstance(node.targets[0], ast.Name):
                    var = node.targets[0].id
                return inner, (var, 32)

        if isinstance(node, ast.AnnAssign) and isinstance(node.value, ast.Await):
            inner = node.value.value
            if isinstance(inner, ast.Call):
                var = None
                width = 32
                if isinstance(node.target, ast.Name):
                    var = node.target.id
                if node.annotation is not None:
                    width = _width_from_ast_ann(node.annotation)
                return inner, (var, width)

        return None, no_result

    def _unwrap_non_await_expr(self, node: ast.stmt) -> Optional[ast.Call]:
        """Return the Call node if *node* is a plain (non-await) call statement."""
        if isinstance(node, ast.Expr) and isinstance(node.value, ast.Call):
            return node.value
        return None
