
from __future__ import annotations
import dataclasses as dc
import enum
from typing import Any, Dict, List, Optional
from .base import Base
from .data_type import DataType
from .expr import Expr

class FieldKind(enum.Enum):
    """Kind of field in a component/class"""
    Field = enum.auto()          # Regular field
    Port = enum.auto()           # Port: Bundle subclass (API consumer)
    Export = enum.auto()         # Export: Bundle subclass (API provider)
    CallablePort = enum.auto()   # Port: Callable[[...], Awaitable[...]]
    ProtocolPort = enum.auto()   # Port: Protocol subclass (bundle of callables)
    CallableExport = enum.auto() # Export: Callable[[...], Awaitable[...]]
    ProtocolExport = enum.auto() # Export: Protocol subclass (bundle of callables)
    QueueField = enum.auto()     # zdc.Queue[T] bounded FIFO component field
    Input = enum.auto()          # PSS flow-object input reference
    Output = enum.auto()         # PSS flow-object output reference
    Lock = enum.auto()           # PSS lock (exclusive) resource claim on an action field
    Share = enum.auto()          # PSS share (concurrent-read) resource claim on an action field

class SignalDirection(enum.Enum):
    """Direction of hardware signals"""
    INPUT = enum.auto()
    OUTPUT = enum.auto()
    INOUT = enum.auto()

class RandKind(enum.Enum):
    """Randomisation kind for a field"""
    RAND  = enum.auto()   # random
    RANDC = enum.auto()   # random-cyclic

class FieldMetaKind(enum.Enum):
    """Kind value stored in Python dataclasses.field metadata under the 'kind' key"""
    FLOW_REF        = enum.auto()
    RESOURCE_REF    = enum.auto()
    POOL            = enum.auto()
    INDEXED_REGFILE = enum.auto()
    INDEXED_POOL    = enum.auto()
    INSTANCE        = enum.auto()
    REG             = enum.auto()
    ARRAY           = enum.auto()
    CONST           = enum.auto()
    BUNDLE          = enum.auto()
    MIRROR          = enum.auto()
    MONITOR         = enum.auto()
    PORT            = enum.auto()
    EXPORT          = enum.auto()
    TUPLE           = enum.auto()

class ClaimMode(enum.Enum):
    """Resource claim mode (mirrors FieldKind.Lock / FieldKind.Share)"""
    LOCK  = enum.auto()   # exclusive
    SHARE = enum.auto()   # concurrent-read

@dc.dataclass
class Bind(Base):
    lhs : Expr = dc.field()
    rhs : Expr = dc.field()

@dc.dataclass
class BindSet(Base):
    binds : List[Bind] = dc.field(default_factory=list)

@dc.dataclass(kw_only=True)
class Field(Base):
    name : str = dc.field()
    datatype : DataType = dc.field()
    kind : FieldKind = dc.field(default=FieldKind.Field)
    bindset : BindSet = dc.field(default_factory=BindSet)
    direction : Optional[SignalDirection] = dc.field(default=None)
    clock : Optional[Expr] = dc.field(default=None)
    initial_value : Optional[Expr] = dc.field(default=None)
    width_expr : Optional[Expr] = dc.field(default=None)  # Width expression (e.g., lambda s:s.WIDTH)
    kwargs_expr : Optional[Expr] = dc.field(default=None)  # Kwargs for instantiation (e.g., lambda s:dict(W=s.WIDTH))
    is_const : bool = dc.field(default=False)  # True for const fields (structural type parameters)
    is_reg : bool = dc.field(default=False)    # True for Reg[T] register fields
    
    # Constraint solver metadata
    rand_kind : Optional[RandKind] = dc.field(default=None)  # RAND, RANDC, or None
    domain : Optional[tuple] = dc.field(default=None)  # Domain constraint (min, max) tuple or list of values
    size : Optional[int] = dc.field(default=None)  # Array size (for fixed-size arrays)
    max_size : Optional[int] = dc.field(default=None)  # Maximum size for variable-size arrays
    is_variable_size : bool = dc.field(default=False)  # True if array has variable size
    pragmas : Dict[str, Any] = dc.field(default_factory=dict)  # From ``# zdc: key=value, flag`` comments
    reset_value : Optional[Any] = dc.field(default=None)  # Reset value from output(reset=N) or reg(reset=N)
    
    @property
    def is_array(self) -> bool:
        """Returns True if this field represents an array (has a size)."""
        from .data_type import DataTypeArray
        return self.size is not None or self.is_variable_size or isinstance(self.datatype, DataTypeArray)

@dc.dataclass(kw_only=True)
class FieldInOut(Field):
    is_out : bool = dc.field()
    is_inout : bool = dc.field(default=False)




@dc.dataclass(kw_only=True)
class Pool(Base):
    """PSS pool declaration within a component."""
    name : str = dc.field()
    element_type_name : str = dc.field()           # flow-object type name
    element_type : Optional[DataType] = dc.field(default=None)
    capacity : Optional[int] = dc.field(default=None)  # None = unbounded; N = bounded pool declared as `pool [N] T name;`


@dc.dataclass(kw_only=True)
class PoolBind(Base):
    """PSS bind directive: pool_name -> field_paths or wildcard."""
    pool_name : str = dc.field()
    field_paths : list = dc.field(default_factory=list)  # empty = wildcard (*)
    is_wildcard : bool = dc.field(default=False)
