#****************************************************************************
# Copyright 2019-2025 Matthew Ballance and contributors
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
#****************************************************************************
from __future__ import annotations
import dataclasses as dc
import enum
from typing import Optional, TYPE_CHECKING
from .base import Base

if TYPE_CHECKING:
    from .data_type import DataType


@dc.dataclass(kw_only=True)
class Expr(Base):
    pass

class BinOp(enum.Enum):
    Add = enum.auto(); Sub = enum.auto(); Mult = enum.auto(); Div = enum.auto(); Mod = enum.auto(); FloorDiv = enum.auto(); Exp = enum.auto()
    BitAnd = enum.auto(); BitOr = enum.auto(); BitXor = enum.auto(); LShift = enum.auto(); RShift = enum.auto()
    Eq = enum.auto(); NotEq = enum.auto(); Lt = enum.auto(); LtE = enum.auto(); Gt = enum.auto(); GtE = enum.auto()
    And = enum.auto(); Or = enum.auto()

class UnaryOp(enum.Enum):
    Invert = enum.auto(); Not = enum.auto(); UAdd = enum.auto(); USub = enum.auto()

class BoolOp(enum.Enum):
    And = enum.auto(); Or = enum.auto()

class CmpOp(enum.Enum):
    Eq = enum.auto(); NotEq = enum.auto(); Lt = enum.auto(); LtE = enum.auto(); Gt = enum.auto(); GtE = enum.auto(); Is = enum.auto(); IsNot = enum.auto(); In = enum.auto(); NotIn = enum.auto()

class AugOp(enum.Enum):
    Add = enum.auto(); Sub = enum.auto(); Mult = enum.auto(); Div = enum.auto(); Mod = enum.auto(); Pow = enum.auto()
    LShift = enum.auto(); RShift = enum.auto(); BitAnd = enum.auto(); BitOr = enum.auto(); BitXor = enum.auto(); FloorDiv = enum.auto()

@dc.dataclass(kw_only=True)
class Keyword(Base):
    arg: Optional[str] = dc.field(default=None)
    value: Expr = dc.field()

@dc.dataclass(kw_only=True)
class ExprBin(Expr):
    lhs : Expr = dc.field()
    op : BinOp = dc.field()
    rhs : Expr = dc.field()

@dc.dataclass(kw_only=True)
class ExprRef(Expr):
    pass

@dc.dataclass(kw_only=True)
class ExprConstant(Expr):
    value: object = dc.field()
    kind: Optional[str] = dc.field(default=None)

@dc.dataclass
class TypeExprRefSelf(ExprRef): 
    """Reference to 'self'"""
    ...

@dc.dataclass(kw_only=True)
class ExprRefField(ExprRef):
    """Reference to a field relative to the base expression"""
    base : Expr = dc.field()
    index : int = dc.field()


@dc.dataclass(kw_only=True)
class ExprRefParam(ExprRef):
    """Reference to a method parameter"""
    name: str = dc.field()
    index: int = dc.field(default=-1)


@dc.dataclass(kw_only=True)
class ExprRefLocal(ExprRef):
    """Reference to a local variable"""
    name: str = dc.field()


@dc.dataclass(kw_only=True)
class ExprRefUnresolved(ExprRef):
    """Unresolved reference - builtin or external"""
    name: str = dc.field()


@dc.dataclass(kw_only=True)
class ExprRefPy(ExprRef):
    """Reference relative to a Python object (base)"""
    base : Expr = dc.field()
    ref : str = dc.field()

class ExprRefBottomUp(ExprRef):
    """Reference to a field relative to the active procedural scope"""
    uplevel : int = dc.field(default=0)
    index : int = dc.field()

# class TypeExprRefTopDown(ABC):
#     @property
#     @abstractmethod
#     def ref(self) -> str:
#         ...

@dc.dataclass(kw_only=True)
class ExprUnary(Expr):
    op: UnaryOp = dc.field()
    operand: Expr = dc.field()

@dc.dataclass(kw_only=True)
class ExprBool(Expr):
    op: BoolOp = dc.field()
    values: list[Expr] = dc.field(default_factory=list)

@dc.dataclass(kw_only=True)
class ExprCompare(Expr):
    left: Expr = dc.field()
    ops: list[CmpOp] = dc.field(default_factory=list)
    comparators: list[Expr] = dc.field(default_factory=list)

@dc.dataclass(kw_only=True)
class ExprAttribute(Expr):
    value: Expr = dc.field()
    attr: str = dc.field()

@dc.dataclass(kw_only=True)
class ExprSlice(Expr):
    lower: Optional[Expr] = dc.field(default=None)
    upper: Optional[Expr] = dc.field(default=None)
    step: Optional[Expr] = dc.field(default=None)
    is_bit_slice: bool = dc.field(default=False)  # True for PSS bit slicing [7:0]


@dc.dataclass(kw_only=True)
class ExprSubscript(Expr):
    value: Expr = dc.field()
    slice: Expr = dc.field()  # Can be ExprSlice or simple expression

@dc.dataclass(kw_only=True)
class ExprCall(Expr):
    func: Expr = dc.field()
    args: list[Expr] = dc.field(default_factory=list)
    keywords: list[Keyword] = dc.field(default_factory=list)


@dc.dataclass(kw_only=True)
class ExprIfExp(Expr):
    """Represents a conditional/ternary expression: test ? body : orelse
    
    PSS syntax: condition ? true_expr : false_expr
    
    Example:
        x > 0 ? x : -x  -> ExprIfExp(
            test=ExprCompare(x, Gt, 0),
            body=ExprRef(x),
            orelse=ExprUnary(USub, x)
        )
    """
    test: Expr = dc.field()
    body: Expr = dc.field()
    orelse: Expr = dc.field()


@dc.dataclass(kw_only=True)
class ExprAwait(Expr):
    """Represents an await expression in async code"""
    value: Expr = dc.field()


@dc.dataclass(kw_only=True)
class ExprIn(Expr):
    """Represents membership test: value in container
    
    PSS syntax: expr in {range_list} or expr in collection
    
    Examples:
        x in [0..10] -> ExprIn(value=x, container=ExprRangeList(...))
        status in {IDLE, READY} -> ExprIn(value=status, container=ExprList(...))
    """
    value: Expr = dc.field()
    container: Expr = dc.field()  # Can be ExprRangeList, ExprList, etc.


@dc.dataclass(kw_only=True)
class ExprLambda(Expr):
    """Represents a lambda/callable expression stored for later evaluation.
    
    Used for width specifications and kwargs that reference const fields.
    The callable is stored as-is and can be evaluated at instantiation time.
    
    Example:
        width=lambda s:s.DATA_WIDTH  -> ExprLambda(callable=<lambda>)
    """
    callable: object = dc.field()  # The actual Python callable


# ============================================================================
# PSS-Specific Expression Types
# ============================================================================

@dc.dataclass(kw_only=True)
class ExprRange(Expr):
    """Represents a range expression: [lo..hi] or single value
    
    Used in PSS for:
    - Constraint domains: rand int x in [0..100];
    - Array initialization: {[0..9] : 0xFF}
    - Match patterns: match (x) { [0..10]: ... }
    
    Examples:
        Single value: ExprRange(lower=5, upper=None)
        Range: ExprRange(lower=0, upper=100)
    """
    lower: Expr = dc.field()
    upper: Optional[Expr] = dc.field(default=None)  # None for single value


@dc.dataclass(kw_only=True)
class ExprRangeList(Expr):
    """Represents a list of ranges: {[1..10], [20..30], 40}
    
    Used in PSS for:
    - Set membership: x in {[0..10], 20, [30..40]}
    - Domain specifications: rand int val in {[0..9], [100..109]}
    - Case values: case {[0..5], 10}: ...
    
    Example:
        {[1..10], 20, [30..40]} -> ExprRangeList(ranges=[
            ExprRange(lower=1, upper=10),
            ExprRange(lower=20, upper=None),
            ExprRange(lower=30, upper=40)
        ])
    """
    ranges: list[ExprRange] = dc.field(default_factory=list)


@dc.dataclass(kw_only=True)
class ExprIn(Expr):
    """Represents membership test: value in container
    
    PSS syntax: expr in {range_list} or expr in collection
    
    Examples:
        x in [0..10] -> ExprIn(value=x, container=ExprRangeList(...))
        status in {IDLE, READY} -> ExprIn(value=status, container=ExprList(...))
    """
    value: Expr = dc.field()
    container: Expr = dc.field()  # Can be ExprRangeList, ExprList, etc.


@dc.dataclass(kw_only=True)
class ExprStructField(Base):
    """Single field assignment in struct literal"""
    name: str = dc.field()
    value: Expr = dc.field()


@dc.dataclass(kw_only=True)
class ExprStructLiteral(Expr):
    """Struct initialization: {.field1 = val1, .field2 = val2}
    
    PSS syntax for struct literals with named field assignments.
    """
    fields: list[ExprStructField] = dc.field(default_factory=list)


# ============================================================================
# Phase 3: Advanced Expression Types
# ============================================================================

@dc.dataclass(kw_only=True)
class ExprCast(Expr):
    """Type cast expression: (type)value
    
    Used for explicit type conversions in PSS.
    
    Example:
        (int)3.14 -> ExprCast(target_type=DataTypeInt(...), value=ExprConstant(3.14))
    """
    target_type: 'DataType' = dc.field()  # Forward reference
    value: Expr = dc.field()


@dc.dataclass(kw_only=True)
class ExprStringMethod(Expr):
    """String method call: str.method(args)
    
    PSS supports various string methods like size(), lower(), upper(), etc.
    
    Examples:
        name.size() -> ExprStringMethod(base=name, method="size", args=[])
        text.find("hello") -> ExprStringMethod(base=text, method="find", args=["hello"])
    """
    base: Expr = dc.field()
    method: str = dc.field()  # "size", "lower", "upper", "split", "find", etc.
    args: list[Expr] = dc.field(default_factory=list)


@dc.dataclass(kw_only=True)
class ExprHierarchicalElem(Base):
    """Single element in a hierarchical path
    
    Each element can have a name, subscript, and parameters.
    """
    name: str = dc.field()
    subscript: Optional[Expr] = dc.field(default=None)
    params: list[Expr] = dc.field(default_factory=list)


@dc.dataclass(kw_only=True)
class ExprHierarchical(Expr):
    """PSS hierarchical reference: comp.instance.field[idx]
    
    Represents dotted paths with optional subscripts and method calls.
    
    Examples:
        top.cpu.regs.CSR -> ExprHierarchical(elements=[...])
        comp.array[i].field -> ExprHierarchical(elements=[...])
    """
    elements: list[ExprHierarchicalElem] = dc.field(default_factory=list)
    is_super: bool = dc.field(default=False)  # True for super.method()


@dc.dataclass(kw_only=True)
class ExprStaticRef(Expr):
    """Static/global reference: ::pkg::Type::member
    
    Used for package-qualified references and global scope.
    
    Examples:
        ::std_pkg::sync_c -> ExprStaticRef(is_global=True, path=["std_pkg", "sync_c"])
        MyClass::static_method -> ExprStaticRef(is_global=False, path=["MyClass", "static_method"])
    """
    is_global: bool = dc.field(default=False)
    path: list[str] = dc.field(default_factory=list)


@dc.dataclass(kw_only=True)
class ExprCompileHas(Expr):
    """Compile-time existence check: compile has field
    
    Used for conditional compilation based on field/member existence.
    
    Example:
        compile has optional_field -> ExprCompileHas(target=ExprRefLocal("optional_field"))
    """
    target: Expr = dc.field()


@dc.dataclass(kw_only=True)
class ExprNull(Expr):
    """Null literal
    
    Represents the null value in PSS.
    """
    pass


# =============================================================================
# Interface-Protocol Expression Nodes (Completion, Queue)
# =============================================================================

@dc.dataclass(kw_only=True)
class CompletionAwaitExpr(Expr):
    """Represents ``await done`` — suspend until Completion is set.

    result_type is the DataType of the resolved value (T in Completion[T]).
    """
    completion_expr: Expr = dc.field()
    result_type: Optional['DataType'] = dc.field(default=None)


@dc.dataclass(kw_only=True)
class QueueGetExpr(Expr):
    """Represents ``await queue.get()`` — block until item available."""
    queue_expr: Expr = dc.field()
    result_type: Optional['DataType'] = dc.field(default=None)


# ---------------------------------------------------------------------------
# zdc built-in typed IR nodes
# These replace the string-matched ExprCall(ExprRefUnresolved("zdc.sext"), ...)
# pattern with proper typed nodes for synthesis, simulation, and the solver.
# ---------------------------------------------------------------------------

@dc.dataclass(kw_only=True)
class ExprSext(Expr):
    """Sign-extend *value* from *bits* to the target width.

    Equivalent to RTL: ``$signed($signed(value << (W-bits)) >>> (W-bits))``

    *bits* is always a compile-time constant (source bit-width).
    """
    value: Expr = dc.field()
    bits: int = dc.field()


@dc.dataclass(kw_only=True)
class ExprZext(Expr):
    """Zero-extend *value* to the lower *bits* and zero the rest.

    Equivalent to RTL: ``value[bits-1:0]``

    *bits* is always a compile-time constant.
    """
    value: Expr = dc.field()
    bits: int = dc.field()


@dc.dataclass(kw_only=True)
class ExprCbit(Expr):
    """Reify a boolean/comparison expression to a 0/1 integer.

    Equivalent to RTL: ``(expr ? 1'b1 : 1'b0)``
    """
    value: Expr = dc.field()


@dc.dataclass(kw_only=True)
class ExprSigned(Expr):
    """Tag *value* as signed for comparison and arithmetic purposes.

    Does not change the bit-pattern; affects how enclosing operations
    interpret the value (signed vs unsigned context).

    Equivalent to RTL: ``$signed(value)``
    """
    value: Expr = dc.field()

